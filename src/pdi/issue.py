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
    content_audit: dict[str, Any] | None = None,
    articles: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    content_audit = content_audit or {}
    articles = articles or []
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
                "title": event.get("summary_zh") or "中文标题暂不可用",
                "title_original": event.get("summary_original") or event.get("summary"),
                "score": event.get("display_score", 0),
            }
        )
    for work in headline_works + brief_works:
        lead_candidates.append(
            {
                "item_type": "scholarly_work",
                "item_id": work["work_id"],
                "title": work.get("title", {}).get("translated_zh") or "中文标题暂不可用",
                "title_original": work.get("title", {}).get("original"),
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
    overview_value = synthesis.get("daily_overview")
    if isinstance(overview_value, dict):
        overview_text = overview_value.get("text")
        overview_support = overview_value.get("supporting_item_ids") or []
    else:
        overview_text = overview_value
        overview_support = []
    synthesis_support = [
        x
        for x in [*(synthesis.get("supporting_item_ids", []) or []), *overview_support]
        if x in supporting_ids
    ]

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
        "llm_validated_runs": sum(a.get("validation_status") == "passed" for a in llm_audit),
        "llm_failed_runs": sum(a.get("validation_status") == "failed" for a in llm_audit),
        "translated_works": sum(bool(w.get("title", {}).get("translated_zh")) for w in works),
        "translated_articles": sum(bool(a.get("title", {}).get("translated_zh")) for a in articles),
        "translated_events": sum(bool(e.get("summary_zh")) for e in events),
        "translation_fallback_successes": sum(
            bool((w.get("translation_audit") or {}).get("fallback_used")) and bool(w.get("title", {}).get("translated_zh"))
            for w in works
        ) + sum(
            bool((a.get("translation_audit") or {}).get("fallback_used")) and bool(a.get("title", {}).get("translated_zh"))
            for a in articles
        ),
        "translation_unavailable": sum(not bool(w.get("title", {}).get("translated_zh")) for w in works)
        + sum(not bool(a.get("title", {}).get("translated_zh")) for a in articles),
        "news_content_fetch_attempted": (content_audit.get("news") or {}).get("attempted", 0),
        "news_content_fetch_success": (content_audit.get("news") or {}).get("success", 0),
        "news_content_fetch_failed": (content_audit.get("news") or {}).get("failed", 0),
        "scholarly_recovery_attempted": (content_audit.get("scholarly") or {}).get("attempted", 0),
        "scholarly_abstract_recovered": (content_audit.get("scholarly") or {}).get("abstract_recovered", 0),
        "scholarly_fulltext_recovered": (content_audit.get("scholarly") or {}).get("fulltext_success", 0),
        "scholarly_metadata_only": (content_audit.get("scholarly") or {}).get("metadata_only", 0),
        "scholarly_abstract_only": (content_audit.get("scholarly") or {}).get("abstract_only", 0),
        "scholarly_partial_fulltext": (content_audit.get("scholarly") or {}).get("pdf_or_html_fulltext", 0),
        "scholarly_structured_fulltext": (content_audit.get("scholarly") or {}).get("structured_fulltext", 0),
        # Backward-compatible aliases retained for downstream dashboards.
        "open_fulltext_fetch_attempted": (content_audit.get("scholarly") or {}).get("attempted", 0),
        "open_fulltext_fetch_success": (content_audit.get("scholarly") or {}).get("success", 0),
        "validated_work_analyses": sum(bool(w.get("ai_analysis")) for w in works),
        "validated_article_analyses": sum(bool(a.get("ai_analysis")) for a in articles),
        "news_background_mentions_archived": sum(
            (a.get("classification") or {}).get("relevance") == "background" for a in articles
        ),
        "news_title_or_snippet_only": sum(
            (a.get("content") or {}).get("coverage_level") in {"title_only", "title_or_snippet_only"}
            for a in articles
        ),
        "scholarly_identifier_conflicts": sum(
            bool((w.get("quality") or {}).get("identifier_conflict")) for w in works
        ),
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
        data_quality_notes.append(
            "部分文献尚无可用摘要或全文：记录仍被保留并进入后续补全队列，本期仅展示书目信息，且不生成研究结论。"
        )
    if any(e.get("official_status") != "official" for e in events):
        data_quality_notes.append("部分事件尚未找到 A 级官方原始来源，应继续核验。")
    if statistics["source_failures"]:
        data_quality_notes.append("本期存在接口失败或部分完成，来源覆盖并不完整。")
    if any(a.get("provider") == "deterministic" for a in llm_audit):
        data_quality_notes.append("部分或全部 AI 任务已降级为无模型模式。")
    if statistics["news_content_fetch_failed"]:
        data_quality_notes.append(
            f"{statistics['news_content_fetch_failed']} 篇新闻未能抓取到可分析正文，相关条目仅使用 RSS、元描述或已有摘要。"
        )
    if statistics["scholarly_abstract_recovered"]:
        data_quality_notes.append(
            f"{statistics['scholarly_abstract_recovered']} 篇文献通过 PubMed、Europe PMC、Crossref、Semantic Scholar 或出版商元数据补回了摘要。"
        )
    if statistics["scholarly_fulltext_recovered"]:
        data_quality_notes.append(
            f"{statistics['scholarly_fulltext_recovered']} 篇文献通过 PMC XML/BioC、开放 HTML、文本挖掘链接或合法开放 PDF 补充了正文证据。"
        )
    if statistics["scholarly_metadata_only"]:
        data_quality_notes.append(
            f"{statistics['scholarly_metadata_only']} 篇文献当前仍为 E0 元数据级记录，已保留并安排后续补抓，未生成研究发现。"
        )
    if statistics["translation_fallback_successes"]:
        data_quality_notes.append(
            f"{statistics['translation_fallback_successes']} 条翻译由备用模型在首选模型未通过校验后完成。"
        )
    if statistics["news_background_mentions_archived"]:
        data_quality_notes.append(
            f"{statistics['news_background_mentions_archived']} 篇仅在背景中提及目标病原的文章已归档，不再生成公共卫生事件。"
        )
    if statistics["news_title_or_snippet_only"]:
        data_quality_notes.append(
            f"{statistics['news_title_or_snippet_only']} 篇新闻仅获得标题或 RSS 摘要，页面将明确标记为未获得正文。"
        )
    if statistics["scholarly_identifier_conflicts"]:
        data_quality_notes.append(
            f"{statistics['scholarly_identifier_conflicts']} 篇文献存在跨来源标识符或书目冲突，已进入复核而不是静默合并。"
        )
    missing_zh = [w for w in works if not w.get("title", {}).get("translated_zh")]
    missing_zh += [e for e in events if not e.get("summary_zh")]
    if missing_zh:
        data_quality_notes.append(f"{len(missing_zh)} 条内容尚无通过校验的中文翻译，页面将明确提示并保留英文原文按钮。")

    generated_at = utc_now_iso()
    issue = {
        "schema_version": "1.5",
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
                "text": overview_text,
                "supporting_item_ids": synthesis_support,
            }
        ]
        if overview_text
        else [],
        "source_health": source_health,
        "data_quality_notes": data_quality_notes,
        "watchlist": synthesis.get("watchlist", []) if synthesis else [],
        "generation_audit": {
            "schema_version": "1.5",
            "profile_version": profile.get("profile_version"),
            "rule_version": "1.5",
            "prompt_version": "1.5",
            "llm_runs": llm_audit,
            "dedup_counts": dedup_counts,
            "partial_failures": [h for h in source_health if h.get("status") in {"failed", "partial"}],
            "content_enrichment": {
                "news": {k: v for k, v in (content_audit.get("news") or {}).items() if k != "audits"},
                "scholarly": {k: v for k, v in (content_audit.get("scholarly") or {}).items() if k != "audits"},
            },
            "pipeline_stages": [
                "profile_and_query_plan",
                "multi_source_collection",
                "conservative_entity_deduplication",
                "current_availability_date_selection",
                "bounded_multistrategy_content_enrichment",
                "rule_entity_annotation",
                "precluster_relevance_filtering",
                "event_clustering",
                "editorial_filtering",
                "sequential_multimodel_translation_and_analysis",
                "evidence_validation",
                "daily_synthesis",
                "render_and_publish",
            ],
            "content_hash": stable_hash(issue_id + generated_at, 32),
        },
        "outputs": {},
    }
    return issue
