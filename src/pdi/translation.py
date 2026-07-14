from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .markup import (
    contains_cjk,
    number_tokens,
    placeholders_preserved,
    protect_scientific_markup,
    restore_scientific_markup,
    strip_scientific_markup,
)
from .utils import content_hash, normalize_space, utc_now_iso

TRANSLATION_PROMPT_VERSION = "bilingual-translation-v1.3"


def _source_fields(item: dict[str, Any], kind: str) -> tuple[str, str | None, str | None]:
    if kind == "work":
        return (
            str(item.get("title", {}).get("original") or ""),
            item.get("abstract", {}).get("original"),
            item.get("title", {}).get("language"),
        )
    return (
        str(item.get("title", {}).get("original") or ""),
        item.get("content", {}).get("translation_text")
        or item.get("content", {}).get("excerpt"),
        item.get("title", {}).get("language"),
    )


def translation_cache_key(item: dict[str, Any], kind: str) -> str:
    title, body, language = _source_fields(item, kind)
    identity = item.get("work_id") if kind == "work" else item.get("article_id")
    return f"translation:{kind}:{identity}:{content_hash({'title': title, 'body': body, 'language': language})}"


def prepare_translation_item(item: dict[str, Any], kind: str) -> tuple[dict[str, Any], dict[str, str]]:
    title, body, language = _source_fields(item, kind)
    protected_title, title_mapping = protect_scientific_markup(title)
    protected_body, raw_body_mapping = protect_scientific_markup(body or "")
    body_mapping: dict[str, str] = {}
    for offset, (old_token, fragment) in enumerate(raw_body_mapping.items(), start=len(title_mapping)):
        new_token = f"[[PDI_SCI_{offset:03d}]]" if old_token != "[[PDI_BR]]" else "[[PDI_BR_BODY]]"
        protected_body = protected_body.replace(old_token, new_token)
        body_mapping[new_token] = fragment
    mapping = {**title_mapping, **body_mapping}
    record_id = item.get("work_id") if kind == "work" else item.get("article_id")
    return (
        {
            "record_id": record_id,
            "item_type": "scholarly_work" if kind == "work" else "news_article",
            "source_language": language,
            "title": protected_title,
            "text": protected_body or None,
            "text_available": bool(protected_body),
            "protected_placeholders": sorted(mapping),
        },
        mapping,
    )


def deterministic_copy_for_chinese(item: dict[str, Any], kind: str) -> bool:
    title, body, language = _source_fields(item, kind)
    is_chinese = str(language or "").casefold() in {"zh", "zh-cn", "chi", "zho", "cn"} or contains_cjk(title)
    if not is_chinese:
        return False
    summary = normalize_space(strip_scientific_markup(body or ""))
    if len(summary) > 720:
        summary = summary[:720].rstrip() + "……"
    if kind == "work":
        item.setdefault("title", {})["translated_zh"] = title
        item.setdefault("abstract", {})["translated_zh"] = body
    else:
        item.setdefault("title", {})["translated_zh"] = title
        item.setdefault("content", {})["translated_excerpt_zh"] = body
    item["display_summary"] = {
        "zh": summary or None,
        "en": None,
    }
    item["translation_audit"] = {
        "provider": "deterministic",
        "model": None,
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "input_hash": content_hash({"title": title, "body": body}),
        "generated_at": utc_now_iso(),
        "fallback_used": False,
        "validation_status": "source_already_chinese",
        "source_language": language or "zh",
        "target_language": "zh-CN",
    }
    return True


def extract_translation_fields(output: dict[str, Any], kind: str) -> dict[str, Any]:
    title = output.get("translated_title_zh") or output.get("translated_title")
    if kind == "work":
        translated_text = output.get("translated_abstract_zh") or output.get("translated_text_zh")
    else:
        translated_text = output.get("translated_excerpt_zh") or output.get("translated_text_zh")
    takeaway = output.get("one_sentence_takeaway")
    if isinstance(takeaway, dict):
        takeaway = takeaway.get("text")
    return {
        "translated_title_zh": title,
        "translated_text_zh": translated_text,
        "display_summary_zh": output.get("display_summary_zh") or takeaway,
        "display_summary_en": output.get("display_summary_en"),
        "uncertainties": output.get("uncertainties") or [],
    }


def restore_scientific_object(value: Any, mapping: dict[str, str]) -> Any:
    """Recursively restore protected scientific markup in validated model output."""
    if isinstance(value, dict):
        return {key: restore_scientific_object(child, mapping) for key, child in value.items()}
    if isinstance(value, list):
        return [restore_scientific_object(child, mapping) for child in value]
    if isinstance(value, str):
        return restore_scientific_markup(value, mapping)
    return value


def restore_translation_fields(fields: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    restored = deepcopy(fields)
    for key in ("translated_title_zh", "translated_text_zh", "display_summary_zh", "display_summary_en"):
        if restored.get(key) is not None:
            restored[key] = restore_scientific_markup(restored[key], mapping)
    return restored


def validate_translation_fields(
    source_title: str,
    source_text: str | None,
    raw_fields: dict[str, Any],
    mapping: dict[str, str],
) -> dict[str, Any]:
    errors: list[str] = []
    title = raw_fields.get("translated_title_zh")
    translated_text = raw_fields.get("translated_text_zh")
    summary_zh = raw_fields.get("display_summary_zh")

    if not isinstance(title, str) or not title.strip():
        errors.append("MISSING_TRANSLATED_TITLE_ZH")
    if source_text and (not isinstance(translated_text, str) or not translated_text.strip()):
        errors.append("MISSING_TRANSLATED_TEXT_ZH")
    if source_text and (not isinstance(summary_zh, str) or not summary_zh.strip()):
        errors.append("MISSING_DISPLAY_SUMMARY_ZH")

    combined = "\n".join(str(raw_fields.get(key) or "") for key in raw_fields)
    if mapping and not placeholders_preserved(combined, mapping):
        missing = [token for token in mapping if token not in combined]
        errors.append("MISSING_SCIENTIFIC_PLACEHOLDERS:" + ",".join(missing))

    title_numbers = number_tokens(source_title)
    translated_title_numbers = number_tokens(title or "")
    for token in title_numbers:
        if token not in translated_title_numbers:
            errors.append(f"TITLE_NUMBER_CHANGED_OR_DROPPED:{token}")

    if source_text:
        text_numbers = number_tokens(source_text)
        translated_numbers = number_tokens(translated_text or "")
        for token in text_numbers:
            if token not in translated_numbers:
                errors.append(f"TEXT_NUMBER_CHANGED_OR_DROPPED:{token}")

    return {"valid": not errors, "errors": errors[:30]}


def apply_translation(
    item: dict[str, Any],
    kind: str,
    fields: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    title, body, language = _source_fields(item, kind)
    translated_title = fields.get("translated_title_zh")
    translated_text = fields.get("translated_text_zh")
    summary_en = fields.get("display_summary_en")
    if not summary_en and body:
        plain = normalize_space(strip_scientific_markup(body))
        summary_en = plain[:720].rstrip() + ("…" if len(plain) > 720 else "")

    item.setdefault("title", {})["translated_zh"] = translated_title
    if kind == "work":
        item.setdefault("abstract", {})["translated_zh"] = translated_text
    else:
        item.setdefault("content", {})["translated_excerpt_zh"] = translated_text
    item["display_summary"] = {
        "zh": fields.get("display_summary_zh") or translated_text,
        "en": summary_en,
    }
    item["translation_audit"] = {
        **audit,
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "source_language": language,
        "target_language": "zh-CN",
        "original_title_hash": content_hash(title),
        "original_text_hash": content_hash(body or ""),
    }


def ensure_bilingual_placeholders(item: dict[str, Any], kind: str) -> None:
    """Keep the Chinese-first UI honest when no validated translation is available."""
    item.setdefault("display_summary", {})
    item["display_summary"].setdefault("zh", None)
    title, body, _ = _source_fields(item, kind)
    plain = normalize_space(strip_scientific_markup(body or ""))
    item["display_summary"].setdefault(
        "en",
        plain[:720].rstrip() + ("…" if len(plain) > 720 else "") if plain else None,
    )
    item.setdefault("translation_audit", {
        "provider": "deterministic",
        "model": None,
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "input_hash": content_hash({"title": title, "body": body}),
        "generated_at": utc_now_iso(),
        "fallback_used": True,
        "validation_status": "translation_unavailable",
        "source_language": item.get("title", {}).get("language"),
        "target_language": "zh-CN",
    })


def apply_event_bilingual(events: list[dict[str, Any]], articles: Iterable[dict[str, Any]]) -> None:
    article_map = {a.get("article_id"): a for a in articles}
    for event in events:
        primary_id = event.get("primary_source", {}).get("article_id")
        primary = article_map.get(primary_id)
        original = event.get("summary") or (primary or {}).get("title", {}).get("original")
        translated = (primary or {}).get("title", {}).get("translated_zh")
        event["summary_original"] = original
        event["summary_zh"] = translated
        display = (primary or {}).get("display_summary") or {}
        event["display_summary"] = {
            "zh": display.get("zh"),
            "en": display.get("en") or (primary or {}).get("content", {}).get("excerpt"),
        }
        event["translation_audit"] = (primary or {}).get("translation_audit")
        event["ai_analysis"] = (primary or {}).get("ai_analysis")
        event["analysis_source_article_id"] = primary_id
