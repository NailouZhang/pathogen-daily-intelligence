from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from .config import project_root
from .utils import read_json, utc_now_iso

_MEMORY_CACHE: dict[str, tuple[float, str]] = {}
_MEMORY_TTL_SECONDS = 300


@dataclass
class FetchResult:
    payload: Any
    source: str
    message: str
    fetched_at: str
    remote_url: str | None = None
    cache_path: str | None = None

    @property
    def is_production(self) -> bool:
        return self.source == "github"


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
    owner, repo = str(spec).strip().split("/", 1)
    if not owner or not repo:
        return None
    return owner, repo, str(branch).strip() or "intelligence-data"


def repository_spec() -> dict[str, str | None]:
    parts = _repo_parts()
    if not parts:
        return {"owner": None, "repo": None, "branch": None, "full_name": None}
    owner, repo, branch = parts
    return {
        "owner": owner,
        "repo": repo,
        "branch": branch,
        "full_name": f"{owner}/{repo}",
    }


def _token() -> str:
    return str(_secret("GITHUB_DATA_TOKEN", "") or _secret("GITHUB_TOKEN", "") or "").strip()


def _headers(*, raw: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
        "User-Agent": "pathogen-daily-intelligence-streamlit/1.1",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _content_url(path: str, *, api: bool) -> str | None:
    parts = _repo_parts()
    if not parts:
        return None
    owner, repo, branch = parts
    clean_path = path.lstrip("/")
    if api:
        return f"https://api.github.com/repos/{owner}/{repo}/contents/{clean_path}?ref={branch}"
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{clean_path}"


def _cache_file(path: str) -> Path:
    digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
    suffix = Path(path).suffix or ".txt"
    return project_root() / "runtime" / "dashboard_cache" / f"{digest}{suffix}"


def _write_disk_cache(path: str, text: str) -> Path:
    target = _cache_file(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(target)
    return target


def _read_disk_cache(path: str) -> str | None:
    target = _cache_file(path)
    if not target.exists() or target.stat().st_size == 0:
        return None
    try:
        return target.read_text(encoding="utf-8")
    except Exception:
        return None


def _memory_key(url: str) -> str:
    token_digest = hashlib.sha256(_token().encode("utf-8")).hexdigest()[:12] if _token() else "anonymous"
    return f"{url}|{token_digest}"


def _remote_text(path: str, timeout: int = 15) -> tuple[str, str]:
    token = _token()
    url = _content_url(path, api=bool(token))
    if not url:
        raise RuntimeError("PDI_GITHUB_REPO 尚未配置")

    key = _memory_key(url)
    cached = _MEMORY_CACHE.get(key)
    now = time.time()
    if cached and now - cached[0] <= _MEMORY_TTL_SECONDS:
        return cached[1], url

    response = requests.get(url, headers=_headers(raw=bool(token)), timeout=timeout)
    response.raise_for_status()
    text = response.text
    if not text.strip():
        raise ValueError(f"GitHub 返回空文件：{path}")
    _MEMORY_CACHE[key] = (now, text)
    return text, url


def fetch_text_result(path: str, fallback: Path | None = None, timeout: int = 15) -> FetchResult:
    errors: list[str] = []
    try:
        text, url = _remote_text(path, timeout=timeout)
        cache_path = _write_disk_cache(path, text)
        return FetchResult(
            payload=text,
            source="github",
            message="已从 intelligence-data 分支读取生产数据",
            fetched_at=utc_now_iso(),
            remote_url=url,
            cache_path=str(cache_path),
        )
    except Exception as exc:
        errors.append(f"远程读取失败：{exc}")

    cached_text = _read_disk_cache(path)
    if cached_text is not None:
        return FetchResult(
            payload=cached_text,
            source="cache",
            message="；".join(errors + ["已回退到 Streamlit 本地缓存"]),
            fetched_at=utc_now_iso(),
            cache_path=str(_cache_file(path)),
        )

    if fallback and fallback.exists() and fallback.stat().st_size > 0:
        return FetchResult(
            payload=fallback.read_text(encoding="utf-8"),
            source="demo",
            message="；".join(errors + ["当前展示包内 Demo，不是生产日报"]),
            fetched_at=utc_now_iso(),
            cache_path=str(fallback),
        )

    return FetchResult(
        payload="",
        source="missing",
        message="；".join(errors + ["未找到远程数据、本地缓存或 Demo"]),
        fetched_at=utc_now_iso(),
    )


def fetch_json_result(path: str, fallback: Path | None = None, timeout: int = 15) -> FetchResult:
    text_result = fetch_text_result(path, fallback=fallback, timeout=timeout)
    if not text_result.payload:
        text_result.payload = {}
        return text_result
    try:
        text_result.payload = json.loads(str(text_result.payload))
        return text_result
    except Exception as exc:
        # A malformed remote/cache file must not silently masquerade as valid production data.
        if fallback and fallback.exists():
            return FetchResult(
                payload=read_json(fallback, {}),
                source="demo",
                message=f"{text_result.message}；JSON 解析失败：{exc}；已改用 Demo",
                fetched_at=utc_now_iso(),
                cache_path=str(fallback),
            )
        return FetchResult(
            payload={},
            source="missing",
            message=f"{text_result.message}；JSON 解析失败：{exc}",
            fetched_at=utc_now_iso(),
        )


def latest_issue_result() -> FetchResult:
    root = project_root()
    return fetch_json_result("data/latest.json", root / "data/demo/latest.json")


def latest_issue() -> dict[str, Any]:
    payload = latest_issue_result().payload
    return payload if isinstance(payload, dict) else {}


def history_index_result() -> FetchResult:
    root = project_root()
    return fetch_json_result("data/history_index.json", root / "data/demo/history_index.json")


def history_index() -> list[dict[str, Any]]:
    payload = history_index_result().payload
    return payload if isinstance(payload, list) else []


def entity_jsonl_result(name: str) -> FetchResult:
    fallback = project_root() / "data/demo" / name
    result = fetch_text_result(f"data/entities/{name}", fallback=fallback)
    rows: list[dict[str, Any]] = []
    parse_errors = 0
    for line in str(result.payload or "").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
        except Exception:
            parse_errors += 1
    result.payload = rows
    if parse_errors:
        result.message += f"；忽略了 {parse_errors} 行无效 JSONL"
    return result


def entity_jsonl(name: str) -> list[dict[str, Any]]:
    payload = entity_jsonl_result(name).payload
    return payload if isinstance(payload, list) else []


def static_report_result() -> FetchResult:
    return fetch_text_result("site/index.html", project_root() / "data/demo/latest.html")


def github_repository_status(timeout: int = 12) -> dict[str, Any]:
    parts = _repo_parts()
    if not parts:
        return {
            "configured": False,
            "message": "未配置 PDI_GITHUB_REPO",
            "branch": None,
            "commit_sha": None,
            "commit_date": None,
            "workflow_status": None,
            "workflow_conclusion": None,
        }

    owner, repo, branch = parts
    base = f"https://api.github.com/repos/{owner}/{repo}"
    status: dict[str, Any] = {
        "configured": True,
        "repository": f"{owner}/{repo}",
        "branch": branch,
        "commit_sha": None,
        "commit_date": None,
        "commit_url": None,
        "workflow_status": None,
        "workflow_conclusion": None,
        "workflow_url": None,
        "message": "",
    }
    errors: list[str] = []

    try:
        response = requests.get(f"{base}/commits/{branch}", headers=_headers(), timeout=timeout)
        response.raise_for_status()
        commit = response.json()
        status["commit_sha"] = commit.get("sha")
        status["commit_date"] = (commit.get("commit", {}).get("committer", {}) or {}).get("date")
        status["commit_url"] = commit.get("html_url")
    except Exception as exc:
        errors.append(f"数据分支状态不可用：{exc}")

    try:
        response = requests.get(
            f"{base}/actions/workflows/daily-intelligence.yml/runs",
            params={"branch": "main", "per_page": 1},
            headers=_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        runs = response.json().get("workflow_runs", [])
        if runs:
            run = runs[0]
            status["workflow_status"] = run.get("status")
            status["workflow_conclusion"] = run.get("conclusion")
            status["workflow_url"] = run.get("html_url")
            status["workflow_updated_at"] = run.get("updated_at")
    except Exception as exc:
        errors.append(f"Workflow 状态不可用：{exc}")

    status["message"] = "；".join(errors) if errors else "GitHub 数据分支和 Workflow 状态读取成功"
    return status


def clear_dashboard_cache() -> None:
    _MEMORY_CACHE.clear()
    cache_dir = project_root() / "runtime" / "dashboard_cache"
    if cache_dir.exists():
        for path in cache_dir.glob("*"):
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass


def source_label(source: str) -> str:
    return {
        "github": "GitHub 生产数据",
        "cache": "Streamlit 本地缓存",
        "demo": "包内 Demo",
        "missing": "未读取到数据",
    }.get(source, source)


def works_dataframe() -> pd.DataFrame:
    rows = []
    for work in entity_jsonl("scholarly_works.jsonl"):
        rows.append(
            {
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
            }
        )
    return pd.DataFrame(rows)


def events_dataframe() -> pd.DataFrame:
    rows = []
    for event in entity_jsonl("public_health_events.jsonl"):
        rows.append(
            {
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
            }
        )
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
    .pdi-status { border:1px solid var(--line); padding:.65rem .8rem; margin:.25rem 0 .7rem; }
    .small-muted { color:#6a6259;font-size:.85rem; }
    a { color:var(--red)!important; }
    </style>
    """


def setup_page(title: str, icon: str = "📰") -> None:
    import streamlit as st

    st.set_page_config(page_title=title, page_icon=icon, layout="wide")
    st.markdown(newspaper_css(), unsafe_allow_html=True)
