from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from .normalization import make_scholarly_work, normalize_news_article
from .utils import canonicalize_doi, normalize_title


def _identifier_tokens(rec: dict[str, Any]) -> set[str]:
    ids = rec.get("identifiers") or {}
    tokens: set[str] = set()
    for key in ("pmid", "doi", "pmcid", "europe_pmc_id", "semantic_scholar_id", "arxiv"):
        value = ids.get(key)
        if key == "doi":
            value = canonicalize_doi(value)
        if value:
            tokens.add(f"{key}:{str(value).strip().casefold()}")
    return tokens


def _bibliographic_match(a: dict[str, Any], b: dict[str, Any]) -> bool:
    title_score = token_set_ratio(normalize_title(a.get("title")), normalize_title(b.get("title"))) / 100
    auth_a = {(x or "").casefold() for x in a.get("authors", [])[:8] if x}
    auth_b = {(x or "").casefold() for x in b.get("authors", [])[:8] if x}
    overlap = len(auth_a & auth_b) / max(1, min(len(auth_a), len(auth_b)))
    year_a = (a.get("published_date") or "")[:4]
    year_b = (b.get("published_date") or "")[:4]
    year_ok = not year_a or not year_b or year_a == year_b
    return title_score >= 0.95 and overlap >= 0.5 and year_ok


def deduplicate_scholarly(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    n = len(records)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    owner: dict[str, int] = {}
    for i, rec in enumerate(records):
        for token in _identifier_tokens(rec):
            if token in owner:
                union(i, owner[token])
            else:
                owner[token] = i

    # Bibliographic pass is deliberately conservative and supplements absent cross-identifiers.
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
        "identifier_tokens": len(owner),
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
        for existing in articles[-200:]:
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
