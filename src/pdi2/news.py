from __future__ import annotations

import re
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser

from .http import HttpClient
from .utils import clean_space, safe_date_string, strip_tags, unique_strings


def _feed_date(entry: Any) -> str | None:
    for key in ("published", "updated"):
        value = entry.get(key)
        if value:
            try:
                return parsedate_to_datetime(value).date().isoformat()
            except (TypeError, ValueError, OverflowError):
                parsed = safe_date_string(value)
                if parsed:
                    return parsed
    return None


def search_google_news(http: HttpClient, queries: list[str], start: date, end: date, per_query: int = 30) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    locales = [
        ("en-US", "US", "US:en", "Google News English"),
        ("zh-CN", "CN", "CN:zh-Hans", "Google News Chinese"),
    ]
    for query in queries:
        query_text = f"{query} when:7d"
        for hl, gl, ceid, source_name in locales:
            url = f"https://news.google.com/rss/search?q={quote_plus(query_text)}&hl={hl}&gl={gl}&ceid={quote_plus(ceid)}"
            try:
                raw = http.get_text(url)
            except Exception:
                continue
            feed = feedparser.parse(raw)
            for entry in feed.entries[:per_query]:
                published = _feed_date(entry)
                if published and not (start.isoformat() <= published <= end.isoformat()):
                    continue
                source_title = clean_space((entry.get("source") or {}).get("title") if isinstance(entry.get("source"), dict) else "")
                output.append({
                    "source": source_name,
                    "title": clean_space(entry.get("title")),
                    "url": clean_space(entry.get("link")),
                    "published_date": published,
                    "excerpt": strip_tags(entry.get("summary")),
                    "publisher": source_title,
                    "language": "zh" if hl.startswith("zh") else "en",
                })
    return output


def search_bing_news(http: HttpClient, queries: list[str], start: date, end: date, per_query: int = 25) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for query in queries:
        url = f"https://www.bing.com/news/search?q={quote_plus(query)}&format=rss"
        try:
            raw = http.get_text(url)
        except Exception:
            continue
        feed = feedparser.parse(raw)
        for entry in feed.entries[:per_query]:
            published = _feed_date(entry)
            if published and not (start.isoformat() <= published <= end.isoformat()):
                continue
            output.append({
                "source": "Bing News RSS",
                "title": clean_space(entry.get("title")),
                "url": clean_space(entry.get("link")),
                "published_date": published,
                "excerpt": strip_tags(entry.get("summary")),
                "publisher": clean_space(entry.get("author")),
                "language": "en",
            })
    return output


def search_gdelt(http: HttpClient, queries: list[str], start: date, end: date, per_query: int = 50) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for query in queries:
        try:
            payload = http.get_json(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params={
                    "query": query,
                    "mode": "ArtList",
                    "format": "json",
                    "maxrecords": per_query,
                    "startdatetime": start.strftime("%Y%m%d000000"),
                    "enddatetime": end.strftime("%Y%m%d235959"),
                    "sort": "HybridRel",
                },
            )
        except Exception:
            continue
        for item in payload.get("articles", []) or []:
            output.append({
                "source": "GDELT DOC 2.0",
                "title": clean_space(item.get("title")),
                "url": clean_space(item.get("url")),
                "published_date": safe_date_string(item.get("seendate")),
                "excerpt": clean_space(item.get("socialimage")),
                "publisher": clean_space(item.get("domain")),
                "language": clean_space(item.get("language")) or "unknown",
            })
    return output


def search_reliefweb(http: HttpClient, queries: list[str], start: date, end: date, per_query: int = 25) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for query in queries:
        payload = {
            "appname": "pathogen-daily-intelligence",
            "limit": per_query,
            "query": {"value": query},
            "filter": {
                "operator": "AND",
                "conditions": [
                    {"field": "date.created", "value": {"from": start.isoformat(), "to": end.isoformat()}},
                ],
            },
            "fields": {"include": ["title", "body", "date.created", "source.name", "url"]},
        }
        try:
            response = http.request("POST", "https://api.reliefweb.int/v2/reports", json=payload)
            body = response.json()
        except Exception:
            continue
        for item in body.get("data", []) or []:
            fields = item.get("fields") or {}
            sources = fields.get("source") or []
            output.append({
                "source": "ReliefWeb",
                "title": clean_space(fields.get("title")),
                "url": clean_space(fields.get("url")),
                "published_date": safe_date_string((fields.get("date") or {}).get("created")),
                "excerpt": strip_tags(fields.get("body"))[:1500],
                "publisher": ", ".join(clean_space(s.get("name")) for s in sources if s.get("name")),
                "language": "en",
            })
    return output


def search_who(http: HttpClient, terms: list[str], start: date, end: date) -> list[dict[str, Any]]:
    # WHO search pages change periodically. Google News site queries are more stable,
    # but this direct page still provides an additional official-source discovery path.
    output: list[dict[str, Any]] = []
    for term in terms[:5]:
        url = f"https://www.who.int/home/search-results?indexCatalogue=genericsearchindex1&searchQuery={quote_plus(term)}&wordsMode=AnyWord"
        try:
            raw = http.get_text(url)
        except Exception:
            continue
        # Extract visible article-like links conservatively.
        for href, title in re.findall(r'href="([^"]+)"[^>]*>([^<]{20,180})</a>', raw, flags=re.I):
            title = clean_space(title)
            if term.lower() not in title.lower():
                continue
            if href.startswith("/"):
                href = "https://www.who.int" + href
            output.append({
                "source": "WHO website search",
                "title": title,
                "url": href,
                "published_date": None,
                "excerpt": "",
                "publisher": "World Health Organization",
                "language": "en",
                "official": True,
            })
    return output


def filter_news_window(records: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for record in records:
        published = record.get("published_date")
        if published and not (start.isoformat() <= published <= end.isoformat()):
            continue
        if not record.get("title") or not record.get("url"):
            continue
        output.append(record)
    return output
