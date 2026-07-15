from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from .http import HttpClient
from .utils import clean_space, utc_now_iso


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict[str, Any] | list[Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start_obj = raw.find("{")
        end_obj = raw.rfind("}")
        start_arr = raw.find("[")
        end_arr = raw.rfind("]")
        candidates: list[str] = []
        if start_obj >= 0 and end_obj > start_obj:
            candidates.append(raw[start_obj : end_obj + 1])
        if start_arr >= 0 and end_arr > start_arr:
            candidates.append(raw[start_arr : end_arr + 1])
        for candidate in candidates:
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise LLMError("Model response did not contain valid JSON")


@dataclass
class LLMResult:
    data: dict[str, Any] | list[Any]
    provider: str
    model: str
    attempts: list[dict[str, Any]]


class LLMRouter:
    def __init__(self, http: HttpClient, gemini_key: str = "", groq_key: str = "") -> None:
        self.http = http
        self.gemini_key = gemini_key
        self.groq_key = groq_key
        self._gemini_models: list[str] | None = None
        self._groq_models: list[str] | None = None

    @property
    def available(self) -> bool:
        return bool(self.gemini_key or self.groq_key)

    def _discover_gemini_models(self) -> list[str]:
        if self._gemini_models is not None:
            return self._gemini_models
        preferred = [
            os.getenv("GEMINI_MODEL", "").strip(),
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        ]
        discovered: list[str] = []
        if self.gemini_key:
            try:
                payload = self.http.get_json(
                    "https://generativelanguage.googleapis.com/v1beta/models",
                    params={"key": self.gemini_key, "pageSize": 100},
                )
                for model in payload.get("models", []):
                    methods = model.get("supportedGenerationMethods") or []
                    name = str(model.get("name", "")).removeprefix("models/")
                    if "generateContent" in methods and name and not any(
                        bad in name.lower() for bad in ("image", "embedding", "aqa")
                    ):
                        discovered.append(name)
            except Exception:
                pass
        ordered: list[str] = []
        for name in preferred + discovered:
            if name and name not in ordered:
                ordered.append(name)
        self._gemini_models = ordered[:8]
        return self._gemini_models

    def _discover_groq_models(self) -> list[str]:
        if self._groq_models is not None:
            return self._groq_models
        configured = os.getenv("GROQ_MODEL", "").strip()
        discovered: list[str] = []
        if self.groq_key:
            try:
                payload = self.http.get_json(
                    "https://api.groq.com/openai/v1/models",
                    headers={"Authorization": f"Bearer {self.groq_key}"},
                )
                for item in payload.get("data", []):
                    name = clean_space(item.get("id"))
                    low = name.lower()
                    if not name or any(
                        bad in low
                        for bad in (
                            "whisper",
                            "speech",
                            "tts",
                            "guard",
                            "moderation",
                            "embedding",
                            "compound",
                        )
                    ):
                        continue
                    discovered.append(name)
            except Exception:
                pass
        def score(name: str) -> tuple[int, str]:
            low = name.lower()
            value = 0
            if "gpt-oss" in low:
                value += 100
            if "qwen" in low:
                value += 90
            if "llama-4" in low:
                value += 80
            if "llama-3.3" in low or "70b" in low:
                value += 70
            if "8b" in low:
                value += 20
            return (-value, name)
        ordered = [configured] if configured else []
        for name in sorted(discovered, key=score):
            if name and name not in ordered:
                ordered.append(name)
        self._groq_models = ordered[:10]
        return self._groq_models

    def _gemini_call(self, model: str, system: str, prompt: str, temperature: float) -> Any:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
            },
        }
        response = self.http.request("POST", url, params={"key": self.gemini_key}, json=payload, timeout=100)
        body = response.json()
        candidates = body.get("candidates") or []
        if not candidates:
            raise LLMError(f"Gemini returned no candidates: {body}")
        parts = candidates[0].get("content", {}).get("parts", [])
        text = "".join(str(part.get("text", "")) for part in parts)
        return _extract_json(text)

    def _groq_call(self, model: str, system: str, prompt: str, temperature: float) -> Any:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        response = self.http.request(
            "POST",
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.groq_key}"},
            json=payload,
            timeout=100,
        )
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            raise LLMError(f"Groq returned no choices: {body}")
        return _extract_json(choices[0].get("message", {}).get("content", ""))

    def json_task(
        self,
        *,
        system: str,
        prompt: str,
        provider_order: tuple[str, ...] = ("gemini", "groq"),
        temperature: float = 0.1,
        validator: Any | None = None,
        max_models_per_provider: int = 3,
    ) -> LLMResult:
        attempts: list[dict[str, Any]] = []
        for provider in provider_order:
            if provider == "gemini" and self.gemini_key:
                models = self._discover_gemini_models()[:max_models_per_provider]
                caller = self._gemini_call
            elif provider == "groq" and self.groq_key:
                models = self._discover_groq_models()[:max_models_per_provider]
                caller = self._groq_call
            else:
                continue
            for model in models:
                attempt = {"provider": provider, "model": model, "at": utc_now_iso()}
                try:
                    data = caller(model, system, prompt, temperature)
                    if validator:
                        valid, reason = validator(data)
                        if not valid:
                            raise LLMError(f"validation_failed: {reason}")
                    attempt["status"] = "success"
                    attempts.append(attempt)
                    return LLMResult(data=data, provider=provider, model=model, attempts=attempts)
                except Exception as exc:
                    attempt.update({"status": "failed", "error": clean_space(exc)[:500]})
                    attempts.append(attempt)
        raise LLMError(json.dumps(attempts, ensure_ascii=False))
