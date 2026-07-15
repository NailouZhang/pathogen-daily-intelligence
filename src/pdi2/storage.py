from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import dump_json, load_json


def load_state(state_dir: Path) -> dict[str, Any]:
    state_dir.mkdir(parents=True, exist_ok=True)
    state = load_json(state_dir / "state.json", default={}) or {}
    state.setdefault("translation_cache", {})
    state.setdefault("seen_papers", {})
    state.setdefault("seen_news", {})
    return state


def save_state(state_dir: Path, state: dict[str, Any]) -> None:
    cache = state.get("translation_cache") or {}
    if len(cache) > 5000:
        keys = list(cache)[-5000:]
        state["translation_cache"] = {key: cache[key] for key in keys}
    dump_json(state_dir / "state.json", state)


def write_issue(output_dir: Path, issue: dict[str, Any]) -> None:
    data_dir = output_dir / "data"
    history_dir = data_dir / "history"
    data_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)
    dump_json(data_dir / "latest.json", issue)
    dump_json(history_dir / f"{issue['issue_date']}.json", issue)
    index = load_json(data_dir / "history_index.json", default=[]) or []
    row = {"issue_date": issue["issue_date"], "issue_id": issue["issue_id"], "profile_id": issue["profile_id"]}
    index = [item for item in index if item.get("issue_date") != issue["issue_date"]]
    index.insert(0, row)
    dump_json(data_dir / "history_index.json", index[:365])
