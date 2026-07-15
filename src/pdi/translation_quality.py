from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from .markup import contains_cjk, strip_scientific_markup
from .utils import normalize_space, sentence_split

GOOGLE_NEWS_BOILERPLATE = (
    "comprehensive up-to-date news coverage, aggregated from sources all over the world by google news",
    "comprehensive up to date news coverage, aggregated from sources all over the world by google news",
    "google news provides comprehensive up-to-date news coverage from sources worldwide",
    "google news provides comprehensive up to date news coverage from sources worldwide",
    "view full coverage on google news",
)

DEFAULT_FORBIDDEN = (
    "宋病毒",
    "汉塔病毒",
    "韩坦病毒",
    "安第斯 hantavirus",
    "安第斯Hantavirus",
    "明显汉坦病毒疫情",
    "汉坦病毒肆虐游轮",
)

DEFAULT_REPAIRS = {
    "宋病毒": "汉坦病毒",
    "汉塔病毒": "汉坦病毒",
    "韩坦病毒": "汉坦病毒",
    "安第斯 hantavirus": "安第斯病毒",
    "安第斯Hantavirus": "安第斯病毒",
    "明显汉坦病毒疫情": "疑似汉坦病毒疫情",
    "汉坦病毒肆虐游轮": "发生汉坦病毒疫情的邮轮",
    "汉坦病毒肆虐的游轮": "发生汉坦病毒疫情的邮轮",
    "汉城病毒": "首尔病毒",
    "安第斯汉坦病毒": "安第斯病毒",
    "受累的谱系": "受累谱",
    "影响的谱系": "影响谱",
    "邮轮关联的": "邮轮相关",
}

DEFAULT_TERMS = (
    (r"\bhantavirus cardiopulmonary syndrome\b", "汉坦病毒心肺综合征"),
    (r"\bhantavirus pulmonary syndrome\b", "汉坦病毒肺综合征"),
    (r"\bhemorrhagic fever with renal syndrome\b", "肾综合征出血热"),
    (r"\borthohantavirus(?:es)?\b", "正汉坦病毒"),
    (r"\bandes hantavirus\b", "安第斯病毒"),
    (r"\bandes virus\b", "安第斯病毒"),
    (r"\bseoul virus\b", "首尔病毒"),
    (r"\bhantaan virus\b", "汉滩病毒"),
    (r"\bpuumala virus\b", "普马拉病毒"),
    (r"\bhantavirus(?:es)?\b", "汉坦病毒"),
)

_TRANSLATABLE_RESIDUAL_WORDS = {
    "hantavirus", "hantaviruses", "outbreak", "suspected", "apparent", "cruise",
    "ship", "infection", "virus", "disease", "cases", "case", "deaths", "death",
    "review", "clinical", "epidemiology", "treatment", "diagnosis", "response",
}


def _glossary_terms(glossary: dict[str, Any] | None) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = list(DEFAULT_TERMS)
    for row in (glossary or {}).get("terms", []):
        target = normalize_space(row.get("target"))
        if not target:
            continue
        for source in row.get("source_patterns") or []:
            source = normalize_space(source)
            if source:
                rows.append((r"\b" + re.escape(source) + r"\b", target))
    rows.sort(key=lambda pair: len(pair[0]), reverse=True)
    return rows


def clean_translation_source(value: Any, *, kind: str) -> str | None:
    """Remove aggregator/navigation boilerplate before translation.

    A missing body remains missing.  It is never replaced by a title-derived pseudo-summary.
    """
    text = normalize_space(strip_scientific_markup(value or ""))
    if not text:
        return None
    low = text.casefold().strip(" .")
    if any(phrase in low for phrase in GOOGLE_NEWS_BOILERPLATE):
        # Remove the known fixed sentence but retain any genuine text surrounding it.
        cleaned = text
        for phrase in GOOGLE_NEWS_BOILERPLATE:
            cleaned = re.sub(re.escape(phrase), " ", cleaned, flags=re.I)
        text = normalize_space(cleaned).strip(" .,:;，。；：")
        low = text.casefold().strip(" .")
    if kind == "work" and low in {"no abstract available", "abstract unavailable", "no abstract"}:
        return None
    if kind == "article" and (not text or low in {"read more", "view full coverage"}):
        return None
    return text or None


def is_boilerplate_only(value: Any) -> bool:
    text = normalize_space(strip_scientific_markup(value or "")).casefold().strip(" .")
    return bool(text) and any(phrase in text for phrase in GOOGLE_NEWS_BOILERPLATE)


def repair_translation_text(
    value: Any,
    source: str | None,
    glossary: dict[str, Any] | None = None,
) -> tuple[str | None, list[dict[str, str]]]:
    if value in (None, ""):
        return None, []
    text = normalize_space(str(value))
    repairs: list[dict[str, str]] = []
    repair_map = {**DEFAULT_REPAIRS, **((glossary or {}).get("repairs") or {})}
    for bad, good in sorted(repair_map.items(), key=lambda row: len(row[0]), reverse=True):
        if bad in text:
            text = text.replace(bad, good)
            repairs.append({"from": bad, "to": good, "reason": "approved_repair"})

    # Replace untranslated domain expressions only when the source actually contains them.
    source_text = source or ""
    for pattern, target in _glossary_terms(glossary):
        if not re.search(pattern, source_text, flags=re.I):
            continue
        changed = re.sub(pattern, target, text, flags=re.I)
        if changed != text:
            repairs.append({"from": pattern, "to": target, "reason": "approved_glossary"})
            text = changed

    text = re.sub(r"\s+([，。；：！？、])", r"\1", text)
    text = re.sub(r"([，。；：！？、])\s+", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or None, repairs


def literal_card_summary(value: Any, limit: int = 720, max_sentences: int = 4) -> str | None:
    text = normalize_space(strip_scientific_markup(value or ""))
    if not text:
        return None
    sentences = sentence_split(text)
    chosen: list[str] = []
    size = 0
    for sentence in sentences:
        sentence = normalize_space(sentence)
        if not sentence:
            continue
        if chosen and size + len(sentence) > limit:
            break
        chosen.append(sentence)
        size += len(sentence)
        if len(chosen) >= max_sentences:
            break
    summary = " ".join(chosen) if chosen else text[:limit]
    if len(summary) > limit:
        summary = summary[:limit].rstrip() + "……"
    return summary or None


def _cjk_ratio(value: str) -> float:
    chars = [ch for ch in value if not ch.isspace() and not ch.isdigit()]
    if not chars:
        return 0.0
    cjk = sum("\u3400" <= ch <= "\u9fff" for ch in chars)
    return cjk / len(chars)


def _latin_residuals(value: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z-]{2,}", strip_scientific_markup(value or ""))
    return [word for word in words if word.casefold() in _TRANSLATABLE_RESIDUAL_WORDS]


def _required_term_errors(source: str, candidate: str, glossary: dict[str, Any] | None) -> list[str]:
    errors: list[str] = []
    for pattern, target in _glossary_terms(glossary):
        if re.search(pattern, source or "", flags=re.I) and target not in candidate:
            errors.append(f"MISSING_APPROVED_TERM:{target}")
    return errors


def _semantic_marker_errors(source: str, candidate: str, *, field_name: str) -> list[str]:
    if field_name != "title":
        return []
    source_low = source.casefold()
    errors: list[str] = []
    if re.search(r"\b(suspected|apparent|possibly|possible|feared)\b", source_low):
        if not re.search(r"疑似|可能|据称|恐|尚未确认|或", candidate):
            errors.append("UNCERTAINTY_MARKER_DROPPED")
    if re.search(r"\b(no|not|without|never)\b", source_low):
        if not re.search(r"不|未|无|没有|并非|从未", candidate):
            errors.append("NEGATION_MARKER_DROPPED")
    return errors


def assess_translation(
    source: str | None,
    candidate: str | None,
    *,
    field_name: str,
    glossary: dict[str, Any] | None = None,
    local_reference: str | None = None,
) -> dict[str, Any]:
    source = normalize_space(strip_scientific_markup(source or ""))
    candidate = normalize_space(strip_scientific_markup(candidate or ""))
    errors: list[str] = []
    warnings: list[str] = []
    metrics: dict[str, Any] = {}
    if not candidate:
        return {"valid": False, "errors": ["EMPTY_TRANSLATION"], "warnings": [], "metrics": {}}

    ratio = _cjk_ratio(candidate)
    metrics["cjk_ratio"] = round(ratio, 4)
    minimum = 0.23 if field_name == "title" else 0.32
    if source and not contains_cjk(source) and ratio < minimum:
        errors.append(f"INSUFFICIENT_CHINESE_RATIO:{ratio:.3f}")

    forbidden = list(DEFAULT_FORBIDDEN) + list((glossary or {}).get("forbidden") or [])
    for token in forbidden:
        if token and token in candidate:
            errors.append(f"FORBIDDEN_TRANSLATION:{token}")

    residuals = sorted(set(_latin_residuals(candidate)))
    metrics["translatable_english_residuals"] = residuals
    if residuals:
        errors.append("UNTRANSLATED_DOMAIN_WORDS:" + ",".join(residuals[:8]))

    if any(phrase in candidate.casefold() for phrase in GOOGLE_NEWS_BOILERPLATE):
        errors.append("SOURCE_BOILERPLATE_TRANSLATED_AS_CONTENT")

    errors.extend(_required_term_errors(source, candidate, glossary))
    errors.extend(_semantic_marker_errors(source, candidate, field_name=field_name))

    if field_name == "title" and len(candidate) <= 100:
        de_density = candidate.count("的") / max(1, len(candidate))
        metrics["de_density"] = round(de_density, 4)
        if candidate.count("的") >= 4 or de_density > 0.12:
            warnings.append("POSSIBLY_AWKWARD_DE_CHAIN")
        if re.search(r"报道了.+报道|文章标题为《.+》", candidate):
            warnings.append("POSSIBLY_META_STYLE_CHINESE")

    if local_reference:
        ref = normalize_space(strip_scientific_markup(local_reference))
        similarity = SequenceMatcher(None, candidate, ref).ratio() if ref else None
        metrics["local_reference_similarity"] = round(similarity, 4) if similarity is not None else None
        if similarity is not None and similarity < 0.16:
            warnings.append(f"LOW_LOCAL_REFERENCE_SIMILARITY:{similarity:.3f}")

    metrics["quality_score"] = max(0, 100 - 40 * len(errors) - 5 * len(warnings))
    return {
        "valid": not errors,
        "errors": errors[:30],
        "warnings": warnings[:30],
        "metrics": metrics,
    }


def repair_translation_fields(
    source_title: str,
    source_text: str | None,
    fields: dict[str, Any],
    glossary: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    out = dict(fields)
    repairs: list[dict[str, str]] = []
    for key, source in (
        ("translated_title_zh", source_title),
        ("translated_text_zh", source_text),
        ("display_summary_zh", source_text),
    ):
        value, rows = repair_translation_text(out.get(key), source, glossary)
        out[key] = value
        repairs.extend({**row, "field": key} for row in rows)
    return out, repairs
