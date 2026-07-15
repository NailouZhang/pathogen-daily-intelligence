from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
from dataclasses import dataclass, asdict
from typing import Any, Iterable
from urllib.parse import quote, urljoin, urlsplit
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup
from rapidfuzz.fuzz import partial_ratio, token_set_ratio

from .http import HttpClient
from .utils import canonicalize_doi, ensure_dict_field, normalize_space, normalize_title, sentence_split, utc_now_iso

_SECTION_TERMS = {
    "abstract": ("abstract", "摘要"),
    "methods": ("method", "materials and methods", "methodology", "experimental procedures", "方法", "材料与方法"),
    "results": ("result", "finding", "findings", "结果", "发现"),
    "discussion": ("discussion", "讨论"),
    "conclusion": ("conclusion", "conclusions", "summary", "结论", "总结"),
    "limitations": ("limitation", "limitations", "局限"),
}
_ALLOWED_FULLTEXT_TYPES = (
    "application/xml",
    "text/xml",
    "application/xhtml+xml",
    "text/html",
    "application/pdf",
    "text/plain",
)


@dataclass
class RetrievalCandidate:
    url: str
    source: str
    content_type: str | None = None
    content_version: str | None = None
    license: str | None = None
    open_access: bool | None = None
    intended_application: str | None = None
    priority: int = 50


def _clean_markup(value: Any) -> str | None:
    text = normalize_space(BeautifulSoup(str(value or ""), "lxml").get_text(" "))
    return text or None


def _work_title(work: dict[str, Any]) -> str:
    return normalize_space((work.get("title") or {}).get("original"))


def _identity_check(work: dict[str, Any], text: str, discovered_title: str | None = None) -> dict[str, Any]:
    expected_title = _work_title(work)
    expected_norm = normalize_title(expected_title)
    title_probe = normalize_title(discovered_title or text[:5000])
    title_score = token_set_ratio(expected_norm, title_probe) if expected_norm and title_probe else 0
    partial_score = partial_ratio(expected_norm, title_probe) if expected_norm and title_probe else 0
    doi = canonicalize_doi((work.get("identifiers") or {}).get("doi"))
    text_low = normalize_space(text).casefold()
    doi_match = bool(doi and doi.casefold() in text_low.replace("https://doi.org/", "").replace("doi:", ""))
    author_tokens = []
    for author in (work.get("authors") or [])[:6]:
        parts = normalize_space(author).casefold().split()
        if parts:
            author_tokens.append(parts[-1])
    author_hits = sum(bool(token and len(token) >= 3 and token in text_low[:12000]) for token in author_tokens)
    identifier_conflict = bool(doi_match and title_score < 45 and partial_score < 58 and author_hits == 0)
    # A DOI string can occur in references, related-article widgets, or a wrongly
    # associated provider record.  DOI presence alone therefore never overrides a
    # strong title/author contradiction.
    accepted = bool(
        title_score >= 78
        or partial_score >= 88
        or (title_score >= 62 and author_hits >= 1)
        or (doi_match and not identifier_conflict and (title_score >= 58 or partial_score >= 72 or author_hits >= 1))
    )
    return {
        "accepted": accepted,
        "identifier_conflict": identifier_conflict,
        "title_score": title_score,
        "partial_title_score": partial_score,
        "doi_match": doi_match,
        "author_hits": author_hits,
        "discovered_title": discovered_title,
    }


def _bounded_sections_from_text(text: str, max_chars: int, max_sentences: int, source_prefix: str = "F") -> list[dict[str, Any]]:
    clean = normalize_space(text)
    if not clean:
        return []
    # Preserve line boundaries for headings before normalising section content.
    raw_lines = [normalize_space(line) for line in re.split(r"[\r\n]+", text) if normalize_space(line)]
    heading_rows: list[tuple[int, str]] = []
    for i, line in enumerate(raw_lines):
        low = line.casefold().strip(" :.-")
        if len(low) > 100:
            continue
        for canonical, terms in _SECTION_TERMS.items():
            if any(low == term or low.startswith(term + ":") for term in terms):
                heading_rows.append((i, canonical))
                break
    chunks: list[tuple[str, str]] = []
    for pos, (start, canonical) in enumerate(heading_rows):
        end = heading_rows[pos + 1][0] if pos + 1 < len(heading_rows) else len(raw_lines)
        body = " ".join(raw_lines[start + 1 : end])
        if len(body) >= 120:
            chunks.append((canonical, body))
    preferred = [row for row in chunks if row[0] in {"methods", "results", "discussion", "conclusion", "limitations"}]
    if not preferred:
        # Text extraction often loses heading line breaks. Retain a bounded evidence
        # window rather than falsely labelling it as a complete structured section.
        preferred = [("full_text_extract", clean)]

    out: list[dict[str, Any]] = []
    chars = 0
    counter = 1
    for title, body in preferred:
        evidence: list[dict[str, str]] = []
        for row in sentence_split(body, "X"):
            if counter > max_sentences:
                break
            sentence = normalize_space(row.get("text"))
            if len(sentence) < 25:
                continue
            if chars + len(sentence) > max_chars:
                break
            evidence.append({"id": f"{source_prefix}{counter}", "text": sentence})
            chars += len(sentence) + 1
            counter += 1
        if evidence:
            out.append({"title": title, "text": " ".join(row["text"] for row in evidence), "sentences": evidence})
        if counter > max_sentences or chars >= max_chars:
            break
    return out


def _sections_from_jats(content: bytes | str, max_chars: int, max_sentences: int) -> tuple[str | None, list[dict[str, Any]], str | None]:
    soup = BeautifulSoup(content, "xml")
    title_node = soup.find("article-title")
    discovered_title = normalize_space(title_node.get_text(" ", strip=True)) if title_node else None
    abstract_node = soup.find("abstract")
    abstract = normalize_space(abstract_node.get_text(" ", strip=True)) if abstract_node else None
    sections: list[dict[str, Any]] = []
    counter = 1
    chars = 0
    for section in soup.find_all("sec"):
        title_tag = section.find("title", recursive=False)
        title = normalize_space(title_tag.get_text(" ", strip=True)) if title_tag else ""
        low = title.casefold()
        if not any(term in low for terms in _SECTION_TERMS.values() for term in terms):
            continue
        text = " ".join(
            normalize_space(p.get_text(" ", strip=True))
            for p in section.find_all("p")
            if len(normalize_space(p.get_text(" ", strip=True))) >= 35
        )
        evidence: list[dict[str, str]] = []
        for row in sentence_split(text, "X"):
            if counter > max_sentences or chars + len(row["text"]) > max_chars:
                break
            evidence.append({"id": f"F{counter}", "text": row["text"]})
            chars += len(row["text"]) + 1
            counter += 1
        if evidence:
            sections.append({"title": title or "full_text_section", "text": " ".join(x["text"] for x in evidence), "sentences": evidence})
        if counter > max_sentences or chars >= max_chars:
            break
    if not sections:
        body = soup.find("body")
        body_text = normalize_space(body.get_text(" ", strip=True)) if body else ""
        sections = _bounded_sections_from_text(body_text, max_chars, max_sentences)
    return abstract, sections, discovered_title


def _sections_from_bioc(data: Any, max_chars: int, max_sentences: int) -> tuple[str | None, list[dict[str, Any]], str | None]:
    documents = data if isinstance(data, list) else [data]
    passages: list[dict[str, Any]] = []
    for collection in documents:
        if isinstance(collection, dict) and isinstance(collection.get("documents"), list):
            for document in collection["documents"]:
                passages.extend(document.get("passages") or [])
        elif isinstance(collection, dict):
            passages.extend(collection.get("passages") or [])
    discovered_title = None
    abstract_parts: list[str] = []
    section_parts: list[tuple[str, str]] = []
    for passage in passages:
        text = normalize_space(passage.get("text"))
        if not text:
            continue
        infons = passage.get("infons") or {}
        section = normalize_space(infons.get("section_type") or infons.get("type") or infons.get("section") or "").casefold()
        if section in {"title", "front", "article-title"} and discovered_title is None:
            discovered_title = text
        elif "abstract" in section:
            abstract_parts.append(text)
        else:
            canonical = next((key for key, terms in _SECTION_TERMS.items() if any(term in section for term in terms)), None)
            if canonical in {"methods", "results", "discussion", "conclusion", "limitations"}:
                section_parts.append((canonical, text))
    sections: list[dict[str, Any]] = []
    counter = 1
    chars = 0
    for title, text in section_parts:
        evidence: list[dict[str, str]] = []
        for row in sentence_split(text, "X"):
            if counter > max_sentences or chars + len(row["text"]) > max_chars:
                break
            evidence.append({"id": f"F{counter}", "text": row["text"]})
            chars += len(row["text"]) + 1
            counter += 1
        if evidence:
            sections.append({"title": title, "text": " ".join(x["text"] for x in evidence), "sentences": evidence})
        if counter > max_sentences or chars >= max_chars:
            break
    return normalize_space(" ".join(abstract_parts)) or None, sections, discovered_title


def _extract_html_article(content: str, base_url: str, max_chars: int, max_sentences: int) -> tuple[str | None, list[dict[str, Any]], str | None, list[RetrievalCandidate]]:
    soup = BeautifulSoup(content, "lxml")
    title = None
    for attrs in ({"name": "citation_title"}, {"property": "og:title"}, {"name": "dc.title"}):
        node = soup.find("meta", attrs=attrs)
        if node and node.get("content"):
            title = normalize_space(node.get("content"))
            break
    if not title:
        h1 = soup.find("h1")
        title = normalize_space(h1.get_text(" ", strip=True)) if h1 else None
    abstract = None
    for attrs in ({"name": "citation_abstract"}, {"name": "dc.description"}, {"name": "description"}, {"property": "og:description"}):
        node = soup.find("meta", attrs=attrs)
        value = normalize_space(node.get("content")) if node else ""
        if len(value) >= 80:
            abstract = value
            break
    candidates: list[RetrievalCandidate] = []
    for node in soup.find_all("meta"):
        name = str(node.get("name") or "").casefold()
        url = node.get("content")
        if not url:
            continue
        if name == "citation_pdf_url":
            candidates.append(RetrievalCandidate(urljoin(base_url, url), "publisher_citation_pdf", "application/pdf", open_access=None, priority=35))
        elif name in {"citation_fulltext_html_url", "citation_full_text_html_url"}:
            candidates.append(RetrievalCandidate(urljoin(base_url, url), "publisher_fulltext_html", "text/html", open_access=None, priority=25))
    for link in soup.find_all("link"):
        href = link.get("href")
        typ = str(link.get("type") or "").casefold()
        rel = " ".join(link.get("rel") or []).casefold()
        if not href:
            continue
        if "pdf" in typ or "alternate" in rel and str(href).casefold().endswith(".pdf"):
            candidates.append(RetrievalCandidate(urljoin(base_url, href), "publisher_link_pdf", "application/pdf", open_access=None, priority=40))
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe"]):
        tag.decompose()
    containers = []
    for selector in ("article", "main", "[role='main']", "[itemprop='articleBody']", ".article-body", ".article__body", ".entry-content", ".article-content", "#article-body"):
        containers.extend(soup.select(selector))
    if not containers:
        containers = [soup]
    best = ""
    for container in containers:
        parts = []
        for node in container.find_all(["h2", "h3", "p"]):
            text = normalize_space(node.get_text(" ", strip=True))
            if len(text) >= 20:
                parts.append(text)
        candidate = "\n".join(parts)
        if len(candidate) > len(best):
            best = candidate
    sections = _bounded_sections_from_text(best, max_chars, max_sentences)
    return abstract, sections, title, candidates


def _extract_pdf(content: bytes, work: dict[str, Any], max_chars: int, max_sentences: int, max_pages: int, enable_ocr: bool, max_ocr_pages: int) -> dict[str, Any]:
    texts: list[str] = []
    page_count = 0
    engine = None
    try:
        try:
            import pymupdf as fitz  # type: ignore
        except ImportError:  # pragma: no cover - older PyMuPDF import name
            import fitz  # type: ignore
        document = fitz.open(stream=content, filetype="pdf")
        page_count = len(document)
        for page in document[:max_pages]:
            texts.append(page.get_text("text"))
        engine = "pymupdf"
        if len(normalize_space(" ".join(texts))) < 500 and enable_ocr and shutil.which("tesseract"):
            try:
                import pytesseract  # type: ignore
                from PIL import Image  # type: ignore
                ocr_texts: list[str] = []
                for page in document[: min(max_ocr_pages, page_count)]:
                    pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
                    image = Image.open(io.BytesIO(pix.tobytes("png")))
                    ocr_texts.append(pytesseract.image_to_string(image, lang="eng"))
                if len(normalize_space(" ".join(ocr_texts))) > len(normalize_space(" ".join(texts))):
                    texts = ocr_texts
                    engine = "pymupdf+tesseract"
            except Exception:
                pass
        document.close()
    except Exception as first_error:
        try:
            from pypdf import PdfReader  # type: ignore
            reader = PdfReader(io.BytesIO(content))
            page_count = len(reader.pages)
            for page in reader.pages[:max_pages]:
                texts.append(page.extract_text() or "")
            engine = "pypdf"
        except Exception as second_error:
            return {"status": "failed", "error": f"PDF_PARSE_FAILED: {first_error}; {second_error}"}
    text = "\n".join(texts)
    clean = normalize_space(text)
    if len(clean) < 500:
        return {
            "status": "failed",
            "error": "PDF_TEXT_TOO_SHORT_OR_SCANNED",
            "text_chars": len(clean),
            "page_count": page_count,
            "engine": engine,
            "ocr_available": bool(shutil.which("tesseract")),
        }
    printable = sum(ch.isprintable() for ch in clean) / max(1, len(clean))
    if printable < 0.85:
        return {"status": "failed", "error": "PDF_TEXT_LOW_QUALITY", "printable_ratio": printable, "engine": engine}
    identity = _identity_check(work, clean)
    if not identity["accepted"]:
        return {"status": "failed", "error": "PDF_IDENTITY_MISMATCH", "identity": identity, "engine": engine}
    sections = _bounded_sections_from_text(text, max_chars, max_sentences)
    if not sections:
        return {"status": "failed", "error": "PDF_NO_ANALYSABLE_SECTIONS", "identity": identity, "engine": engine}
    return {
        "status": "success",
        "sections": sections,
        "identity": identity,
        "engine": engine,
        "page_count": page_count,
        "text_chars": len(clean),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _exact_pubmed(work: dict[str, Any], client: HttpClient) -> dict[str, Any]:
    pmid = (work.get("identifiers") or {}).get("pmid")
    audit = {"stage": "pubmed_exact", "status": "skipped", "retrieved_at": utc_now_iso(), "error": None}
    if not pmid:
        audit["error"] = "NO_PMID"
        return {"audit": audit}
    response, http = client.request("GET", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi", params={"db": "pubmed", "id": pmid, "retmode": "xml"}, max_attempts=2)
    audit["http"] = asdict(http)
    if response is None:
        audit.update({"status": "failed", "error": http.error or "PUBMED_EXACT_FAILED"})
        return {"audit": audit}
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        audit.update({"status": "failed", "error": f"PUBMED_XML_PARSE:{exc}"})
        return {"audit": audit}
    article = root.find(".//PubmedArticle")
    if article is None:
        audit.update({"status": "failed", "error": "PUBMED_RECORD_NOT_FOUND"})
        return {"audit": audit}
    title_node = article.find(".//ArticleTitle")
    title = normalize_space("".join(title_node.itertext()) if title_node is not None else "")
    abstract = " ".join(normalize_space("".join(node.itertext())) for node in article.findall(".//Abstract/AbstractText"))
    identity = _identity_check(work, title or abstract, title)
    if not identity["accepted"]:
        audit.update({"status": "failed", "error": "PUBMED_IDENTITY_MISMATCH", "identity": identity})
        return {"audit": audit}
    ids: dict[str, Any] = {}
    for node in article.findall(".//ArticleId"):
        typ = node.attrib.get("IdType", "").casefold()
        value = normalize_space(node.text)
        if typ in {"doi", "pmc", "pubmed"} and value:
            ids[{"pmc": "pmcid", "pubmed": "pmid"}.get(typ, typ)] = value
    audit.update({"status": "success", "abstract_chars": len(abstract), "identity": identity})
    return {"audit": audit, "abstract": abstract or None, "identifiers": ids}


def _exact_europe_pmc(work: dict[str, Any], client: HttpClient) -> dict[str, Any]:
    ids = work.get("identifiers") or {}
    queries = []
    if ids.get("pmid"):
        queries.append(f'EXT_ID:{ids["pmid"]} AND SRC:MED')
    if ids.get("doi"):
        queries.append(f'DOI:"{ids["doi"]}"')
    audit = {"stage": "europe_pmc_exact", "status": "skipped", "queries": queries, "retrieved_at": utc_now_iso(), "error": None}
    if not queries:
        audit["error"] = "NO_EXACT_IDENTIFIER"
        return {"audit": audit}
    for query in queries:
        data, http = client.get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": query, "format": "json", "resultType": "core", "pageSize": 3}, max_attempts=2)
        audit.setdefault("http", []).append(asdict(http))
        for rec in ((data or {}).get("resultList") or {}).get("result", []):
            title = normalize_space(rec.get("title"))
            abstract = normalize_space(rec.get("abstractText"))
            identity = _identity_check(work, title or abstract, title)
            if not identity["accepted"]:
                continue
            audit.update({"status": "success", "query": query, "matched_id": rec.get("id"), "identity": identity, "abstract_chars": len(abstract)})
            return {
                "audit": audit,
                "abstract": abstract or None,
                "identifiers": {"pmid": rec.get("pmid"), "pmcid": rec.get("pmcid"), "doi": rec.get("doi"), "europe_pmc_id": rec.get("id")},
                "open_access": rec.get("isOpenAccess"),
                "full_text_available": rec.get("inEPMC"),
            }
    audit.update({"status": "failed", "error": "EUROPE_PMC_EXACT_NOT_FOUND"})
    return {"audit": audit}


def _exact_crossref(work: dict[str, Any], client: HttpClient) -> dict[str, Any]:
    doi = canonicalize_doi((work.get("identifiers") or {}).get("doi"))
    audit = {"stage": "crossref_exact", "status": "skipped", "doi": doi, "retrieved_at": utc_now_iso(), "error": None}
    if not doi:
        audit["error"] = "NO_DOI"
        return {"audit": audit, "candidates": []}
    mailto = os.getenv("CROSSREF_MAILTO", "")
    params = {"mailto": mailto} if mailto else None
    data, http = client.get_json(f"https://api.crossref.org/v1/works/{quote(doi, safe='')}", params=params, max_attempts=2)
    audit["http"] = asdict(http)
    rec = (data or {}).get("message") or {}
    if not rec:
        audit.update({"status": "failed", "error": http.error or "CROSSREF_EXACT_NOT_FOUND"})
        return {"audit": audit, "candidates": []}
    title = normalize_space((rec.get("title") or [""])[0])
    abstract = _clean_markup(rec.get("abstract"))
    identity = _identity_check(work, title or abstract or "", title)
    if not identity["accepted"]:
        audit.update({"status": "failed", "error": "CROSSREF_IDENTITY_MISMATCH", "identity": identity})
        return {"audit": audit, "candidates": []}
    licenses = [row.get("URL") for row in rec.get("license") or [] if row.get("URL")]
    candidates: list[RetrievalCandidate] = []
    for row in rec.get("link") or []:
        url = row.get("URL")
        content_type = row.get("content-type")
        if not url or (content_type and content_type.casefold() not in _ALLOWED_FULLTEXT_TYPES):
            continue
        candidates.append(
            RetrievalCandidate(
                url=url,
                source="crossref_tdm",
                content_type=content_type,
                content_version=row.get("content-version"),
                license=licenses[0] if licenses else None,
                intended_application=row.get("intended-application"),
                priority=20 if "xml" in str(content_type).casefold() else (30 if "html" in str(content_type).casefold() else 45),
            )
        )
    audit.update({"status": "success", "identity": identity, "abstract_chars": len(abstract or ""), "candidate_count": len(candidates), "licenses": licenses})
    return {"audit": audit, "abstract": abstract, "candidates": candidates, "licenses": licenses}


def _exact_semantic_scholar(work: dict[str, Any], client: HttpClient) -> dict[str, Any]:
    ids = work.get("identifiers") or {}
    identifier = ids.get("semantic_scholar_id") or (f"DOI:{ids['doi']}" if ids.get("doi") else None) or (f"PMID:{ids['pmid']}" if ids.get("pmid") else None)
    audit = {"stage": "semantic_scholar_exact", "status": "skipped", "identifier": identifier, "retrieved_at": utc_now_iso(), "error": None}
    if not identifier:
        audit["error"] = "NO_SEMANTIC_IDENTIFIER"
        return {"audit": audit, "candidates": []}
    headers = {"x-api-key": os.getenv("SEMANTIC_SCHOLAR_API_KEY", "")} if os.getenv("SEMANTIC_SCHOLAR_API_KEY") else None
    fields = "paperId,externalIds,title,abstract,authors,venue,publicationDate,openAccessPdf"
    data, http = client.get_json(f"https://api.semanticscholar.org/graph/v1/paper/{quote(str(identifier), safe=':')}", params={"fields": fields}, headers=headers, max_attempts=2)
    audit["http"] = asdict(http)
    if not data:
        audit.update({"status": "failed", "error": http.error or "SEMANTIC_EXACT_NOT_FOUND"})
        return {"audit": audit, "candidates": []}
    title = normalize_space(data.get("title"))
    abstract = normalize_space(data.get("abstract"))
    identity = _identity_check(work, title or abstract, title)
    if not identity["accepted"]:
        audit.update({"status": "failed", "error": "SEMANTIC_IDENTITY_MISMATCH", "identity": identity})
        return {"audit": audit, "candidates": []}
    candidate_rows = []
    oa_pdf = data.get("openAccessPdf") or {}
    if not isinstance(oa_pdf, dict):
        oa_pdf = {}
    if oa_pdf.get("url"):
        candidate_rows.append(RetrievalCandidate(oa_pdf["url"], "semantic_scholar_open_access_pdf", "application/pdf", open_access=True, priority=32))
    audit.update({"status": "success", "identity": identity, "abstract_chars": len(abstract), "candidate_count": len(candidate_rows)})
    return {"audit": audit, "abstract": abstract or None, "candidates": candidate_rows, "identifiers": {"semantic_scholar_id": data.get("paperId")}}


def _unpaywall(work: dict[str, Any], client: HttpClient) -> dict[str, Any]:
    doi = canonicalize_doi((work.get("identifiers") or {}).get("doi"))
    email = os.getenv("UNPAYWALL_EMAIL") or os.getenv("CROSSREF_MAILTO")
    audit = {"stage": "unpaywall", "status": "skipped", "doi": doi, "retrieved_at": utc_now_iso(), "error": None}
    if not doi:
        audit["error"] = "NO_DOI"
        return {"audit": audit, "candidates": []}
    if not email:
        audit["error"] = "UNPAYWALL_EMAIL_NOT_CONFIGURED"
        return {"audit": audit, "candidates": []}
    data, http = client.get_json(f"https://api.unpaywall.org/v2/{quote(doi, safe='')}", params={"email": email}, max_attempts=2)
    audit["http"] = asdict(http)
    if not data:
        audit.update({"status": "failed", "error": http.error or "UNPAYWALL_LOOKUP_FAILED"})
        return {"audit": audit, "candidates": []}
    candidates: list[RetrievalCandidate] = []
    locations = []
    if isinstance(data.get("best_oa_location"), dict):
        locations.append(data["best_oa_location"])
    locations.extend(row for row in data.get("oa_locations") or [] if isinstance(row, dict))
    seen: set[str] = set()
    for row in locations:
        for key, typ, priority in (("url_for_pdf", "application/pdf", 28), ("url", "text/html", 38), ("url_for_landing_page", "text/html", 42)):
            url = row.get(key)
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(
                RetrievalCandidate(
                    url=url,
                    source="unpaywall",
                    content_type=typ,
                    content_version=row.get("version"),
                    license=row.get("license"),
                    open_access=True,
                    priority=priority,
                )
            )
    audit.update({"status": "success", "is_oa": data.get("is_oa"), "oa_status": data.get("oa_status"), "candidate_count": len(candidates)})
    return {"audit": audit, "candidates": candidates}


def _doi_landing(work: dict[str, Any], client: HttpClient, max_chars: int, max_sentences: int) -> dict[str, Any]:
    doi = canonicalize_doi((work.get("identifiers") or {}).get("doi"))
    audit = {"stage": "doi_landing", "status": "skipped", "doi": doi, "retrieved_at": utc_now_iso(), "error": None}
    if not doi:
        audit["error"] = "NO_DOI"
        return {"audit": audit, "candidates": []}
    response, http = client.request("GET", f"https://doi.org/{doi}", max_attempts=2, allow_redirects=True)
    audit["http"] = asdict(http)
    if response is None:
        audit.update({"status": "failed", "error": http.error or "DOI_LANDING_FAILED"})
        return {"audit": audit, "candidates": []}
    content_type = response.headers.get("Content-Type", "")
    if "html" not in content_type.casefold() and not response.text.lstrip().startswith("<"):
        audit.update({"status": "failed", "error": "DOI_LANDING_NON_HTML", "final_url": response.url})
        return {"audit": audit, "candidates": []}
    abstract, sections, title, candidates = _extract_html_article(response.text, response.url, max_chars, max_sentences)
    identity = _identity_check(work, title or abstract or response.text[:5000], title)
    if not identity["accepted"]:
        audit.update({"status": "failed", "error": "DOI_LANDING_IDENTITY_MISMATCH", "identity": identity, "final_url": response.url})
        return {"audit": audit, "candidates": []}
    # The landing page body is accepted only when it contains substantial article
    # text; otherwise it remains a metadata and candidate-discovery source.
    audit.update({"status": "success", "final_url": response.url, "identity": identity, "abstract_chars": len(abstract or ""), "section_count": len(sections), "candidate_count": len(candidates)})
    return {"audit": audit, "abstract": abstract, "sections": sections, "url": response.url, "candidates": candidates}


def _pmc_fulltext_xml(work: dict[str, Any], client: HttpClient, max_chars: int, max_sentences: int) -> dict[str, Any]:
    pmcid = (work.get("identifiers") or {}).get("pmcid")
    audit = {"stage": "europe_pmc_fulltext_xml", "status": "skipped", "pmcid": pmcid, "retrieved_at": utc_now_iso(), "error": None}
    if not pmcid:
        audit["error"] = "NO_PMCID"
        return {"audit": audit}
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    response, http = client.request("GET", url, max_attempts=2)
    audit["http"] = asdict(http)
    if response is None:
        audit.update({"status": "failed", "error": http.error or "EUROPE_PMC_FULLTEXT_FAILED"})
        return {"audit": audit}
    abstract, sections, title = _sections_from_jats(response.content, max_chars, max_sentences)
    identity = _identity_check(work, title or abstract or response.text[:5000], title)
    if not identity["accepted"]:
        audit.update({"status": "failed", "error": "EUROPE_PMC_FULLTEXT_IDENTITY_MISMATCH", "identity": identity})
        return {"audit": audit}
    if not abstract and not sections:
        audit.update({"status": "failed", "error": "NO_USABLE_EUROPE_PMC_FULLTEXT", "identity": identity})
        return {"audit": audit}
    audit.update({"status": "success", "identity": identity, "abstract_chars": len(abstract or ""), "section_count": len(sections), "evidence_sentence_count": sum(len(row.get("sentences") or []) for row in sections)})
    return {"audit": audit, "abstract": abstract, "sections": sections, "url": url, "source": "europe_pmc_open_access_xml", "evidence_level": "E3"}


def _pmc_bioc(work: dict[str, Any], client: HttpClient, max_chars: int, max_sentences: int) -> dict[str, Any]:
    ids = work.get("identifiers") or {}
    identifier = ids.get("pmcid") or ids.get("pmid")
    audit = {"stage": "pmc_bioc_json", "status": "skipped", "identifier": identifier, "retrieved_at": utc_now_iso(), "error": None}
    if not identifier:
        audit["error"] = "NO_PMID_OR_PMCID"
        return {"audit": audit}
    url = f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{identifier}/unicode"
    data, http = client.get_json(url, max_attempts=2)
    audit["http"] = asdict(http)
    if not data:
        audit.update({"status": "failed", "error": http.error or "BIOC_NOT_AVAILABLE"})
        return {"audit": audit}
    abstract, sections, title = _sections_from_bioc(data, max_chars, max_sentences)
    identity = _identity_check(work, title or abstract or json.dumps(data, ensure_ascii=False)[:5000], title)
    if not identity["accepted"]:
        audit.update({"status": "failed", "error": "BIOC_IDENTITY_MISMATCH", "identity": identity})
        return {"audit": audit}
    if not abstract and not sections:
        audit.update({"status": "failed", "error": "BIOC_NO_USABLE_TEXT", "identity": identity})
        return {"audit": audit}
    audit.update({"status": "success", "identity": identity, "abstract_chars": len(abstract or ""), "section_count": len(sections)})
    return {"audit": audit, "abstract": abstract, "sections": sections, "url": url, "source": "ncbi_pmc_bioc", "evidence_level": "E3"}


def _fetch_candidate(work: dict[str, Any], candidate: RetrievalCandidate, client: HttpClient, policy: dict[str, Any]) -> dict[str, Any]:
    audit = {"stage": "fulltext_candidate", "status": "failed", "candidate": asdict(candidate), "retrieved_at": utc_now_iso(), "error": None}
    headers = {}
    if candidate.content_type:
        headers["Accept"] = candidate.content_type
    response, http = client.request("GET", candidate.url, headers=headers or None, max_attempts=2, allow_redirects=True)
    audit["http"] = asdict(http)
    if response is None:
        audit["error"] = http.error or "FULLTEXT_CANDIDATE_FETCH_FAILED"
        return {"audit": audit}
    max_bytes = int(policy.get("max_pdf_bytes", 25_000_000))
    content = response.content
    if len(content) > max_bytes:
        audit["error"] = "FULLTEXT_FILE_TOO_LARGE"
        audit["size_bytes"] = len(content)
        return {"audit": audit}
    content_type = (response.headers.get("Content-Type") or candidate.content_type or "").casefold()
    final_url = response.url
    max_chars = int(policy.get("max_open_fulltext_analysis_chars", 16000))
    max_sentences = int(policy.get("max_open_fulltext_evidence_sentences", 50))
    if "pdf" in content_type or content.lstrip().startswith(b"%PDF"):
        parsed = _extract_pdf(
            content,
            work,
            max_chars,
            max_sentences,
            int(policy.get("max_pdf_pages", 80)),
            bool(policy.get("enable_pdf_ocr", False)),
            int(policy.get("max_pdf_ocr_pages", 4)),
        )
        audit.update({key: value for key, value in parsed.items() if key not in {"sections"}})
        if parsed.get("status") != "success":
            return {"audit": audit}
        audit.update({"status": "success", "final_url": final_url, "content_type": "application/pdf", "size_bytes": len(content)})
        return {
            "audit": audit,
            "sections": parsed.get("sections") or [],
            "url": final_url,
            "source": candidate.source,
            "evidence_level": "E2",
            "content_version": candidate.content_version,
            "license": candidate.license,
            "sha256": parsed.get("sha256"),
            "extraction_method": parsed.get("engine"),
        }
    if "xml" in content_type or content.lstrip().startswith(b"<?xml") or b"<article" in content[:1000].lower():
        abstract, sections, title = _sections_from_jats(content, max_chars, max_sentences)
        identity = _identity_check(work, title or abstract or response.text[:5000], title)
        if not identity["accepted"] or (not abstract and not sections):
            audit.update({"error": "XML_IDENTITY_OR_CONTENT_FAILED", "identity": identity})
            return {"audit": audit}
        audit.update({"status": "success", "final_url": final_url, "content_type": content_type, "identity": identity, "section_count": len(sections)})
        return {"audit": audit, "abstract": abstract, "sections": sections, "url": final_url, "source": candidate.source, "evidence_level": "E3", "content_version": candidate.content_version, "license": candidate.license, "extraction_method": "jats_xml"}
    if "html" in content_type or response.text.lstrip().startswith("<"):
        abstract, sections, title, nested = _extract_html_article(response.text, final_url, max_chars, max_sentences)
        identity = _identity_check(work, title or abstract or response.text[:5000], title)
        if not identity["accepted"]:
            audit.update({"error": "HTML_IDENTITY_MISMATCH", "identity": identity})
            return {"audit": audit, "nested_candidates": nested}
        if not abstract and not sections:
            audit.update({"error": "HTML_NO_USABLE_ARTICLE_CONTENT", "identity": identity})
            return {"audit": audit, "nested_candidates": nested}
        audit.update({"status": "success", "final_url": final_url, "content_type": content_type, "identity": identity, "section_count": len(sections)})
        return {"audit": audit, "abstract": abstract, "sections": sections, "url": final_url, "source": candidate.source, "evidence_level": "E2", "content_version": candidate.content_version, "license": candidate.license, "extraction_method": "publisher_html", "nested_candidates": nested}
    text = normalize_space(response.text)
    identity = _identity_check(work, text)
    sections = _bounded_sections_from_text(text, max_chars, max_sentences)
    if identity["accepted"] and sections:
        audit.update({"status": "success", "final_url": final_url, "content_type": content_type or "text/plain", "identity": identity, "section_count": len(sections)})
        return {"audit": audit, "sections": sections, "url": final_url, "source": candidate.source, "evidence_level": "E2", "content_version": candidate.content_version, "license": candidate.license, "extraction_method": "plain_text"}
    audit.update({"error": "UNSUPPORTED_OR_EMPTY_FULLTEXT_CONTENT", "identity": identity})
    return {"audit": audit}


def _dedupe_candidates(candidates: Iterable[RetrievalCandidate]) -> list[RetrievalCandidate]:
    out: list[RetrievalCandidate] = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda row: row.priority):
        url = normalize_space(candidate.url)
        if not url or urlsplit(url).scheme not in {"http", "https"} or url in seen:
            continue
        seen.add(url)
        out.append(candidate)
    return out


def recover_scholarly_work(work: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    policy = profile.get("content_policy") or {}
    timeout = int(profile.get("search_policy", {}).get("request_timeout_seconds", 20))
    user_agent = str(policy.get("user_agent") or "PathogenDailyIntelligence/1.5 (research monitoring)")
    client = HttpClient(timeout=timeout, user_agent=user_agent)
    audits: list[dict[str, Any]] = []
    candidates: list[RetrievalCandidate] = []
    ids = ensure_dict_field(work, "identifiers")
    initial_has_abstract = bool((work.get("abstract") or {}).get("original"))
    initial_has_fulltext = bool((work.get("full_text") or {}).get("available"))

    # Reuse discoverer-provided full-text links before issuing more discovery calls.
    for record in work.get("source_records") or []:
        for row in record.get("fulltext_links") or []:
            if not isinstance(row, dict) or not row.get("URL"):
                continue
            content_type = row.get("content-type")
            candidates.append(
                RetrievalCandidate(
                    url=row["URL"],
                    source="crossref_tdm_source_record",
                    content_type=content_type,
                    content_version=row.get("content-version"),
                    intended_application=row.get("intended-application"),
                    license=next((x.get("URL") for x in record.get("licenses") or [] if isinstance(x, dict) and x.get("URL")), None),
                    priority=18 if "xml" in str(content_type).casefold() else (28 if "html" in str(content_type).casefold() else 42),
                )
            )
        oa_pdf = record.get("open_access_pdf") or {}
        if isinstance(oa_pdf, dict) and oa_pdf.get("url"):
            candidates.append(RetrievalCandidate(oa_pdf["url"], "semantic_scholar_source_record", "application/pdf", open_access=True, priority=30))

    # Prefer structured, identifier-bound metadata APIs for abstract recovery.
    metadata_results = [
        _exact_pubmed(work, client),
        _exact_europe_pmc(work, client),
        _exact_crossref(work, client),
        _exact_semantic_scholar(work, client),
    ]
    for result in metadata_results:
        audit = result.get("audit") or {}
        audits.append(audit)
        for key, value in (result.get("identifiers") or {}).items():
            if value and not ids.get(key):
                ids[key] = canonicalize_doi(value) if key == "doi" else value
        candidates.extend(result.get("candidates") or [])
        if result.get("abstract") and not (work.get("abstract") or {}).get("original"):
            abstract = ensure_dict_field(work, "abstract")
            abstract["original"] = result["abstract"]
            abstract["sentences"] = sentence_split(result["abstract"], "A")
            abstract["source"] = audit.get("stage")
            abstract["availability_status"] = "recovered"

    # Structured full text has priority over PDF because it preserves sections.
    full_result = _pmc_fulltext_xml(work, client, int(policy.get("max_open_fulltext_analysis_chars", 16000)), int(policy.get("max_open_fulltext_evidence_sentences", 50)))
    audits.append(full_result.get("audit") or {})
    if full_result.get("audit", {}).get("status") != "success":
        bioc_result = _pmc_bioc(work, client, int(policy.get("max_open_fulltext_analysis_chars", 16000)), int(policy.get("max_open_fulltext_evidence_sentences", 50)))
        audits.append(bioc_result.get("audit") or {})
        if bioc_result.get("audit", {}).get("status") == "success":
            full_result = bioc_result

    # Discover legal/open locations only after exact metadata has been checked.
    unpaywall_result = _unpaywall(work, client)
    audits.append(unpaywall_result.get("audit") or {})
    candidates.extend(unpaywall_result.get("candidates") or [])
    landing_result = _doi_landing(work, client, int(policy.get("max_open_fulltext_analysis_chars", 16000)), int(policy.get("max_open_fulltext_evidence_sentences", 50)))
    audits.append(landing_result.get("audit") or {})
    candidates.extend(landing_result.get("candidates") or [])
    if landing_result.get("abstract") and not (work.get("abstract") or {}).get("original"):
        abstract = ensure_dict_field(work, "abstract")
        abstract["original"] = landing_result["abstract"]
        abstract["sentences"] = sentence_split(landing_result["abstract"], "A")
        abstract["source"] = "doi_landing_metadata"
        abstract["availability_status"] = "recovered"
    if landing_result.get("sections") and full_result.get("audit", {}).get("status") != "success":
        full_result = {
            "audit": landing_result.get("audit"),
            "sections": landing_result.get("sections"),
            "url": landing_result.get("url"),
            "source": "publisher_html",
            "evidence_level": "E2",
            "extraction_method": "publisher_html",
        }

    nested_candidates: list[RetrievalCandidate] = []
    max_candidates = int(policy.get("max_fulltext_candidates_per_work", 8))
    if full_result.get("audit", {}).get("status") != "success":
        for candidate in _dedupe_candidates(candidates)[:max_candidates]:
            result = _fetch_candidate(work, candidate, client, policy)
            audits.append(result.get("audit") or {})
            nested_candidates.extend(result.get("nested_candidates") or [])
            if result.get("abstract") and not (work.get("abstract") or {}).get("original"):
                abstract = ensure_dict_field(work, "abstract")
                abstract["original"] = result["abstract"]
                abstract["sentences"] = sentence_split(result["abstract"], "A")
                abstract["source"] = candidate.source
                abstract["availability_status"] = "recovered"
            if result.get("audit", {}).get("status") == "success" and result.get("sections"):
                full_result = result
                break
    if full_result.get("audit", {}).get("status") != "success" and nested_candidates:
        for candidate in _dedupe_candidates(nested_candidates)[:2]:
            result = _fetch_candidate(work, candidate, client, policy)
            audits.append(result.get("audit") or {})
            if result.get("audit", {}).get("status") == "success" and result.get("sections"):
                full_result = result
                break

    abstract = ensure_dict_field(work, "abstract")
    full_text = ensure_dict_field(work, "full_text")
    quality = ensure_dict_field(work, "quality")
    if full_result.get("audit", {}).get("status") == "success" and full_result.get("sections"):
        if full_result.get("abstract") and not abstract.get("original"):
            abstract["original"] = full_result["abstract"]
            abstract["sentences"] = sentence_split(full_result["abstract"], "A")
            abstract["source"] = full_result.get("source")
            abstract["availability_status"] = "recovered"
        full_text.update(
            {
                "available": True,
                "source": full_result.get("source"),
                "url": full_result.get("url"),
                "sections": full_result.get("sections") or [],
                "availability_status": "available",
                "evidence_level": full_result.get("evidence_level") or "E2",
                "content_version": full_result.get("content_version"),
                "license": full_result.get("license"),
                "sha256": full_result.get("sha256"),
                "extraction_method": full_result.get("extraction_method"),
                "temporary_file_persisted": False,
            }
        )
    else:
        full_text.setdefault("available", False)
        full_text["availability_status"] = "not_retrieved"
        full_text.setdefault("sections", [])

    has_abstract = bool(abstract.get("original"))
    has_fulltext = bool(full_text.get("available") and full_text.get("sections"))
    evidence_level = full_text.get("evidence_level") if has_fulltext else ("E1" if has_abstract else "E0")
    status = "fulltext_available" if has_fulltext else ("abstract_only" if has_abstract else "metadata_only")
    reason_codes = []
    if not has_abstract:
        reason_codes.append("ABSTRACT_NOT_RETRIEVED")
    if not has_fulltext:
        reason_codes.append("FULLTEXT_NOT_RETRIEVED")
    failed_stages = [row.get("stage") for row in audits if row.get("status") == "failed"]
    acquisition = ensure_dict_field(work, "evidence_acquisition")
    acquisition.update(
        {
            "status": status,
            "evidence_level": evidence_level,
            "analysis_eligible": evidence_level in {"E1", "E2", "E3", "E4"},
            "last_attempt_at": utc_now_iso(),
            "attempt_count": int(acquisition.get("attempt_count") or 0) + 1,
            "reason_codes": reason_codes,
            "failed_stages": failed_stages,
            "attempts": audits,
            "retry_recommended": evidence_level == "E0",
            "copyright_policy": "Only public or explicitly text-mining/open-access locations are requested; PDFs are temporary and are not persisted.",
        }
    )
    abstract["availability_status"] = "available" if has_abstract and abstract.get("availability_status") != "recovered" else abstract.get("availability_status", "recovered")
    quality["has_abstract"] = has_abstract
    quality["open_full_text_available"] = has_fulltext
    return {
        "work_id": work.get("work_id"),
        "status": status,
        "evidence_level": evidence_level,
        "abstract_recovered": has_abstract and not initial_has_abstract,
        "fulltext_recovered": has_fulltext and not initial_has_fulltext,
        "audits": audits,
    }


def enrich_scholarly_works_with_fallbacks(works: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    policy = profile.get("content_policy") or {}
    maximum = int(policy.get("max_scholarly_recovery_works_per_issue", max(20, len(works))))
    # Prioritise metadata-only records, then abstract-only records, then records
    # with strong editorial scores for richer full-text analysis.
    candidates = sorted(
        works,
        key=lambda work: (
            0 if not (work.get("abstract") or {}).get("original") else 1,
            0 if not (work.get("full_text") or {}).get("available") else 1,
            -float((work.get("filter_result") or {}).get("score") or 0),
            (work.get("bibliography") or {}).get("availability_date") or "9999",
        ),
    )[:maximum]
    results = [recover_scholarly_work(work, profile) for work in candidates]
    attempted_ids = {row.get("work_id") for row in results}
    for work in works:
        if work.get("work_id") in attempted_ids:
            continue
        abstract = ensure_dict_field(work, "abstract")
        full_text = ensure_dict_field(work, "full_text")
        has_abstract = bool(abstract.get("original"))
        has_fulltext = bool(full_text.get("available"))
        ensure_dict_field(work, "evidence_acquisition").update(
            {
                "status": "not_attempted_budget",
                "evidence_level": "E3" if has_fulltext else ("E1" if has_abstract else "E0"),
                "analysis_eligible": has_fulltext or has_abstract,
                "retry_recommended": not has_abstract,
                "reason_codes": ["RECOVERY_BUDGET_EXHAUSTED"],
                "last_attempt_at": utc_now_iso(),
            }
        )
    audits = [audit for result in results for audit in result.get("audits") or []]
    return {
        "attempted": len(results),
        "abstract_recovered": sum(row.get("abstract_recovered") for row in results),
        "fulltext_success": sum(row.get("fulltext_recovered") for row in results),
        "success": sum(row.get("evidence_level") in {"E2", "E3", "E4"} for row in results),
        "metadata_only": sum(row.get("evidence_level") == "E0" for row in results),
        "abstract_only": sum(row.get("evidence_level") == "E1" for row in results),
        "pdf_or_html_fulltext": sum(row.get("evidence_level") == "E2" for row in results),
        "structured_fulltext": sum(row.get("evidence_level") == "E3" for row in results),
        "failed": sum(row.get("status") == "metadata_only" for row in results),
        "audits": audits,
    }
