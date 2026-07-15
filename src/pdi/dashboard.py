from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import project_root
from .markup import safe_scientific_html, strip_scientific_markup
from .utils import read_json


def _secret(name: str, default: Any = None) -> Any:
    try:
        import streamlit as st

        return st.secrets.get(name, default)
    except Exception:
        return os.getenv(name, default)


def _repo_parts() -> tuple[str, str, str] | None:
    spec = _secret("PDI_GITHUB_REPO", "") or _secret("GITHUB_REPO", "")
    branch = _secret("PDI_DATA_BRANCH", "intelligence-data")
    if not spec or "/" not in str(spec):
        return None
    owner, repo = str(spec).split("/", 1)
    return owner, repo, str(branch)


def _content_url(path: str, *, api: bool) -> str | None:
    parts = _repo_parts()
    if not parts:
        return None
    owner, repo, branch = parts
    if api:
        return f"https://api.github.com/repos/{owner}/{repo}/contents/{path.lstrip('/')}?ref={branch}"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path.lstrip('/')}"


def fetch_json(path: str, fallback: Path | None = None, timeout: int = 12) -> Any:
    token = _secret("GITHUB_DATA_TOKEN", "") or _secret("GITHUB_TOKEN", "")
    url = _content_url(path, api=bool(token))
    if url:
        headers = {"Accept": "application/vnd.github.raw+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.json()
        except Exception:
            pass
    if fallback and fallback.exists():
        return read_json(fallback, {})
    return {}



def fetch_text(path: str, fallback: Path | None = None, timeout: int = 12) -> str:
    """Read a generated text/JSONL asset from intelligence-data with local fallback."""
    token = _secret("GITHUB_DATA_TOKEN", "") or _secret("GITHUB_TOKEN", "")
    url = _content_url(path, api=bool(token))
    if url:
        headers = {"Accept": "application/vnd.github.raw+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        try:
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            return response.text
        except Exception:
            pass
    if fallback and fallback.exists():
        return fallback.read_text(encoding="utf-8")
    return ""

def latest_issue() -> dict[str, Any]:
    root = project_root()
    return fetch_json("data/latest.json", root / "data/demo/latest.json") or {}


def history_index() -> list[dict[str, Any]]:
    root = project_root()
    return fetch_json("data/history_index.json", root / "data/demo/history_index.json") or []


def entity_jsonl(name: str) -> list[dict[str, Any]]:
    token = _secret("GITHUB_DATA_TOKEN", "") or _secret("GITHUB_TOKEN", "")
    url = _content_url(f"data/entities/{name}", api=bool(token))
    if url:
        headers = {"Accept": "application/vnd.github.raw+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            return [json.loads(line) for line in response.text.splitlines() if line.strip()]
        except Exception:
            pass
    fallback = project_root() / "data/demo" / name
    if fallback.exists():
        return [json.loads(line) for line in fallback.read_text(encoding="utf-8").splitlines() if line.strip()]
    return []


def all_works() -> list[dict[str, Any]]:
    return entity_jsonl("scholarly_works.jsonl")


def all_events() -> list[dict[str, Any]]:
    return entity_jsonl("public_health_events.jsonl")


def all_articles() -> list[dict[str, Any]]:
    return entity_jsonl("news_articles.jsonl")


def works_dataframe() -> pd.DataFrame:
    rows = []
    for work in all_works():
        rows.append(
            {
                "work_id": work.get("work_id"),
                "中文标题": strip_scientific_markup(work.get("title", {}).get("translated_zh"))
                or "中文翻译暂不可用",
                "英文标题": strip_scientific_markup(work.get("title", {}).get("original")),
                "期刊": work.get("bibliography", {}).get("journal"),
                "当前可报道日期": work.get("bibliography", {}).get("availability_date") or work.get("bibliography", {}).get("published_date"),
                "日期依据": work.get("bibliography", {}).get("availability_basis"),
                "期刊卷期日期": work.get("bibliography", {}).get("issue_date") or work.get("bibliography", {}).get("print_date"),
                "版面决策": work.get("filter_result", {}).get("decision"),
                "评分": work.get("filter_result", {}).get("score"),
                "来源数": work.get("quality", {}).get("source_count"),
                "DOI": work.get("identifiers", {}).get("doi"),
                "PMID": work.get("identifiers", {}).get("pmid"),
                "主题": ", ".join(work.get("entities", {}).get("topics") or []),
                "翻译状态": (work.get("translation_audit") or {}).get("validation_status"),
            }
        )
    return pd.DataFrame(rows)


def events_dataframe() -> pd.DataFrame:
    rows = []
    for event in all_events():
        rows.append(
            {
                "event_id": event.get("event_id"),
                "中文标题": strip_scientific_markup(event.get("summary_zh")) or "中文翻译暂不可用",
                "英文标题": strip_scientific_markup(event.get("summary_original") or event.get("summary")),
                "事件类型": event.get("event_type"),
                "国家/地区": event.get("location", {}).get("country"),
                "官方状态": event.get("official_status"),
                "确诊": event.get("case_counts", {}).get("confirmed"),
                "可能": event.get("case_counts", {}).get("probable"),
                "疑似": event.get("case_counts", {}).get("suspected"),
                "死亡": event.get("case_counts", {}).get("deaths"),
                "事件版本": event.get("event_version"),
                "实质更新": event.get("material_change"),
                "版面决策": event.get("display_decision"),
                "来源数": len(event.get("source_articles", [])),
                "原始来源": event.get("primary_source", {}).get("url"),
                "翻译状态": (event.get("translation_audit") or {}).get("validation_status"),
            }
        )
    return pd.DataFrame(rows)


def _streamlit_bilingual_values(item: dict[str, Any], kind: str) -> dict[str, Any]:
    if kind == "work":
        return {
            "id": item.get("work_id"),
            "zh_title": item.get("title", {}).get("translated_zh"),
            "en_title": item.get("title", {}).get("original"),
            "zh_summary": (item.get("display_summary") or {}).get("zh")
            or item.get("abstract", {}).get("translated_zh"),
            "en_summary": (item.get("display_summary") or {}).get("en")
            or item.get("abstract", {}).get("original"),
            "meta": " · ".join(
                str(value)
                for value in [
                    item.get("bibliography", {}).get("journal"),
                    item.get("bibliography", {}).get("availability_date") or item.get("bibliography", {}).get("published_date"),
                    ", ".join(item.get("authors", [])[:4]),
                ]
                if value
            ),
            "url": (
                f"https://doi.org/{item.get('identifiers', {}).get('doi')}"
                if item.get("identifiers", {}).get("doi")
                else next((row.get("url") for row in item.get("source_records", []) if row.get("url")), None)
            ),
        }
    if kind == "article":
        return {
            "id": item.get("article_id"),
            "zh_title": item.get("title", {}).get("translated_zh"),
            "en_title": item.get("title", {}).get("original"),
            "zh_summary": (item.get("display_summary") or {}).get("zh")
            or item.get("content", {}).get("translated_excerpt_zh"),
            "en_summary": (item.get("display_summary") or {}).get("en")
            or item.get("content", {}).get("excerpt"),
            "meta": " · ".join(
                str(value)
                for value in [
                    item.get("source", {}).get("name"),
                    item.get("published_at"),
                    item.get("source", {}).get("reliability_tier"),
                    item.get("classification", {}).get("decision"),
                ]
                if value
            ),
            "url": item.get("canonical_url"),
        }
    return {
        "id": item.get("event_id"),
        "zh_title": item.get("summary_zh"),
        "en_title": item.get("summary_original") or item.get("summary"),
        "zh_summary": (item.get("display_summary") or {}).get("zh"),
        "en_summary": (item.get("display_summary") or {}).get("en"),
        "meta": " · ".join(
            str(value)
            for value in [
                item.get("location", {}).get("country"),
                item.get("event_type"),
                item.get("official_status"),
                f"v{item.get('event_version', 1)}",
            ]
            if value
        ),
        "url": item.get("primary_source", {}).get("url"),
    }



def _audit_chain(audit: dict[str, Any]) -> str:
    labels: list[str] = []
    for attempt in audit.get("attempt_chain") or []:
        provider = str(attempt.get("provider") or "unknown")
        status = str(attempt.get("validation_status") or attempt.get("status") or "unknown")
        label = f"{provider}:{status}"
        if label not in labels:
            labels.append(label)
    return " → ".join(labels)


def _render_analysis_details(item: dict[str, Any], kind: str) -> None:
    import streamlit as st

    analysis = item.get("ai_analysis") or {}
    label = "深度解读与证据" if analysis else "深度解读未通过校验"
    with st.expander(label, expanded=False):
        if not analysis:
            st.info("该条目没有通过证据 ID、数字和实体校验的模型解读；页面仅保留来源内容和翻译。")
        elif kind == "work":
            study = analysis.get("study") or {}
            c1, c2 = st.columns(2)
            c1.markdown(f"**研究类型：** {study.get('study_type') or '未明确'}")
            coverage = analysis.get("evidence_coverage") or {}
            c2.markdown(f"**证据范围：** {coverage.get('level') or '未明确'}")
            for title, value in [("研究问题", study.get("research_question")), ("研究设计", study.get("design")), ("样本或数据", study.get("sample_or_dataset"))]:
                if isinstance(value, dict) and value.get("text"):
                    st.markdown(f"**{title}：** {value['text']}  `{', '.join(value.get('evidence_ids') or [])}`")
            if study.get("methods"):
                st.markdown("**方法：**")
                for row in study["methods"]:
                    st.markdown(f"- {row.get('method')}  `{', '.join(row.get('evidence_ids') or [])}`")
            if analysis.get("key_findings"):
                st.markdown("**关键发现：**")
                for row in analysis["key_findings"]:
                    st.markdown(f"- {row.get('finding')}  `{', '.join(row.get('evidence_ids') or [])}`")
            if analysis.get("quantitative_results"):
                st.markdown("**定量结果：**")
                for row in analysis["quantitative_results"]:
                    text = " ".join(str(x) for x in [row.get('value'), row.get('unit'), row.get('context')] if x)
                    st.markdown(f"- {text}  `{', '.join(row.get('evidence_ids') or [])}`")
            significance = analysis.get("significance") or {}
            if significance.get("statement"):
                st.markdown(f"**意义：** {significance['statement']}  `{', '.join(significance.get('evidence_ids') or [])}`")
            limitations = analysis.get("limitations") or {}
            if limitations.get("author_reported") or limitations.get("evidence_gaps"):
                st.markdown("**局限与证据缺口：**")
                for row in limitations.get("author_reported") or []:
                    st.markdown(f"- 作者报告：{row.get('limitation')}  `{', '.join(row.get('evidence_ids') or [])}`")
                for row in limitations.get("evidence_gaps") or []:
                    st.markdown(f"- 证据缺口：{row if isinstance(row, str) else row.get('text')}")
            strength = analysis.get("evidence_strength") or {}
            if strength:
                st.markdown(f"**证据强度：** {strength.get('level') or 'unclear'}；{strength.get('basis') or ''}")
        else:
            for title, key, child in [("官方行动", "official_actions", "official_action"), ("实验室发现", "laboratory_findings", "laboratory_finding"), ("本次变化", "what_changed", "what_changed"), ("待核实说法", "claims_requiring_confirmation", "claim"), ("已确认说法", "confirmed_claims", "claim")]:
                rows = analysis.get(key) or []
                if rows:
                    st.markdown(f"**{title}：**")
                    for row in rows:
                        if isinstance(row, dict):
                            st.markdown(f"- {row.get(child) or row.get('text')}  `{', '.join(row.get('evidence_ids') or [])}`")
                        else:
                            st.markdown(f"- {row}")
            risk = analysis.get("risk_assessment") or {}
            if isinstance(risk, dict) and risk.get("statement"):
                st.markdown(f"**来源风险评估：** {risk['statement']}（归因：{risk.get('attributed_to') or '未明确'}）")
            quality = analysis.get("source_content_quality") or {}
            if quality:
                st.markdown(f"**正文覆盖：** {quality.get('level') or '未明确'}；{quality.get('note') or ''}")
            if analysis.get("uncertainties"):
                st.markdown("**不确定性：**")
                for row in analysis["uncertainties"]:
                    st.markdown(f"- {row if isinstance(row, str) else row.get('text')}")
        with st.expander("查看完整结构化模型输出", expanded=False):
            st.json(analysis, expanded=False)


def render_bilingual_card(item: dict[str, Any], kind: str, key_prefix: str = "card") -> None:
    import streamlit as st

    values = _streamlit_bilingual_values(item, kind)
    item_id = str(values["id"] or "unknown")
    with st.container(border=False):
        body_col, language_col = st.columns([20, 1], vertical_alignment="top")
        with language_col:
            language = st.segmented_control(
                "语言",
                options=["zh", "en"],
                default="zh",
                key=f"{key_prefix}_{kind}_{item_id}",
                label_visibility="collapsed",
                help="zh：中文；en：英文原题和原始摘要/摘录。",
            ) or "zh"
        show_english = language == "en"
        if show_english:
            title = values["en_title"] or "English title unavailable"
            summary = values["en_summary"] or "Original abstract or excerpt is unavailable."
        else:
            title = values["zh_title"] or "中文标题暂不可用"
            summary = values["zh_summary"] or "中文摘要暂不可用；系统不会根据标题编造内容。"
        with body_col:
            st.markdown(
                f'<div class="pdi-card"><h3>{safe_scientific_html(title)}</h3>'
                f'<div class="small-muted">{safe_scientific_html(values["meta"])}</div>'
                f'<p>{safe_scientific_html(summary)}</p></div>',
                unsafe_allow_html=True,
            )
        _render_analysis_details(item, kind)
        if values.get("url"):
            st.link_button("查看原始来源", str(values["url"]))
        audit = item.get("translation_audit") or {}
        chain = _audit_chain(audit)
        st.caption(
            f"翻译：{audit.get('provider') or '不可用'} · {audit.get('validation_status') or 'unknown'}"
            + (f" · {chain}" if chain else "")
        )
        if kind == "article":
            fetch = item.get("retrieval_audit", {}).get("content_fetch") or {}
            coverage = (item.get("content") or {}).get("coverage_level")
            if coverage in {"title_only", "title_or_snippet_only", "unavailable"}:
                st.warning("未抓获可分析正文；本条仅基于标题、RSS 摘要或来源元数据。")
            elif coverage == "focused_partial":
                st.info("仅抓获部分与目标病原直接相关的正文，解读范围受限。")
            else:
                st.success("已抓取并聚焦提取与目标病原直接相关的正文证据。")
            st.caption(
                f"正文抓取：{fetch.get('status') or '未执行'} · {fetch.get('method') or '无'} · "
                f"覆盖 {coverage or 'unknown'} · 证据句 {fetch.get('sentence_count') or len(item.get('content', {}).get('sentences') or [])}"
            )
        elif kind == "work":
            full = item.get("full_text") or {}
            abstract = item.get("abstract") or {}
            acquisition = item.get("evidence_acquisition") or {}
            level = acquisition.get("evidence_level") or full.get("evidence_level") or ("E1" if abstract.get("original") else "E0")
            if not abstract.get("original") and not full.get("available"):
                st.warning("证据等级 E0：未抓获摘要或可分析正文；文献仍保留并进入补全重试队列，当前不得生成研究发现。")
            elif full.get("available"):
                st.success(f"证据等级 {level}：已获得摘要及 {full.get('source') or '开放全文'} 正文证据。")
            else:
                st.info("证据等级 E1：已获得摘要；已尝试开放 XML/HTML/PDF 兜底，暂未获得可分析全文。")
            st.caption(
                f"内容补全：{acquisition.get('status') or 'unknown'} · 尝试轮次 {acquisition.get('attempt_count') or 0} · "
                f"解析 {full.get('extraction_method') or '无'} · 临时 PDF 不持久化"
            )
            bib = item.get("bibliography") or {}
            st.caption(
                f"当前可报道日期：{bib.get('availability_date') or bib.get('published_date') or '未知'}"
                f"（{bib.get('availability_basis') or 'unknown'}）；期刊卷期日期：{bib.get('issue_date') or bib.get('print_date') or '未提供'}"
            )


def newspaper_css() -> str:
    return """
    <style>
    :root { --paper:#f5f0e6; --ink:#171412; --red:#7d1f1b; --line:#b8aa96; }
    [data-testid="stAppViewContainer"] { background:var(--paper); color:var(--ink); }
    [data-testid="stSidebar"] { background:#eee6d8; border-right:1px solid var(--line); }
    html, body, [class*="css"] { font-family:"Noto Serif SC","Source Han Serif SC","Songti SC",STSong,SimSun,serif; }
    h1,h2,h3 { color:var(--ink); letter-spacing:.04em; }
    h1 { border-top:4px double var(--ink); border-bottom:2px solid var(--ink); padding:.45rem 0; text-align:center; }
    h2 { border-bottom:1px solid var(--ink); padding-bottom:.25rem; }
    sub,sup { line-height:0; font-size:.72em; }
    [data-testid="stMetric"] { border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:.7rem; background:transparent; }
    [data-testid="stMetricValue"] { color:var(--red); }
    .pdi-note { border-left:4px solid var(--red); padding:.55rem .9rem; background:rgba(125,31,27,.05); }
    .pdi-kicker { color:var(--red); font-size:.82rem; font-weight:700; letter-spacing:.08em; }
    .pdi-card { border-bottom:1px solid var(--line); padding:.3rem 0 1rem; margin-bottom:.5rem; }
    .pdi-card h3 { line-height:1.4; }
    .small-muted { color:#6a6259;font-size:.85rem; }
    a { color:var(--red)!important; }
    </style>
    """


def setup_page(title: str, icon: str = "📰") -> None:
    import streamlit as st

    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    st.markdown(newspaper_css(), unsafe_allow_html=True)
