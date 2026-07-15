from __future__ import annotations

from typing import Any
import re

from .utils import normalize_space, utc_now_iso


def _text_item(item: dict[str, Any], kind: str) -> str:
    if kind == "work":
        full_text = " ".join(section.get("text") or "" for section in (item.get("full_text") or {}).get("sections", []))
        return " ".join([(item.get("title") or {}).get("original") or "", (item.get("abstract") or {}).get("original") or "", full_text])
    return " ".join([(item.get("title") or {}).get("original") or "", (item.get("content") or {}).get("analysis_text") or (item.get("content") or {}).get("excerpt") or ""])


def _accepted_matches(text: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    low = text.casefold()
    return [
        term
        for term in profile.get("lexicon", [])
        if term.get("status") == "accepted_for_search"
        and term.get("term")
        and str(term.get("term")).casefold() in low
    ]


def pathogen_prominence(item: dict[str, Any], profile: dict[str, Any], kind: str) -> dict[str, Any]:
    title = normalize_space((item.get("title") or {}).get("original")).casefold()
    if kind == "work":
        body = normalize_space((item.get("abstract") or {}).get("original")).casefold()
    else:
        body = normalize_space((item.get("content") or {}).get("analysis_text") or (item.get("content") or {}).get("excerpt")).casefold()
    terms = [
        str(term.get("term")).casefold()
        for term in profile.get("lexicon", [])
        if term.get("status") == "accepted_for_search"
        and term.get("term_type") in {"pathogen_name", "virus_entity_name", "taxonomy_family", "taxonomy_genus", "disease_name", "clinical_syndrome"}
        and term.get("term")
        and len(str(term.get("term"))) >= 4
    ]
    title_hits = sorted({term for term in terms if term in title})
    lead = body[:900]
    lead_hits = sorted({term for term in terms if term in lead})
    all_hits = sorted({term for term in terms if term in body})
    first_position = min((body.find(term) for term in terms if term in body), default=-1)
    mention_count = sum(body.count(term) for term in terms if term in body)
    background_cues = (
        "previously", "earlier", "recall", "recalled", "mentioned", "including",
        "pointing to", "such as", "for example", "prior outbreak", "past outbreak",
        "此前", "早前", "曾经", "提及", "例如", "包括",
    )
    mention_window = body[max(0, first_position - 140) : first_position + 260] if first_position >= 0 else ""
    direct_event_signals = (
        "confirmed case", "laboratory-confirmed", "tested positive", "diagnosed",
        "case of hantavirus", "hantavirus case", "hantavirus death", "died from hantavirus",
        "确诊病例", "检测阳性", "诊断为", "汉坦病毒病例", "死于汉坦病毒",
    )
    numeric_case_signal = bool(
        re.search(r"\d[\d,]*\s+(?:confirmed\s+)?(?:cases?|deaths?)", mention_window)
    )
    single_cued_background = (
        not title_hits
        and mention_count == 1
        and (
            any(cue in mention_window for cue in background_cues)
            or not (numeric_case_signal or any(signal in mention_window for signal in direct_event_signals))
        )
    )
    if title_hits:
        level = "title_focus"
    elif single_cued_background:
        level = "background_only"
    elif lead_hits and mention_count >= 1:
        level = "lead_focus"
    elif all_hits and (mention_count >= 2 or first_position <= 1600):
        level = "body_focus"
    elif all_hits:
        level = "background_only"
    else:
        level = "none"
    return {
        "level": level,
        "title_hits": title_hits,
        "lead_hits": lead_hits,
        "all_hits": all_hits,
        "mention_count": mention_count,
        "first_mention_position": first_position if first_position >= 0 else None,
    }


def relevance(item: dict[str, Any], profile: dict[str, Any], kind: str) -> tuple[str, list[str]]:
    text = normalize_space(_text_item(item, kind))
    low = text.casefold()
    reasons: list[str] = []
    matched = _accepted_matches(text, profile)
    for ambiguous in profile.get("ambiguous_terms", []):
        term = (ambiguous.get("term") or "").casefold()
        if term and term in low and not any((x or "").casefold() in low for x in ambiguous.get("must_cooccur_with", [])):
            return "ambiguous", ["AMBIGUOUS_TERM_WITHOUT_REQUIRED_CONTEXT"]
    if not matched:
        return "irrelevant", ["NO_APPROVED_PATHOGEN_TERM"]
    prominence = pathogen_prominence(item, profile, kind)
    if kind == "article" and prominence["level"] == "background_only":
        return "background", ["PATHOGEN_MENTION_ONLY_IN_BACKGROUND"]
    if any(term.get("term_type") in {"pathogen_name", "virus_entity_name", "taxonomy_family", "taxonomy_genus"} for term in matched):
        reasons.append("APPROVED_PATHOGEN_TERM")
        return "strong", reasons
    return "combined", ["APPROVED_DISEASE_OR_SYNDROME_TERM"]


def classify_work(work: dict[str, Any], profile: dict[str, Any], is_new: bool = True) -> dict[str, Any]:
    rel, reasons = relevance(work, profile, "work")
    score = 0.0
    if rel == "strong":
        score += 0.45
    elif rel == "combined":
        score += 0.3
    if (work.get("abstract") or {}).get("original"):
        score += 0.15
    if (work.get("quality") or {}).get("source_count", 0) >= 2:
        score += 0.15
    if is_new:
        score += 0.15
    topics = (work.get("entities") or {}).get("topics") or []
    if any(topic in topics for topic in ["outbreak", "clinical", "genomics", "intervention"]):
        score += 0.1
    recovery_context = work.get("recovery_context") or {}
    current_level = str((work.get("evidence_acquisition") or {}).get("evidence_level") or ("E1" if (work.get("abstract") or {}).get("original") else "E0"))
    previous_level = str(recovery_context.get("previous_evidence_level") or current_level)
    level_rank = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4}
    content_recovered = bool(recovery_context) and level_rank.get(current_level, 0) > level_rank.get(previous_level, 0)
    if content_recovered:
        score += 0.18
        reasons.append("SCHOLARLY_CONTENT_RECOVERED_UPDATE")
    elif recovery_context:
        score -= 0.5
        reasons.append("RECOVERY_RETRY_NO_NEW_EVIDENCE")
    if (work.get("quality") or {}).get("identifier_conflict"):
        score -= 0.2
        reasons.append("IDENTIFIER_CONFLICT_REQUIRES_REVIEW")
    decision = "archive"
    missing_date = not (work.get("bibliography") or {}).get("published_date")
    if missing_date:
        reasons.append("MISSING_CURRENT_AVAILABILITY_DATE")
    if rel in {"irrelevant", "ambiguous"}:
        decision = "review" if rel == "ambiguous" else "archive"
    elif (work.get("quality") or {}).get("identifier_conflict"):
        decision = "review"
    elif missing_date:
        decision = "review"
    elif recovery_context and not content_recovered:
        decision = "archive"
    elif score >= 0.72:
        decision = "headline"
    elif score >= 0.42:
        decision = "brief"
    work["filter_result"] = {"decision": decision, "reason_codes": reasons, "score": round(max(0.0, score), 3), "rule_version": "1.5", "processed_at": utc_now_iso(), "relevance": rel}
    return work


def classify_article(article: dict[str, Any], profile: dict[str, Any], event: dict[str, Any] | None = None) -> dict[str, Any]:
    rel, reasons = relevance(article, profile, "article")
    prominence = pathogen_prominence(article, profile, "article")
    tier = (article.get("source") or {}).get("reliability_tier", "unknown")
    score = {"A": 0.35, "B": 0.25, "C": 0.12, "D": 0.05, "unknown": 0.0}.get(tier, 0.0)
    if rel == "strong":
        score += 0.3
    elif rel == "combined":
        score += 0.2
    if prominence["level"] == "title_focus":
        score += 0.2
        reasons.append("PATHOGEN_IN_TITLE")
    elif prominence["level"] == "lead_focus":
        score += 0.12
        reasons.append("PATHOGEN_IN_LEAD")
    elif prominence["level"] == "body_focus":
        score += 0.04
    elif prominence["level"] == "background_only":
        score -= 0.4
        reasons.append("BACKGROUND_MENTION_ONLY")
    if event and event.get("material_change"):
        score += 0.2
        reasons.append("MATERIAL_EVENT_CHANGE")
    if event and event.get("official_status") == "official":
        score += 0.1
        reasons.append("OFFICIAL_SOURCE_PRESENT")
    content = article.get("content") or {}
    coverage = content.get("coverage_level")
    if coverage in {"full_relevant_extract", "focused_partial"} and content.get("analysis_text"):
        score += 0.1
        reasons.append("RELEVANT_CONTENT_EXTRACTED")
    elif content.get("excerpt"):
        score += 0.03
        reasons.append("SNIPPET_ONLY")
    else:
        reasons.append("CONTENT_NOT_RETRIEVED")
    decision = "archive"
    missing_date = not article.get("published_at")
    if missing_date:
        reasons.append("MISSING_PUBLISHED_DATE")
    if rel == "ambiguous":
        decision = "review"
    elif rel in {"irrelevant", "background"} or prominence["level"] == "background_only":
        decision = "archive"
    elif missing_date:
        decision = "review"
    elif score >= 0.78:
        decision = "headline"
    elif score >= 0.46:
        decision = "brief"
    article["classification"] = {
        "decision": decision,
        "reason_codes": reasons,
        "score": round(max(0.0, score), 3),
        "rule_version": "1.5",
        "processed_at": utc_now_iso(),
        "relevance": rel,
        "pathogen_prominence": prominence,
        "content_coverage": coverage,
    }
    return article
