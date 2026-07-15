from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
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
    model_attempts: list[dict[str, Any]] = field(default_factory=list)

    def audit(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("output", None)
        return data


class ModelRouter:
    """Task-aware sequential provider router with intra-provider model rotation.

    Providers are still strictly sequential for a task.  Within one provider,
    several currently available models may be tried in ranked order when a
    default model is retired, unsupported, rate-limited, or returns invalid JSON.
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
        self._successful_model: dict[tuple[str, str], str] = {}
        self._rejected_models: dict[tuple[str, str], set[str]] = {}

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
        models: list[str] = []
        for model in (data or {}).get("models", []):
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
                "X-GitHub-Api-Version": "2022-11-28",
            },
            max_attempts=2,
        )
        models: list[str] = []
        for row in data or []:
            model_id = str(row.get("id") or "")
            task = str(row.get("task") or row.get("task_type") or "").casefold()
            if model_id and (not task or "chat" in task or "completion" in task or "text" in task):
                models.append(model_id)
        return models

    def _groq_models(self, key: str) -> list[str]:
        data, _ = self.client.get_json(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            max_attempts=2,
        )
        excluded = ("whisper", "guard", "orpheus", "tts", "speech", "embedding", "moderation")
        models: list[str] = []
        for row in (data or {}).get("data", []):
            model_id = str(row.get("id") or "")
            active = row.get("active", True)
            if not model_id or active is False or any(token in model_id.casefold() for token in excluded):
                continue
            models.append(model_id)
        return models

    @staticmethod
    def _route_key(task_name: str) -> str:
        if task_name in {"bilingual_translation_batch", "translation_repair"}:
            return "translation"
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

    def _discover_models(self, provider: str, key: str) -> list[str]:
        if provider not in self._model_lists:
            if provider == "gemini":
                self._model_lists[provider] = self._gemini_models(key)
            elif provider == "github_models":
                self._model_lists[provider] = self._github_models(key)
            elif provider == "groq":
                self._model_lists[provider] = self._groq_models(key)
            else:
                raise RuntimeError(f"Unsupported provider: {provider}")
        return list(self._model_lists[provider])

    @staticmethod
    def _rank(models: list[str], preferred: str | None, keywords: Iterable[str]) -> list[str]:
        unique: list[str] = []
        for model in models:
            if model and model not in unique:
                unique.append(model)
        keyword_list = list(keywords)

        def score(model: str) -> tuple[int, int, str]:
            if preferred and model == preferred:
                return (0, 0, model)
            low = model.casefold()
            for index, keyword in enumerate(keyword_list, 1):
                if keyword.casefold() in low:
                    return (1, index, model)
            return (2, 999, model)

        return sorted(unique, key=score)

    def _model_candidates(self, provider: str, task_name: str) -> tuple[str, list[str]]:
        key, preferred = self._provider_credentials(provider)
        if not key:
            raise RuntimeError(f"Missing credentials for {provider}")
        models = self._discover_models(provider, key)
        if provider == "groq":
            excluded = ("whisper", "guard", "orpheus", "tts", "speech", "embedding", "moderation")
            models = [model for model in models if not any(token in model.casefold() for token in excluded)]
        task_key = self._route_key(task_name)
        preferences = {
            "github_models": {
                "translation": ["gpt-4.1-mini", "gpt-4o-mini", "phi-4", "llama", "qwen"],
                "default": ["gpt-4.1-mini", "gpt-4o-mini", "llama", "phi", "qwen"],
            },
            "gemini": {
                "translation": ["flash-lite", "flash", "pro"],
                "default": ["flash", "flash-lite", "pro"],
            },
            "groq": {
                "translation": ["qwen", "gpt-oss-20b", "llama-4", "llama-3", "gemma"],
                "default": ["gpt-oss-120b", "qwen", "gpt-oss-20b", "llama-4", "llama-3", "gemma", "compound"],
            },
        }
        provider_pref = preferences.get(provider, {})
        ranked = self._rank(models, preferred, provider_pref.get(task_key, provider_pref.get("default", [])))
        rejected = self._rejected_models.get((provider, task_key), set())
        ranked = [model for model in ranked if model not in rejected]
        successful = self._successful_model.get((provider, task_key))
        if successful in ranked:
            ranked.remove(successful)
            ranked.insert(0, successful)
        max_models_cfg = self.policy.get("max_models_per_provider") or {}
        maximum = max(1, int(max_models_cfg.get(provider, 1 if provider != "groq" else 4)))
        return key, ranked[:maximum]

    def _call_gemini(self, key: str, model: str, prompt: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt + "\n\nINPUT JSON:\n" + json.dumps(payload, ensure_ascii=False)}]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
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
        request_headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
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
        if not data and audit.status_code in {400, 404, 422}:
            # Some otherwise usable hosted models do not implement response_format.
            # Retry once without it and parse the JSON object from plain text.
            body.pop("response_format", None)
            data, audit = self.client.post_json(url, headers=request_headers, json=body, max_attempts=1)
        if not data:
            raise RuntimeError(audit.error or f"Model HTTP {audit.status_code}")
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return self._json_from_text(text)

    def reject_model(self, provider: str, task_name: str, model: str | None, reason: str = "validation_failed") -> None:
        """Exclude a model for the rest of this workflow after semantic validation fails."""
        if not model:
            return
        task_key = self._route_key(task_name)
        self._rejected_models.setdefault((provider, task_key), set()).add(model)
        if self._successful_model.get((provider, task_key)) == model:
            self._successful_model.pop((provider, task_key), None)

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
        max_retries = int(self.policy.get("max_retries_per_model", self.policy.get("max_retries_per_provider", 1)))
        model_attempts: list[dict[str, Any]] = []
        try:
            key, models = self._model_candidates(provider, task_name)
        except Exception as exc:
            return LLMRun(
                provider=provider,
                model=None,
                status="unavailable",
                output=None,
                error=f"{type(exc).__name__}: {exc}",
                retry_count=0,
                fallback_used=fallback_used,
                input_hash=input_hash,
                generated_at=utc_now_iso(),
                task_name=task_name,
                model_attempts=model_attempts,
            )
        if not models:
            return LLMRun(
                provider=provider,
                model=None,
                status="unavailable",
                output=None,
                error=f"No usable {provider} model available after dynamic discovery",
                retry_count=0,
                fallback_used=fallback_used,
                input_hash=input_hash,
                generated_at=utc_now_iso(),
                task_name=task_name,
                model_attempts=model_attempts,
            )

        total_retries = 0
        last_error: Exception | None = None
        for model in models:
            for attempt in range(max_retries + 1):
                started = time.monotonic()
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
                            {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
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
                    model_attempts.append({"model": model, "status": "success", "retry": attempt, "elapsed_ms": int((time.monotonic() - started) * 1000)})
                    self._successful_model[(provider, self._route_key(task_name))] = model
                    return LLMRun(
                        provider=provider,
                        model=model,
                        status="success",
                        output=output,
                        error=None,
                        retry_count=total_retries,
                        fallback_used=fallback_used,
                        input_hash=input_hash,
                        generated_at=utc_now_iso(),
                        task_name=task_name,
                        model_attempts=model_attempts,
                    )
                except Exception as exc:
                    last_error = exc
                    total_retries += 1
                    model_attempts.append({"model": model, "status": "failed", "retry": attempt, "elapsed_ms": int((time.monotonic() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"})
                    if attempt < max_retries:
                        time.sleep(min(8, 2**attempt))
            # The current model failed all retries. Continue to the next active
            # model in the same provider before escalating to another provider.

        return LLMRun(
            provider=provider,
            model=models[-1] if models else None,
            status="failed",
            output=None,
            error=f"{type(last_error).__name__}: {last_error}" if last_error else "All dynamically discovered models failed",
            retry_count=total_retries,
            fallback_used=fallback_used,
            input_hash=input_hash,
            generated_at=utc_now_iso(),
            task_name=task_name,
            model_attempts=model_attempts,
        )

    def run(self, task_name: str, payload: dict[str, Any]) -> LLMRun:
        fallback_used = False
        for provider in self.provider_sequence(task_name):
            run = self.run_provider(task_name, payload, provider, fallback_used=fallback_used)
            if run.output is not None:
                return run
            fallback_used = True
        return self.run_provider(task_name, payload, "deterministic", fallback_used=True)
