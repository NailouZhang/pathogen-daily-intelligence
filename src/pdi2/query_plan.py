from __future__ import annotations

from typing import Any

from .utils import clean_space, unique_strings


def _quote(term: str) -> str:
    term = clean_space(term)
    if not term:
        return ""
    return f'"{term}"' if " " in term else term


def build_query_plan(profile: dict[str, Any], max_groups: int = 8) -> list[dict[str, Any]]:
    groups = profile.get("query_groups") or []
    plan: list[dict[str, Any]] = []
    for index, group in enumerate(groups[:max_groups]):
        terms = unique_strings(group.get("terms") or [])[:12]
        topics = unique_strings(group.get("topics") or [])[:10]
        negatives = unique_strings(group.get("negative_terms") or profile.get("negative_terms") or [])[:8]
        if not terms:
            continue
        term_expr = " OR ".join(_quote(t) for t in terms)
        topic_expr = " OR ".join(_quote(t) for t in topics)
        scholarly = f"({term_expr})"
        news = f"({term_expr})"
        if topic_expr:
            scholarly += f" AND ({topic_expr})"
            news += f" ({topic_expr})"
        for negative in negatives:
            news += f" -{_quote(negative)}"
        plan.append({
            "group_id": clean_space(group.get("id")) or f"group_{index + 1}",
            "purpose": clean_space(group.get("purpose")) or "pathogen intelligence",
            "terms": terms,
            "topics": topics,
            "scholarly_query": scholarly,
            "news_query": news,
        })
    if not plan:
        terms = unique_strings(profile.get("english_terms") or [profile.get("profile_id")])[:8]
        expression = " OR ".join(_quote(t) for t in terms)
        plan.append({
            "group_id": "core",
            "purpose": "core pathogen query",
            "terms": terms,
            "topics": [],
            "scholarly_query": f"({expression})",
            "news_query": f"({expression})",
        })
    return plan
