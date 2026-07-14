from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from typing import Any

from .base import SourceResult
from ..dates import CoverageWindow
from ..http import HttpClient
from ..query_planner import QueryTask
from ..utils import canonicalize_doi, normalize_space, parse_date_loose, sentence_split, stable_hash, utc_now_iso


def _audit_dict(audit: Any) -> dict[str, Any]:
    return audit.__dict__.copy()


def _pubmed_date(article: ET.Element) -> tuple[str | None, str]:
    for path in [".//ArticleDate", ".//PubDate", ".//DateCompleted", ".//DateRevised"]:
        node = article.find(path)
        if node is None:
            continue
        year = normalize_space(node.findtext("Year"))
        month = normalize_space(node.findtext("Month"))
        day = normalize_space(node.findtext("Day"))
        if year and month and day and month.isdigit() and day.isdigit():
            return f"{int(year):04d}-{int(month):02d}-{int(day):02d}", "day"
        if year and month:
            month_map = {m: i for i, m in enumerate(["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"], 1)}
            m = int(month) if month.isdigit() else month_map.get(month[:3].title())
            if m:
                return f"{int(year):04d}-{m:02d}", "month"
        if year:
            return year, "year"
        medline = normalize_space(node.findtext("MedlineDate"))
        if medline:
            return parse_date_loose(medline)
    return None, "unknown"


def collect_pubmed(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    api_key = os.getenv("NCBI_API_KEY", "")
    for task in tasks:
        result.query_count += 1
        date_clause = f'("{window.start}"[Date - Publication] : "{window.end}"[Date - Publication])'
        query = f"({task.query}) AND {date_clause}"
        params = {"db":"pubmed","term":query,"retmode":"json","retmax":task.limit,"sort":"pub date"}
        if api_key: params["api_key"] = api_key
        data, audit = client.get_json(source["base_url"] + "esearch.fcgi", params=params)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or f"PubMed search failed for {task.group_id}")
            continue
        ids = data.get("esearchresult", {}).get("idlist", [])
        if not ids:
            continue
        params2 = {"db":"pubmed","id":",".join(ids),"retmode":"xml"}
        if api_key: params2["api_key"] = api_key
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
            title = normalize_space("".join(article.find(".//ArticleTitle").itertext()) if article.find(".//ArticleTitle") is not None else "")
            abstract = " ".join(normalize_space("".join(x.itertext())) for x in article.findall(".//Abstract/AbstractText"))
            doi = None; pmcid = None
            for aid in article.findall(".//ArticleId"):
                typ = aid.attrib.get("IdType", "").lower(); val = normalize_space(aid.text)
                if typ == "doi": doi = canonicalize_doi(val)
                if typ == "pmc": pmcid = val
            pub_date, precision = _pubmed_date(article)
            authors = []
            for a in article.findall(".//Author"):
                name = normalize_space(" ".join(x for x in [a.findtext("ForeName"), a.findtext("LastName")] if x))
                if name: authors.append(name)
            journal = normalize_space(article.findtext(".//Journal/Title")) or None
            result.records.append({
                "record_type":"scholarly_source","source_id":"pubmed","source_record_id":pmid or stable_hash(title),"query_group":task.group_id,"query":task.query,
                "identifiers":{"pmid":pmid,"pmcid":pmcid,"doi":doi},"title":title,"abstract":abstract or None,"abstract_sentences":sentence_split(abstract,"A"),
                "authors":authors,"journal":journal,"published_date":pub_date,"published_date_precision":precision,"language":normalize_space(article.findtext(".//Language")) or None,
                "url":f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,"retrieved_at":utc_now_iso()
            })
    if result.errors and not result.records: result.status = "failed"
    elif result.errors: result.status = "partial"
    return result


def collect_europe_pmc(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    for task in tasks:
        result.query_count += 1
        query = f'({task.query}) AND FIRST_PDATE:[{window.start} TO {window.end}]'
        params = {"query":query,"format":"json","pageSize":task.limit,"resultType":"core","sort":"FIRST_PDATE_D desc"}
        data, audit = client.get_json(source["base_url"] + "search", params=params)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or f"Europe PMC search failed for {task.group_id}")
            continue
        for rec in data.get("resultList", {}).get("result", []):
            title = normalize_space(rec.get("title"))
            pub_date, precision = parse_date_loose(rec.get("firstPublicationDate") or rec.get("electronicPublicationDate") or rec.get("journalInfo", {}).get("printPublicationDate"))
            doi = canonicalize_doi(rec.get("doi"))
            result.records.append({
                "record_type":"scholarly_source","source_id":"europe_pmc","source_record_id":rec.get("id") or stable_hash(title),"query_group":task.group_id,"query":task.query,
                "identifiers":{"pmid":rec.get("pmid"),"pmcid":rec.get("pmcid"),"doi":doi,"europe_pmc_id":rec.get("id")},
                "title":title,"abstract":normalize_space(rec.get("abstractText")) or None,"abstract_sentences":sentence_split(rec.get("abstractText"),"A"),
                "authors":[normalize_space(x.get("fullName")) for x in rec.get("authorList", {}).get("author", []) if normalize_space(x.get("fullName"))],
                "journal":normalize_space(rec.get("journalTitle") or rec.get("journalInfo", {}).get("journal", {}).get("title")) or None,
                "published_date":pub_date,"published_date_precision":precision,"language":rec.get("language"),"is_preprint":str(rec.get("pubType", "")).casefold()=="preprint",
                "open_access":rec.get("isOpenAccess"),"full_text_available":rec.get("inEPMC"),"url":f"https://europepmc.org/article/{rec.get('source','MED')}/{rec.get('id')}" if rec.get("id") else None,"retrieved_at":utc_now_iso()
            })
    if result.errors and not result.records: result.status = "failed"
    elif result.errors: result.status = "partial"
    return result


def collect_crossref(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    mailto = os.getenv("CROSSREF_MAILTO", "")
    for task in tasks:
        result.query_count += 1
        params = {"query.bibliographic":task.query,"filter":f"from-pub-date:{window.start},until-pub-date:{window.end}","rows":min(task.limit,30),"select":"DOI,title,author,published-online,published-print,created,container-title,publisher,volume,issue,page,article-number,ISSN,type,URL,relation,update-to,subtype,abstract,language"}
        if mailto: params["mailto"] = mailto
        data, audit = client.get_json(source["base_url"] + "works", params=params)
        result.audits.append(_audit_dict(audit))
        if not data:
            result.errors.append(audit.error or f"Crossref search failed for {task.group_id}")
            continue
        for rec in data.get("message", {}).get("items", []):
            title = normalize_space((rec.get("title") or [""])[0])
            date_parts = None
            for key in ("published-online","published-print","created"):
                date_parts = (rec.get(key) or {}).get("date-parts")
                if date_parts: break
            pub_date, precision = parse_date_loose(date_parts[0] if date_parts else None)
            authors=[]
            for a in rec.get("author",[]):
                name=normalize_space(" ".join(x for x in [a.get("given"),a.get("family")] if x))
                if name: authors.append(name)
            abstract = re.sub(r"<[^>]+>", " ", rec.get("abstract") or "")
            result.records.append({
                "record_type":"scholarly_source","source_id":"crossref","source_record_id":canonicalize_doi(rec.get("DOI")) or stable_hash(title),"query_group":task.group_id,"query":task.query,
                "identifiers":{"doi":canonicalize_doi(rec.get("DOI"))},"title":title,"abstract":normalize_space(abstract) or None,"abstract_sentences":sentence_split(abstract,"A"),"authors":authors,
                "journal":normalize_space((rec.get("container-title") or [""])[0]) or None,"publisher":rec.get("publisher"),"volume":rec.get("volume"),"issue":rec.get("issue"),"pages":rec.get("page"),"article_number":rec.get("article-number"),
                "issn":rec.get("ISSN") or [],"published_date":pub_date,"published_date_precision":precision,"publication_types":[rec.get("type")] if rec.get("type") else [],"relations":{"relation":rec.get("relation"),"update_to":rec.get("update-to")},
                "url":rec.get("URL"),"retrieved_at":utc_now_iso()
            })
    if result.errors and not result.records: result.status = "failed"
    elif result.errors: result.status = "partial"
    return result


def collect_semantic_scholar(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    result = SourceResult(source["source_id"], "success")
    headers={}
    key=os.getenv("SEMANTIC_SCHOLAR_API_KEY","")
    if key: headers["x-api-key"] = key
    fields="paperId,externalIds,title,abstract,authors,venue,year,publicationDate,publicationTypes,url,openAccessPdf,citationCount,referenceCount"
    for task in tasks:
        result.query_count += 1
        params={"query":task.query,"limit":min(task.limit,20),"fields":fields,"publicationDateOrYear":f"{window.start}:{window.end}"}
        data,audit=client.get_json(source["base_url"]+"paper/search",params=params,headers=headers)
        result.audits.append(_audit_dict(audit))
        if not data and audit.status_code==400:
            params.pop("publicationDateOrYear",None)
            data,audit2=client.get_json(source["base_url"]+"paper/search",params=params,headers=headers)
            result.audits.append(_audit_dict(audit2))
            audit=audit2
        if not data:
            result.errors.append(audit.error or f"Semantic Scholar search failed for {task.group_id}")
            continue
        for rec in data.get("data",[]):
            pub_date,precision=parse_date_loose(rec.get("publicationDate") or rec.get("year"))
            ids=rec.get("externalIds") or {}
            title=normalize_space(rec.get("title"))
            result.records.append({
                "record_type":"scholarly_source","source_id":"semantic_scholar","source_record_id":rec.get("paperId") or stable_hash(title),"query_group":task.group_id,"query":task.query,
                "identifiers":{"semantic_scholar_id":rec.get("paperId"),"doi":canonicalize_doi(ids.get("DOI")),"pmid":ids.get("PubMed"),"pmcid":ids.get("PubMedCentral"),"arxiv":ids.get("ArXiv")},
                "title":title,"abstract":normalize_space(rec.get("abstract")) or None,"abstract_sentences":sentence_split(rec.get("abstract"),"A"),"authors":[normalize_space(x.get("name")) for x in rec.get("authors",[]) if normalize_space(x.get("name"))],
                "journal":normalize_space(rec.get("venue")) or None,"published_date":pub_date,"published_date_precision":precision,"publication_types":rec.get("publicationTypes") or [],"citation_count":rec.get("citationCount"),"reference_count":rec.get("referenceCount"),"url":rec.get("url"),"retrieved_at":utc_now_iso()
            })
    if result.errors and not result.records: result.status="failed"
    elif result.errors: result.status="partial"
    return result


def collect_scholarly(client: HttpClient, source: dict[str, Any], tasks: list[QueryTask], window: CoverageWindow) -> SourceResult:
    adapter=source.get("adapter")
    if adapter=="pubmed": return collect_pubmed(client,source,tasks,window)
    if adapter=="europe_pmc": return collect_europe_pmc(client,source,tasks,window)
    if adapter=="crossref": return collect_crossref(client,source,tasks,window)
    if adapter=="semantic_scholar": return collect_semantic_scholar(client,source,tasks,window)
    return SourceResult(source.get("source_id","unknown"),"disabled",errors=[f"Unsupported scholarly adapter: {adapter}"])
