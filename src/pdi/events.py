from __future__ import annotations

from datetime import date
from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from .utils import normalize_title, stable_hash, utc_now_iso


_TIER_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3, "unknown": 4}
_EVENT_FAMILY = {
    "human_case": "human_health",
    "outbreak": "human_health",
    "host_surveillance": "host_surveillance",
    "surveillance": "surveillance",
    "other": "other",
}


def _day(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def _compatible_event_type(a: str | None, b: str | None) -> bool:
    return _EVENT_FAMILY.get(a or "other", "other") == _EVENT_FAMILY.get(b or "other", "other")


def _similar(a: dict[str, Any], b: dict[str, Any]) -> float:
    ea = a.get("entities", {})
    eb = b.get("entities", {})
    if not _compatible_event_type(ea.get("event_type"), eb.get("event_type")):
        return 0.0
    pathogens_a = set(ea.get("pathogens") or [])
    pathogens_b = set(eb.get("pathogens") or [])
    if pathogens_a and pathogens_b and not (pathogens_a & pathogens_b):
        return 0.0
    pathogen = 1.0 if pathogens_a & pathogens_b else 0.5
    country_a, country_b = ea.get("country"), eb.get("country")
    if country_a and country_b and country_a != country_b:
        return 0.0
    location = 1.0 if country_a and country_a == country_b else 0.45
    event = 1.0 if ea.get("event_type") == eb.get("event_type") else 0.65
    da, db = _day(a.get("published_at")), _day(b.get("published_at"))
    date_score = 0.5
    if da and db:
        date_score = max(0.0, 1 - abs((da - db).days) / 10)
    title = token_set_ratio(
        normalize_title(a.get("title", {}).get("original")),
        normalize_title(b.get("title", {}).get("original")),
    ) / 100
    return 0.29 * pathogen + 0.25 * location + 0.18 * event + 0.13 * date_score + 0.15 * title


def _primary_key(article: dict[str, Any]) -> tuple[Any, ...]:
    classification = article.get("classification") or {}
    prominence = (classification.get("pathogen_prominence") or {}).get("level")
    prominence_order = {"title_focus": 0, "lead_focus": 1, "body_focus": 2}.get(prominence, 9)
    coverage = (article.get("content") or {}).get("coverage_level")
    coverage_order = {"full_relevant_extract": 0, "focused_partial": 1, "title_or_snippet_only": 2}.get(coverage, 9)
    return (
        _TIER_ORDER.get((article.get("source") or {}).get("reliability_tier", "unknown"), 4),
        prominence_order,
        coverage_order,
        -(float(classification.get("score") or 0)),
        article.get("published_at") or "9999",
    )


def _max_or_none(values: list[int | None]) -> int | None:
    valid = [value for value in values if value is not None]
    return max(valid) if valid else None


def cluster_events(
    articles: list[dict[str, Any]],
    previous_state: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    previous_state = previous_state or {}
    prior = previous_state.get("events") or []
    clusters: list[dict[str, Any]] = []

    for article in articles:
        best = None
        best_score = 0.0
        for cluster in clusters:
            score = max(_similar(article, other) for other in cluster["articles"])
            if score > best_score:
                best, best_score = cluster, score
        if best is not None and best_score >= 0.80:
            best["articles"].append(article)
            best["scores"].append(best_score)
        else:
            clusters.append({"articles": [article], "scores": [1.0]})

    events: list[dict[str, Any]] = []
    new_state: list[dict[str, Any]] = []
    for cluster in clusters:
        arts = cluster["articles"]
        primary = sorted(arts, key=_primary_key)[0]
        entity = primary.get("entities", {})
        signature = "|".join(
            [
                str((entity.get("pathogens") or ["unknown"])[0]),
                str(entity.get("country") or "unknown"),
                str(entity.get("event_type") or "other"),
                normalize_title(primary.get("title", {}).get("original"))[:100],
            ]
        )
        event_id = None
        version = 1
        history: list[dict[str, Any]] = []
        for old in prior:
            old_stub = {
                "entities": {
                    "pathogens": old.get("pathogens") or [],
                    "country": old.get("country"),
                    "event_type": old.get("event_type"),
                },
                "published_at": old.get("published_at"),
                "title": {"original": old.get("title") or ""},
            }
            if _similar(primary, old_stub) >= 0.84:
                event_id = old.get("event_id")
                version = int(old.get("event_version", 1))
                history = list(old.get("change_history") or [])
                break
        if not event_id:
            event_id = "event-" + stable_hash(signature)

        counts = {
            key: _max_or_none([a.get("entities", {}).get(f"{key}_cases") for a in arts])
            for key in ["confirmed", "probable", "suspected"]
        }
        counts["deaths"] = _max_or_none([a.get("entities", {}).get("deaths") for a in arts])

        old_match = next((old for old in prior if old.get("event_id") == event_id), None)
        material = old_match is None
        if old_match and old_match.get("case_counts") != counts:
            material = True
            version += 1
            history.append(
                {
                    "changed_at": utc_now_iso(),
                    "type": "case_count_change",
                    "previous": old_match.get("case_counts"),
                    "current": counts,
                }
            )

        event = {
            "schema_version": "1.0",
            "event_id": event_id,
            "event_version": version,
            "event_type": entity.get("event_type") or "other",
            "pathogens": sorted({p for a in arts for p in (a.get("entities", {}).get("pathogens") or [])}),
            "diseases": [],
            "location": {
                "country": entity.get("country"),
                "admin1": None,
                "admin2": None,
                "city": None,
                "latitude": None,
                "longitude": None,
            },
            "timeline": {
                "event_date": None,
                "first_reported_at": min((a.get("published_at") for a in arts if a.get("published_at")), default=None),
                "first_seen_at": min((a.get("first_seen_at") for a in arts if a.get("first_seen_at")), default=utc_now_iso()),
                "last_updated_at": utc_now_iso(),
            },
            "case_counts": {
                **counts,
                "as_of": max((a.get("published_at") for a in arts if a.get("published_at")), default=None),
            },
            "hosts": sorted({h for a in arts for h in (a.get("entities", {}).get("hosts") or [])}),
            "official_status": "official" if any((a.get("source") or {}).get("reliability_tier") == "A" for a in arts) else "unconfirmed",
            "primary_source": {
                "article_id": primary["article_id"],
                "name": (primary.get("source") or {}).get("name"),
                "url": primary.get("canonical_url"),
            },
            "source_articles": [a["article_id"] for a in arts],
            "change_history": history,
            "cluster_quality": {
                "score": round(sum(cluster["scores"]) / len(cluster["scores"]), 3),
                "decision": "auto_merge" if len(arts) > 1 else "single_article",
                "rule_version": "1.5",
            },
            "summary": (primary.get("title") or {}).get("original"),
            "content_availability": {
                "status": (primary.get("content") or {}).get("availability_status"),
                "coverage_level": (primary.get("content") or {}).get("coverage_level"),
                "analysis_text_available": bool((primary.get("content") or {}).get("analysis_text")),
            },
            "material_change": material,
        }
        for article in arts:
            article["event_id"] = event_id
        events.append(event)
        new_state.append(
            {
                "event_id": event_id,
                "event_version": version,
                "title": (primary.get("title") or {}).get("original"),
                "pathogens": event["pathogens"],
                "country": event["location"]["country"],
                "event_type": event["event_type"],
                "published_at": event["timeline"]["first_reported_at"],
                "case_counts": counts,
                "change_history": history,
            }
        )
    return events, {"events": new_state, "updated_at": utc_now_iso()}
