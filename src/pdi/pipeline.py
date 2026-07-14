from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import load_profile
from .dates import coverage_window
from .dedup import deduplicate_news, deduplicate_scholarly
from .demo import demo_source_results
from .entities import annotate_article, annotate_work
from .events import cluster_events
from .filters import classify_article, classify_work, relevance
from .http import HttpClient
from .issue import build_daily_issue
from .llm import ModelRouter
from .query_planner import build_query_tasks
from .render import build_email_html, build_report_html, build_rss
from .sources.news import collect_news
from .sources.scholarly import collect_scholarly
from .storage import load_state, save_outputs
from .utils import read_json, utc_now_iso
from .validation import validate_ai_output, validate_schema


def _evidence_for_work(work: dict[str, Any]) -> list[dict[str, str]]:
    return work.get("abstract", {}).get("sentences") or []


def _evidence_for_article(article: dict[str, Any]) -> list[dict[str, str]]:
    return article.get("content", {}).get("sentences") or []


def _source_health_disabled(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": s.get("source_id"),
            "status": "disabled",
            "record_count": 0,
            "query_count": 0,
            "errors": [],
            "audits": [],
        }
        for s in profile.get("source_registry", {}).get("sources", [])
        if not s.get("enabled", False)
    ]


def _live_collect(profile: dict[str, Any], window: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    client = HttpClient(
        timeout=int(profile.get("search_policy", {}).get("request_timeout_seconds", 20)),
        user_agent=os.getenv("PDI_USER_AGENT", "PathogenDailyIntelligence/1.0 (research monitoring; contact configured by operator)"),
    )
    scholarly_records: list[dict[str, Any]] = []
    news_records: list[dict[str, Any]] = []
    health: list[dict[str, Any]] = []
    for source in profile.get("source_registry", {}).get("sources", []):
        if not source.get("enabled", False):
            continue
        tasks = build_query_tasks(profile, source)
        try:
            if source.get("category") == "scholarly":
                result = collect_scholarly(client, source, tasks, window)
                scholarly_records.extend(result.records)
            else:
                result = collect_news(client, source, tasks, window)
                news_records.extend(result.records)
            health.append(result.health())
        except Exception as exc:  # one source must never stop the issue
            health.append(
                {
                    "source_id": source.get("source_id"),
                    "status": "failed",
                    "record_count": 0,
                    "query_count": len(tasks),
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "audits": [],
                }
            )
    health.extend(_source_health_disabled(profile))
    return scholarly_records, news_records, health


def _translate_and_analyse(
    root: Path,
    profile: dict[str, Any],
    works: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    router = ModelRouter(root, profile.get("llm_policy", {}))
    audits: list[dict[str, Any]] = []
    max_items = int(profile.get("llm_policy", {}).get("max_items_per_issue", 12))
    selected_works = [w for w in works if w.get("filter_result", {}).get("decision") in {"headline", "brief"}][:max_items]
    selected_articles = [a for a in articles if a.get("classification", {}).get("decision") in {"headline", "brief", "review"}][:max_items]
    approved_terms = [x.get("term") for x in profile.get("lexicon", []) if x.get("status") == "accepted_for_search"]

    for work in selected_works:
        evidence = _evidence_for_work(work)
        if not evidence:
            continue
        payload = {
            "record_id": work["work_id"],
            "bibliography": work.get("bibliography", {}),
            "title": work.get("title", {}).get("original"),
            "abstract_sentences": evidence,
            "source_metadata": work.get("source_records", []),
            "full_text_available": False,
        }
        run = router.run("literature_analysis", payload)
        audit = run.audit()
        if run.output:
            validation = validate_ai_output(run.output, evidence, approved_terms)
            audit["validation_status"] = "passed" if validation["valid"] else "failed"
            audit["unsupported_claim_count"] = validation["unsupported_claim_count"]
            audit["validation_errors"] = validation["errors"]
            if validation["valid"]:
                work["ai_analysis"] = run.output
                translated = run.output.get("translated_title")
                if translated:
                    work["title"]["translated_zh"] = translated
        audits.append(audit)

    article_map = {a["article_id"]: a for a in articles}
    for article in selected_articles:
        evidence = _evidence_for_article(article)
        if not evidence:
            continue
        is_official = article.get("source", {}).get("reliability_tier") == "A"
        task = "official_notice_analysis" if is_official else "media_news_analysis"
        payload = {
            "record_id": article["article_id"],
            "source": article.get("source", {}),
            "title": article.get("title", {}).get("original"),
            "content_sentences": evidence,
            "entities_from_rules": article.get("entities", {}),
        }
        run = router.run(task, payload)
        audit = run.audit()
        if run.output:
            validation = validate_ai_output(run.output, evidence, approved_terms)
            audit["validation_status"] = "passed" if validation["valid"] else "failed"
            audit["unsupported_claim_count"] = validation["unsupported_claim_count"]
            audit["validation_errors"] = validation["errors"]
            if validation["valid"]:
                article["ai_analysis"] = run.output
        audits.append(audit)

    support_ids = {w["work_id"] for w in works} | {e["event_id"] for e in events}
    synthesis_payload = {
        "items": [
            {
                "item_id": e["event_id"],
                "type": "event",
                "summary": e.get("summary"),
                "official_status": e.get("official_status"),
                "material_change": e.get("material_change"),
            }
            for e in events
            if e.get("display_decision") in {"headline", "brief"}
        ]
        + [
            {
                "item_id": w["work_id"],
                "type": "work",
                "title": w.get("title", {}).get("original"),
                "analysis": w.get("ai_analysis"),
            }
            for w in works
            if w.get("filter_result", {}).get("decision") in {"headline", "brief"}
        ]
    }
    daily_synthesis = None
    if synthesis_payload["items"] and profile.get("llm_policy", {}).get("daily_synthesis", True):
        run = router.run("daily_synthesis", synthesis_payload)
        audit = run.audit()
        if run.output:
            validation = validate_ai_output(run.output, [], approved_terms, support_ids=support_ids)
            # Daily synthesis has no sentence evidence; supporting IDs are the hard gate.
            non_numeric_errors = [x for x in validation["errors"] if not x.startswith("Unsupported numeric")]
            audit["validation_status"] = "passed" if not non_numeric_errors else "failed"
            audit["unsupported_claim_count"] = validation["unsupported_claim_count"]
            audit["validation_errors"] = non_numeric_errors
            if not non_numeric_errors:
                daily_synthesis = run.output
        audits.append(audit)
    return works, daily_synthesis, audits


def run_daily_pipeline(
    root: Path,
    profile_id: str,
    output_dir: Path,
    state_dir: Path | None = None,
    demo_mode: bool = False,
    disable_llm: bool = False,
) -> dict[str, Any]:
    profile = load_profile(profile_id, root)
    window = coverage_window(
        int(profile.get("search_policy", {}).get("window_days", 7)),
        profile.get("search_policy", {}).get("timezone", "Asia/Shanghai"),
    )
    previous_state = load_state(state_dir or (output_dir / "data" / "state"))

    if demo_mode:
        scholarly_records, news_records, source_health = demo_source_results(window.issue_date)
    else:
        scholarly_records, news_records, source_health = _live_collect(profile, window)

    works, scholarly_counts = deduplicate_scholarly(scholarly_records)
    articles, news_counts = deduplicate_news(news_records)
    works = [annotate_work(w, profile.get("lexicon", [])) for w in works]
    articles = [annotate_article(a, profile.get("lexicon", [])) for a in articles]

    seen_works = set(previous_state.get("work_ids", []))
    works = [classify_work(w, profile, is_new=w["work_id"] not in seen_works) for w in works]
    event_candidates = [a for a in articles if relevance(a, profile, "article")[0] in {"strong", "combined"}]
    events, event_state = cluster_events(event_candidates, previous_state)
    event_map = {event["event_id"]: event for event in events}
    articles = [classify_article(a, profile, event_map.get(a.get("event_id"))) for a in articles]
    for event in events:
        decisions = [
            a.get("classification", {}).get("decision", "archive")
            for a in articles
            if a.get("event_id") == event["event_id"]
        ]
        scores = [
            a.get("classification", {}).get("score", 0)
            for a in articles
            if a.get("event_id") == event["event_id"]
        ]
        if not event.get("material_change"):
            event["display_decision"] = "archive"
        elif "headline" in decisions:
            event["display_decision"] = "headline"
        elif "brief" in decisions:
            event["display_decision"] = "brief"
        elif "review" in decisions:
            event["display_decision"] = "review"
        else:
            event["display_decision"] = "archive"
        event["display_score"] = max(scores or [0])

    llm_audit: list[dict[str, Any]] = []
    daily_synthesis = None
    if not disable_llm:
        works, daily_synthesis, llm_audit = _translate_and_analyse(root, profile, works, articles, events)
    else:
        llm_audit.append(
            {
                "provider": "deterministic",
                "model": None,
                "status": "disabled",
                "error": "LLM disabled by command option.",
                "retry_count": 0,
                "fallback_used": False,
                "generated_at": utc_now_iso(),
                "validation_status": "not_applicable",
                "unsupported_claim_count": 0,
            }
        )

    state = {
        "schema_version": "1.0",
        "updated_at": utc_now_iso(),
        "work_ids": sorted(seen_works | {w["work_id"] for w in works}),
        "article_ids": sorted(set(previous_state.get("article_ids", [])) | {a["article_id"] for a in articles}),
        "events": event_state.get("events", []),
    }
    issue = build_daily_issue(
        profile,
        window,
        works,
        events,
        source_health,
        {"scholarly": scholarly_counts, "news": news_counts},
        llm_audit,
        daily_synthesis,
    )
    html_text = build_report_html(issue, works, events, profile)
    email_html = build_email_html(issue, works, events, profile)
    rss_text = build_rss(issue, profile)
    manifest = save_outputs(output_dir, issue, works, articles, events, state, html_text, email_html, rss_text)
    issue["outputs"] = manifest
    # Rewrite latest after output paths are known.
    from .utils import write_json
    write_json(output_dir / "data" / "latest.json", issue)
    write_json(output_dir / "site" / "latest.json", issue)
    archive_parts = issue["issue_date"].split("-")
    write_json(output_dir / "data" / "archive" / archive_parts[0] / archive_parts[1] / archive_parts[2] / "issue.json", issue)

    schema_errors = validate_schema(issue, root / "schemas" / "daily_issue.schema.json")
    if schema_errors:
        raise RuntimeError("DailyIssue schema validation failed: " + "; ".join(schema_errors[:10]))
    return {
        "issue": issue,
        "works": works,
        "articles": articles,
        "events": events,
        "manifest": manifest,
    }
