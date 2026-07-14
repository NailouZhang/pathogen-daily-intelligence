from __future__ import annotations

from collections import Counter
from typing import Any

from .utils import stable_hash, utc_now_iso


def _decision(item: dict[str, Any], kind: str) -> str:
    if kind == "work":
        return item.get("filter_result", {}).get("decision", "archive")
    return item.get("display_decision", "archive")


def build_daily_issue(
    profile: dict[str, Any],
    window: Any,
    works: list[dict[str, Any]],
    events: list[dict[str, Any]],
    source_health: list[dict[str, Any]],
    dedup_counts: dict[str, Any],
    llm_audit: list[dict[str, Any]],
    daily_synthesis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headline_works = [w for w in works if _decision(w, "work") == "headline"]
    brief_works = [w for w in works if _decision(w, "work") == "brief"]
    headline_events = [e for e in events if _decision(e, "event") == "headline"]
    brief_events = [e for e in events if _decision(e, "event") == "brief"]

    lead_candidates: list[dict[str, Any]] = []
    for event in headline_events + brief_events:
        lead_candidates.append(
            {
                "item_type": "public_health_event",
                "item_id": event["event_id"],
                "title": event.get("summary"),
                "score": event.get("display_score", 0),
            }
        )
    for work in headline_works + brief_works:
        lead_candidates.append(
            {
                "item_type": "scholarly_work",
                "item_id": work["work_id"],
                "title": work.get("title", {}).get("translated_zh") or work.get("title", {}).get("original"),
                "score": work.get("filter_result", {}).get("score", 0),
            }
        )
    lead_candidates.sort(key=lambda x: float(x.get("score") or 0), reverse=True)

    topics = Counter(
        topic
        for work in works
        for topic in (work.get("entities", {}).get("topics") or [])
        if topic
    )
    countries = sorted(
        {
            e.get("location", {}).get("country")
            for e in events
            if e.get("location", {}).get("country")
        }
        | {
            country
            for work in works
            for country in (work.get("entities", {}).get("countries") or [])
            if country
        }
    )
    pathogens = sorted(
        {
            pathogen
            for event in events
            for pathogen in (event.get("pathogens") or [])
            if pathogen
        }
        | {
            pathogen
            for work in works
            for pathogen in (work.get("entities", {}).get("pathogens") or [])
            if pathogen
        }
    )
    hosts = sorted(
        {
            host
            for event in events
            for host in (event.get("hosts") or [])
            if host
        }
        | {
            host
            for work in works
            for host in (work.get("entities", {}).get("hosts") or [])
            if host
        }
    )

    supporting_ids = {w["work_id"] for w in works} | {e["event_id"] for e in events}
    synthesis = daily_synthesis or {}
    synthesis_support = [x for x in synthesis.get("supporting_item_ids", []) if x in supporting_ids]

    issue_id = f"{profile['profile_id']}:{window.issue_date}:r1"
    statistics = {
        "scholarly_raw": dedup_counts.get("scholarly", {}).get("raw", 0),
        "scholarly_unique": dedup_counts.get("scholarly", {}).get("unique", len(works)),
        "scholarly_selected": len(headline_works) + len(brief_works),
        "news_raw": dedup_counts.get("news", {}).get("raw", 0),
        "news_unique": dedup_counts.get("news", {}).get("unique", 0),
        "public_health_events": len(events),
        "new_or_updated_events": sum(bool(e.get("material_change")) for e in events),
        "official_events": sum(e.get("official_status") == "official" for e in events),
        "countries": len(countries),
        "pathogens": len(pathogens),
        "hosts": len(hosts),
        "topics": dict(topics),
        "source_failures": sum(h.get("status") in {"failed", "partial"} for h in source_health),
        "llm_fallbacks": sum(bool(a.get("fallback_used")) for a in llm_audit),
    }

    sections = [
        {
            "section_id": "lead",
            "title": "今日要闻",
            "item_ids": [x["item_id"] for x in lead_candidates[: profile.get("editorial_preferences", {}).get("max_headlines", 3)]],
        },
        {
            "section_id": "events",
            "title": "疫情与公共卫生",
            "item_ids": [e["event_id"] for e in headline_events + brief_events],
        },
        {
            "section_id": "research",
            "title": "学术文献",
            "item_ids": [w["work_id"] for w in headline_works + brief_works],
        },
    ]
    if not profile.get("editorial_preferences", {}).get("show_empty_sections", False):
        sections = [s for s in sections if s["item_ids"]]

    data_quality_notes: list[str] = []
    if any(not w.get("abstract", {}).get("original") for w in works):
        data_quality_notes.append("部分文献尚无可用摘要，系统仅展示书目信息。")
    if any(e.get("official_status") != "official" for e in events):
        data_quality_notes.append("部分事件尚未找到 A 级官方原始来源，应继续核验。")
    if statistics["source_failures"]:
        data_quality_notes.append("本期存在接口失败或部分完成，来源覆盖并不完整。")
    if any(a.get("provider") == "deterministic" for a in llm_audit):
        data_quality_notes.append("部分或全部 AI 任务已降级为无模型模式。")

    generated_at = utc_now_iso()
    issue = {
        "schema_version": "1.0",
        "issue_id": issue_id,
        "issue_revision": 1,
        "issue_date": window.issue_date,
        "issue_number": None,
        "profile_id": profile["profile_id"],
        "profile_version": profile.get("profile_version"),
        "coverage_window": {
            "start": window.start,
            "end": window.end,
            "timezone": window.timezone,
        },
        "generated_at": generated_at,
        "statistics": statistics,
        "lead_story": lead_candidates[0] if lead_candidates else None,
        "sections": sections,
        "daily_observations": [
            {
                "text": synthesis.get("daily_overview"),
                "supporting_item_ids": synthesis_support,
            }
        ]
        if synthesis.get("daily_overview")
        else [],
        "source_health": source_health,
        "data_quality_notes": data_quality_notes,
        "watchlist": synthesis.get("watchlist", []) if synthesis else [],
        "generation_audit": {
            "schema_version": "1.0",
            "profile_version": profile.get("profile_version"),
            "rule_version": "1.0",
            "prompt_version": "1.0",
            "llm_runs": llm_audit,
            "dedup_counts": dedup_counts,
            "partial_failures": [h for h in source_health if h.get("status") in {"failed", "partial"}],
            "content_hash": stable_hash(issue_id + generated_at, 32),
        },
        "outputs": {},
    }
    return issue
