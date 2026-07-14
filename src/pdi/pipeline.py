from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from .config import load_profile
from .dates import coverage_window
from .dedup import deduplicate_news, deduplicate_scholarly
from .demo import demo_source_results
from .enrichment import enrich_news_articles, enrich_scholarly_works
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
from .translation import (
    apply_event_bilingual,
    apply_translation,
    deterministic_copy_for_chinese,
    ensure_bilingual_placeholders,
    extract_translation_fields,
    prepare_translation_item,
    restore_scientific_object,
    restore_translation_fields,
    translation_cache_key,
    validate_translation_fields,
)
from .utils import content_hash, ensure_dict_field, utc_now_iso, write_json
from .validation import validate_ai_output, validate_schema


def _evidence_for_work(work: dict[str, Any]) -> list[dict[str, str]]:
    evidence = list((work.get("abstract") or {}).get("sentences") or [])
    for section in (work.get("full_text") or {}).get("sections", []):
        evidence.extend(section.get("sentences") or [])
    return evidence


def _evidence_for_article(article: dict[str, Any]) -> list[dict[str, str]]:
    return (article.get("content") or {}).get("sentences") or []


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
            "PathogenDailyIntelligence/1.3 (research monitoring; contact configured by operator)",
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
    translated_title = (item.get("title") or {}).get("translated_zh")
    if not translated_title:
        return False
    if kind == "work":
        source_text = (item.get("abstract") or {}).get("original")
        translated_text = (item.get("abstract") or {}).get("translated_zh")
    else:
        source_text = (item.get("content") or {}).get("translation_text") or (item.get("content") or {}).get("excerpt")
        translated_text = (item.get("content") or {}).get("translated_excerpt_zh")
    return not source_text or bool(translated_text)


def _apply_model_translation(
    item: dict[str, Any],
    kind: str,
    raw_output: dict[str, Any],
    mapping: dict[str, str],
    audit: dict[str, Any],
) -> tuple[bool, list[str]]:
    title = (item.get("title") or {}).get("original") or ""
    source_text = (
        (item.get("abstract") or {}).get("original")
        if kind == "work"
        else (item.get("content") or {}).get("translation_text") or (item.get("content") or {}).get("excerpt")
    )
    raw_fields = extract_translation_fields(raw_output, kind)
    validation = validate_translation_fields(title, source_text, raw_fields, mapping)
    if not validation["valid"]:
        return False, validation["errors"]
    restored = restore_translation_fields(raw_fields, mapping)
    apply_translation(item, kind, restored, audit)
    return True, []


def _aggregate_attempt_audit(
    task_name: str,
    record_id: str,
    attempts: list[dict[str, Any]],
    accepted: dict[str, Any] | None,
    errors: list[str],
) -> dict[str, Any]:
    accepted = accepted or {}
    return {
        "task_name": task_name,
        "record_id": record_id,
        "provider": accepted.get("provider") or "deterministic",
        "model": accepted.get("model"),
        "status": "success" if accepted else "failed",
        "generated_at": accepted.get("generated_at") or utc_now_iso(),
        "fallback_used": bool(accepted and attempts and accepted.get("provider") != attempts[0].get("provider")),
        "validation_status": "passed" if accepted else "failed",
        "validation_errors": errors[:40],
        "unsupported_claim_count": accepted.get("unsupported_claim_count", 0),
        "attempt_chain": attempts,
    }


def _run_validated_analysis(
    router: ModelRouter,
    task_name: str,
    payload: dict[str, Any],
    item: dict[str, Any],
    kind: str,
    evidence: list[dict[str, str]],
    approved_terms: list[str],
    cache: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one provider at a time and stop at the first fully validated result."""
    record_id = str(item.get("work_id") if kind == "work" else item.get("article_id"))
    cache_key = f"analysis:{task_name}:" + content_hash(payload)
    cached, cached_audit = _cache_hit(cache, cache_key)
    _prepared, mapping = prepare_translation_item(item, kind)
    validation_evidence = evidence + [
        {"id": "T0", "text": (item.get("title") or {}).get("original") or ""}
    ]
    source_text = (
        (item.get("abstract") or {}).get("original")
        if kind == "work"
        else item.get("content", {}).get("analysis_text")
        or (item.get("content") or {}).get("excerpt")
    )

    attempts: list[dict[str, Any]] = []
    all_errors: list[str] = []
    best_translation: tuple[dict[str, Any], dict[str, Any]] | None = None

    def evaluate(
        output: dict[str, Any] | None,
        run_audit: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        nonlocal best_translation
        attempt = dict(run_audit)
        if not output:
            attempt["validation_status"] = "failed"
            attempt["validation_errors"] = ["NO_OUTPUT"]
            attempts.append(attempt)
            all_errors.append(f"{attempt.get('provider')}:NO_OUTPUT")
            return None

        ai_validation = validate_ai_output(output, validation_evidence, approved_terms)
        raw_fields = extract_translation_fields(output, kind)
        translation_validation = validate_translation_fields(
            (item.get("title") or {}).get("original") or "",
            source_text,
            raw_fields,
            mapping,
        )
        errors = list(ai_validation["errors"]) + list(translation_validation["errors"])
        attempt["validation_status"] = "passed" if not errors else "failed"
        attempt["validation_errors"] = errors[:40]
        attempt["unsupported_claim_count"] = ai_validation["unsupported_claim_count"]
        attempts.append(attempt)
        all_errors.extend(f"{attempt.get('provider')}:{error}" for error in errors)

        if translation_validation["valid"] and best_translation is None:
            best_translation = (output, attempt)
        if errors:
            return None

        restored_audit = {**attempt, "attempt_chain": list(attempts)}
        _apply_model_translation(item, kind, output, mapping, restored_audit)
        restored_output = restore_scientific_object(output, mapping)
        item["ai_analysis"] = restored_output
        accepted_audit = _aggregate_attempt_audit(
            task_name, record_id, attempts, attempt, []
        )
        ensure_dict_field(item, "processing_audit")["llm_analysis"] = accepted_audit
        _save_cache(cache, cache_key, output, accepted_audit)
        return restored_output, accepted_audit

    # Cache is evaluated first. A stale/invalid cache entry is discarded and
    # does not prevent a real provider fallback.
    if cached is not None:
        accepted = evaluate(cached, dict(cached_audit or {}))
        if accepted is not None:
            return accepted
        cache.pop(cache_key, None)

    fallback_used = bool(attempts)
    for provider in router.provider_sequence(task_name):
        if provider == "deterministic":
            break
        run = router.run_provider(
            task_name,
            payload,
            provider,
            fallback_used=fallback_used,
        )
        accepted = evaluate(run.output, run.audit())
        if accepted is not None:
            return accepted
        fallback_used = True

    # A provider may have produced a valid faithful translation but an invalid
    # analytical claim. Keep only the validated translation and discard the
    # analytical output.
    if best_translation is not None and not _has_valid_existing_translation(item, kind):
        output, attempt = best_translation
        _apply_model_translation(
            item,
            kind,
            output,
            mapping,
            {**attempt, "attempt_chain": list(attempts)},
        )
    item["ai_analysis"] = None
    final_audit = _aggregate_attempt_audit(
        task_name, record_id, attempts, None, all_errors
    )
    ensure_dict_field(item, "processing_audit")["llm_analysis"] = final_audit
    return {}, final_audit

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
        "identifiers": work.get("identifiers", {}),
        "title": prepared["title"],
        "title_evidence": {"id": "T0", "text": prepared["title"]},
        "abstract_sentences": _protect_evidence((work.get("abstract") or {}).get("sentences") or [], mapping),
        "open_full_text_evidence": _protect_evidence(
            [row for section in (work.get("full_text") or {}).get("sections", []) for row in section.get("sentences", [])],
            mapping,
        ),
        "source_metadata": work.get("source_records", []),
        "full_text_available": bool(work.get("full_text", {}).get("available")),
        "protected_placeholders": prepared["protected_placeholders"],
    }
    return _run_validated_analysis(
        router, "literature_analysis", payload, work, "work", evidence, approved_terms, cache
    )


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
        if (article.get("source") or {}).get("reliability_tier") == "A"
        else "media_news_analysis"
    )
    payload = {
        "record_id": article["article_id"],
        "source": article.get("source", {}),
        "canonical_url": article.get("canonical_url"),
        "published_at": article.get("published_at"),
        "title": prepared["title"],
        "title_evidence": {"id": "T0", "text": prepared["title"]},
        "translation_source_text": prepared.get("text"),
        "content_sentences": _protect_evidence(evidence, mapping),
        "content_extraction_audit": (article.get("retrieval_audit") or {}).get("content_fetch"),
        "entities_from_rules": article.get("entities", {}),
        "protected_placeholders": prepared["protected_placeholders"],
    }
    return _run_validated_analysis(
        router, task, payload, article, "article", evidence, approved_terms, cache
    )


def _translation_source_text(item: dict[str, Any], kind: str) -> str | None:
    if kind == "work":
        return (item.get("abstract") or {}).get("original")
    return (item.get("content") or {}).get("translation_text") or (item.get("content") or {}).get("excerpt")


def _translate_remaining_items(
    router: ModelRouter,
    items: list[tuple[dict[str, Any], str]],
    cache: dict[str, Any],
    audits: list[dict[str, Any]],
    policy: dict[str, Any],
) -> None:
    pending: dict[str, tuple[dict[str, Any], str, dict[str, Any], dict[str, str], str]] = {}
    attempt_history: dict[str, list[dict[str, Any]]] = {}
    for item, kind in items:
        record_id = str(item.get("work_id") if kind == "work" else item.get("article_id"))
        if _has_valid_existing_translation(item, kind):
            existing_audit = ensure_dict_field(item, "translation_audit")
            if not existing_audit:
                existing_audit.update(
                    {
                        "provider": "source_or_demo",
                        "model": None,
                        "status": "existing",
                        "validation_status": "passed",
                        "generated_at": utc_now_iso(),
                        "fallback_used": False,
                        "attempt_chain": [],
                    }
                )
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
        pending[record_id] = (item, kind, prepared, mapping, cache_key)
        attempt_history[record_id] = []

    # Backward compatibility for v1.2 callers/tests that supplied only a batch-size integer.
    if isinstance(policy, int):
        policy = {"translation_provider_batch_sizes": {"legacy": max(1, policy)}}
    provider_sizes = policy.get("translation_provider_batch_sizes") or {
        "github_models": 6,
        "gemini": 2,
        "groq": 1,
    }
    if hasattr(router, "provider_sequence") and hasattr(router, "run_provider"):
        providers = [
            provider
            for provider in router.provider_sequence("bilingual_translation_batch")
            if provider != "deterministic"
        ]
    else:
        # Legacy router contract: one validated call, no hidden parallelism.
        providers = ["legacy"]
    for provider_index, provider in enumerate(providers):
        if not pending:
            break
        batch_size = max(1, int(provider_sizes.get(provider, 1)))
        task_name = "bilingual_translation_batch" if provider_index == 0 else "translation_repair"
        record_ids = list(pending)
        for start in range(0, len(record_ids), batch_size):
            batch_ids = [record_id for record_id in record_ids[start : start + batch_size] if record_id in pending]
            if not batch_ids:
                continue
            batch = [pending[record_id] for record_id in batch_ids]
            payload = {"items": [entry[2] for entry in batch]}
            if provider == "legacy":
                run = router.run("bilingual_translation_batch", payload)
            else:
                run = router.run_provider(
                    task_name,
                    payload,
                    provider,
                    fallback_used=provider_index > 0,
                )
            run_audit = run.audit()
            output_items = {
                str(row.get("record_id")): row
                for row in (run.output or {}).get("items", [])
                if isinstance(row, dict) and row.get("record_id")
            }
            translated_in_call = 0
            validation_errors: list[str] = []
            for record_id in batch_ids:
                item, kind, prepared, mapping, cache_key = pending[record_id]
                raw = output_items.get(record_id)
                item_attempt = dict(run_audit)
                if not raw:
                    item_attempt.update(
                        {
                            "record_id": record_id,
                            "validation_status": "failed",
                            "validation_errors": ["MISSING_RECORD_IN_MODEL_OUTPUT"],
                        }
                    )
                    attempt_history[record_id].append(item_attempt)
                    validation_errors.append(f"{record_id}:MISSING_RECORD_IN_MODEL_OUTPUT")
                    continue
                fields = extract_translation_fields(raw, kind)
                validation = validate_translation_fields(
                    (item.get("title") or {}).get("original") or "",
                    _translation_source_text(item, kind),
                    fields,
                    mapping,
                )
                item_attempt.update(
                    {
                        "record_id": record_id,
                        "validation_status": "passed" if validation["valid"] else "failed",
                        "validation_errors": validation["errors"],
                    }
                )
                attempt_history[record_id].append(item_attempt)
                if not validation["valid"]:
                    validation_errors.extend(f"{record_id}:{error}" for error in validation["errors"])
                    continue
                restored = restore_translation_fields(fields, mapping)
                item_audit = {
                    **item_attempt,
                    "fallback_used": provider_index > 0,
                    "attempt_chain": attempt_history[record_id],
                    "accepted_provider_index": provider_index,
                }
                apply_translation(item, kind, restored, item_audit)
                ensure_dict_field(item, "processing_audit")["translation"] = item_audit
                _save_cache(cache, cache_key, restored, item_audit)
                translated_in_call += 1
                pending.pop(record_id, None)

            call_audit = {
                **run_audit,
                "task_name": task_name,
                "requested_items": len(batch_ids),
                "translated_items": translated_in_call,
                "validation_status": "passed" if translated_in_call == len(batch_ids) else "partial",
                "validation_errors": validation_errors[:50],
                "unresolved_after_call": len(pending),
            }
            audits.append(call_audit)

    for record_id, (item, kind, _prepared, _mapping, _cache_key) in pending.items():
        ensure_bilingual_placeholders(item, kind)
        translation_audit = ensure_dict_field(item, "translation_audit")
        translation_audit.update(
            {
                "attempt_chain": attempt_history.get(record_id, []),
                "fallback_used": bool(attempt_history.get(record_id)),
                "validation_status": "translation_unavailable_after_all_providers",
            }
        )
        ensure_dict_field(item, "processing_audit")["translation"] = dict(translation_audit)


def _run_daily_synthesis(
    router: ModelRouter,
    payload: dict[str, Any],
    approved_terms: list[str],
    support_ids: set[str],
    cache: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Run synthesis providers lazily; only a rejected result triggers fallback."""
    cache_key = "analysis:daily_synthesis:" + content_hash(payload)
    cached, cached_audit = _cache_hit(cache, cache_key)
    attempts: list[dict[str, Any]] = []
    errors: list[str] = []

    def evaluate(
        candidate: dict[str, Any] | None,
        audit: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        attempt = dict(audit)
        if not candidate:
            attempt.update(
                {"validation_status": "failed", "validation_errors": ["NO_OUTPUT"]}
            )
            attempts.append(attempt)
            errors.append(f"{attempt.get('provider')}:NO_OUTPUT")
            return None
        validation = validate_ai_output(
            candidate, [], approved_terms, support_ids=support_ids
        )
        # Daily synthesis references already validated objects. Numeric claims
        # are governed by supporting IDs and deterministic statistics; claims
        # with unknown item IDs still fail.
        relevant_errors = [
            error
            for error in validation["errors"]
            if not error.startswith("Unsupported numeric")
        ]
        attempt["validation_status"] = "passed" if not relevant_errors else "failed"
        attempt["validation_errors"] = relevant_errors
        attempt["unsupported_claim_count"] = validation["unsupported_claim_count"]
        attempts.append(attempt)
        errors.extend(f"{attempt.get('provider')}:{error}" for error in relevant_errors)
        if relevant_errors:
            return None
        final_audit = _aggregate_attempt_audit(
            "daily_synthesis", "daily_issue", attempts, attempt, []
        )
        _save_cache(cache, cache_key, candidate, final_audit)
        return candidate, final_audit

    if cached is not None:
        accepted = evaluate(cached, dict(cached_audit or {}))
        if accepted is not None:
            return accepted
        cache.pop(cache_key, None)

    fallback_used = bool(attempts)
    for provider in router.provider_sequence("daily_synthesis"):
        if provider == "deterministic":
            break
        run = router.run_provider(
            "daily_synthesis",
            payload,
            provider,
            fallback_used=fallback_used,
        )
        accepted = evaluate(run.output, run.audit())
        if accepted is not None:
            return accepted
        fallback_used = True

    return None, _aggregate_attempt_audit(
        "daily_synthesis", "daily_issue", attempts, None, errors
    )

def _translate_and_analyse(
    root: Path,
    profile: dict[str, Any],
    works: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    events: list[dict[str, Any]],
    previous_cache: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    policy = profile.get("llm_policy", {})
    router = ModelRouter(root, policy)
    audits: list[dict[str, Any]] = []
    cache = dict(previous_cache or {})
    max_analysis_items = int(policy.get("max_items_per_issue", 18))
    selected_works = [
        work
        for work in works
        if work.get("filter_result", {}).get("decision") in {"headline", "brief", "review"}
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

    _translate_remaining_items(
        router,
        [(work, "work") for work in works] + [(article, "article") for article in articles],
        cache,
        audits,
        policy,
    )
    apply_event_bilingual(events, articles)

    support_ids = {work["work_id"] for work in works} | {event["event_id"] for event in events}
    synthesis_payload = {
        "statistics": {
            "works": len(works),
            "events": len(events),
            "validated_work_analyses": sum(bool(work.get("ai_analysis")) for work in works),
            "validated_article_analyses": sum(bool(article.get("ai_analysis")) for article in articles),
        },
        "items": [
            {
                "item_id": event["event_id"],
                "type": "event",
                "summary": event.get("summary_zh") or event.get("summary_original") or event.get("summary"),
                "analysis": event.get("ai_analysis"),
                "official_status": event.get("official_status"),
                "material_change": event.get("material_change"),
            }
            for event in events
            if event.get("display_decision") in {"headline", "brief", "review"}
        ]
        + [
            {
                "item_id": work["work_id"],
                "type": "work",
                "title": work.get("title", {}).get("translated_zh") or (work.get("title") or {}).get("original"),
                "analysis": work.get("ai_analysis"),
            }
            for work in works
            if work.get("filter_result", {}).get("decision") in {"headline", "brief", "review"}
        ],
    }
    daily_synthesis = None
    if synthesis_payload["items"] and policy.get("daily_synthesis", True):
        daily_synthesis, audit = _run_daily_synthesis(
            router, synthesis_payload, approved_terms, support_ids, cache
        )
        audits.append(audit)

    cache_limit = int(policy.get("translation_cache_max_entries", 3500))
    if len(cache) > cache_limit:
        cache = dict(list(cache.items())[-cache_limit:])
    return daily_synthesis, audits, cache


def _write_audit_outputs(
    output_dir: Path,
    content_audit: dict[str, Any],
    llm_audit: list[dict[str, Any]],
    works: list[dict[str, Any]],
    articles: list[dict[str, Any]],
) -> None:
    audit_dir = output_dir / "data" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    write_json(audit_dir / "content_enrichment.json", content_audit)
    (audit_dir / "llm_runs.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in llm_audit)
        + ("\n" if llm_audit else ""),
        encoding="utf-8",
    )
    object_rows: list[dict[str, Any]] = []
    for work in works:
        object_rows.append(
            {
                "object_type": "scholarly_work",
                "object_id": work.get("work_id"),
                "translation_audit": work.get("translation_audit"),
                "processing_audit": work.get("processing_audit"),
            }
        )
    for article in articles:
        object_rows.append(
            {
                "object_type": "news_article",
                "object_id": article.get("article_id"),
                "translation_audit": article.get("translation_audit"),
                "processing_audit": article.get("processing_audit"),
                "content_fetch": (article.get("retrieval_audit") or {}).get("content_fetch"),
            }
        )
    (audit_dir / "object_audit.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, default=str) for row in object_rows)
        + ("\n" if object_rows else ""),
        encoding="utf-8",
    )




def _apply_deterministic_bilingual_fallback(
    works: list[dict[str, Any]],
    articles: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    reason: str,
    error: str | None = None,
) -> None:
    """Guarantee publishable bilingual placeholders when the LLM layer is unavailable.

    This boundary is intentionally broader than provider-level error handling. A
    programming or data-shape error inside LLM orchestration must be audited, but
    must not prevent deterministic JSON/HTML/RSS publication.
    """
    for item, kind in [
        *[(work, "work") for work in works],
        *[(article, "article") for article in articles],
    ]:
        if _has_valid_existing_translation(item, kind):
            audit = ensure_dict_field(item, "translation_audit")
            if not audit:
                audit.update(
                    {
                        "provider": "source_or_demo",
                        "model": None,
                        "status": "existing",
                        "validation_status": "passed",
                        "generated_at": utc_now_iso(),
                        "fallback_used": False,
                    }
                )
        else:
            deterministic_copy_for_chinese(item, kind)
            ensure_bilingual_placeholders(item, kind)
        processing = ensure_dict_field(item, "processing_audit")
        processing["llm_orchestration"] = {
            "status": reason,
            "error": error,
            "processed_at": utc_now_iso(),
        }
    apply_event_bilingual(events, articles)


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

    if demo_mode:
        content_audit = {
            "news": {"attempted": 0, "success": 0, "failed": 0, "audits": [], "mode": "demo"},
            "scholarly": {"attempted": 0, "success": 0, "failed": 0, "audits": [], "mode": "demo"},
        }
    else:
        content_audit = {
            "scholarly": enrich_scholarly_works(works, profile),
            "news": enrich_news_articles(articles, profile),
        }

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
        try:
            daily_synthesis, llm_audit, llm_cache = _translate_and_analyse(
                root, profile, works, articles, events, llm_cache
            )
        except Exception as exc:  # final safety boundary: publish deterministic output
            error = f"{type(exc).__name__}: {exc}"
            _apply_deterministic_bilingual_fallback(
                works,
                articles,
                events,
                reason="orchestration_failed_deterministic_fallback",
                error=error,
            )
            llm_audit.append(
                {
                    "task_name": "llm_orchestration",
                    "provider": "deterministic",
                    "model": None,
                    "status": "failed",
                    "error": error,
                    "retry_count": 0,
                    "fallback_used": True,
                    "generated_at": utc_now_iso(),
                    "validation_status": "orchestration_failed_fallback_published",
                    "unsupported_claim_count": 0,
                }
            )
    else:
        _apply_deterministic_bilingual_fallback(
            works,
            articles,
            events,
            reason="llm_disabled",
        )
        llm_audit.append(
            {
                "task_name": "all",
                "provider": "deterministic",
                "model": None,
                "status": "disabled",
                "error": "LLM disabled; validated source or demo translations were retained.",
                "retry_count": 0,
                "fallback_used": False,
                "generated_at": utc_now_iso(),
                "validation_status": "not_applicable",
                "unsupported_claim_count": 0,
            }
        )

    state = {
        "schema_version": "1.3.1",
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
        content_audit=content_audit,
        articles=articles,
    )
    html_text = build_report_html(issue, works, events, profile)
    email_html = build_email_html(issue, works, events, profile)
    rss_text = build_rss(issue, profile)
    manifest = save_outputs(
        output_dir, issue, works, articles, events, state, html_text, email_html, rss_text
    )
    _write_audit_outputs(output_dir, content_audit, llm_audit, works, articles)
    manifest.update(
        {
            "content_audit": "data/audit/content_enrichment.json",
            "llm_audit": "data/audit/llm_runs.jsonl",
            "object_audit": "data/audit/object_audit.jsonl",
        }
    )
    write_json(output_dir / "output_manifest.json", manifest)
    issue["outputs"] = manifest

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
