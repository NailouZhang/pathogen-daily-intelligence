from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Any

from .analysis import analyze_news, analyze_paper
from .bootstrap import build_profile
from .config import Settings, load_profile
from .content import enrich_scholarly_work, resolve_and_extract_news
from .dates import date_window
from .dedup import attach_news_to_papers, dedup_news, dedup_papers, llm_review_ambiguous_duplicates
from .http import HttpClient
from .llm import LLMRouter
from .news import filter_news_window, search_bing_news, search_gdelt, search_google_news, search_reliefweb, search_who
from .query_plan import build_query_plan
from .relevance import filter_relevant_news, filter_relevant_papers
from .render import render_site
from .scholarly import (
    filter_window,
    search_crossref,
    search_europe_pmc,
    search_openalex,
    search_pubmed,
    search_semantic_scholar,
    search_biorxiv_medrxiv,
)
from .storage import load_state, save_state, write_issue
from .translation import translate_record
from .utils import append_jsonl, clean_space, dump_json, sha256_text, utc_now_iso


def _demo_records() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    papers = [
        {
            "source": "Demo PubMed",
            "source_ids": {"pmid": "00000001"},
            "doi": "10.0000/demo.research",
            "title": "Serologic evidence of hantavirus exposure in forest workers",
            "abstract": "A cross-sectional study tested 371 forest workers for hantavirus antibodies. Hantavirus IgG was detected in 7.3% of participants. Exposure was associated with frequent rodent contact. The study was limited by its single-region design and lack of longitudinal follow-up.",
            "authors": ["Demo Author"],
            "journal": "Demo Virology",
            "year": 2026,
            "volume": "1",
            "issue": "1",
            "pages": "1-8",
            "online_date": date.today().isoformat(),
            "availability_date": date.today().isoformat(),
            "availability_date_basis": "online_date",
            "publication_types": ["Journal Article"],
            "url": "https://example.org/research",
        },
        {
            "source": "Demo Europe PMC",
            "source_ids": {"pmid": "00000002"},
            "doi": "10.0000/demo.review",
            "title": "Hantavirus infections in a changing world: a narrative review",
            "abstract": "This review discusses hantavirus epidemiology, reservoir ecology, pathogenesis, diagnosis, and prevention. Evidence indicates that environmental change and human contact with rodent reservoirs alter spillover risk. Gaps include limited prospective surveillance and few licensed countermeasures. Future work should integrate One Health surveillance and standardized clinical studies.",
            "authors": ["Review Author"],
            "journal": "Demo Reviews",
            "year": 2026,
            "online_date": date.today().isoformat(),
            "availability_date": date.today().isoformat(),
            "availability_date_basis": "online_date",
            "publication_types": ["Review"],
            "url": "https://example.org/review",
        },
    ]
    news = [
        {
            "source": "Demo News",
            "title": "Health authority reports a suspected hantavirus case",
            "url": "https://example.org/news",
            "published_date": date.today().isoformat(),
            "excerpt": "On Tuesday, the regional health authority reported one suspected hantavirus case in County A. The patient is stable and confirmatory testing is under way. Officials advised residents to avoid contact with rodent droppings. No additional cases have been reported.",
            "publisher": "Demo Public Health News",
            "language": "en",
        }
    ]
    return papers, news


def _parallel_map(items: list[dict[str, Any]], fn: Any, workers: int) -> list[dict[str, Any]]:
    if not items:
        return []
    output: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(fn, item) for item in items]
        for future in as_completed(futures):
            try:
                output.append(future.result())
            except Exception:
                continue
    return output


def run_pipeline(settings: Settings, *, demo: bool = False) -> dict[str, Any]:
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    http = HttpClient(settings.user_agent)
    secrets = settings.secrets
    llm = LLMRouter(http, gemini_key=secrets.get("GEMINI_API_KEY", ""), groq_key=secrets.get("GROQ_API_KEY", ""))
    state = load_state(settings.state_dir)
    profile = load_profile(settings)
    if profile is None or (not demo and profile.get("generated_by") == "bundled_seed"):
        profile = build_profile(settings, http, llm)
    plan = build_query_plan(profile, max_groups=7)
    start, end = date_window(settings.window_days, timezone_name=settings.timezone)
    scholarly_queries = [row["scholarly_query"] for row in plan]
    news_queries = [row["news_query"] for row in plan]

    if demo:
        raw_papers, raw_news = _demo_records()
    else:
        raw_papers: list[dict[str, Any]] = []
        source_calls = [
            lambda: search_pubmed(http, scholarly_queries, start, end, secrets.get("NCBI_API_KEY", "")),
            lambda: search_europe_pmc(http, scholarly_queries, start, end),
            lambda: search_crossref(http, scholarly_queries, start, end, secrets.get("CROSSREF_MAILTO", "")),
            lambda: search_semantic_scholar(http, scholarly_queries, start, end, secrets.get("SEMANTIC_SCHOLAR_API_KEY", "")),
            lambda: search_openalex(http, scholarly_queries, start, end, secrets.get("CROSSREF_MAILTO", "")),
            lambda: search_biorxiv_medrxiv(http, start, end),
        ]
        with ThreadPoolExecutor(max_workers=len(source_calls)) as executor:
            for future in as_completed([executor.submit(call) for call in source_calls]):
                try:
                    raw_papers.extend(future.result())
                except Exception:
                    continue
        raw_news: list[dict[str, Any]] = []
        news_calls = [
            lambda: search_google_news(http, news_queries, start, end),
            lambda: search_bing_news(http, news_queries, start, end),
            lambda: search_gdelt(http, news_queries, start, end),
            lambda: search_reliefweb(http, news_queries[:4], start, end),
            lambda: search_who(http, (profile.get("english_terms") or [])[:8], start, end),
        ]
        with ThreadPoolExecutor(max_workers=len(news_calls)) as executor:
            for future in as_completed([executor.submit(call) for call in news_calls]):
                try:
                    raw_news.extend(future.result())
                except Exception:
                    continue

    raw_papers = filter_window(raw_papers, start, end)
    raw_papers = filter_relevant_papers(raw_papers, profile)
    papers = dedup_papers(raw_papers)
    dedup_prompt = (settings.project_root / "prompts" / "ambiguous_dedup.md").read_text(encoding="utf-8")
    papers = llm_review_ambiguous_duplicates(papers, llm, dedup_prompt)
    papers.sort(key=lambda x: (x.get("availability_date") or "", x.get("relevance_score") or 0), reverse=True)
    papers = papers[: settings.max_papers]

    raw_news = filter_news_window(raw_news, start, end)
    raw_news = filter_relevant_news(raw_news, profile)
    news = dedup_news(raw_news)
    news = llm_review_ambiguous_duplicates(news, llm, dedup_prompt)
    news.sort(key=lambda x: (x.get("published_date") or "", x.get("relevance_score") or 0), reverse=True)
    news = news[: settings.max_news]
    news, papers = attach_news_to_papers(news, papers)

    if demo:
        for paper in papers:
            paper["paper_id"] = paper.get("paper_id") or "paper-" + sha256_text(paper.get("title", ""))[:16]
            paper["evidence_level"] = "E1" if paper.get("abstract") else "E0"
        for article in news:
            article["news_id"] = article.get("news_id") or "news-" + sha256_text(article.get("title", ""))[:16]
            article["content"] = article.get("excerpt")
            article["content_status"] = "partial"
            article["resolved_url"] = article.get("url")
    else:
        enrich_targets = papers[: settings.max_fulltexts]
        enriched = _parallel_map(
            enrich_targets,
            lambda item: enrich_scholarly_work(http, item, secrets.get("CROSSREF_MAILTO", "")),
            workers=5,
        )
        enriched_by_id = {item.get("paper_id"): item for item in enriched}
        updated_papers: list[dict[str, Any]] = []
        for item in papers:
            if item.get("paper_id") in enriched_by_id:
                updated_papers.append(enriched_by_id[item.get("paper_id")])
            else:
                item["evidence_level"] = "E1" if item.get("abstract") else "E0"
                item["full_text_method"] = "not_attempted_budget"
                updated_papers.append(item)
        papers = updated_papers
        news = _parallel_map(
            news[: settings.max_news_fetches],
            lambda item: resolve_and_extract_news(http, item),
            workers=8,
        )
        news.sort(key=lambda x: (x.get("published_date") or "", x.get("relevance_score") or 0), reverse=True)

    prompts_dir = settings.project_root / "prompts"
    for paper in papers:
        analyze_paper(paper, llm, prompts_dir)
    for article in news:
        analyze_news(article, llm, prompts_dir)

    translation_cache = state.setdefault("translation_cache", {})
    if demo:
        demo_titles = {
            "Serologic evidence of hantavirus exposure in forest workers": "林业工作者汉坦病毒暴露的血清学证据",
            "Hantavirus infections in a changing world: a narrative review": "变化世界中的汉坦病毒感染：叙述性综述",
            "Health authority reports a suspected hantavirus case": "卫生部门报告一例疑似汉坦病毒病例",
        }
        demo_analysis = {
            "Serologic evidence of hantavirus exposure in forest workers": {
                "background": "林业工作者经常接触啮齿动物，可能存在未识别的汉坦病毒暴露风险。",
                "methods": "采用横断面设计，对371名林业工作者检测汉坦病毒抗体并评估啮齿动物接触史。",
                "results": "7.3%的参与者检出汉坦病毒IgG；频繁接触啮齿动物与血清阳性相关。",
                "contribution": "结果提示职业人群应加强啮齿动物暴露监测和针对性防护。",
                "limitations": "研究仅覆盖单一区域，且缺少纵向随访，不能直接推断感染时间和因果关系。",
            },
            "Hantavirus infections in a changing world: a narrative review": {
                "background": "环境变化与人兽接触增加正在改变汉坦病毒的传播和溢出风险。",
                "main_directions": "综述流行病学、储存宿主生态、致病机制、诊断和预防等主要方向。",
                "current_state": "现有证据支持环境与宿主生态变化会影响人群暴露，但不同地区的监测能力不均。",
                "gaps": "前瞻性监测、标准化临床研究和获批干预措施仍然不足。",
                "future_research": "应推进一体化健康监测、长期宿主生态研究和可比的临床队列研究。",
            },
            "Health authority reports a suspected hantavirus case": {
                "time": "本周二报告。",
                "location": "A县。",
                "event": "地区卫生部门报告1例疑似汉坦病毒病例，正在进行确证检测。",
                "impact": "患者病情稳定；部门同时提醒居民避免接触啮齿动物排泄物。",
                "status": "截至报道时未发现新增病例，事件仍处于调查和实验室确认阶段。",
            },
        }
        for item in papers + news:
            item["title_zh"] = demo_titles.get(item.get("title"), item.get("title"))
            analysis = (item.get("analysis") or {}).get("analysis") or {}
            if item in papers and item.get("paper_type") == "review":
                labels = [("背景", "background"), ("主要方向", "main_directions"), ("研究现状", "current_state"), ("不足", "gaps"), ("后续研究", "future_research")]
            elif item in papers:
                labels = [("背景", "background"), ("方法", "methods"), ("结果", "results"), ("贡献", "contribution"), ("局限", "limitations")]
            else:
                labels = [("时间", "time"), ("地点", "location"), ("事件", "event"), ("影响", "impact"), ("状态", "status")]
            zh_source = demo_analysis.get(item.get("title"), {})
            item["analysis_zh"] = {key: clean_space(zh_source.get(key)) or clean_space(analysis.get(key)) or "未报告" for _, key in labels}
            item["summary_zh"] = " ".join(f"{label}：{item['analysis_zh'][key]}" for label, key in labels)[:300]
            item["translation_audit"] = {"title": {"status": "demo", "provider": "deterministic_demo"}, "fields": {}}
    else:
        for paper in papers:
            translate_record(
                paper,
                profile=profile,
                llm=llm,
                prompts_dir=prompts_dir,
                cache=translation_cache,
                kind=paper.get("paper_type") or "research",
            )
        for article in news:
            translate_record(article, profile=profile, llm=llm, prompts_dir=prompts_dir, cache=translation_cache, kind="news")

    translated = sum(1 for item in papers + news if item.get("title_zh"))
    issue_date = end.isoformat()
    issue = {
        "schema_version": "2.0",
        "issue_id": f"{settings.profile_id}-{issue_date}",
        "profile_id": settings.profile_id,
        "issue_date": issue_date,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "generated_at": utc_now_iso(),
        "title_zh": f"{profile.get('display_name_zh') or settings.profile_id}每日情报",
        "title_en": f"{profile.get('display_name_en') or settings.profile_id} Daily Intelligence",
        "profile": profile,
        "query_plan": plan,
        "papers": papers,
        "news": news,
        "metrics": {
            "raw_papers": len(raw_papers),
            "papers": len(papers),
            "research": sum(1 for p in papers if p.get("paper_type") == "research"),
            "reviews": sum(1 for p in papers if p.get("paper_type") == "review"),
            "raw_news": len(raw_news),
            "news": len(news),
            "translated": translated,
        },
    }
    write_issue(settings.output_dir, issue)
    render_site(issue, settings.output_dir)
    audit_dir = settings.output_dir / "data" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    dump_json(audit_dir / "query_plan.json", plan)
    dump_json(audit_dir / "profile.json", profile)
    for paper in papers:
        append_jsonl(audit_dir / "papers.jsonl", {
            "paper_id": paper.get("paper_id"),
            "doi": paper.get("doi"),
            "title": paper.get("title"),
            "analysis": paper.get("analysis"),
            "translation_audit": paper.get("translation_audit"),
            "content_audit": paper.get("content_audit"),
        })
    for article in news:
        append_jsonl(audit_dir / "news.jsonl", {
            "news_id": article.get("news_id"),
            "title": article.get("title"),
            "analysis": article.get("analysis"),
            "translation_audit": article.get("translation_audit"),
            "content_audit": article.get("content_audit"),
        })
    save_state(settings.state_dir, state)
    return issue
