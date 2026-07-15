from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from .normalization import make_scholarly_work, normalize_news_article
from .utils import canonicalize_doi, normalize_title


def _identifier_map(rec: dict[str, Any]) -> dict[str, str]:
    ids = rec.get("identifiers") or {}
    out: dict[str, str] = {}
    for key in ("pmid", "doi", "pmcid", "europe_pmc_id", "semantic_scholar_id", "arxiv"):
        value = ids.get(key)
        if key == "doi":
            value = canonicalize_doi(value)
        if value:
            out[key] = str(value).strip().casefold()
    return out


def _years(rec: dict[str, Any]) -> set[int]:
    values = [
        rec.get("published_date"),
        rec.get("online_date"),
        rec.get("print_date"),
        rec.get("issue_date"),
    ]
    out: set[int] = set()
    for value in values:
        token = str(value or "")[:4]
        if token.isdigit():
            out.add(int(token))
    return out


def _title_score(a: dict[str, Any], b: dict[str, Any]) -> float:
    return token_set_ratio(normalize_title(a.get("title")), normalize_title(b.get("title"))) / 100


def _author_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    auth_a = {(x or "").casefold() for x in a.get("authors", [])[:12] if x}
    auth_b = {(x or "").casefold() for x in b.get("authors", [])[:12] if x}
    if not auth_a or not auth_b:
        return 0.0
    return len(auth_a & auth_b) / max(1, min(len(auth_a), len(auth_b)))


def _date_compatible(a: dict[str, Any], b: dict[str, Any], tolerance: int = 2) -> bool:
    ya, yb = _years(a), _years(b)
    if not ya or not yb:
        return True
    return min(abs(x - y) for x in ya for y in yb) <= tolerance


def _identifier_merge_safe(a: dict[str, Any], b: dict[str, Any], shared_key: str) -> bool:
    """Prevent a bad identifier from transitively merging unrelated works."""
    title = _title_score(a, b)
    authors = _author_overlap(a, b)
    dates_ok = _date_compatible(a, b)
    # PMID/PMCID are strong, but an extreme title/date contradiction is still audited
    # rather than silently merged. DOI metadata can be mistyped by providers, so it
    # requires explicit bibliographic compatibility.
    if shared_key in {"pmid", "pmcid"}:
        return dates_ok and (title >= 0.68 or authors >= 0.5)
    if shared_key == "doi":
        return dates_ok and (title >= 0.82 or (title >= 0.70 and authors >= 0.5))
    return dates_ok and title >= 0.82


def _bibliographic_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    title = _title_score(a, b)
    authors = _author_overlap(a, b)
    return title >= 0.95 and authors >= 0.5 and _date_compatible(a, b, tolerance=1)


def deduplicate_scholarly(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    n = len(records)
    parent = list(range(n))
    conflicts: list[dict[str, Any]] = []

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    owners: dict[tuple[str, str], list[int]] = {}
    for i, rec in enumerate(records):
        for key, value in _identifier_map(rec).items():
            token = (key, value)
            for other in owners.get(token, []):
                if _identifier_merge_safe(rec, records[other], key):
                    union(i, other)
                else:
                    conflicts.append(
                        {
                            "identifier_type": key,
                            "identifier": value,
                            "record_a": rec.get("source_record_id"),
                            "record_b": records[other].get("source_record_id"),
                            "reason": "IDENTIFIER_BIBLIOGRAPHY_CONFLICT",
                        }
                    )
            owners.setdefault(token, []).append(i)

    # Conservative bibliographic pass supplements records without shared identifiers.
    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            if _bibliographic_match(records[i], records[j]):
                union(i, j)

    groups: dict[int, list[dict[str, Any]]] = {}
    for i, rec in enumerate(records):
        groups.setdefault(find(i), []).append(rec)
    works = [make_scholarly_work(group) for _, group in sorted(groups.items())]
    return works, {
        "raw": len(records),
        "identifier_tokens": len(owners),
        "identifier_conflicts": len(conflicts),
        "groups": len(groups),
        "merged": len(records) - len(works),
        "unique": len(works),
    }


def deduplicate_news(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    articles: list[dict[str, Any]] = []
    by_url: dict[str, str] = {}
    duplicates = 0
    for rec in records:
        article = normalize_news_article(rec)
        url = article["canonical_url"]
        if url and url in by_url:
            duplicates += 1
            continue
        norm = normalize_title(article["title"]["original"])
        duplicate = False
        for existing in articles[-300:]:
            ex = normalize_title(existing["title"]["original"])
            if token_set_ratio(norm, ex) >= 96 and SequenceMatcher(None, norm, ex).ratio() >= 0.92:
                duplicate = True
                break
        if duplicate:
            duplicates += 1
            continue
        articles.append(article)
        if url:
            by_url[url] = article["article_id"]
    return articles, {"raw": len(records), "duplicates": duplicates, "unique": len(articles)}
