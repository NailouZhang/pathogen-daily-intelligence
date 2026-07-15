from __future__ import annotations

import re
from typing import Any

from .utils import clean_space, unique_strings


def profile_terms(profile: dict[str, Any]) -> list[str]:
    return unique_strings(
        (profile.get("english_terms") or [])
        + (profile.get("chinese_terms") or [])
        + (profile.get("virus_names") or [])
        + (profile.get("disease_names_en") or [])
        + (profile.get("disease_names_zh") or [])
    )


def relevance_score(title: str, body: str, profile: dict[str, Any]) -> float:
    title_l = clean_space(title).lower()
    body_l = clean_space(body).lower()
    terms = [t.lower() for t in profile_terms(profile) if len(clean_space(t)) >= 4]
    title_hits = sum(1 for term in terms if term in title_l)
    body_hits = sum(1 for term in terms if term in body_l)
    core = clean_space(profile.get("profile_id")).lower()
    negative_only = bool(re.search(r"\b(negative|tested negative|no evidence of)\b.{0,80}" + re.escape(core), body_l))
    score = min(1.0, title_hits * 0.45 + body_hits * 0.08)
    if negative_only and title_hits == 0:
        score -= 0.4
    return max(0.0, score)


def filter_relevant_papers(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for record in records:
        score = relevance_score(record.get("title", ""), record.get("abstract", ""), profile)
        record["relevance_score"] = round(score, 3)
        if score >= 0.18:
            output.append(record)
    return output


def filter_relevant_news(records: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    output = []
    for record in records:
        score = relevance_score(record.get("title", ""), record.get("excerpt", ""), profile)
        record["relevance_score"] = round(score, 3)
        if score >= 0.24:
            output.append(record)
    return output
