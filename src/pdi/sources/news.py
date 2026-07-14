from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit

import feedparser
from bs4 import BeautifulSoup

from .base import SourceResult
from ..dates import CoverageWindow, in_window
from ..http import HttpClient
from ..query_planner import QueryTask
from ..utils import canonicalize_url, normalize_space, parse_date_loose, sentence_split, stable_hash, utc_now_iso


def _audit_dict(audit: Any) -> dict[str, Any]:
    return audit.__dict__.copy()


def _source_record(source: dict[str, Any], task: QueryTask, *, title: str, url: str, published: Any = None, excerpt: str | None = None, language: str | None = None, original_source: str | None = None) -> dict[str, Any]:
    pub_date, precision = parse_date_loose(published)
    canon=canonicalize_url(url)
    return {
        "record_type":"news_source","source_id":source["source_id"],"source_name":source.get("name"),"source_tier":source.get("reliability_tier","unknown"),"source_category":source.get("category","discoverer"),
        "source_record_id":stable_hash(canon or title),"query_group":task.group_id,"query":task.query,"language":language or task.language,
        "title":normalize_space(title),"excerpt":normalize_space(excerpt) or None,"content_sentences":sentence_split(excerpt,"N"),"published_at":pub_date,"published_date_precision":precision,
        "url":url,"canonical_url":canon,"domain":urlsplit(canon).netloc,"original_source":original_source,"retrieved_at":utc_now_iso()
    }


def collect_google_news(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result=SourceResult(source["source_id"],"success")
    loc=source.get("locale",{})
    for task in tasks:
        result.query_count+=1
        q=f"({task.query}) when:{max(1,(datetime.fromisoformat(window.end)-datetime.fromisoformat(window.start)).days+1)}d"
        url=f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={quote_plus(loc.get('hl','en-US'))}&gl={quote_plus(loc.get('gl','US'))}&ceid={quote_plus(loc.get('ceid','US:en'))}"
        response,audit=client.request("GET",url)
        result.audits.append(_audit_dict(audit))
        if response is None:
            result.errors.append(audit.error or "Google News RSS failed"); continue
        feed=feedparser.parse(response.content)
        for entry in feed.entries[:task.limit]:
            source_name=normalize_space((entry.get("source") or {}).get("title")) if isinstance(entry.get("source"),dict) else None
            result.records.append(_source_record(source,task,title=entry.get("title",""),url=entry.get("link","") or "",published=entry.get("published"),excerpt=entry.get("summary"),language=task.language,original_source=source_name))
    if result.errors and not result.records: result.status="failed"
    elif result.errors: result.status="partial"
    return result


def collect_gdelt(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result=SourceResult(source["source_id"],"success")
    start=window.start.replace("-","")+"000000"; end=window.end.replace("-","")+"235959"
    for task in tasks:
        result.query_count+=1
        params={"query":task.query,"mode":"ArtList","maxrecords":min(task.limit,50),"format":"json","sort":"HybridRel","startdatetime":start,"enddatetime":end}
        data,audit=client.get_json(source["base_url"],params=params)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or "GDELT failed"); continue
        for rec in data.get("articles",[]):
            result.records.append(_source_record(source,task,title=rec.get("title",""),url=rec.get("url","") or "",published=rec.get("seendate"),excerpt=rec.get("socialimage") and None,language=rec.get("language") or task.language,original_source=rec.get("domain")))
    if result.errors and not result.records: result.status="failed"
    elif result.errors: result.status="partial"
    return result


def collect_reliefweb(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result=SourceResult(source["source_id"],"success")
    for task in tasks:
        result.query_count+=1
        payload={"appname":"pathogen-daily-intelligence","query":{"value":task.query},"filter":{"field":"date.created","value":{"from":window.start+"T00:00:00+00:00","to":window.end+"T23:59:59+00:00"}},"limit":min(task.limit,30),"sort":["date.created:desc"],"fields":{"include":["id","url","title","date.created","source.name","body","country.name","language.name"]}}
        data,audit=client.post_json(source["base_url"],json=payload)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or "ReliefWeb failed"); continue
        for item in data.get("data",[]):
            fields=item.get("fields",{})
            src_names=[x.get("name") for x in fields.get("source",[]) if isinstance(x,dict)]
            result.records.append(_source_record(source,task,title=fields.get("title",""),url=fields.get("url","") or "",published=(fields.get("date") or {}).get("created"),excerpt=(fields.get("body") or "")[:1500],language=((fields.get("language") or [{}])[0].get("name") if fields.get("language") else task.language),original_source=", ".join(x for x in src_names if x)))
    if result.errors and not result.records: result.status="failed"
    elif result.errors: result.status="partial"
    return result


def _detail_date(soup: BeautifulSoup) -> str | None:
    selectors = [
        ('meta', {'property': 'article:published_time'}, 'content'),
        ('meta', {'name': 'date'}, 'content'),
        ('meta', {'name': 'DC.date'}, 'content'),
        ('meta', {'itemprop': 'datePublished'}, 'content'),
        ('time', {'datetime': True}, 'datetime'),
    ]
    for tag, attrs, value_attr in selectors:
        node = soup.find(tag, attrs=attrs)
        if node and node.get(value_attr):
            return str(node.get(value_attr))
    return None


def _detail_excerpt(soup: BeautifulSoup) -> str | None:
    for attrs in ({'name': 'description'}, {'property': 'og:description'}):
        node = soup.find('meta', attrs=attrs)
        if node and node.get('content'):
            return normalize_space(node.get('content'))[:1800]
    paragraphs = [normalize_space(p.get_text(' ')) for p in soup.find_all('p')]
    paragraphs = [p for p in paragraphs if len(p) >= 60]
    return normalize_space(' '.join(paragraphs[:3]))[:1800] or None


def collect_official_page(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result=SourceResult(source["source_id"],"success")
    url=source.get("list_url")
    response,audit=client.request("GET",url)
    result.audits.append(_audit_dict(audit)); result.query_count=1
    if response is None:
        result.status="failed"; result.errors.append(audit.error or "Official page failed"); return result
    soup=BeautifulSoup(response.text,"lxml")
    task=tasks[0] if tasks else QueryTask(source["source_id"],"identity","en","hantavirus",100,25)
    tokens={x.casefold().strip('"()') for t in tasks for x in re.split(r"\s+OR\s+|\s+",t.query) if len(x.strip('"()'))>3 and not x.startswith('-')}
    seen=set()
    for link in soup.find_all("a",href=True):
        title=normalize_space(link.get_text(" "))
        href=urljoin(url,link.get("href"))
        if not title or href in seen: continue
        if not any(token in title.casefold() for token in tokens): continue
        seen.add(href)
        detail,detail_audit=client.request("GET",href,max_attempts=2)
        result.audits.append(_audit_dict(detail_audit)); result.query_count+=1
        published=None; excerpt=None; detail_title=title
        if detail is not None:
            detail_soup=BeautifulSoup(detail.text,"lxml")
            published=_detail_date(detail_soup)
            excerpt=_detail_excerpt(detail_soup)
            heading=detail_soup.find('h1')
            if heading and normalize_space(heading.get_text(' ')):
                detail_title=normalize_space(heading.get_text(' '))
        record=_source_record(source,task,title=detail_title,url=href,published=published,excerpt=excerpt,language="en",original_source=source.get("name"))
        if record.get('published_at') and not in_window(record.get('published_at'),window.start,window.end):
            continue
        if not record.get('published_at'):
            record['retrieval_flags']=['MISSING_PUBLISHED_DATE_REQUIRES_REVIEW']
        result.records.append(record)
        if len(result.records)>=12: break
    if result.errors and not result.records: result.status='failed'
    elif result.errors: result.status='partial'
    return result


def collect_news(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    adapter=source.get("adapter")
    if adapter=="google_news": return collect_google_news(client,source,tasks,window)
    if adapter=="gdelt": return collect_gdelt(client,source,tasks,window)
    if adapter=="reliefweb": return collect_reliefweb(client,source,tasks,window)
    if adapter=="official_page": return collect_official_page(client,source,tasks,window)
    if adapter=="registry_only": return SourceResult(source.get("source_id","unknown"),"disabled")
    return SourceResult(source.get("source_id","unknown"),"disabled",errors=[f"Unsupported news adapter: {adapter}"])
