from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from .base import SourceResult
from ..dates import CoverageWindow, choose_current_availability_date, in_window
from ..http import HttpClient
from ..query_planner import QueryTask
from ..utils import (
    canonicalize_doi,
    normalize_space,
    normalize_title,
    parse_date_loose,
    sentence_split,
    stable_hash,
    utc_now_iso,
)


def _audit_dict(audit: Any) -> dict[str, Any]:
    return audit.__dict__.copy()


def _node_date(node: ET.Element | None) -> tuple[str | None, str]:
    if node is None:
        return None, "unknown"
    year = normalize_space(node.findtext("Year"))
    month = normalize_space(node.findtext("Month"))
    day = normalize_space(node.findtext("Day"))
    month_map = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}
    if year and month and day:
        try:
            m = int(month) if month.isdigit() else month_map.get(month[:3].title())
            if m:
                return f"{int(year):04d}-{m:02d}-{int(day):02d}", "day"
        except ValueError:
            pass
    if year and month:
        try:
            m = int(month) if month.isdigit() else month_map.get(month[:3].title())
            if m:
                return f"{int(year):04d}-{m:02d}", "month"
        except ValueError:
            pass
    if year:
        return year, "year"
    return parse_date_loose(normalize_space(node.findtext("MedlineDate")))


def _pubmed_dates(article: ET.Element, window: CoverageWindow) -> dict[str, Any]:
    dates: dict[str, Any] = {
        "online_date": None,
        "print_date": None,
        "issue_date": None,
        "source_created_date": None,
        "source_entry_date": None,
        "source_indexed_date": None,
    }
    precisions: dict[str, str] = {}

    article_date, article_precision = _node_date(article.find(".//Article/ArticleDate"))
    if article_date:
        dates["online_date"] = article_date
        precisions["online_date"] = article_precision

    issue_date, issue_precision = _node_date(article.find(".//Journal/JournalIssue/PubDate"))
    if issue_date:
        dates["issue_date"] = issue_date
        precisions["issue_date"] = issue_precision

    for node in article.findall(".//PubmedData/History/PubMedPubDate"):
        status = str(node.attrib.get("PubStatus") or "").casefold()
        value, precision = _node_date(node)
        if not value:
            continue
        if status in {"epublish", "aheadofprint"}:
            dates["online_date"] = dates["online_date"] or value
            precisions.setdefault("online_date", precision)
        elif status == "ppublish":
            dates["print_date"] = value
            precisions["print_date"] = precision
        elif status == "pubmed":
            dates["source_created_date"] = value
            precisions["source_created_date"] = precision
        elif status == "entrez":
            dates["source_entry_date"] = value
            precisions["source_entry_date"] = precision
        elif status in {"medline", "pmc-release"}:
            dates["source_indexed_date"] = value
            precisions["source_indexed_date"] = precision

    candidates = [
        (field, value, precisions.get(field, ""))
        for field, value in dates.items()
        if value
    ]
    effective, precision, basis = choose_current_availability_date(candidates, window)
    dates.update(
        {
            "published_date": effective,
            "published_date_precision": precision,
            "availability_date": effective,
            "availability_basis": basis,
        }
    )
    return dates


def _title_relevance(title: str, abstract: str | None, task: QueryTask) -> float:
    haystack = normalize_title(" ".join([title or "", abstract or ""]))
    terms = [part.strip('" ') for part in re.split(r"\s+OR\s+", task.query, flags=re.I)]
    scores = [token_set_ratio(normalize_title(term), haystack) / 100 for term in terms if term]
    exact = 1.0 if any(normalize_title(term) in haystack for term in terms if normalize_title(term)) else 0.0
    return max([exact, *scores] or [0.0])


def collect_pubmed(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    api_key = os.getenv("NCBI_API_KEY", "")
    for task in tasks:
        result.query_count += 1
        # CRDT/EDAT catch citations made available now even when a future issue date
        # is already assigned. EPDAT catches online-first publication explicitly.
        date_clause = (
            f'(("{window.start}"[dp] : "{window.end}"[dp]) OR '
            f'("{window.start}"[epdat] : "{window.end}"[epdat]) OR '
            f'("{window.start}"[crdt] : "{window.end}"[crdt]) OR '
            f'("{window.start}"[edat] : "{window.end}"[edat]))'
        )
        query = f"({task.query}) AND {date_clause}"
        params = {"db": "pubmed", "term": query, "retmode": "json", "retmax": task.limit, "sort": "most recent"}
        if api_key:
            params["api_key"] = api_key
        data, audit = client.get_json(source["base_url"] + "esearch.fcgi", params=params)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or f"PubMed search failed for {task.group_id}")
            continue
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            continue
        params2 = {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"}
        if api_key:
            params2["api_key"] = api_key
        response, audit2 = client.request("GET", source["base_url"] + "efetch.fcgi", params=params2)
        result.audits.append(_audit_dict(audit2))
        if response is None:
            result.errors.append(audit2.error or "PubMed fetch failed")
            continue
        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            result.errors.append(f"PubMed XML parse error: {exc}")
            continue
        for article in root.findall(".//PubmedArticle"):
            pmid = normalize_space(article.findtext(".//PMID")) or None
            title_node = article.find(".//ArticleTitle")
            title = normalize_space("".join(title_node.itertext()) if title_node is not None else "")
            abstract = " ".join(normalize_space("".join(x.itertext())) for x in article.findall(".//Abstract/AbstractText"))
            doi = None
            pmcid = None
            for aid in article.findall(".//ArticleId"):
                typ = aid.attrib.get("IdType", "").lower()
                val = normalize_space(aid.text)
                if typ == "doi":
                    doi = canonicalize_doi(val)
                if typ == "pmc":
                    pmcid = val
            date_fields = _pubmed_dates(article, window)
            if not date_fields.get("published_date"):
                continue
            authors: list[str] = []
            for author in article.findall(".//Author"):
                name = normalize_space(" ".join(x for x in [author.findtext("ForeName"), author.findtext("LastName")] if x))
                if name:
                    authors.append(name)
            journal = normalize_space(article.findtext(".//Journal/Title")) or None
            result.records.append(
                {
                    "record_type": "scholarly_source",
                    "source_id": "pubmed",
                    "source_record_id": pmid or stable_hash(title),
                    "query_group": task.group_id,
                    "query": task.query,
                    "identifiers": {"pmid": pmid, "pmcid": pmcid, "doi": doi},
                    "title": title,
                    "abstract": abstract or None,
                    "abstract_sentences": sentence_split(abstract, "A"),
                    "authors": authors,
                    "journal": journal,
                    **date_fields,
                    "language": normalize_space(article.findtext(".//Language")) or None,
                    "discovery_relevance": _title_relevance(title, abstract, task),
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
                    "retrieved_at": utc_now_iso(),
                }
            )
    if result.errors and not result.records:
        result.status = "failed"
    elif result.errors:
        result.status = "partial"
    return result


def collect_europe_pmc(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    for task in tasks:
        result.query_count += 1
        query = f'({task.query}) AND (FIRST_PDATE:[{window.start} TO {window.end}] OR E_PDATE:[{window.start} TO {window.end}])'
        params = {"query": query, "format": "json", "pageSize": task.limit, "resultType": "core", "sort": "FIRST_PDATE_D desc"}
        data, audit = client.get_json(source["base_url"] + "search", params=params)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or f"Europe PMC search failed for {task.group_id}")
            continue
        for rec in data.get("resultList", {}).get("result", []):
            title = normalize_space(rec.get("title"))
            abstract = normalize_space(rec.get("abstractText")) or None
            first_date = rec.get("firstPublicationDate")
            online_date = rec.get("electronicPublicationDate")
            print_date = (rec.get("journalInfo") or {}).get("printPublicationDate")
            created_date = rec.get("firstIndexDate") or rec.get("dateOfCreation")
            indexed_date = rec.get("dateOfRevision")
            effective, precision, basis = choose_current_availability_date(
                [
                    ("online_date", online_date, ""),
                    ("first_publication_date", first_date, ""),
                    ("source_created_date", created_date, ""),
                    ("source_indexed_date", indexed_date, ""),
                    ("print_date", print_date, ""),
                ],
                window,
            )
            if not effective:
                continue
            doi = canonicalize_doi(rec.get("doi"))
            result.records.append(
                {
                    "record_type": "scholarly_source",
                    "source_id": "europe_pmc",
                    "source_record_id": rec.get("id") or stable_hash(title),
                    "query_group": task.group_id,
                    "query": task.query,
                    "identifiers": {"pmid": rec.get("pmid"), "pmcid": rec.get("pmcid"), "doi": doi, "europe_pmc_id": rec.get("id")},
                    "title": title,
                    "abstract": abstract,
                    "abstract_sentences": sentence_split(abstract, "A"),
                    "authors": [normalize_space(x.get("fullName")) for x in (rec.get("authorList") or {}).get("author", []) if normalize_space(x.get("fullName"))],
                    "journal": normalize_space(rec.get("journalTitle") or ((rec.get("journalInfo") or {}).get("journal") or {}).get("title")) or None,
                    "published_date": effective,
                    "published_date_precision": precision,
                    "availability_date": effective,
                    "availability_basis": basis,
                    "online_date": parse_date_loose(online_date)[0],
                    "print_date": parse_date_loose(print_date)[0],
                    "first_publication_date": parse_date_loose(first_date)[0],
                    "source_created_date": parse_date_loose(created_date)[0],
                    "source_indexed_date": parse_date_loose(indexed_date)[0],
                    "language": rec.get("language"),
                    "is_preprint": str(rec.get("pubType", "")).casefold() == "preprint",
                    "open_access": rec.get("isOpenAccess"),
                    "full_text_available": rec.get("inEPMC"),
                    "discovery_relevance": _title_relevance(title, abstract, task),
                    "url": f"https://europepmc.org/article/{rec.get('source', 'MED')}/{rec.get('id')}" if rec.get("id") else None,
                    "retrieved_at": utc_now_iso(),
                }
            )
    if result.errors and not result.records:
        result.status = "failed"
    elif result.errors:
        result.status = "partial"
    return result


def _crossref_date(rec: dict[str, Any], key: str) -> Any:
    parts = (rec.get(key) or {}).get("date-parts")
    return parts[0] if parts else None


def collect_crossref(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    mailto = os.getenv("CROSSREF_MAILTO", "")
    select = "DOI,title,author,published-online,published-print,published,issued,created,indexed,deposited,container-title,publisher,volume,issue,page,article-number,ISSN,type,URL,relation,update-to,subtype,abstract,language,score,link,license"
    seen: set[str] = set()
    for task in tasks:
        for discovery_mode, date_filter in (
            ("created", f"from-created-date:{window.start},until-created-date:{window.end}"),
            ("online", f"from-online-pub-date:{window.start},until-online-pub-date:{window.end}"),
        ):
            result.query_count += 1
            params = {"query.bibliographic": task.query, "filter": date_filter, "rows": min(task.limit, 40), "select": select, "sort": "score", "order": "desc"}
            if mailto:
                params["mailto"] = mailto
            data, audit = client.get_json(source["base_url"] + "works", params=params)
            result.audits.append(_audit_dict(audit))
            if not data:
                result.errors.append(audit.error or f"Crossref {discovery_mode} search failed for {task.group_id}")
                continue
            for rec in (data.get("message") or {}).get("items", []):
                title = normalize_space((rec.get("title") or [""])[0])
                abstract = normalize_space(re.sub(r"<[^>]+>", " ", rec.get("abstract") or "")) or None
                relevance = _title_relevance(title, abstract, task)
                # Crossref bibliographic search is intentionally broad.  Keep only
                # records with direct title/abstract support for the pathogen query.
                if relevance < 0.72:
                    continue
                doi = canonicalize_doi(rec.get("DOI"))
                key = doi or stable_hash(title + str(rec.get("created")))
                if key in seen:
                    continue
                seen.add(key)
                online_date = _crossref_date(rec, "published-online")
                print_date = _crossref_date(rec, "published-print")
                issued_date = _crossref_date(rec, "issued") or _crossref_date(rec, "published")
                created_date = (rec.get("created") or {}).get("date-time") or _crossref_date(rec, "created")
                indexed_date = (rec.get("indexed") or {}).get("date-time") or _crossref_date(rec, "indexed")
                deposited_date = (rec.get("deposited") or {}).get("date-time") or _crossref_date(rec, "deposited")
                effective, precision, basis = choose_current_availability_date(
                    [
                        ("online_date", online_date, ""),
                        ("source_created_date", created_date, ""),
                        ("source_indexed_date", indexed_date, ""),
                        ("source_deposited_date", deposited_date, ""),
                        ("publication_date", issued_date, ""),
                        ("print_date", print_date, ""),
                    ],
                    window,
                )
                if not effective:
                    continue
                authors: list[str] = []
                for author in rec.get("author", []):
                    name = normalize_space(" ".join(x for x in [author.get("given"), author.get("family")] if x))
                    if name:
                        authors.append(name)
                result.records.append(
                    {
                        "record_type": "scholarly_source",
                        "source_id": "crossref",
                        "source_record_id": doi or stable_hash(title),
                        "query_group": task.group_id,
                        "query": task.query,
                        "identifiers": {"doi": doi},
                        "title": title,
                        "abstract": abstract,
                        "abstract_sentences": sentence_split(abstract, "A"),
                        "authors": authors,
                        "journal": normalize_space((rec.get("container-title") or [""])[0]) or None,
                        "publisher": rec.get("publisher"),
                        "volume": rec.get("volume"),
                        "issue": rec.get("issue"),
                        "pages": rec.get("page"),
                        "article_number": rec.get("article-number"),
                        "issn": rec.get("ISSN") or [],
                        "published_date": effective,
                        "published_date_precision": precision,
                        "availability_date": effective,
                        "availability_basis": basis or discovery_mode,
                        "online_date": parse_date_loose(online_date)[0],
                        "print_date": parse_date_loose(print_date)[0],
                        "issue_date": parse_date_loose(issued_date)[0],
                        "source_created_date": parse_date_loose(created_date)[0],
                        "source_indexed_date": parse_date_loose(indexed_date)[0],
                        "source_deposited_date": parse_date_loose(deposited_date)[0],
                        "publication_types": [rec.get("type")] if rec.get("type") else [],
                        "relations": {"relation": rec.get("relation"), "update_to": rec.get("update-to")},
                        "discovery_relevance": relevance,
                        "crossref_score": rec.get("score"),
                        "fulltext_links": rec.get("link") or [],
                        "licenses": rec.get("license") or [],
                        "url": rec.get("URL"),
                        "retrieved_at": utc_now_iso(),
                    }
                )
    if result.errors and not result.records:
        result.status = "failed"
    elif result.errors:
        result.status = "partial"
    return result


def collect_semantic_scholar(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    headers: dict[str, str] = {}
    key = os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")
    if key:
        headers["x-api-key"] = key
    fields = "paperId,externalIds,title,abstract,authors,venue,year,publicationDate,publicationTypes,url,openAccessPdf,citationCount,referenceCount"
    for task in tasks:
        result.query_count += 1
        params = {"query": task.query, "limit": min(task.limit, 30), "fields": fields, "publicationDateOrYear": f"{window.start}:{window.end}"}
        data, audit = client.get_json(source["base_url"] + "paper/search", params=params, headers=headers)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or f"Semantic Scholar search failed for {task.group_id}")
            continue
        for rec in data.get("data", []):
            title = normalize_space(rec.get("title"))
            abstract = normalize_space(rec.get("abstract")) or None
            relevance = _title_relevance(title, abstract, task)
            if relevance < 0.65:
                continue
            pub_date, precision = parse_date_loose(rec.get("publicationDate") or rec.get("year"))
            if not pub_date or not in_window(pub_date, window.start, window.end):
                continue
            ids = rec.get("externalIds") or {}
            result.records.append(
                {
                    "record_type": "scholarly_source",
                    "source_id": "semantic_scholar",
                    "source_record_id": rec.get("paperId") or stable_hash(title),
                    "query_group": task.group_id,
                    "query": task.query,
                    "identifiers": {"semantic_scholar_id": rec.get("paperId"), "doi": canonicalize_doi(ids.get("DOI")), "pmid": ids.get("PubMed"), "pmcid": ids.get("PubMedCentral"), "arxiv": ids.get("ArXiv")},
                    "title": title,
                    "abstract": abstract,
                    "abstract_sentences": sentence_split(abstract, "A"),
                    "authors": [normalize_space(x.get("name")) for x in rec.get("authors", []) if normalize_space(x.get("name"))],
                    "journal": normalize_space(rec.get("venue")) or None,
                    "published_date": pub_date,
                    "published_date_precision": precision,
                    "availability_date": pub_date,
                    "availability_basis": "publication_date",
                    "publication_types": rec.get("publicationTypes") or [],
                    "citation_count": rec.get("citationCount"),
                    "reference_count": rec.get("referenceCount"),
                    "open_access_pdf": rec.get("openAccessPdf"),
                    "discovery_relevance": relevance,
                    "url": rec.get("url"),
                    "retrieved_at": utc_now_iso(),
                }
            )
    if result.errors and not result.records:
        result.status = "failed"
    elif result.errors:
        result.status = "partial"
    return result


def collect_scholarly(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    adapter = source.get("adapter")
    if adapter == "pubmed":
        return collect_pubmed(client, source, tasks, window)
    if adapter == "europe_pmc":
        return collect_europe_pmc(client, source, tasks, window)
    if adapter == "crossref":
        return collect_crossref(client, source, tasks, window)
    if adapter == "semantic_scholar":
        return collect_semantic_scholar(client, source, tasks, window)
    return SourceResult(source.get("source_id", "unknown"), "disabled", errors=[f"Unsupported scholarly adapter: {adapter}"])
