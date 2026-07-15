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
from .utils import content_hash, ensure_dict_field, normalize_space, utc_now_iso
from .translation_quality import (
    assess_translation,
    clean_translation_source,
    literal_card_summary,
    repair_translation_fields,
)

TRANSLATION_PROMPT_VERSION = "bilingual-translation-v1.6"


def _source_fields(item: dict[str, Any], kind: str) -> tuple[str, str | None, str | None]:
    if kind == "work":
        return (
            str((item.get("title") or {}).get("original") or ""),
            clean_translation_source((item.get("abstract") or {}).get("original"), kind="work"),
            (item.get("title") or {}).get("language"),
        )
    return (
        str((item.get("title") or {}).get("original") or ""),
        clean_translation_source(
            (item.get("content") or {}).get("translation_text")
            or (item.get("content") or {}).get("excerpt"),
            kind="article",
        ),
        (item.get("title") or {}).get("language"),
    )


def translation_cache_key(item: dict[str, Any], kind: str) -> str:
    title, body, language = _source_fields(item, kind)
    identity = item.get("work_id") if kind == "work" else item.get("article_id")
    return f"translation:{TRANSLATION_PROMPT_VERSION}:{kind}:{identity}:{content_hash({'title': title, 'body': body, 'language': language})}"


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
        ensure_dict_field(item, "title")["translated_zh"] = title
        ensure_dict_field(item, "abstract")["translated_zh"] = body
    else:
        ensure_dict_field(item, "title")["translated_zh"] = title
        ensure_dict_field(item, "content")["translated_excerpt_zh"] = body
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


def _canonical_number_token(token: str) -> str:
    token = str(token or "").replace("，", ",").replace("％", "%")
    suffix = "%" if token.endswith("%") else ""
    core = token[:-1] if suffix else token
    # Commas inside an all-digit number are grouping separators, not part of
    # the value. The previous version only recognised strict Western
    # thousands grouping (groups of exactly 3 digits after the first), so a
    # source number written in Indian/Hindi grouping (e.g. "2,00,000") or any
    # other convention was left with its commas intact. The translated text
    # then legitimately renders it as "200,000" or "200000", the literal
    # substring never matches, and validate_translation_fields raises a false
    # TITLE_NUMBER_CHANGED_OR_DROPPED / TEXT_NUMBER_CHANGED_OR_DROPPED error —
    # which silently fails otherwise-correct translations across every
    # provider. Comparing on digits only (regardless of grouping convention)
    # still catches genuine value changes while accepting any legitimate
    # regrouping or removal of separators.
    if "," in core and "." not in core:
        parts = core.split(",")
        if len(parts) > 1 and all(part.isdigit() for part in parts):
            core = "".join(parts)
    return core + suffix


def _canonical_number_set(value: Any) -> set[str]:
    return {_canonical_number_token(token) for token in number_tokens(value)}


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

    combined = "\n".join(str(raw_fields.get(key) or "") for key in raw_fields)
    if mapping and not placeholders_preserved(combined, mapping):
        missing = [token for token in mapping if token not in combined]
        errors.append("MISSING_SCIENTIFIC_PLACEHOLDERS:" + ",".join(missing))

    title_numbers = _canonical_number_set(source_title)
    translated_title_numbers = _canonical_number_set(title or "")
    for token in sorted(title_numbers):
        if token not in translated_title_numbers:
            errors.append(f"TITLE_NUMBER_CHANGED_OR_DROPPED:{token}")

    if source_text:
        text_numbers = _canonical_number_set(source_text)
        translated_numbers = _canonical_number_set(translated_text or "")
        for token in sorted(text_numbers):
            if token not in translated_numbers:
                errors.append(f"TEXT_NUMBER_CHANGED_OR_DROPPED:{token}")

    return {"valid": not errors, "errors": errors[:30]}


def review_translation_candidate(
    source_title: str,
    source_text: str | None,
    raw_fields: dict[str, Any],
    mapping: dict[str, str],
    *,
    glossary: dict[str, Any] | None = None,
    local_translator: Any | None = None,
) -> dict[str, Any]:
    structural = validate_translation_fields(source_title, source_text, raw_fields, mapping)
    restored = restore_translation_fields(raw_fields, mapping)
    repaired, repairs = repair_translation_fields(source_title, source_text, restored, glossary)
    local_reference = None
    local_reference_audit: dict[str, Any] | None = None
    if structural["valid"] and local_translator is not None:
        local_reference, local_reference_audit = local_translator.reference_title(source_title)
    title_quality = assess_translation(
        source_title,
        repaired.get("translated_title_zh"),
        field_name="title",
        glossary=glossary,
        local_reference=local_reference,
    )
    text_quality = (
        assess_translation(
            source_text,
            repaired.get("translated_text_zh"),
            field_name="text",
            glossary=glossary,
        )
        if source_text
        else {"valid": True, "errors": [], "warnings": [], "metrics": {}}
    )
    errors = list(structural["errors"]) + list(title_quality["errors"]) + list(text_quality["errors"])
    if source_text and not repaired.get("display_summary_zh"):
        repaired["display_summary_zh"] = literal_card_summary(repaired.get("translated_text_zh"))
    return {
        "valid": not errors,
        "errors": errors[:40],
        "warnings": (title_quality["warnings"] + text_quality["warnings"])[:40],
        "fields": repaired,
        "repairs": repairs,
        "quality_metrics": {"title": title_quality["metrics"], "text": text_quality["metrics"]},
        "local_reference": local_reference,
        "local_reference_audit": local_reference_audit,
    }


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

    ensure_dict_field(item, "title")["translated_zh"] = translated_title
    if kind == "work":
        ensure_dict_field(item, "abstract")["translated_zh"] = translated_text
    else:
        ensure_dict_field(item, "content")["translated_excerpt_zh"] = translated_text
    pure_translation = audit.get("task_name") in {
        "bilingual_translation_batch",
        "translation_repair",
        "local_machine_translation",
    }
    if pure_translation:
        summary_zh = literal_card_summary(translated_text) if body else None
    else:
        summary_zh = fields.get("display_summary_zh") or literal_card_summary(translated_text)
    item["display_summary"] = {
        "zh": summary_zh,
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
    display_summary = ensure_dict_field(item, "display_summary")
    display_summary.setdefault("zh", None)
    title, body, _ = _source_fields(item, kind)
    plain = normalize_space(strip_scientific_markup(body or ""))
    display_summary.setdefault(
        "en",
        plain[:720].rstrip() + ("…" if len(plain) > 720 else "") if plain else None,
    )
    audit = ensure_dict_field(item, "translation_audit")
    defaults = {
        "provider": "deterministic",
        "model": None,
        "prompt_version": TRANSLATION_PROMPT_VERSION,
        "input_hash": content_hash({"title": title, "body": body}),
        "generated_at": utc_now_iso(),
        "fallback_used": True,
        "validation_status": "translation_unavailable",
        "source_language": (item.get("title") or {}).get("language"),
        "target_language": "zh-CN",
    }
    for key, value in defaults.items():
        audit.setdefault(key, value)


def apply_event_bilingual(events: list[dict[str, Any]], articles: Iterable[dict[str, Any]]) -> None:
    article_map = {a.get("article_id"): a for a in articles}
    for event in events:
        primary_id = (event.get("primary_source") or {}).get("article_id")
        primary = article_map.get(primary_id)
        original = event.get("summary") or ((primary or {}).get("title") or {}).get("original")
        translated = ((primary or {}).get("title") or {}).get("translated_zh")
        event["summary_original"] = original
        event["summary_zh"] = translated
        display = (primary or {}).get("display_summary") or {}
        event["display_summary"] = {
            "zh": display.get("zh"),
            "en": display.get("en") or _source_fields(primary or {}, "article")[1],
        }
        event["translation_audit"] = (primary or {}).get("translation_audit")
        event["ai_analysis"] = (primary or {}).get("ai_analysis")
        event["processing_audit"] = {
            "llm_analysis": ((primary or {}).get("processing_audit") or {}).get("llm_analysis")
        }
        event["analysis_source_article_id"] = primary_id
