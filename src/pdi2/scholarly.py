from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any
from urllib.parse import quote

from .dates import choose_availability_date
from .http import HttpClient
from .utils import clean_space, extract_doi, safe_date_string, strip_tags, unique_strings


def _xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    return clean_space("".join(node.itertext()))


def _date_from_parts(year: Any, month: Any = 1, day: Any = 1) -> str | None:
    months = {name.lower(): i for i, name in enumerate(
        ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    )}
    try:
        y = int(str(year))
        m_raw = str(month or 1)
        m = months.get(m_raw[:3].lower(), int(m_raw) if m_raw.isdigit() else 1)
        d = int(str(day or 1))
        return date(y, m, d).isoformat()
    except (TypeError, ValueError):
        return None


def _pubmed_search(http: HttpClient, query: str, start: date, end: date, api_key: str, limit: int) -> list[str]:
    date_exprs = [
        f'("{start:%Y/%m/%d}"[CRDT] : "{end:%Y/%m/%d}"[CRDT])',
        f'("{start:%Y/%m/%d}"[EDAT] : "{end:%Y/%m/%d}"[EDAT])',
        f'("{start:%Y/%m/%d}"[EPDAT] : "{end:%Y/%m/%d}"[EPDAT])',
        f'("{start:%Y/%m/%d}"[PDAT] : "{end:%Y/%m/%d}"[PDAT])',
    ]
    term = f"({query}) AND ({' OR '.join(date_exprs)})"
    params = {"db": "pubmed", "term": term, "retmode": "json", "retmax": limit, "sort": "pub date"}
    if api_key:
        params["api_key"] = api_key
    payload = http.get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", params=params)
    return list(payload.get("esearchresult", {}).get("idlist", []))


def _pubmed_fetch(http: HttpClient, pmids: list[str], api_key: str) -> list[dict[str, Any]]:
    if not pmids:
        return []
    params = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
    if api_key:
        params["api_key"] = api_key
    raw = http.get_text("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params=params)
    root = ET.fromstring(raw)
    output: list[dict[str, Any]] = []
    for article_node in root.findall(".//PubmedArticle"):
        citation = article_node.find("MedlineCitation")
        article = citation.find("Article") if citation is not None else None
        if article is None:
            continue
        journal = article.find("Journal")
        issue = journal.find("JournalIssue") if journal is not None else None
        pub_date = issue.find("PubDate") if issue is not None else None
        title = _xml_text(article.find("ArticleTitle"))
        abstract_parts = []
        for part in article.findall("Abstract/AbstractText"):
            label = clean_space(part.attrib.get("Label"))
            text = _xml_text(part)
            abstract_parts.append(f"{label}: {text}" if label else text)
        authors = []
        for author in article.findall("AuthorList/Author"):
            collective = _xml_text(author.find("CollectiveName"))
            name = collective or clean_space(f"{_xml_text(author.find('ForeName'))} {_xml_text(author.find('LastName'))}")
            if name:
                authors.append(name)
        ids: dict[str, str] = {}
        pubmed_data = article_node.find("PubmedData")
        if pubmed_data is not None:
            for node in pubmed_data.findall("ArticleIdList/ArticleId"):
                ids[node.attrib.get("IdType", "")] = _xml_text(node)
        article_dates = article.findall("ArticleDate")
        online = None
        if article_dates:
            online = _date_from_parts(
                _xml_text(article_dates[0].find("Year")),
                _xml_text(article_dates[0].find("Month")),
                _xml_text(article_dates[0].find("Day")),
            )
        print_date = _date_from_parts(
            _xml_text(pub_date.find("Year")) if pub_date is not None else None,
            _xml_text(pub_date.find("Month")) if pub_date is not None else None,
            _xml_text(pub_date.find("Day")) if pub_date is not None else None,
        )
        created = None
        date_created = citation.find("DateCreated") if citation is not None else None
        if date_created is not None:
            created = _date_from_parts(_xml_text(date_created.find("Year")), _xml_text(date_created.find("Month")), _xml_text(date_created.find("Day")))
        pagination = _xml_text(article.find("Pagination/MedlinePgn"))
        types = [_xml_text(n) for n in article.findall("PublicationTypeList/PublicationType")]
        output.append({
            "source": "PubMed",
            "source_ids": {"pmid": ids.get("pubmed") or _xml_text(citation.find("PMID") if citation is not None else None), "pmcid": ids.get("pmc")},
            "doi": (ids.get("doi") or "").lower() or None,
            "title": title,
            "abstract": clean_space(" ".join(abstract_parts)),
            "authors": authors,
            "journal": _xml_text(journal.find("Title") if journal is not None else None),
            "year": int(print_date[:4]) if print_date else (int(online[:4]) if online else None),
            "volume": _xml_text(issue.find("Volume") if issue is not None else None),
            "issue": _xml_text(issue.find("Issue") if issue is not None else None),
            "pages": pagination,
            "online_date": online,
            "created_date": created,
            "published_date": print_date,
            "print_date": print_date,
            "publication_types": types,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{ids.get('pubmed') or _xml_text(citation.find('PMID'))}/",
        })
    return output


def search_pubmed(http: HttpClient, queries: list[str], start: date, end: date, api_key: str, per_query: int = 40) -> list[dict[str, Any]]:
    ids: list[str] = []
    for query in queries:
        try:
            ids.extend(_pubmed_search(http, query, start, end, api_key, per_query))
        except Exception:
            continue
    ids = unique_strings(ids)[:250]
    works: list[dict[str, Any]] = []
    for index in range(0, len(ids), 100):
        try:
            works.extend(_pubmed_fetch(http, ids[index:index + 100], api_key))
        except Exception:
            continue
    return works


def search_europe_pmc(http: HttpClient, queries: list[str], start: date, end: date, per_query: int = 50) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for query in queries:
        epmc_query = f"({query}) AND (FIRST_PDATE:[{start.isoformat()} TO {end.isoformat()}] OR CREATION_DATE:[{start.isoformat()} TO {end.isoformat()}])"
        try:
            payload = http.get_json(
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                params={"query": epmc_query, "format": "json", "resultType": "core", "pageSize": per_query},
            )
        except Exception:
            continue
        for item in payload.get("resultList", {}).get("result", []):
            authors = [a.get("fullName") for a in item.get("authorList", {}).get("author", []) if a.get("fullName")]
            full_text_urls = []
            for url_item in item.get("fullTextUrlList", {}).get("fullTextUrl", []) or []:
                if url_item.get("url"):
                    full_text_urls.append(url_item.get("url"))
            output.append({
                "source": "Europe PMC",
                "source_ids": {"pmid": item.get("pmid"), "pmcid": item.get("pmcid"), "epmc": item.get("id")},
                "doi": clean_space(item.get("doi")).lower() or None,
                "title": strip_tags(item.get("title")),
                "abstract": strip_tags(item.get("abstractText")),
                "authors": authors or unique_strings(str(item.get("authorString", "")).split(",")),
                "journal": clean_space(item.get("journalTitle")),
                "year": item.get("pubYear"),
                "volume": clean_space(item.get("journalVolume")),
                "issue": clean_space(item.get("issue")),
                "pages": clean_space(item.get("pageInfo")),
                "online_date": safe_date_string(item.get("firstPublicationDate") or item.get("electronicPublicationDate")),
                "first_publication_date": safe_date_string(item.get("firstPublicationDate")),
                "created_date": safe_date_string(item.get("creationDate")),
                "published_date": safe_date_string(item.get("journalInfo", {}).get("printPublicationDate") if isinstance(item.get("journalInfo"), dict) else None),
                "publication_types": [item.get("pubType")] if item.get("pubType") else [],
                "open_access": str(item.get("isOpenAccess", "")).upper() == "Y",
                "full_text_urls": full_text_urls,
                "url": f"https://europepmc.org/article/{item.get('source', 'MED')}/{item.get('id')}",
            })
    return output


def _crossref_date(item: dict[str, Any], key: str) -> str | None:
    parts = (((item.get(key) or {}).get("date-parts") or [[None]])[0])
    return _date_from_parts(*parts) if parts and parts[0] else None


def search_crossref(http: HttpClient, queries: list[str], start: date, end: date, mailto: str, per_query: int = 40) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    filters = [
        f"from-created-date:{start.isoformat()},until-created-date:{end.isoformat()}",
        f"from-online-pub-date:{start.isoformat()},until-online-pub-date:{end.isoformat()}",
    ]
    for query in queries:
        for filter_value in filters:
            try:
                payload = http.get_json(
                    "https://api.crossref.org/works",
                    params={"query.bibliographic": query, "filter": filter_value, "rows": per_query, "mailto": mailto},
                )
            except Exception:
                continue
            for item in payload.get("message", {}).get("items", []):
                authors = []
                for author in item.get("author", []) or []:
                    name = clean_space(f"{author.get('given', '')} {author.get('family', '')}")
                    if name:
                        authors.append(name)
                links = [link for link in item.get("link", []) or [] if link.get("URL")]
                output.append({
                    "source": "Crossref",
                    "source_ids": {},
                    "doi": clean_space(item.get("DOI")).lower() or None,
                    "title": strip_tags(" ".join(item.get("title") or [])),
                    "abstract": strip_tags(item.get("abstract")),
                    "authors": authors,
                    "journal": clean_space(" ".join(item.get("container-title") or [])),
                    "year": (_crossref_date(item, "published-online") or _crossref_date(item, "published") or "")[:4] or None,
                    "volume": clean_space(item.get("volume")),
                    "issue": clean_space(item.get("issue")),
                    "pages": clean_space(item.get("page")),
                    "online_date": _crossref_date(item, "published-online"),
                    "created_date": safe_date_string((item.get("created") or {}).get("date-time")),
                    "indexed_date": safe_date_string((item.get("indexed") or {}).get("date-time")),
                    "published_date": _crossref_date(item, "published"),
                    "print_date": _crossref_date(item, "published-print"),
                    "publication_types": [item.get("type")] if item.get("type") else [],
                    "full_text_links": links,
                    "url": clean_space(item.get("URL")) or (f"https://doi.org/{item.get('DOI')}" if item.get("DOI") else None),
                })
    return output


def search_semantic_scholar(http: HttpClient, queries: list[str], start: date, end: date, api_key: str = "", per_query: int = 40) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    headers = {"x-api-key": api_key} if api_key else {}
    fields = "paperId,title,abstract,authors,year,publicationDate,publicationTypes,journal,externalIds,openAccessPdf,url"
    for query in queries:
        try:
            payload = http.get_json(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": query, "limit": per_query, "fields": fields, "publicationDateOrYear": f"{start.isoformat()}:{end.isoformat()}"},
                headers=headers,
            )
        except Exception:
            continue
        for item in payload.get("data", []) or []:
            ext = item.get("externalIds") or {}
            journal = item.get("journal") or {}
            oa = item.get("openAccessPdf") or {}
            output.append({
                "source": "Semantic Scholar",
                "source_ids": {"semantic_scholar": item.get("paperId"), "pmid": ext.get("PubMed"), "pmcid": ext.get("PubMedCentral")},
                "doi": clean_space(ext.get("DOI")).lower() or None,
                "title": clean_space(item.get("title")),
                "abstract": clean_space(item.get("abstract")),
                "authors": [clean_space(a.get("name")) for a in item.get("authors", []) if a.get("name")],
                "journal": clean_space(journal.get("name")),
                "year": item.get("year"),
                "volume": clean_space(journal.get("volume")),
                "issue": clean_space(journal.get("pages")),
                "pages": clean_space(journal.get("pages")),
                "online_date": safe_date_string(item.get("publicationDate")),
                "publication_types": item.get("publicationTypes") or [],
                "open_access_pdf": clean_space(oa.get("url")),
                "url": clean_space(item.get("url")),
            })
    return output


def search_openalex(http: HttpClient, queries: list[str], start: date, end: date, mailto: str, per_query: int = 40) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for query in queries:
        try:
            payload = http.get_json(
                "https://api.openalex.org/works",
                params={
                    "search": query,
                    "filter": f"from_publication_date:{start.isoformat()},to_publication_date:{end.isoformat()}",
                    "per-page": per_query,
                    "mailto": mailto,
                },
            )
        except Exception:
            continue
        for item in payload.get("results", []) or []:
            primary = item.get("primary_location") or {}
            source = primary.get("source") or {}
            ids = item.get("ids") or {}
            abstract_index = item.get("abstract_inverted_index") or {}
            positions: list[tuple[int, str]] = []
            for word, indexes in abstract_index.items():
                positions.extend((int(i), word) for i in indexes)
            abstract = " ".join(word for _, word in sorted(positions))
            output.append({
                "source": "OpenAlex",
                "source_ids": {"openalex": item.get("id"), "pmid": clean_space(ids.get("pmid")).split("/")[-1] or None},
                "doi": clean_space(ids.get("doi")).removeprefix("https://doi.org/").lower() or None,
                "title": clean_space(item.get("title")),
                "abstract": clean_space(abstract),
                "authors": [clean_space(a.get("author", {}).get("display_name")) for a in item.get("authorships", []) if a.get("author", {}).get("display_name")],
                "journal": clean_space(source.get("display_name")),
                "year": item.get("publication_year"),
                "online_date": safe_date_string(item.get("publication_date")),
                "publication_types": [item.get("type")] if item.get("type") else [],
                "open_access_pdf": clean_space(primary.get("pdf_url")),
                "url": clean_space(primary.get("landing_page_url") or item.get("doi") or item.get("id")),
            })
    return output


def filter_window(records: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for record in records:
        available, basis = choose_availability_date(record, start, end)
        if not available:
            continue
        record["availability_date"] = available
        record["availability_date_basis"] = basis
        out.append(record)
    return out


def search_biorxiv_medrxiv(http: HttpClient, start: date, end: date, max_records_per_server: int = 300) -> list[dict[str, Any]]:
    """Collect recent bioRxiv and medRxiv preprint metadata, then let the shared
    pathogen relevance filter select matching records locally.
    """
    output: list[dict[str, Any]] = []
    for server in ("biorxiv", "medrxiv"):
        cursor = 0
        while cursor < max_records_per_server:
            try:
                payload = http.get_json(
                    f"https://api.biorxiv.org/details/{server}/{start.isoformat()}/{end.isoformat()}/{cursor}"
                )
            except Exception:
                break
            collection = payload.get("collection") or []
            if not collection:
                break
            for item in collection:
                doi = clean_space(item.get("doi")).lower() or None
                authors = unique_strings(re.split(r";|,", clean_space(item.get("authors"))))
                output.append({
                    "source": server,
                    "source_ids": {"preprint": doi},
                    "doi": doi,
                    "title": strip_tags(item.get("title")),
                    "abstract": strip_tags(item.get("abstract")),
                    "authors": authors,
                    "journal": server,
                    "year": int(str(item.get("date", ""))[:4]) if str(item.get("date", ""))[:4].isdigit() else None,
                    "online_date": safe_date_string(item.get("date")),
                    "first_publication_date": safe_date_string(item.get("date")),
                    "publication_types": ["preprint"],
                    "url": f"https://www.{server}.org/content/{doi}v{item.get('version', '1')}" if doi else None,
                })
            if len(collection) < 100:
                break
            cursor += 100
    return output
