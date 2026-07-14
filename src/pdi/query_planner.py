from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class QueryTask:
    source_id: str
    group_id: str
    language: str
    query: str
    priority: int
    limit: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _language_of(text: str) -> str:
    return "zh" if any("\u3400" <= c <= "\u9fff" for c in text) else "en"


def build_query_tasks(profile: dict[str, Any], source: dict[str, Any]) -> list[QueryTask]:
    groups = profile.get("query_groups", {}).get("groups", [])
    allowed_languages = set(source.get("languages") or profile.get("priority_languages") or ["en", "zh"])
    max_groups = int(profile.get("search_policy", {}).get("max_query_groups_per_source", 8))
    default_limit = int(profile.get("search_policy", {}).get("max_results_per_group", 25))
    tasks: list[QueryTask] = []
    for group in sorted(groups, key=lambda g: int(g.get("priority", 0)), reverse=True):
        anchors = group.get("anchors") or []
        by_lang: dict[str, list[str]] = {}
        for anchor in anchors:
            by_lang.setdefault(_language_of(anchor), []).append(anchor)
        for lang, lang_anchors in by_lang.items():
            if lang not in allowed_languages:
                continue
            chosen = lang_anchors[: max(1, int(group.get("query_budget", 1)))]
            if not chosen:
                continue
            query = " OR ".join(f'"{x}"' if " " in x else x for x in chosen)
            negatives = [x for x in group.get("negative_terms", []) if x]
            if negatives:
                query = f"({query}) " + " ".join(f'-"{x}"' for x in negatives)
            tasks.append(QueryTask(source["source_id"], group["group_id"], lang, query, int(group.get("priority", 0)), default_limit))
    # Stable high-priority truncation prevents Cartesian query explosions.
    tasks.sort(key=lambda t: (-t.priority, t.group_id, t.language, t.query))
    return tasks[:max_groups]
