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
from .markup import protect_scientific_markup
from .query_planner import build_query_tasks
from .render import build_email_html, build_report_html, build_rss
from .sources.news import collect_news
from .sources.scholarly import collect_scholarly
from .storage import load_state, save_outputs
from .translation import (
    apply_event_bilingual,
    apply_translation,
    deterministic_copy_for_chinese,
    ensure_bilingual_placeholders,
    extract_translation_fields,
    prepare_translation_item,
    restore_translation_fields,
    translation_cache_key,
    validate_translation_fields,
)
from .utils import content_hash, utc_now_iso
from .validation import validate_ai_output, validate_schema


def _evidence_for_work(work: dict[str, Any]) -> list[dict[str, str]]:
    return work.get("abstract", {}).get("sentences") or []


def _evidence_for_article(article: dict[str, Any]) -> list[dict[str, str]]:
    return article.get("content", {}).get("sentences") or []


def _source_health_disabled(profile: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "source_id": source.get("source_id"),
            "status": "disabled",
            "record_count": 0,
            "query_count": 0,
            "errors": [],
            "audits": [],
        }
        for source in profile.get("source_registry", {}).get("sources", [])
        if not source.get("enabled", False)
    ]


def _live_collect(
    profile: dict[str, Any], window: Any
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    client = HttpClient(
        timeout=int(profile.get("search_policy", {}).get("request_timeout_seconds", 20)),
        user_agent=os.getenv(
            "PDI_USER_AGENT",
            "PathogenDailyIntelligence/1.2 (research monitoring; contact configured by operator)",
        ),
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


def _protect_evidence(
    evidence: list[dict[str, str]], mapping: dict[str, str]
) -> list[dict[str, str]]:
    protected: list[dict[str, str]] = []
    for sentence in evidence:
        text = str(sentence.get("text") or "")
        for token, fragment in mapping.items():
            text = text.replace(fragment, token)
        protected.append({"id": sentence.get("id", ""), "text": text})
    return protected


def _cache_hit(cache: dict[str, Any], key: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    entry = cache.get(key)
    if not isinstance(entry, dict) or not isinstance(entry.get("output"), dict):
        return None, None
    audit = dict(entry.get("audit") or {})
    audit.update({"status": "cache_hit", "cache_hit": True, "generated_at": entry.get("saved_at")})
    return dict(entry["output"]), audit


def _save_cache(cache: dict[str, Any], key: str, output: dict[str, Any], audit: dict[str, Any]) -> None:
    cache[key] = {"output": output, "audit": audit, "saved_at": utc_now_iso()}


def _has_valid_existing_translation(item: dict[str, Any], kind: str) -> bool:
    translated_title = item.get("title", {}).get("translated_zh")
    if not translated_title:
        return False
    if kind == "work":
        source_text = item.get("abstract", {}).get("original")
        translated_text = item.get("abstract", {}).get("translated_zh")
    else:
        source_text = item.get("content", {}).get("excerpt")
        translated_text = item.get("content", {}).get("translated_excerpt_zh")
    return not source_text or bool(translated_text)


def _apply_model_translation(
    item: dict[str, Any],
    kind: str,
    raw_output: dict[str, Any],
    mapping: dict[str, str],
    audit: dict[str, Any],
) -> tuple[bool, list[str]]:
    title = item.get("title", {}).get("original") or ""
    source_text = (
        item.get("abstract", {}).get("original")
        if kind == "work"
        else item.get("content", {}).get("excerpt")
    )
    raw_fields = extract_translation_fields(raw_output, kind)
    validation = validate_translation_fields(title, source_text, raw_fields, mapping)
    if not validation["valid"]:
        return False, validation["errors"]
    restored = restore_translation_fields(raw_fields, mapping)
    apply_translation(item, kind, restored, audit)
    return True, []


def _run_analysis_for_work(
    router: ModelRouter,
    work: dict[str, Any],
    approved_terms: list[str],
    cache: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared, mapping = prepare_translation_item(work, "work")
    evidence = _evidence_for_work(work)
    payload = {
        "record_id": work["work_id"],
        "bibliography": work.get("bibliography", {}),
        "title": prepared["title"],
        "title_evidence": {"id": "T0", "text": prepared["title"]},
        "abstract_sentences": _protect_evidence(evidence, mapping),
        "source_metadata": work.get("source_records", []),
        "full_text_available": False,
        "protected_placeholders": prepared["protected_placeholders"],
    }
    cache_key = "analysis:literature_analysis:" + content_hash(payload)
    output, audit = _cache_hit(cache, cache_key)
    if output is None:
        run = router.run("literature_analysis", payload)
        output, audit = run.output, run.audit()
    audit = dict(audit or {})
    if not output:
        audit.update({"validation_status": "failed", "validation_errors": ["NO_OUTPUT"]})
        return {}, audit

    validation_evidence = evidence + [{"id": "T0", "text": work.get("title", {}).get("original") or ""}]
    ai_validation = validate_ai_output(output, validation_evidence, approved_terms)
    translated, translation_errors = _apply_model_translation(work, "work", output, mapping, audit)
    errors = list(ai_validation["errors"]) + translation_errors
    audit["validation_status"] = "passed" if not errors else "failed"
    audit["unsupported_claim_count"] = ai_validation["unsupported_claim_count"]
    audit["validation_errors"] = errors[:30]
    if translated:
        work.setdefault("translation_audit", {}).update({
            "validation_status": "passed" if not translation_errors else "failed",
            "validation_errors": translation_errors[:30],
        })
    if not errors:
        work["ai_analysis"] = output
        _save_cache(cache, cache_key, output, audit)
    elif translated:
        # Translation may be useful even if a non-translation analytical field failed.
        work["ai_analysis"] = None
    return output, audit


def _run_analysis_for_article(
    router: ModelRouter,
    article: dict[str, Any],
    approved_terms: list[str],
    cache: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    prepared, mapping = prepare_translation_item(article, "article")
    evidence = _evidence_for_article(article)
    task = (
        "official_notice_analysis"
        if article.get("source", {}).get("reliability_tier") == "A"
        else "media_news_analysis"
    )
    payload = {
        "record_id": article["article_id"],
        "source": article.get("source", {}),
        "title": prepared["title"],
        "title_evidence": {"id": "T0", "text": prepared["title"]},
        "content_sentences": _protect_evidence(evidence, mapping),
        "entities_from_rules": article.get("entities", {}),
        "protected_placeholders": prepared["protected_placeholders"],
    }
    cache_key = f"analysis:{task}:" + content_hash(payload)
    output, audit = _cache_hit(cache, cache_key)
    if output is None:
        run = router.run(task, payload)
        output, audit = run.output, run.audit()
    audit = dict(audit or {})
    if not output:
        audit.update({"validation_status": "failed", "validation_errors": ["NO_OUTPUT"]})
        return {}, audit

    validation_evidence = evidence + [{"id": "T0", "text": article.get("title", {}).get("original") or ""}]
    ai_validation = validate_ai_output(output, validation_evidence, approved_terms)
    translated, translation_errors = _apply_model_translation(article, "article", output, mapping, audit)
    errors = list(ai_validation["errors"]) + translation_errors
    audit["validation_status"] = "passed" if not errors else "failed"
    audit["unsupported_claim_count"] = ai_validation["unsupported_claim_count"]
    audit["validation_errors"] = errors[:30]
    if translated:
        article.setdefault("translation_audit", {}).update({
            "validation_status": "passed" if not translation_errors else "failed",
            "validation_errors": translation_errors[:30],
        })
    if not errors:
        article["ai_analysis"] = output
        _save_cache(cache, cache_key, output, audit)
    elif translated:
        article["ai_analysis"] = None
    return output, audit


def _translate_remaining_items(
    router: ModelRouter,
    items: list[tuple[dict[str, Any], str]],
    cache: dict[str, Any],
    audits: list[dict[str, Any]],
    batch_size: int,
) -> None:
    pending: list[tuple[dict[str, Any], str, dict[str, Any], dict[str, str], str]] = []
    for item, kind in items:
        if _has_valid_existing_translation(item, kind):
            item.setdefault("translation_audit", {
                "provider": "source_or_demo",
                "model": None,
                "status": "existing",
                "validation_status": "passed",
                "generated_at": utc_now_iso(),
                "fallback_used": False,
            })
            continue
        if deterministic_copy_for_chinese(item, kind):
            continue
        cache_key = translation_cache_key(item, kind)
        cached_fields, cached_audit = _cache_hit(cache, cache_key)
        if cached_fields is not None:
            apply_translation(item, kind, cached_fields, cached_audit or {})
            audits.append(cached_audit or {})
            continue
        prepared, mapping = prepare_translation_item(item, kind)
        pending.append((item, kind, prepared, mapping, cache_key))

    for start in range(0, len(pending), max(1, batch_size)):
        batch = pending[start : start + max(1, batch_size)]
        payload = {"items": [entry[2] for entry in batch]}
        run = router.run("bilingual_translation_batch", payload)
        batch_audit = run.audit()
        output_items = {
            str(row.get("record_id")): row
            for row in (run.output or {}).get("items", [])
            if isinstance(row, dict) and row.get("record_id")
        }
        passed = 0
        batch_errors: list[str] = []
        for item, kind, prepared, mapping, cache_key in batch:
            record_id = str(prepared["record_id"])
            raw = output_items.get(record_id)
            if not raw:
                ensure_bilingual_placeholders(item, kind)
                batch_errors.append(f"MISSING_RECORD:{record_id}")
                continue
            fields = extract_translation_fields(raw, kind)
            title = item.get("title", {}).get("original") or ""
            source_text = (
                item.get("abstract", {}).get("original")
                if kind == "work"
                else item.get("content", {}).get("excerpt")
            )
            validation = validate_translation_fields(title, source_text, fields, mapping)
            if not validation["valid"]:
                ensure_bilingual_placeholders(item, kind)
                batch_errors.extend(f"{record_id}:{error}" for error in validation["errors"])
                continue
            restored = restore_translation_fields(fields, mapping)
            item_audit = {
                **batch_audit,
                "validation_status": "passed",
                "validation_errors": [],
                "record_id": record_id,
            }
            apply_translation(item, kind, restored, item_audit)
            _save_cache(cache, cache_key, restored, item_audit)
            passed += 1
        batch_audit["validation_status"] = "passed" if passed == len(batch) else "partial"
        batch_audit["translated_items"] = passed
        batch_audit["requested_items"] = len(batch)
        batch_audit["validation_errors"] = batch_errors[:30]
        audits.append(batch_audit)

    for item, kind in items:
        if not _has_valid_existing_translation(item, kind):
            ensure_bilingual_placeholders(item, kind)


def _translate_and_analyse(
    root: Path,
    profile: dict[str, Any],
    works: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    events: list[dict[str, Any]],
    previous_cache: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    router = ModelRouter(root, profile.get("llm_policy", {}))
    audits: list[dict[str, Any]] = []
    cache = dict(previous_cache or {})
    max_analysis_items = int(profile.get("llm_policy", {}).get("max_items_per_issue", 12))
    batch_size = int(profile.get("llm_policy", {}).get("translation_batch_size", 6))
    selected_works = [
        work
        for work in works
        if work.get("filter_result", {}).get("decision") in {"headline", "brief"}
        and _evidence_for_work(work)
    ][:max_analysis_items]
    selected_articles = [
        article
        for article in articles
        if article.get("classification", {}).get("decision") in {"headline", "brief", "review"}
        and _evidence_for_article(article)
    ][:max_analysis_items]
    approved_terms = [
        term.get("term")
        for term in profile.get("lexicon", [])
        if term.get("status") == "accepted_for_search" and term.get("term")
    ]

    for work in selected_works:
        _, audit = _run_analysis_for_work(router, work, approved_terms, cache)
        audits.append(audit)
    for article in selected_articles:
        _, audit = _run_analysis_for_article(router, article, approved_terms, cache)
        audits.append(audit)

    # Every normalized work and news article receives a Chinese-first display record.
    _translate_remaining_items(
        router,
        [(work, "work") for work in works] + [(article, "article") for article in articles],
        cache,
        audits,
        batch_size,
    )
    apply_event_bilingual(events, articles)

    support_ids = {work["work_id"] for work in works} | {event["event_id"] for event in events}
    synthesis_payload = {
        "items": [
            {
                "item_id": event["event_id"],
                "type": "event",
                "summary": event.get("summary_zh") or event.get("summary_original") or event.get("summary"),
                "official_status": event.get("official_status"),
                "material_change": event.get("material_change"),
            }
            for event in events
            if event.get("display_decision") in {"headline", "brief"}
        ]
        + [
            {
                "item_id": work["work_id"],
                "type": "work",
                "title": work.get("title", {}).get("translated_zh")
                or work.get("title", {}).get("original"),
                "analysis": work.get("ai_analysis"),
            }
            for work in works
            if work.get("filter_result", {}).get("decision") in {"headline", "brief"}
        ]
    }
    daily_synthesis = None
    if synthesis_payload["items"] and profile.get("llm_policy", {}).get("daily_synthesis", True):
        cache_key = "analysis:daily_synthesis:" + content_hash(synthesis_payload)
        output, audit = _cache_hit(cache, cache_key)
        if output is None:
            run = router.run("daily_synthesis", synthesis_payload)
            output, audit = run.output, run.audit()
        audit = dict(audit or {})
        if output:
            validation = validate_ai_output(output, [], approved_terms, support_ids=support_ids)
            non_numeric_errors = [
                error for error in validation["errors"] if not error.startswith("Unsupported numeric")
            ]
            audit["validation_status"] = "passed" if not non_numeric_errors else "failed"
            audit["unsupported_claim_count"] = validation["unsupported_claim_count"]
            audit["validation_errors"] = non_numeric_errors
            if not non_numeric_errors:
                daily_synthesis = output
                _save_cache(cache, cache_key, output, audit)
        audits.append(audit)

    # Keep state bounded while retaining enough overlap for delayed indexing and repeated records.
    if len(cache) > 2500:
        cache = dict(list(cache.items())[-2500:])
    return daily_synthesis, audits, cache


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
    works = [annotate_work(work, profile.get("lexicon", [])) for work in works]
    articles = [annotate_article(article, profile.get("lexicon", [])) for article in articles]

    seen_works = set(previous_state.get("work_ids", []))
    works = [classify_work(work, profile, is_new=work["work_id"] not in seen_works) for work in works]
    event_candidates = [
        article
        for article in articles
        if relevance(article, profile, "article")[0] in {"strong", "combined"}
    ]
    events, event_state = cluster_events(event_candidates, previous_state)
    event_map = {event["event_id"]: event for event in events}
    articles = [classify_article(article, profile, event_map.get(article.get("event_id"))) for article in articles]
    for event in events:
        decisions = [
            article.get("classification", {}).get("decision", "archive")
            for article in articles
            if article.get("event_id") == event["event_id"]
        ]
        scores = [
            article.get("classification", {}).get("score", 0)
            for article in articles
            if article.get("event_id") == event["event_id"]
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
    llm_cache = dict(previous_state.get("llm_cache", {}))
    if not disable_llm:
        daily_synthesis, llm_audit, llm_cache = _translate_and_analyse(
            root, profile, works, articles, events, llm_cache
        )
    else:
        for work in works:
            if _has_valid_existing_translation(work, "work"):
                work.setdefault("translation_audit", {
                    "provider": "source_or_demo",
                    "model": None,
                    "status": "existing",
                    "validation_status": "passed",
                    "generated_at": utc_now_iso(),
                    "fallback_used": False,
                })
            else:
                deterministic_copy_for_chinese(work, "work")
                ensure_bilingual_placeholders(work, "work")
        for article in articles:
            if _has_valid_existing_translation(article, "article"):
                article.setdefault("translation_audit", {
                    "provider": "source_or_demo",
                    "model": None,
                    "status": "existing",
                    "validation_status": "passed",
                    "generated_at": utc_now_iso(),
                    "fallback_used": False,
                })
            else:
                deterministic_copy_for_chinese(article, "article")
                ensure_bilingual_placeholders(article, "article")
        apply_event_bilingual(events, articles)
        llm_audit.append(
            {
                "provider": "deterministic",
                "model": None,
                "status": "disabled",
                "error": "LLM disabled by command option; validated source or demo translations were retained.",
                "retry_count": 0,
                "fallback_used": False,
                "generated_at": utc_now_iso(),
                "validation_status": "not_applicable",
                "unsupported_claim_count": 0,
            }
        )

    state = {
        "schema_version": "1.1",
        "updated_at": utc_now_iso(),
        "work_ids": sorted(seen_works | {work["work_id"] for work in works}),
        "article_ids": sorted(
            set(previous_state.get("article_ids", [])) | {article["article_id"] for article in articles}
        ),
        "events": event_state.get("events", []),
        "llm_cache": llm_cache,
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
    manifest = save_outputs(
        output_dir, issue, works, articles, events, state, html_text, email_html, rss_text
    )
    issue["outputs"] = manifest

    from .utils import write_json

    write_json(output_dir / "data" / "latest.json", issue)
    write_json(output_dir / "site" / "latest.json", issue)
    archive_parts = issue["issue_date"].split("-")
    write_json(
        output_dir
        / "data"
        / "archive"
        / archive_parts[0]
        / archive_parts[1]
        / archive_parts[2]
        / "issue.json",
        issue,
    )

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
