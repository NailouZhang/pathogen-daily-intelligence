from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import project_root
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


def works_dataframe() -> pd.DataFrame:
    rows = []
    for work in entity_jsonl("scholarly_works.jsonl"):
        rows.append({
            "work_id": work.get("work_id"),
            "title": work.get("title", {}).get("translated_zh") or work.get("title", {}).get("original"),
            "original_title": work.get("title", {}).get("original"),
            "journal": work.get("bibliography", {}).get("journal"),
            "published_date": work.get("bibliography", {}).get("published_date"),
            "decision": work.get("filter_result", {}).get("decision"),
            "score": work.get("filter_result", {}).get("score"),
            "sources": work.get("quality", {}).get("source_count"),
            "doi": work.get("identifiers", {}).get("doi"),
            "pmid": work.get("identifiers", {}).get("pmid"),
            "topics": ", ".join(work.get("entities", {}).get("topics") or []),
        })
    return pd.DataFrame(rows)


def events_dataframe() -> pd.DataFrame:
    rows = []
    for event in entity_jsonl("public_health_events.jsonl"):
        rows.append({
            "event_id": event.get("event_id"),
            "summary": event.get("summary"),
            "event_type": event.get("event_type"),
            "country": event.get("location", {}).get("country"),
            "official_status": event.get("official_status"),
            "confirmed": event.get("case_counts", {}).get("confirmed"),
            "probable": event.get("case_counts", {}).get("probable"),
            "suspected": event.get("case_counts", {}).get("suspected"),
            "deaths": event.get("case_counts", {}).get("deaths"),
            "event_version": event.get("event_version"),
            "material_change": event.get("material_change"),
            "decision": event.get("display_decision"),
            "source_count": len(event.get("source_articles", [])),
            "source_url": event.get("primary_source", {}).get("url"),
        })
    return pd.DataFrame(rows)


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
    [data-testid="stMetric"] { border-top:1px solid var(--line); border-bottom:1px solid var(--line); padding:.7rem; background:transparent; }
    [data-testid="stMetricValue"] { color:var(--red); }
    .pdi-note { border-left:4px solid var(--red); padding:.55rem .9rem; background:rgba(125,31,27,.05); }
    .pdi-kicker { color:var(--red); font-size:.82rem; font-weight:700; letter-spacing:.08em; }
    .pdi-card { border-bottom:1px solid var(--line); padding:.6rem 0 1rem; }
    .small-muted { color:#6a6259;font-size:.85rem; }
    a { color:var(--red)!important; }
    </style>
    """


def setup_page(title: str, icon: str = "📰") -> None:
    import streamlit as st
    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    st.markdown(newspaper_css(), unsafe_allow_html=True)
