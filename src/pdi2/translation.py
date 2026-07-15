from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from deep_translator import GoogleTranslator, MyMemoryTranslator

from .llm import LLMError, LLMRouter
from .utils import clean_space, extract_numbers, sha256_text, truncate


DEFAULT_REPAIRS = {
    "汉塔病毒": "汉坦病毒",
    "韩坦病毒": "汉坦病毒",
    "宋病毒": "汉坦病毒",
    "安第斯汉坦病毒": "安第斯病毒",
    "安第斯 hantavirus": "安第斯病毒",
    "汉坦病毒肆虐的": "发生汉坦病毒疫情的",
    "汉坦病毒肆虐": "发生汉坦病毒疫情",
    "明显的汉坦病毒疫情": "疑似汉坦病毒疫情",
    "明显汉坦病毒疫情": "疑似汉坦病毒疫情",
}


def _glossary(profile: dict[str, Any]) -> list[dict[str, str]]:
    rows = profile.get("translation_glossary") or []
    out: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            source = clean_space(row.get("source"))
            target = clean_space(row.get("target"))
            if source and target:
                out.append({"source": source, "target": target})
    return out


def _protect(text: str, glossary: list[dict[str, str]]) -> tuple[str, dict[str, str]]:
    protected = text
    mapping: dict[str, str] = {}
    for index, row in enumerate(sorted(glossary, key=lambda x: len(x["source"]), reverse=True)):
        pattern = re.compile(re.escape(row["source"]), flags=re.I)
        if not pattern.search(protected):
            continue
        token = f"__PDI_TERM_{index:03d}__"
        protected = pattern.sub(token, protected)
        mapping[token] = row["target"]
    return protected, mapping


def _restore(text: str, mapping: dict[str, str]) -> str:
    value = text
    for token, target in mapping.items():
        value = value.replace(token, target)
        value = value.replace(token.lower(), target)
    return value


def _repair_zh(text: str, glossary: list[dict[str, str]]) -> str:
    value = clean_space(text)
    for wrong, correct in DEFAULT_REPAIRS.items():
        value = value.replace(wrong, correct)
    for row in glossary:
        source, target = row["source"], row["target"]
        value = re.sub(re.escape(source), target, value, flags=re.I)
    value = value.replace(" ,", "，").replace(",", "，")
    value = value.replace(" ;", "；").replace(";", "；")
    value = value.replace(" :", "：")
    value = re.sub(r"\s+([。！？；，])", r"\1", value)
    return clean_space(value)


def _looks_chinese(text: str, source: str = "") -> tuple[bool, str]:
    value = clean_space(text)
    if not value:
        return False, "empty"
    chinese = len(re.findall(r"[\u4e00-\u9fff]", value))
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fff]", value))
    if chinese < 2 or (letters and chinese / letters < 0.28):
        return False, "insufficient_chinese"
    if re.search(r"(.)\1{5,}", value):
        return False, "repeated_character_gibberish"
    if any(bad in value for bad in ("宋病毒", "汉塔病毒", "韩坦病毒")):
        return False, "forbidden_pathogen_translation"
    src_numbers = extract_numbers(source)
    dst_numbers = extract_numbers(value)
    if src_numbers and not set(src_numbers).issubset(set(dst_numbers)):
        return False, "number_loss"
    return True, "ok"


def _python_translate(text: str) -> tuple[str, str]:
    errors: list[str] = []
    try:
        translated = GoogleTranslator(source="auto", target="zh-CN").translate(text)
        if translated:
            return clean_space(translated), "python_google_translate"
    except Exception as exc:
        errors.append(str(exc))
    try:
        translated = MyMemoryTranslator(source="en", target="zh-CN").translate(text)
        if translated:
            return clean_space(translated), "python_mymemory"
    except Exception as exc:
        errors.append(str(exc))
    raise RuntimeError("; ".join(errors) or "python translators unavailable")


def translate_text(
    text: str,
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompt_text: str,
    cache: dict[str, Any],
    max_chars: int = 2600,
) -> tuple[str, dict[str, Any]]:
    source = truncate(text, max_chars)
    if not source:
        return "", {"status": "empty_source", "provider": "none"}
    if re.search(r"[\u4e00-\u9fff]", source) and len(re.findall(r"[\u4e00-\u9fff]", source)) > len(source) * 0.2:
        return source, {"status": "source_already_chinese", "provider": "source"}
    glossary = _glossary(profile)
    key = sha256_text("v2-translation|" + source + json.dumps(glossary, ensure_ascii=False))
    cached = cache.get(key)
    if isinstance(cached, dict) and cached.get("text"):
        return cached["text"], {**cached.get("audit", {}), "from_cache": True}

    protected, mapping = _protect(source, glossary)
    prompt = json.dumps({
        "source_language": "English",
        "target_language": "Simplified Chinese",
        "text": protected,
        "protected_tokens": list(mapping),
        "glossary": glossary,
    }, ensure_ascii=False)
    attempts: list[dict[str, Any]] = []
    if llm.available:
        try:
            result = llm.json_task(system=prompt_text, prompt=prompt, max_models_per_provider=3)
            raw = result.data.get("translation_zh") if isinstance(result.data, dict) else ""
            candidate = _repair_zh(_restore(clean_space(raw), mapping), glossary)
            valid, reason = _looks_chinese(candidate, source)
            attempts.extend(result.attempts)
            if valid:
                audit = {"status": "passed_llm", "provider": result.provider, "model": result.model, "attempts": attempts}
                cache[key] = {"text": candidate, "audit": audit}
                return candidate, audit
            attempts.append({"provider": result.provider, "model": result.model, "status": "quality_rejected", "reason": reason})
        except LLMError as exc:
            attempts.append({"provider": "llm_router", "status": "failed", "error": clean_space(exc)[:600]})

    # Python translation is a true last-resort path. It is independent of the LLM
    # prompt and therefore also acts as a useful sanity reference for rejected LLM output.
    try:
        raw, provider = _python_translate(protected)
        candidate = _repair_zh(_restore(raw, mapping), glossary)
        valid, reason = _looks_chinese(candidate, source)
        attempts.append({"provider": provider, "status": "success" if valid else "quality_rejected", "reason": reason})
        if valid:
            audit = {"status": "passed_python_fallback", "provider": provider, "attempts": attempts}
            cache[key] = {"text": candidate, "audit": audit}
            return candidate, audit
    except Exception as exc:
        attempts.append({"provider": "python_translation", "status": "failed", "error": clean_space(exc)[:500]})

    audit = {"status": "translation_unavailable", "provider": "none", "attempts": attempts}
    cache[key] = {"text": "", "audit": audit}
    return "", audit


def translate_record(
    record: dict[str, Any],
    *,
    profile: dict[str, Any],
    llm: LLMRouter,
    prompts_dir: Path,
    cache: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    prompt_text = (prompts_dir / "translate_zh.md").read_text(encoding="utf-8")
    analysis = (record.get("analysis") or {}).get("analysis") or {}
    if kind == "research":
        fields = ["background", "methods", "results", "contribution", "limitations"]
        labels = ["背景", "方法", "结果", "贡献", "局限"]
    elif kind == "review":
        fields = ["background", "main_directions", "current_state", "gaps", "future_research"]
        labels = ["背景", "主要方向", "研究现状", "不足", "后续研究"]
    else:
        fields = ["time", "location", "event", "impact", "status"]
        labels = ["时间", "地点", "事件", "影响", "状态"]

    source_fields: dict[str, str] = {"title": truncate(clean_space(record.get("title")), 700)}
    for field in fields:
        source_fields[field] = truncate(clean_space(analysis.get(field)), 900)

    glossary = _glossary(profile)
    translated: dict[str, str] = {}
    audits: dict[str, Any] = {}
    unresolved: dict[str, str] = {}
    cache_keys: dict[str, str] = {}
    for key, source in source_fields.items():
        if not source:
            translated[key] = "" if key == "title" else "未报告"
            audits[key] = {"status": "empty_source", "provider": "none"}
            continue
        if re.search(r"[\u4e00-\u9fff]", source) and len(re.findall(r"[\u4e00-\u9fff]", source)) > len(source) * 0.2:
            translated[key] = source
            audits[key] = {"status": "source_already_chinese", "provider": "source"}
            continue
        cache_key = sha256_text("v2-batch-translation|" + key + "|" + source + json.dumps(glossary, ensure_ascii=False))
        cache_keys[key] = cache_key
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and cached.get("text"):
            translated[key] = cached["text"]
            audits[key] = {**cached.get("audit", {}), "from_cache": True}
        else:
            unresolved[key] = source

    if unresolved:
        protected_fields: dict[str, str] = {}
        field_mappings: dict[str, dict[str, str]] = {}
        for field_index, (key, source) in enumerate(unresolved.items()):
            protected = source
            mapping: dict[str, str] = {}
            for term_index, row in enumerate(sorted(glossary, key=lambda x: len(x["source"]), reverse=True)):
                pattern = re.compile(re.escape(row["source"]), flags=re.I)
                if not pattern.search(protected):
                    continue
                token = f"[PDI{field_index:02d}{term_index:02d}]"
                protected = pattern.sub(token, protected)
                mapping[token] = row["target"]
            protected_fields[key] = protected
            field_mappings[key] = mapping

        llm_attempts: list[dict[str, Any]] = []
        if llm.available:
            prompt = json.dumps({
                "source_language": "English",
                "target_language": "Simplified Chinese",
                "fields": protected_fields,
                "glossary": glossary,
                "instruction": "Return one translation for every input field; do not omit a field.",
            }, ensure_ascii=False)
            try:
                result = llm.json_task(system=prompt_text, prompt=prompt, max_models_per_provider=3)
                llm_attempts = result.attempts
                response_fields = result.data.get("translations") if isinstance(result.data, dict) else {}
                if not isinstance(response_fields, dict):
                    response_fields = {}
                for key, source in list(unresolved.items()):
                    raw = clean_space(response_fields.get(key))
                    candidate = _repair_zh(_restore(raw, field_mappings.get(key, {})), glossary)
                    valid, reason = _looks_chinese(candidate, source)
                    if valid:
                        audit = {
                            "status": "passed_llm",
                            "provider": result.provider,
                            "model": result.model,
                            "attempts": llm_attempts,
                        }
                        translated[key] = candidate
                        audits[key] = audit
                        cache[cache_keys[key]] = {"text": candidate, "audit": audit}
                        unresolved.pop(key, None)
                    elif raw:
                        audits[key] = {
                            "status": "llm_quality_rejected",
                            "provider": result.provider,
                            "model": result.model,
                            "reason": reason,
                            "attempts": llm_attempts,
                        }
            except LLMError as exc:
                llm_attempts = [{"provider": "llm_router", "status": "failed", "error": clean_space(exc)[:600]}]

        # Only unresolved fields enter the independent Python fallback. This keeps
        # the routine fast while ensuring that a single malformed model field does
        # not invalidate the other successful translations in the same record.
        for key, source in list(unresolved.items()):
            try:
                raw, provider = _python_translate(protected_fields[key])
                candidate = _repair_zh(_restore(raw, field_mappings.get(key, {})), glossary)
                valid, reason = _looks_chinese(candidate, source)
                if valid:
                    audit = {
                        "status": "passed_python_fallback",
                        "provider": provider,
                        "attempts": llm_attempts + [{"provider": provider, "status": "success"}],
                    }
                    translated[key] = candidate
                    audits[key] = audit
                    cache[cache_keys[key]] = {"text": candidate, "audit": audit}
                    unresolved.pop(key, None)
                else:
                    audits[key] = {
                        "status": "translation_unavailable",
                        "provider": provider,
                        "reason": reason,
                        "attempts": llm_attempts + [{"provider": provider, "status": "quality_rejected", "reason": reason}],
                    }
            except Exception as exc:
                audits[key] = {
                    "status": "translation_unavailable",
                    "provider": "none",
                    "attempts": llm_attempts + [{"provider": "python_translation", "status": "failed", "error": clean_space(exc)[:500]}],
                }

    record["title_zh"] = translated.get("title") or "中文标题翻译暂不可用"
    translated_fields = {field: translated.get(field) or ("未报告" if source_fields.get(field) else "未报告") for field in fields}
    parts = [f"{label}：{truncate(translated_fields[field], 72)}" for label, field in zip(labels, fields)]
    summary_zh = " ".join(parts)
    if len(summary_zh) > 300:
        parts = [f"{label}：{truncate(translated_fields[field], 48)}" for label, field in zip(labels, fields)]
        summary_zh = truncate(" ".join(parts), 300)
    record["analysis_zh"] = translated_fields
    record["summary_zh"] = summary_zh
    record["translation_audit"] = {"title": audits.get("title", {}), "fields": {field: audits.get(field, {}) for field in fields}}
    return record
