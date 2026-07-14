from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .http import HttpClient
from .utils import content_hash, utc_now_iso


@dataclass
class LLMRun:
    provider: str
    model: str | None
    status: str
    output: dict[str, Any] | None
    error: str | None
    retry_count: int
    fallback_used: bool
    input_hash: str
    generated_at: str
    task_name: str

    def audit(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("output", None)
        return data


class ModelRouter:
    """Task-aware sequential model router.

    A provider is never called in parallel with another provider for the same
    task.  The next provider is attempted only after transport failure,
    unusable JSON, or caller-side validation rejection.
    """

    def __init__(self, root: Path, policy: dict[str, Any], client: HttpClient | None = None) -> None:
        self.root = root
        self.policy = policy
        self.client = client or HttpClient(timeout=int(policy.get("model_timeout_seconds", 75)))
        primary = policy.get("primary_provider", "gemini")
        fallbacks = [x for x in policy.get("fallback_providers", []) if x != primary]
        self.default_providers = [primary, *fallbacks]
        if "deterministic" not in self.default_providers:
            self.default_providers.append("deterministic")
        self._model_lists: dict[str, list[str]] = {}
        self._selected_models: dict[str, str | None] = {}

    def _prompt(self, name: str) -> str:
        return (self.root / "prompts" / f"{name}.txt").read_text(encoding="utf-8")

    def _json_from_text(self, text: str) -> dict[str, Any]:
        text = (text or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text.removeprefix("json").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("Model output must be a JSON object")
        return data

    def _gemini_models(self, key: str) -> list[str]:
        data, _ = self.client.get_json(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": key},
            max_attempts=2,
        )
        if not data:
            return []
        models: list[str] = []
        for model in data.get("models", []):
            if "generateContent" in (model.get("supportedGenerationMethods") or []):
                name = str(model.get("name") or "").removeprefix("models/")
                if name:
                    models.append(name)
        return models

    def _github_models(self, token: str) -> list[str]:
        data, _ = self.client.get_json(
            "https://models.github.ai/catalog/models",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2026-03-10",
            },
            max_attempts=2,
        )
        return [str(x.get("id")) for x in (data or []) if x.get("id")]

    def _groq_models(self, key: str) -> list[str]:
        data, _ = self.client.get_json(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            max_attempts=2,
        )
        return [str(x.get("id")) for x in (data or {}).get("data", []) if x.get("id")]

    @staticmethod
    def _choose(models: list[str], preferred: str | None, keywords: Iterable[str]) -> str | None:
        if preferred and preferred in models:
            return preferred
        for keyword in keywords:
            hit = next((model for model in models if keyword.casefold() in model.casefold()), None)
            if hit:
                return hit
        return models[0] if models else preferred

    @staticmethod
    def _route_key(task_name: str) -> str:
        if task_name in {"bilingual_translation_batch", "translation_repair"}:
            return "translation"
        if task_name == "literature_analysis":
            return "literature_analysis"
        if task_name == "official_notice_analysis":
            return "official_notice_analysis"
        if task_name == "media_news_analysis":
            return "media_news_analysis"
        if task_name == "daily_synthesis":
            return "daily_synthesis"
        if task_name == "pathogen_bootstrap":
            return "pathogen_bootstrap"
        return task_name

    def provider_sequence(self, task_name: str) -> list[str]:
        routes = self.policy.get("task_routes") or {}
        selected = routes.get(self._route_key(task_name)) or routes.get(task_name) or self.default_providers
        providers: list[str] = []
        for provider in selected:
            provider = str(provider)
            if provider and provider not in providers:
                providers.append(provider)
        if "deterministic" not in providers:
            providers.append("deterministic")
        return providers

    def _provider_credentials(self, provider: str) -> tuple[str, str | None]:
        if provider == "gemini":
            return os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_AI_STUDIO_API_KEY", ""), os.getenv("GEMINI_MODEL")
        if provider == "github_models":
            return os.getenv("GITHUB_MODELS_TOKEN", "") or os.getenv("GITHUB_TOKEN", ""), os.getenv("GITHUB_MODELS_MODEL")
        if provider == "groq":
            return os.getenv("GROQ_API_KEY", ""), os.getenv("GROQ_MODEL")
        return "", None

    def _model_for(self, provider: str) -> tuple[str, str]:
        if provider in self._selected_models and self._selected_models[provider]:
            key, _ = self._provider_credentials(provider)
            return key, str(self._selected_models[provider])

        key, preferred = self._provider_credentials(provider)
        if not key:
            raise RuntimeError(f"Missing credentials for {provider}")

        if provider not in self._model_lists:
            if provider == "gemini":
                self._model_lists[provider] = self._gemini_models(key)
            elif provider == "github_models":
                self._model_lists[provider] = self._github_models(key)
            elif provider == "groq":
                self._model_lists[provider] = self._groq_models(key)
            else:
                raise RuntimeError(f"Unsupported provider: {provider}")

        preferences = {
            "github_models": ["gpt-4.1-mini", "gpt-4o-mini", "phi-4-mini", "phi", "llama"],
            "gemini": ["flash-lite", "flash", "pro"],
            "groq": ["gpt-oss-20b", "qwen", "llama", "gemma"],
        }
        model = self._choose(self._model_lists[provider], preferred, preferences.get(provider, []))
        if not model:
            raise RuntimeError(f"No usable {provider} model available")
        self._selected_models[provider] = model
        return key, model

    def _call_gemini(self, key: str, model: str, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": prompt
                            + "\n\nINPUT JSON:\n"
                            + json.dumps(payload, ensure_ascii=False)
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
        data, audit = self.client.post_json(url, params={"key": key}, json=body, max_attempts=1)
        if not data:
            raise RuntimeError(audit.error or f"Gemini HTTP {audit.status_code}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise RuntimeError("Gemini returned no candidates")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(str(part.get("text") or "") for part in parts)
        return self._json_from_text(text)

    def _call_openai_compatible(
        self,
        url: str,
        key: str,
        model: str,
        prompt: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        data, audit = self.client.post_json(url, headers=request_headers, json=body, max_attempts=1)
        if not data:
            raise RuntimeError(audit.error or f"Model HTTP {audit.status_code}")
        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return self._json_from_text(text)

    def run_provider(
        self,
        task_name: str,
        payload: dict[str, Any],
        provider: str,
        *,
        fallback_used: bool = False,
    ) -> LLMRun:
        input_hash = content_hash(payload)
        if provider == "deterministic":
            return LLMRun(
                provider="deterministic",
                model=None,
                status="fallback",
                output=None,
                error="No validated LLM output; deterministic source data retained.",
                retry_count=0,
                fallback_used=fallback_used,
                input_hash=input_hash,
                generated_at=utc_now_iso(),
                task_name=task_name,
            )

        prompt = self._prompt(task_name)
        max_retries = int(self.policy.get("max_retries_per_provider", 1))
        model: str | None = None
        last_error: Exception | None = None
        try:
            key, model = self._model_for(provider)
        except Exception as exc:
            return LLMRun(
                provider=provider,
                model=model,
                status="unavailable",
                output=None,
                error=f"{type(exc).__name__}: {exc}",
                retry_count=0,
                fallback_used=fallback_used,
                input_hash=input_hash,
                generated_at=utc_now_iso(),
                task_name=task_name,
            )

        for attempt in range(max_retries + 1):
            try:
                if provider == "gemini":
                    output = self._call_gemini(key, model, prompt, payload)
                elif provider == "github_models":
                    output = self._call_openai_compatible(
                        "https://models.github.ai/inference/chat/completions",
                        key,
                        model,
                        prompt,
                        payload,
                        {
                            "Accept": "application/vnd.github+json",
                            "X-GitHub-Api-Version": "2026-03-10",
                        },
                    )
                elif provider == "groq":
                    output = self._call_openai_compatible(
                        "https://api.groq.com/openai/v1/chat/completions",
                        key,
                        model,
                        prompt,
                        payload,
                    )
                else:
                    raise RuntimeError(f"Unsupported provider: {provider}")
                return LLMRun(
                    provider=provider,
                    model=model,
                    status="success",
                    output=output,
                    error=None,
                    retry_count=attempt,
                    fallback_used=fallback_used,
                    input_hash=input_hash,
                    generated_at=utc_now_iso(),
                    task_name=task_name,
                )
            except Exception as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(min(8, 2**attempt))

        return LLMRun(
            provider=provider,
            model=model,
            status="failed",
            output=None,
            error=f"{type(last_error).__name__}: {last_error}" if last_error else "Unknown model error",
            retry_count=max_retries,
            fallback_used=fallback_used,
            input_hash=input_hash,
            generated_at=utc_now_iso(),
            task_name=task_name,
        )

    def run(self, task_name: str, payload: dict[str, Any]) -> LLMRun:
        fallback_used = False
        for provider in self.provider_sequence(task_name):
            run = self.run_provider(task_name, payload, provider, fallback_used=fallback_used)
            if run.output is not None:
                return run
            fallback_used = True
        return self.run_provider(task_name, payload, "deterministic", fallback_used=True)
