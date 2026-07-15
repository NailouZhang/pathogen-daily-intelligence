from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, asdict
from typing import Any

from .config import env_bool, env_int
from .markup import protect_scientific_markup, restore_scientific_markup
from .translation_quality import (
    assess_translation,
    clean_translation_source,
    literal_card_summary,
    repair_translation_fields,
)
from .utils import content_hash, normalize_space, utc_now_iso


@dataclass
class LocalMTResult:
    status: str
    fields: dict[str, Any] | None
    audit: dict[str, Any]
    error: str | None = None


class LocalMachineTranslator:
    """Lazy CPU MarianMT fallback and independent translation reference.

    Dependencies and model weights are loaded only when a remote translation must be
    verified or when all remote providers fail.  A failure here is audited and does
    not prevent deterministic publication.
    """

    def __init__(self, policy: dict[str, Any] | None = None, glossary: dict[str, Any] | None = None):
        policy = policy or {}
        self.glossary = glossary or {}
        self.enabled = env_bool("PDI_ENABLE_LOCAL_MT", bool(policy.get("enable_local_mt", True)))
        self.verify_remote = env_bool(
            "PDI_VERIFY_LLM_WITH_LOCAL_MT",
            bool(policy.get("verify_llm_with_local_mt", True)),
        )
        self.model_id = os.getenv("PDI_LOCAL_MT_MODEL") or str(
            policy.get("local_mt_model") or "Helsinki-NLP/opus-mt-en-zh"
        )
        self.back_model_id = os.getenv("PDI_LOCAL_MT_BACK_MODEL") or str(
            policy.get("local_mt_back_model") or "Helsinki-NLP/opus-mt-zh-en"
        )
        self.backtranslate_enabled = env_bool(
            "PDI_LOCAL_MT_BACKTRANSLATE",
            bool(policy.get("local_mt_backtranslate", False)),
        )
        self.max_chars = env_int("PDI_LOCAL_MT_MAX_CHARS", int(policy.get("local_mt_max_chars", 6000)))
        self.chunk_chars = env_int("PDI_LOCAL_MT_CHUNK_CHARS", int(policy.get("local_mt_chunk_chars", 420)))
        self._models: dict[str, tuple[Any, Any, Any]] = {}
        self._load_errors: dict[str, str] = {}
        self._reference_cache: dict[str, str | None] = {}

    def _load(self, model_id: str) -> tuple[Any, Any, Any]:
        if model_id in self._models:
            return self._models[model_id]
        if model_id in self._load_errors:
            raise RuntimeError(self._load_errors[model_id])
        try:
            import torch  # type: ignore
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore

            tokenizer = AutoTokenizer.from_pretrained(model_id)
            model = AutoModelForSeq2SeqLM.from_pretrained(model_id)
            model.eval()
            self._models[model_id] = (tokenizer, model, torch)
            return self._models[model_id]
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._load_errors[model_id] = error
            raise RuntimeError(error) from exc

    def _chunks(self, text: str) -> list[str]:
        text = normalize_space(text)
        if not text:
            return []
        sentences = re.split(r"(?<=[.!?。！？；;])\s+", text)
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            sentence = normalize_space(sentence)
            if not sentence:
                continue
            if len(sentence) > self.chunk_chars:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(sentence[i : i + self.chunk_chars] for i in range(0, len(sentence), self.chunk_chars))
                continue
            proposed = (current + " " + sentence).strip()
            if current and len(proposed) > self.chunk_chars:
                chunks.append(current)
                current = sentence
            else:
                current = proposed
        if current:
            chunks.append(current)
        return chunks

    def _translate_plain(self, text: str, model_id: str) -> str:
        tokenizer, model, torch = self._load(model_id)
        outputs: list[str] = []
        chunks = self._chunks(text[: self.max_chars])
        for start in range(0, len(chunks), 8):
            batch = chunks[start : start + 8]
            encoded = tokenizer(batch, return_tensors="pt", padding=True, truncation=True, max_length=512)
            with torch.no_grad():
                generated = model.generate(**encoded, max_new_tokens=640, num_beams=3, do_sample=False)
            outputs.extend(tokenizer.batch_decode(generated, skip_special_tokens=True))
        result = normalize_space(" ".join(outputs))
        try:
            from opencc import OpenCC  # type: ignore
            result = OpenCC("t2s").convert(result)
        except Exception:
            pass
        return result

    def translate_text(self, text: str | None, *, model_id: str | None = None) -> str | None:
        if not self.enabled or not text:
            return None
        model_id = model_id or self.model_id
        protected, mapping = protect_scientific_markup(text)
        token_pattern = re.compile(r"(\[\[PDI_[A-Z0-9_]+\]\])")
        pieces = token_pattern.split(protected)
        translatable = [piece for piece in pieces if piece and not token_pattern.fullmatch(piece)]
        translated_iter = iter([self._translate_plain(piece, model_id) for piece in translatable])
        rebuilt: list[str] = []
        for piece in pieces:
            if not piece:
                continue
            if token_pattern.fullmatch(piece):
                rebuilt.append(piece)
            else:
                rebuilt.append(next(translated_iter))
        return restore_scientific_markup(normalize_space(" ".join(rebuilt)), mapping)

    def reference_title(self, source_title: str) -> tuple[str | None, dict[str, Any]]:
        audit = {
            "provider": "local_marian_reference",
            "model": self.model_id,
            "status": "skipped",
            "generated_at": utc_now_iso(),
        }
        if not self.enabled or not self.verify_remote or not source_title:
            audit["reason"] = "LOCAL_REFERENCE_DISABLED_OR_EMPTY"
            return None, audit
        cache_key = content_hash(source_title)
        if cache_key in self._reference_cache:
            audit.update({"status": "cache_hit", "cache_hit": True})
            return self._reference_cache[cache_key], audit
        started = time.monotonic()
        try:
            translated = self.translate_text(source_title)
            fields, repairs = repair_translation_fields(source_title, None, {"translated_title_zh": translated}, self.glossary)
            audit.update(
                {
                    "status": "success",
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "repairs": repairs,
                }
            )
            self._reference_cache[cache_key] = fields.get("translated_title_zh")
            return fields.get("translated_title_zh"), audit
        except Exception as exc:
            audit.update(
                {
                    "status": "failed",
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            return None, audit

    def translate_record(self, item: dict[str, Any], kind: str) -> LocalMTResult:
        title = str((item.get("title") or {}).get("original") or "")
        raw_body = (
            (item.get("abstract") or {}).get("original")
            if kind == "work"
            else (item.get("content") or {}).get("translation_text")
            or (item.get("content") or {}).get("excerpt")
        )
        body = clean_translation_source(raw_body, kind=kind)
        audit = {
            "task_name": "local_machine_translation",
            "provider": "local_marian",
            "model": self.model_id,
            "status": "failed",
            "validation_status": "failed",
            "input_hash": content_hash({"title": title, "body": body, "kind": kind}),
            "generated_at": utc_now_iso(),
            "fallback_used": True,
            "translation_mode": "title_and_text" if body else "title_only",
        }
        if not self.enabled:
            audit["error"] = "LOCAL_MT_DISABLED"
            return LocalMTResult("unavailable", None, audit, audit["error"])
        started = time.monotonic()
        try:
            title_zh = self.translate_text(title)
            text_zh = self.translate_text(body) if body else None
            fields = {
                "translated_title_zh": title_zh,
                "translated_text_zh": text_zh,
                "display_summary_zh": literal_card_summary(text_zh) if body else None,
                "display_summary_en": literal_card_summary(body) if body else None,
                "uncertainties": [],
            }
            fields, repairs = repair_translation_fields(title, body, fields, self.glossary)
            title_quality = assess_translation(title, fields.get("translated_title_zh"), field_name="title", glossary=self.glossary)
            text_quality = (
                assess_translation(body, fields.get("translated_text_zh"), field_name="text", glossary=self.glossary)
                if body
                else {"valid": True, "errors": [], "warnings": [], "metrics": {}}
            )
            errors = title_quality["errors"] + text_quality["errors"]
            audit.update(
                {
                    "status": "success" if not errors else "failed",
                    "validation_status": "passed_local_mt_with_glossary_repair" if repairs and not errors else ("passed_local_mt" if not errors else "failed"),
                    "validation_errors": errors,
                    "validation_warnings": title_quality["warnings"] + text_quality["warnings"],
                    "quality_metrics": {"title": title_quality["metrics"], "text": text_quality["metrics"]},
                    "repairs": repairs,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
            )
            if errors:
                return LocalMTResult("failed", None, audit, ";".join(errors))
            return LocalMTResult("success", fields, audit)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            audit.update({"error": error, "elapsed_ms": int((time.monotonic() - started) * 1000)})
            return LocalMTResult("failed", None, audit, error)

    def backtranslate(self, candidate_zh: str) -> tuple[str | None, dict[str, Any]]:
        audit = {
            "provider": "local_marian_backtranslation",
            "model": self.back_model_id,
            "status": "skipped",
            "generated_at": utc_now_iso(),
        }
        if not self.enabled or not self.backtranslate_enabled or not candidate_zh:
            audit["reason"] = "BACKTRANSLATION_DISABLED_OR_EMPTY"
            return None, audit
        started = time.monotonic()
        try:
            translated = self.translate_text(candidate_zh, model_id=self.back_model_id)
            audit.update({"status": "success", "elapsed_ms": int((time.monotonic() - started) * 1000)})
            return translated, audit
        except Exception as exc:
            audit.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
            return None, audit
