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
                "发表日期": work.get("bibliography", {}).get("published_date"),
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
                    item.get("bibliography", {}).get("published_date"),
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


def render_bilingual_card(item: dict[str, Any], kind: str, key_prefix: str = "card") -> None:
    import streamlit as st

    values = _streamlit_bilingual_values(item, kind)
    item_id = str(values["id"] or "unknown")
    with st.container(border=False):
        show_english = st.toggle(
            "显示英文",
            value=False,
            key=f"{key_prefix}_{kind}_{item_id}",
            help="默认显示经过校验的中文翻译；打开后显示英文原题和原始摘要/摘录。",
        )
        if show_english:
            title = values["en_title"] or "English title unavailable"
            summary = values["en_summary"] or "Original abstract or excerpt is unavailable."
        else:
            title = values["zh_title"] or "中文标题暂不可用"
            summary = values["zh_summary"] or "中文摘要暂不可用；系统不会根据标题编造内容。"
        st.markdown(
            f'<div class="pdi-card"><h3>{safe_scientific_html(title)}</h3>'
            f'<div class="small-muted">{safe_scientific_html(values["meta"])}</div>'
            f'<p>{safe_scientific_html(summary)}</p></div>',
            unsafe_allow_html=True,
        )
        if values.get("url"):
            st.link_button("查看原始来源", str(values["url"]))
        audit = item.get("translation_audit") or {}
        st.caption(
            f"翻译：{audit.get('provider') or '不可用'} · {audit.get('validation_status') or 'unknown'}"
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
