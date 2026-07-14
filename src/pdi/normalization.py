from __future__ import annotations

from collections import defaultdict
from typing import Any

from .utils import canonicalize_doi, canonicalize_url, normalize_title, stable_hash, utc_now_iso

SOURCE_FIELD_PRIORITY = {
    "identifiers.doi": ["pubmed", "crossref", "europe_pmc", "semantic_scholar"],
    "abstract": ["pubmed", "europe_pmc", "semantic_scholar", "crossref"],
    "journal": ["pubmed", "crossref", "europe_pmc", "semantic_scholar"],
    "published_date": ["pubmed", "europe_pmc", "crossref", "semantic_scholar"],
}


def identifier_key(rec: dict[str, Any]) -> tuple[str, str] | None:
    ids=rec.get("identifiers") or {}
    for key in ("pmid","doi","pmcid","europe_pmc_id","semantic_scholar_id","arxiv"):
        value=ids.get(key)
        if value:
            return key,str(value).casefold()
    return None


def scholarly_candidate_key(rec: dict[str, Any]) -> str:
    ident=identifier_key(rec)
    if ident: return f"{ident[0]}:{ident[1]}"
    title=normalize_title(rec.get("title"))
    first=(rec.get("authors") or [""])[0].casefold()
    year=(rec.get("published_date") or "")[:4]
    return f"bib:{stable_hash(title+'|'+first+'|'+year)}"


def make_scholarly_work(records: list[dict[str, Any]], work_id: str | None = None) -> dict[str, Any]:
    if not records: raise ValueError("records must not be empty")
    by_source={r.get("source_id"):r for r in records}
    identifiers={}
    for key in ("doi","pmid","pmcid","europe_pmc_id","semantic_scholar_id","arxiv"):
        candidates=[]
        for rec in records:
            value=(rec.get("identifiers") or {}).get(key)
            if key=="doi": value=canonicalize_doi(value)
            if value and value not in candidates: candidates.append(value)
        identifiers[key]=candidates[0] if candidates else None
    title_rec=max(records,key=lambda r:len(r.get("title") or ""))
    abstract_rec=max(records,key=lambda r:len(r.get("abstract") or ""))
    author_rec=max(records,key=lambda r:len(r.get("authors") or []))
    date_rec=next((by_source[s] for s in ["pubmed","europe_pmc","crossref","semantic_scholar"] if s in by_source and by_source[s].get("published_date")),title_rec)
    journal_rec=next((by_source[s] for s in ["pubmed","crossref","europe_pmc","semantic_scholar"] if s in by_source and by_source[s].get("journal")),title_rec)
    work_id=work_id or "work-"+stable_hash("|".join(str(identifiers.get(k) or "") for k in identifiers)+"|"+normalize_title(title_rec.get("title")))
    conflicts={}
    for field in ("title","journal","published_date"):
        values=[]
        for rec in records:
            val=rec.get(field)
            if val and val not in values: values.append(val)
        if len(values)>1: conflicts[field]=values
    first_seen=min((r.get("retrieved_at") for r in records if r.get("retrieved_at")),default=utc_now_iso())
    return {
        "schema_version":"1.0","work_id":work_id,"entity_version":1,"identifiers":identifiers,
        "title":{"original":title_rec.get("title") or "","translated_zh":None,"language":title_rec.get("language")},
        "abstract":{"original":abstract_rec.get("abstract"),"source":abstract_rec.get("source_id"),"sentences":abstract_rec.get("abstract_sentences") or []},
        "authors":author_rec.get("authors") or [],"affiliations":[],
        "bibliography":{"journal":journal_rec.get("journal"),"publisher":next((r.get("publisher") for r in records if r.get("publisher")),None),"issn":next((r.get("issn") for r in records if r.get("issn")),[]),"volume":next((r.get("volume") for r in records if r.get("volume")),None),"issue":next((r.get("issue") for r in records if r.get("issue")),None),"pages":next((r.get("pages") for r in records if r.get("pages")),None),"article_number":next((r.get("article_number") for r in records if r.get("article_number")),None),"published_date":date_rec.get("published_date"),"published_date_precision":date_rec.get("published_date_precision","unknown"),"publication_types":sorted({x for r in records for x in (r.get("publication_types") or []) if x})},
        "entities":{"pathogens":[],"diseases":[],"hosts":[],"countries":[],"populations":[],"topics":sorted({r.get("query_group") for r in records if r.get("query_group")})},
        "relationships":[],"field_provenance":{"title":title_rec.get("source_id"),"abstract":abstract_rec.get("source_id"),"authors":author_rec.get("source_id"),"published_date":date_rec.get("source_id"),"journal":journal_rec.get("source_id")},
        "source_records":[{"source_id":r.get("source_id"),"source_record_id":r.get("source_record_id"),"url":r.get("url"),"query_group":r.get("query_group"),"retrieved_at":r.get("retrieved_at")} for r in records],
        "conflicts":conflicts,"ai_analysis":None,"quality":{"has_abstract":bool(abstract_rec.get("abstract")),"source_count":len(records)},"first_seen_at":first_seen,"last_seen_at":utc_now_iso(),"last_updated_at":utc_now_iso()
    }


def normalize_news_article(rec: dict[str, Any]) -> dict[str, Any]:
    article_id="article-"+stable_hash(rec.get("canonical_url") or rec.get("url") or rec.get("title") or "")
    return {"schema_version":"1.0","article_id":article_id,"canonical_url":canonicalize_url(rec.get("canonical_url") or rec.get("url")),"original_url":rec.get("url"),"source":{"source_id":rec.get("source_id"),"name":rec.get("source_name"),"domain":rec.get("domain"),"organization_type":rec.get("source_category"),"reliability_tier":rec.get("source_tier","unknown")},"title":{"original":rec.get("title") or "","translated_zh":None,"language":rec.get("language")},"published_at":rec.get("published_at"),"first_seen_at":rec.get("retrieved_at"),"last_seen_at":rec.get("retrieved_at"),"content":{"excerpt":rec.get("excerpt"),"sentences":rec.get("content_sentences") or []},"entities":{"pathogens":[],"diseases":[],"hosts":[],"event_type":None,"country":None,"admin1":None,"city":None,"event_date":None,"confirmed_cases":None,"probable_cases":None,"suspected_cases":None,"deaths":None},"official_references":[],"fingerprints":{"title":stable_hash(normalize_title(rec.get("title"))),"url":stable_hash(canonicalize_url(rec.get("canonical_url") or rec.get("url")))},"classification":{},"ai_analysis":None,"event_id":None,"retrieval_audit":{"query_group":rec.get("query_group"),"query":rec.get("query"),"original_source":rec.get("original_source")}}
