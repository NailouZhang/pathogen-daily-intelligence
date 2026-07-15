from __future__ import annotations

from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from .utils import canonicalize_doi, canonicalize_url, normalize_title, stable_hash, utc_now_iso

SOURCE_FIELD_PRIORITY = {
    "identifiers.doi": ["pubmed", "crossref", "europe_pmc", "semantic_scholar"],
    "abstract": ["pubmed", "europe_pmc", "semantic_scholar", "crossref"],
    "journal": ["pubmed", "crossref", "europe_pmc", "semantic_scholar"],
    "published_date": ["pubmed", "europe_pmc", "crossref", "semantic_scholar"],
}


def identifier_key(rec: dict[str, Any]) -> tuple[str, str] | None:
    ids = rec.get("identifiers") or {}
    for key in ("pmid", "doi", "pmcid", "europe_pmc_id", "semantic_scholar_id", "arxiv"):
        value = ids.get(key)
        if value:
            return key, str(value).casefold()
    return None


def scholarly_candidate_key(rec: dict[str, Any]) -> str:
    ident = identifier_key(rec)
    if ident:
        return f"{ident[0]}:{ident[1]}"
    title = normalize_title(rec.get("title"))
    first = (rec.get("authors") or [""])[0].casefold()
    year = (rec.get("published_date") or "")[:4]
    return f"bib:{stable_hash(title + '|' + first + '|' + year)}"


def _select_canonical_title(records: list[dict[str, Any]]) -> dict[str, Any]:
    # Prefer authoritative records, then richer titles. Crossref broad-search
    # candidates must not win merely because their title is longer.
    rank = {"pubmed": 0, "europe_pmc": 1, "semantic_scholar": 2, "crossref": 3}
    return sorted(records, key=lambda r: (rank.get(r.get("source_id"), 9), -len(r.get("title") or "")))[0]


def _compatible_with_title(rec: dict[str, Any], canonical_title: str) -> bool:
    if not rec.get("title") or not canonical_title:
        return True
    return token_set_ratio(normalize_title(rec.get("title")), normalize_title(canonical_title)) >= 78


def _pick_identifier(records: list[dict[str, Any]], key: str, canonical_title: str) -> Any:
    priority = SOURCE_FIELD_PRIORITY.get(f"identifiers.{key}", ["pubmed", "europe_pmc", "crossref", "semantic_scholar"])
    for source_id in priority:
        for rec in records:
            if rec.get("source_id") != source_id or not _compatible_with_title(rec, canonical_title):
                continue
            value = (rec.get("identifiers") or {}).get(key)
            if key == "doi":
                value = canonicalize_doi(value)
            if value:
                return value
    for rec in records:
        if not _compatible_with_title(rec, canonical_title):
            continue
        value = (rec.get("identifiers") or {}).get(key)
        if key == "doi":
            value = canonicalize_doi(value)
        if value:
            return value
    return None


def make_scholarly_work(records: list[dict[str, Any]], work_id: str | None = None) -> dict[str, Any]:
    if not records:
        raise ValueError("records must not be empty")
    by_source: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_source.setdefault(str(record.get("source_id")), []).append(record)

    title_rec = _select_canonical_title(records)
    canonical_title = title_rec.get("title") or ""
    identifiers = {
        key: _pick_identifier(records, key, canonical_title)
        for key in ("doi", "pmid", "pmcid", "europe_pmc_id", "semantic_scholar_id", "arxiv")
    }
    compatible_records = [record for record in records if _compatible_with_title(record, canonical_title)] or records
    abstract_rec = max(compatible_records, key=lambda r: len(r.get("abstract") or ""))
    author_rec = max(compatible_records, key=lambda r: len(r.get("authors") or []))
    date_rec = next(
        (
            rec
            for source_id in ["pubmed", "europe_pmc", "crossref", "semantic_scholar"]
            for rec in by_source.get(source_id, [])
            if _compatible_with_title(rec, canonical_title) and rec.get("published_date")
        ),
        title_rec,
    )
    journal_rec = next(
        (
            rec
            for source_id in ["pubmed", "crossref", "europe_pmc", "semantic_scholar"]
            for rec in by_source.get(source_id, [])
            if _compatible_with_title(rec, canonical_title) and rec.get("journal")
        ),
        title_rec,
    )
    work_id = work_id or "work-" + stable_hash(
        "|".join(str(identifiers.get(k) or "") for k in identifiers) + "|" + normalize_title(canonical_title)
    )

    conflicts: dict[str, list[Any]] = {}
    for field in ("title", "journal", "published_date", "online_date", "print_date"):
        values: list[Any] = []
        for rec in records:
            value = rec.get(field)
            if value and value not in values:
                values.append(value)
        if len(values) > 1:
            conflicts[field] = values
    excluded_identifier_records = [
        {
            "source_id": rec.get("source_id"),
            "source_record_id": rec.get("source_record_id"),
            "title": rec.get("title"),
            "identifiers": rec.get("identifiers"),
            "reason": "TITLE_INCOMPATIBLE_WITH_CANONICAL_WORK",
        }
        for rec in records
        if not _compatible_with_title(rec, canonical_title)
    ]
    if excluded_identifier_records:
        conflicts["excluded_identifier_records"] = excluded_identifier_records

    first_seen = min((r.get("retrieved_at") for r in records if r.get("retrieved_at")), default=utc_now_iso())
    bibliography_dates = {
        "published_date": date_rec.get("published_date"),
        "published_date_precision": date_rec.get("published_date_precision", "unknown"),
        "availability_date": date_rec.get("availability_date") or date_rec.get("published_date"),
        "availability_basis": date_rec.get("availability_basis"),
        "online_date": next((r.get("online_date") for r in compatible_records if r.get("online_date")), None),
        "print_date": next((r.get("print_date") for r in compatible_records if r.get("print_date")), None),
        "issue_date": next((r.get("issue_date") for r in compatible_records if r.get("issue_date")), None),
        "source_created_date": next((r.get("source_created_date") for r in compatible_records if r.get("source_created_date")), None),
        "source_indexed_date": next((r.get("source_indexed_date") for r in compatible_records if r.get("source_indexed_date")), None),
    }
    return {
        "schema_version": "1.0",
        "work_id": work_id,
        "entity_version": 1,
        "identifiers": identifiers,
        "title": {
            "original": canonical_title,
            "translated_zh": next((r.get("translated_title_zh") for r in records if r.get("translated_title_zh")), None),
            "language": title_rec.get("language"),
        },
        "abstract": {
            "original": abstract_rec.get("abstract"),
            "translated_zh": next((r.get("translated_abstract_zh") for r in records if r.get("translated_abstract_zh")), None),
            "source": abstract_rec.get("source_id"),
            "sentences": abstract_rec.get("abstract_sentences") or [],
            "availability_status": "available" if abstract_rec.get("abstract") else "not_retrieved",
        },
        "display_summary": {
            "zh": next((r.get("display_summary_zh") for r in records if r.get("display_summary_zh")), None),
            "en": next((r.get("display_summary_en") for r in records if r.get("display_summary_en")), None),
        },
        "translation_audit": {},
        "authors": author_rec.get("authors") or [],
        "affiliations": [],
        "bibliography": {
            "journal": journal_rec.get("journal"),
            "publisher": next((r.get("publisher") for r in compatible_records if r.get("publisher")), None),
            "issn": next((r.get("issn") for r in compatible_records if r.get("issn")), []),
            "volume": next((r.get("volume") for r in compatible_records if r.get("volume")), None),
            "issue": next((r.get("issue") for r in compatible_records if r.get("issue")), None),
            "pages": next((r.get("pages") for r in compatible_records if r.get("pages")), None),
            "article_number": next((r.get("article_number") for r in compatible_records if r.get("article_number")), None),
            **bibliography_dates,
            "publication_types": sorted({x for r in compatible_records for x in (r.get("publication_types") or []) if x}),
        },
        "entities": {
            "pathogens": [],
            "diseases": [],
            "hosts": [],
            "countries": [],
            "populations": [],
            "topics": sorted({r.get("query_group") for r in compatible_records if r.get("query_group")}),
        },
        "relationships": [],
        "recovery_context": next((dict(r.get("recovery_context") or {}) for r in records if r.get("recovery_context")), {}),
        "field_provenance": {
            "title": title_rec.get("source_id"),
            "abstract": abstract_rec.get("source_id"),
            "authors": author_rec.get("source_id"),
            "published_date": date_rec.get("source_id"),
            "journal": journal_rec.get("source_id"),
        },
        "source_records": [
            {
                "source_id": r.get("source_id"),
                "source_record_id": r.get("source_record_id"),
                "url": r.get("url"),
                "query_group": r.get("query_group"),
                "retrieved_at": r.get("retrieved_at"),
                "full_text_available": r.get("full_text_available"),
                "open_access": r.get("open_access"),
                "published_date": r.get("published_date"),
                "availability_basis": r.get("availability_basis"),
                "discovery_relevance": r.get("discovery_relevance"),
                "fulltext_links": r.get("fulltext_links") or [],
                "licenses": r.get("licenses") or [],
                "open_access_pdf": r.get("open_access_pdf"),
            }
            for r in records
        ],
        "conflicts": conflicts,
        "ai_analysis": next((r.get("demo_ai_analysis") for r in records if r.get("demo_ai_analysis")), None),
        "full_text": {"available": False, "source": None, "url": None, "sections": [], "availability_status": "not_retrieved", "evidence_level": None},
        "evidence_acquisition": {
            "status": "abstract_only" if bool(abstract_rec.get("abstract")) else "metadata_only",
            "evidence_level": "E1" if bool(abstract_rec.get("abstract")) else "E0",
            "analysis_eligible": bool(abstract_rec.get("abstract")),
            "attempt_count": 0,
            "retry_recommended": not bool(abstract_rec.get("abstract")),
            "reason_codes": [] if bool(abstract_rec.get("abstract")) else ["ABSTRACT_NOT_RETRIEVED"],
        },
        "processing_audit": {},
        "quality": {
            "has_abstract": bool(abstract_rec.get("abstract")),
            "source_count": len([r for r in records if r.get("source_id") != "recovery_queue"]) or 1,
            "compatible_source_count": len([r for r in compatible_records if r.get("source_id") != "recovery_queue"]) or 1,
            "open_full_text_available": any(bool(r.get("full_text_available")) for r in compatible_records),
            "identifier_conflict": bool(excluded_identifier_records),
        },
        "first_seen_at": first_seen,
        "last_seen_at": utc_now_iso(),
        "last_updated_at": utc_now_iso(),
    }


def normalize_news_article(rec: dict[str, Any]) -> dict[str, Any]:
    article_id = "article-" + stable_hash(rec.get("canonical_url") or rec.get("url") or rec.get("title") or "")
    excerpt = rec.get("excerpt")
    return {
        "schema_version": "1.0",
        "article_id": article_id,
        "canonical_url": canonicalize_url(rec.get("canonical_url") or rec.get("url")),
        "original_url": rec.get("url"),
        "source": {
            "source_id": rec.get("source_id"),
            "name": rec.get("source_name"),
            "domain": rec.get("domain"),
            "organization_type": rec.get("source_category"),
            "reliability_tier": rec.get("source_tier", "unknown"),
        },
        "title": {"original": rec.get("title") or "", "translated_zh": rec.get("translated_title_zh"), "language": rec.get("language")},
        "published_at": rec.get("published_at"),
        "first_seen_at": rec.get("retrieved_at"),
        "last_seen_at": rec.get("retrieved_at"),
        "content": {
            "excerpt": excerpt,
            "original_excerpt": excerpt,
            "analysis_text": None,
            "translation_text": excerpt,
            "translated_excerpt_zh": rec.get("translated_excerpt_zh"),
            "sentences": rec.get("content_sentences") or [],
            "extraction": None,
            "availability_status": "snippet_only" if excerpt else "title_only",
            "coverage_level": "title_or_snippet_only",
        },
        "display_summary": {"zh": rec.get("display_summary_zh"), "en": rec.get("display_summary_en")},
        "translation_audit": {},
        "entities": {
            "pathogens": [],
            "diseases": [],
            "hosts": [],
            "event_type": None,
            "country": None,
            "admin1": None,
            "city": None,
            "event_date": None,
            "confirmed_cases": None,
            "probable_cases": None,
            "suspected_cases": None,
            "deaths": None,
        },
        "official_references": [],
        "fingerprints": {
            "title": stable_hash(normalize_title(rec.get("title"))),
            "url": stable_hash(canonicalize_url(rec.get("canonical_url") or rec.get("url"))),
        },
        "classification": {},
        "ai_analysis": rec.get("demo_ai_analysis"),
        "event_id": None,
        "retrieval_audit": {
            "query_group": rec.get("query_group"),
            "query": rec.get("query"),
            "original_source": rec.get("original_source"),
            "content_fetch": None,
        },
        "processing_audit": {},
    }
