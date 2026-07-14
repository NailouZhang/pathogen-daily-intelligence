from __future__ import annotations

from typing import Any

from .utils import sentence_split, utc_now_iso


def demo_source_results(issue_date: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    retrieved = utc_now_iso()
    paper_analysis = {
        "one_sentence_takeaway": {
            "text": "该综述讨论正汉坦病毒糖蛋白刺突参与细胞进入的作用及其作为免疫干预靶点的潜力。",
            "evidence_ids": ["A1"],
        },
        "study": {
            "research_question": {"text": "正汉坦病毒刺突蛋白如何参与感染，并能否作为抗病毒免疫干预靶点？", "evidence_ids": ["A1"]},
            "study_type": "narrative review",
            "design": {"text": "对分子机制和潜在干预靶点进行综述性整合。", "evidence_ids": ["A1"]},
            "sample_or_dataset": {"text": None, "evidence_ids": []},
            "methods": [],
        },
        "key_findings": [
            {"finding": "G<sub>N</sub>/G<sub>C</sub> 刺突蛋白参与细胞进入，并被讨论为潜在抗病毒免疫干预靶点。", "evidence_ids": ["A1"], "quantitative": False, "interpretation_boundary": "演示性综述，不是新的实验研究。"}
        ],
        "quantitative_results": [],
        "significance": {"statement": "为理解病毒进入和候选免疫干预靶点提供综述性线索。", "evidence_ids": ["A1"], "scope": "机制综述"},
        "limitations": {"author_reported": [], "evidence_gaps": ["演示摘要未提供系统检索方法、纳入标准或定量证据。"]},
        "evidence_strength": {"level": "low", "basis": "当前 Demo 仅提供摘要级综述证据。", "evidence_ids": ["A1", "A2"]},
        "evidence_coverage": {"level": "abstract", "sections_used": ["abstract"], "note": "Demo 仅提供摘要证据。"},
        "uncertainties": [],
    }
    official_analysis = {
        "event_type": "human_case_report",
        "official_status": "laboratory_confirmed",
        "case_counts": {
            "confirmed": {"value": "2", "evidence_ids": ["N1"]},
            "probable": {"value": None, "evidence_ids": []},
            "suspected": {"value": None, "evidence_ids": []},
            "deaths": {"value": None, "evidence_ids": []},
            "as_of": {"value": None, "evidence_ids": []},
        },
        "locations": [{"name": "Chile", "level": "country", "evidence_ids": ["N1"]}],
        "official_actions": [{"official_action": "开展流行病学调查", "evidence_ids": ["N1"]}],
        "laboratory_findings": [{"laboratory_finding": "2 例病例经实验室确认", "evidence_ids": ["N1"]}],
        "what_changed": [{"what_changed": "本次通报新增 2 例实验室确认病例及调查行动。", "evidence_ids": ["N1"]}],
        "risk_assessment": {"statement": None, "attributed_to": None, "evidence_ids": []},
        "source_content_quality": {"level": "partial", "note": "Demo 只提供一条官方通报证据句。"},
        "uncertainties": [],
    }
    paper_title = "The Orthohantavirus G<sub>N</sub>/G<sub>C</sub> Spikes: Molecular Determinants of Infection and Targets for Antiviral Immune Interventions."
    paper_title_zh = "正汉坦病毒 G<sub>N</sub>/G<sub>C</sub> 刺突蛋白：感染的分子决定因素及抗病毒免疫干预靶点"
    paper_abstract = (
        "This demonstration review summarizes how the Orthohantavirus G<sub>N</sub>/G<sub>C</sub> spikes participate in cell entry and discusses their potential as targets for antiviral immune interventions. "
        "It does not report a new outbreak or provide individual medical advice."
    )
    paper_abstract_zh = (
        "该演示性综述概述了正汉坦病毒 G<sub>N</sub>/G<sub>C</sub> 刺突蛋白参与细胞进入的机制，并讨论其作为抗病毒免疫干预靶点的潜力。"
        "该文不报告新的暴发，也不提供个体医疗建议。"
    )
    scholarly = [
        {
            "record_type": "scholarly_source",
            "source_id": "pubmed",
            "source_record_id": "99900001",
            "query_group": "genomics",
            "query": "hantavirus spike antiviral",
            "identifiers": {"pmid": "99900001", "doi": "10.0000/demo.hanta.2026"},
            "title": paper_title,
            "translated_title_zh": paper_title_zh,
            "abstract": paper_abstract,
            "translated_abstract_zh": paper_abstract_zh,
            "display_summary_zh": "综述正汉坦病毒 G<sub>N</sub>/G<sub>C</sub> 刺突蛋白参与细胞进入的分子机制及其作为抗病毒免疫干预靶点的潜力。",
            "display_summary_en": "A review of Orthohantavirus G<sub>N</sub>/G<sub>C</sub> spike biology and their potential as antiviral immune-intervention targets.",
            "demo_ai_analysis": paper_analysis,
            "abstract_sentences": sentence_split(paper_abstract, "A"),
            "authors": ["Demo Zhang", "Example Li"],
            "journal": "Demonstration Journal of Viral Surveillance",
            "published_date": issue_date,
            "published_date_precision": "day",
            "language": "en",
            "url": "https://pubmed.ncbi.nlm.nih.gov/99900001/",
            "retrieved_at": retrieved,
        },
        {
            "record_type": "scholarly_source",
            "source_id": "europe_pmc",
            "source_record_id": "MED/99900001",
            "query_group": "genomics",
            "query": "hantavirus spike antiviral",
            "identifiers": {
                "pmid": "99900001",
                "doi": "10.0000/demo.hanta.2026",
                "europe_pmc_id": "MED/99900001",
            },
            "title": paper_title,
            "translated_title_zh": paper_title_zh,
            "abstract": paper_abstract,
            "translated_abstract_zh": paper_abstract_zh,
            "display_summary_zh": "综述正汉坦病毒 G<sub>N</sub>/G<sub>C</sub> 刺突蛋白参与细胞进入的分子机制及其作为抗病毒免疫干预靶点的潜力。",
            "display_summary_en": "A review of Orthohantavirus G<sub>N</sub>/G<sub>C</sub> spike biology and their potential as antiviral immune-intervention targets.",
            "demo_ai_analysis": paper_analysis,
            "abstract_sentences": sentence_split(paper_abstract, "A"),
            "authors": ["Demo Zhang", "Example Li"],
            "journal": "Demonstration Journal of Viral Surveillance",
            "published_date": issue_date,
            "published_date_precision": "day",
            "language": "en",
            "url": "https://europepmc.org/article/MED/99900001",
            "retrieved_at": retrieved,
        },
        {
            "record_type": "scholarly_source",
            "source_id": "crossref",
            "source_record_id": "10.0000/demo.hfrs.2026",
            "query_group": "clinical",
            "query": "hemorrhagic fever with renal syndrome",
            "identifiers": {"doi": "10.0000/demo.hfrs.2026"},
            "title": "Serological surveillance of hemorrhagic fever with renal syndrome",
            "translated_title_zh": "肾综合征出血热的血清学监测",
            "abstract": None,
            "translated_abstract_zh": None,
            "display_summary_zh": None,
            "display_summary_en": None,
            "abstract_sentences": [],
            "authors": ["Example Wang"],
            "journal": "Demo Epidemiology Reports",
            "published_date": issue_date,
            "published_date_precision": "day",
            "language": "en",
            "url": "https://doi.org/10.0000/demo.hfrs.2026",
            "retrieved_at": retrieved,
        },
    ]
    news = [
        {
            "source_id": "demo_health_authority",
            "source_name": "Demonstration Health Authority",
            "source_category": "official",
            "source_tier": "A",
            "domain": "example.org",
            "title": "Laboratory confirms 2 hantavirus pulmonary syndrome cases in Chile",
            "translated_title_zh": "实验室确认智利 2 例汉坦病毒肺综合征病例",
            "url": "https://example.org/notices/hantavirus-cases",
            "canonical_url": "https://example.org/notices/hantavirus-cases",
            "published_at": issue_date,
            "excerpt": "The authority reported 2 laboratory-confirmed cases of hantavirus pulmonary syndrome in Chile and announced epidemiological investigation.",
            "translated_excerpt_zh": "该机构报告智利出现 2 例经实验室确认的汉坦病毒肺综合征病例，并宣布开展流行病学调查。",
            "display_summary_zh": "智利卫生机构报告 2 例实验室确认病例，并启动流行病学调查。",
            "display_summary_en": "The Chilean authority reported 2 laboratory-confirmed cases and initiated an epidemiological investigation.",
            "demo_ai_analysis": official_analysis,
            "content_sentences": sentence_split(
                "The authority reported 2 laboratory-confirmed cases of hantavirus pulmonary syndrome in Chile and announced epidemiological investigation.",
                "N",
            ),
            "language": "en",
            "query_group": "outbreak",
            "query": "hantavirus cases",
            "retrieved_at": retrieved,
            "original_source": "Demonstration Health Authority",
        },
        {
            "source_id": "google_news_en",
            "source_name": "Google News RSS English",
            "source_category": "discoverer",
            "source_tier": "D",
            "domain": "news.example.net",
            "title": "Two hantavirus pulmonary syndrome cases confirmed in Chile",
            "translated_title_zh": "智利确认 2 例汉坦病毒肺综合征病例",
            "url": "https://news.example.net/story?utm_source=rss&id=1",
            "canonical_url": "https://news.example.net/story?id=1",
            "published_at": issue_date,
            "excerpt": "Media coverage repeats the health authority report of 2 laboratory-confirmed cases in Chile.",
            "translated_excerpt_zh": "媒体报道转述了卫生机构关于智利 2 例实验室确认病例的通报。",
            "display_summary_zh": "媒体转述智利卫生机构关于 2 例实验室确认病例的通报。",
            "display_summary_en": "Media coverage repeats the health authority report of 2 laboratory-confirmed cases in Chile.",
            "content_sentences": sentence_split(
                "Media coverage repeats the health authority report of 2 laboratory-confirmed cases in Chile.",
                "N",
            ),
            "language": "en",
            "query_group": "outbreak",
            "query": "hantavirus cases",
            "retrieved_at": retrieved,
            "original_source": "Example News",
        },
        {
            "source_id": "demo_lab",
            "source_name": "Demonstration Public Health Laboratory",
            "source_category": "official",
            "source_tier": "A",
            "domain": "lab.example.org",
            "title": "中国开展汉坦病毒啮齿动物宿主监测",
            "translated_title_zh": "中国开展汉坦病毒啮齿动物宿主监测",
            "url": "https://lab.example.org/zh/rodent-surveillance",
            "canonical_url": "https://lab.example.org/zh/rodent-surveillance",
            "published_at": issue_date,
            "excerpt": "公共卫生实验室报告开展汉坦病毒啮齿动物宿主监测，未报告人类病例。",
            "translated_excerpt_zh": "公共卫生实验室报告开展汉坦病毒啮齿动物宿主监测，未报告人类病例。",
            "display_summary_zh": "公共卫生实验室开展汉坦病毒啮齿动物宿主监测，通报中未报告人类病例。",
            "display_summary_en": None,
            "content_sentences": sentence_split(
                "公共卫生实验室报告开展汉坦病毒啮齿动物宿主监测，未报告人类病例。", "N"
            ),
            "language": "zh",
            "query_group": "ecology",
            "query": "汉坦病毒 宿主监测",
            "retrieved_at": retrieved,
            "original_source": "示例公共卫生实验室",
        },
    ]
    health = [
        {"source_id": "pubmed", "status": "success_with_results", "record_count": 1, "query_count": 1, "errors": [], "audits": []},
        {"source_id": "europe_pmc", "status": "success_with_results", "record_count": 1, "query_count": 1, "errors": [], "audits": []},
        {"source_id": "crossref", "status": "success_with_results", "record_count": 1, "query_count": 1, "errors": [], "audits": []},
        {"source_id": "semantic_scholar", "status": "success_no_results", "record_count": 0, "query_count": 1, "errors": [], "audits": []},
        {"source_id": "demo_official_news", "status": "success_with_results", "record_count": 3, "query_count": 2, "errors": [], "audits": []},
    ]
    return scholarly, news, health
