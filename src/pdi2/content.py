from __future__ import annotations

import io
import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urljoin, urlparse

import fitz
import trafilatura
from bs4 import BeautifulSoup
from rapidfuzz.fuzz import partial_ratio

from .http import HttpClient
from .utils import clean_space, extract_doi, normalize_title, sha256_text, split_sentences, strip_tags, truncate, unique_strings, utc_now_iso


BOILERPLATE_PATTERNS = [
    r"comprehensive up[- ]to[- ]date news coverage",
    r"aggregated from sources all over the world",
    r"google news provides",
    r"by google news",
    r"view full coverage",
    r"click here to read more",
    r"original abstract or excerpt is unavailable",
]


def remove_boilerplate(text: str) -> str:
    value = clean_space(text)
    for pattern in BOILERPLATE_PATTERNS:
        value = re.sub(pattern, " ", value, flags=re.I)
    value = re.sub(r"^[\s,;:.\-–—]+|[\s,;:.\-–—]+$", "", value)
    return clean_space(value)


def _meta_content(soup: BeautifulSoup, names: list[tuple[str, str]]) -> str:
    for attr, value in names:
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            return clean_space(tag.get("content"))
    return ""


def _extract_jsonld(soup: BeautifulSoup) -> tuple[str, str]:
    title = ""
    body = ""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text(" ")
        try:
            data = json.loads(raw)
        except Exception:
            continue
        nodes = data if isinstance(data, list) else [data]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph") if isinstance(node.get("@graph"), list) else [node]
            for item in graph:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get("@type", "")).lower()
                if "article" in item_type or "report" in item_type or "news" in item_type:
                    title = title or clean_space(item.get("headline") or item.get("name"))
                    body = body or clean_space(item.get("articleBody") or item.get("description"))
    return title, body


def resolve_and_extract_news(http: HttpClient, record: dict[str, Any], max_chars: int = 14000) -> dict[str, Any]:
    audit: dict[str, Any] = {"attempted_urls": [], "retrieved_at": utc_now_iso()}
    candidates = unique_strings([record.get("url")])
    best_text = remove_boilerplate(record.get("excerpt") or "")
    best_title = clean_space(record.get("title"))
    method = "rss_excerpt" if best_text else "none"
    final_url = record.get("url")

    for url in candidates[:4]:
        try:
            audit["attempted_urls"].append(url)
            response = http.request("GET", url, allow_redirects=True)
            final_url = response.url
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type and "text" not in content_type:
                continue
            raw = response.text
            extracted = trafilatura.extract(raw, include_comments=False, include_tables=False, favor_precision=True) or ""
            soup = BeautifulSoup(raw, "lxml")
            jsonld_title, jsonld_body = _extract_jsonld(soup)
            if len(jsonld_body) > len(extracted):
                extracted = jsonld_body
                method = "jsonld_articleBody"
            elif extracted:
                method = "trafilatura"
            if not extracted:
                article = soup.find("article") or soup.find("main") or soup.find(attrs={"role": "main"})
                if article:
                    extracted = clean_space(article.get_text(" "))
                    method = "article_or_main"
            if not extracted:
                extracted = _meta_content(soup, [("name", "description"), ("property", "og:description")])
                method = "meta_description" if extracted else method
            extracted = remove_boilerplate(extracted)
            candidate_title = jsonld_title or _meta_content(soup, [("property", "og:title"), ("name", "citation_title")])
            if candidate_title:
                best_title = candidate_title
            if len(extracted) > len(best_text):
                best_text = extracted
            canonical = soup.find("link", rel=lambda x: x and "canonical" in x)
            if canonical and canonical.get("href"):
                final_url = urljoin(final_url, canonical.get("href"))
            if len(best_text) >= 800:
                break
        except Exception as exc:
            audit.setdefault("errors", []).append(clean_space(exc)[:300])

    record["resolved_url"] = final_url
    record["content_title"] = best_title
    record["content"] = truncate(best_text, max_chars)
    record["content_status"] = "full" if len(best_text) >= 1800 else ("partial" if len(best_text) >= 300 else "unavailable")
    record["content_method"] = method
    record["content_hash"] = sha256_text(best_text) if best_text else None
    record["content_audit"] = audit
    return record


def _jats_sections(xml_text: str) -> dict[str, str]:
    root = ET.fromstring(xml_text)
    sections: dict[str, list[str]] = {"abstract": [], "methods": [], "results": [], "discussion": [], "conclusion": [], "other": []}
    for abstract in root.findall(".//abstract"):
        text = clean_space(" ".join("".join(abstract.itertext()).split()))
        if text:
            sections["abstract"].append(text)
    for sec in root.findall(".//body//sec"):
        title_node = sec.find("title")
        title = clean_space("".join(title_node.itertext()) if title_node is not None else "").lower()
        text = clean_space(" ".join("".join(sec.itertext()).split()))
        if not text:
            continue
        if any(k in title for k in ("method", "material", "patient", "study design")):
            key = "methods"
        elif any(k in title for k in ("result", "finding")):
            key = "results"
        elif "discussion" in title:
            key = "discussion"
        elif any(k in title for k in ("conclusion", "summary")):
            key = "conclusion"
        else:
            key = "other"
        sections[key].append(text)
    return {key: clean_space(" ".join(values)) for key, values in sections.items() if values}


def _pdf_text(raw: bytes, max_pages: int = 60) -> str:
    document = fitz.open(stream=raw, filetype="pdf")
    texts: list[str] = []
    for index, page in enumerate(document):
        if index >= max_pages:
            break
        texts.append(page.get_text("text"))
    return clean_space(" ".join(texts))


def _identity_score(work: dict[str, Any], candidate_text: str, candidate_url: str) -> tuple[bool, dict[str, Any]]:
    expected_title = normalize_title(work.get("title"))
    head = normalize_title(candidate_text[:2500])
    title_score = partial_ratio(expected_title, head) / 100 if expected_title and head else 0.0
    expected_doi = (work.get("doi") or "").lower()
    doi_candidates = {x.lower() for x in re.findall(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", candidate_text[:12000], flags=re.I)}
    doi_match = bool(expected_doi and expected_doi in {x.rstrip(".,;)]}") for x in doi_candidates})
    author_match = False
    lower = candidate_text[:8000].lower()
    for author in (work.get("authors") or [])[:4]:
        family = clean_space(author).split(" ")[-1].lower()
        if len(family) > 3 and family in lower:
            author_match = True
            break
    accepted = title_score >= 0.82 or (doi_match and (title_score >= 0.45 or author_match)) or (title_score >= 0.62 and author_match)
    return accepted, {"title_score": round(title_score, 3), "doi_match": doi_match, "author_match": author_match, "candidate_url": candidate_url}


def enrich_scholarly_work(http: HttpClient, work: dict[str, Any], mailto: str, max_chars: int = 18000) -> dict[str, Any]:
    audit: dict[str, Any] = {"attempts": [], "retrieved_at": utc_now_iso()}
    pmcid = clean_space((work.get("source_ids") or {}).get("pmcid"))
    if pmcid and not pmcid.upper().startswith("PMC"):
        pmcid = "PMC" + pmcid
    candidates: list[tuple[str, str]] = []
    if pmcid:
        candidates.extend([
            ("europe_pmc_xml", f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"),
            ("pmc_bioc", f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmcid}/unicode"),
        ])
    for link in work.get("full_text_links") or []:
        if isinstance(link, dict) and link.get("URL"):
            candidates.append(("crossref_link", link.get("URL")))
    if work.get("open_access_pdf"):
        candidates.append(("open_access_pdf", work.get("open_access_pdf")))
    for url in work.get("full_text_urls") or []:
        candidates.append(("europe_pmc_link", url))
    doi = work.get("doi")
    if doi and mailto:
        try:
            payload = http.get_json(f"https://api.unpaywall.org/v2/{doi}", params={"email": mailto})
            for location in [payload.get("best_oa_location")] + list(payload.get("oa_locations") or []):
                if not isinstance(location, dict):
                    continue
                if location.get("url_for_pdf"):
                    candidates.append(("unpaywall_pdf", location.get("url_for_pdf")))
                if location.get("url_for_landing_page"):
                    candidates.append(("unpaywall_landing", location.get("url_for_landing_page")))
        except Exception as exc:
            audit["attempts"].append({"method": "unpaywall", "status": "failed", "error": clean_space(exc)[:200]})
        candidates.append(("doi_landing", f"https://doi.org/{doi}"))
    if work.get("url"):
        candidates.append(("source_landing", work.get("url")))

    seen: set[str] = set()
    best_text = clean_space(work.get("abstract"))
    best_sections: dict[str, str] = {"abstract": best_text} if best_text else {}
    best_method = "abstract_api" if best_text else "metadata_only"
    best_url = None

    for method, url in candidates[:12]:
        if not url or url in seen:
            continue
        seen.add(url)
        attempt: dict[str, Any] = {"method": method, "url": url}
        try:
            response = http.request("GET", url, allow_redirects=True, timeout=35)
            content_type = response.headers.get("Content-Type", "").lower()
            final_url = response.url
            if "pdf" in content_type or response.content.startswith(b"%PDF"):
                text = _pdf_text(response.content)
                sections = {"full_text": text}
                parse_method = "pymupdf"
            elif method == "pmc_bioc" or "json" in content_type:
                payload = response.json()
                documents = payload if isinstance(payload, list) else [payload]
                parts = []
                for document in documents:
                    for passage in document.get("documents", [document])[0].get("passages", []) if isinstance(document, dict) else []:
                        if passage.get("text"):
                            parts.append(passage.get("text"))
                text = clean_space(" ".join(parts))
                sections = {"full_text": text}
                parse_method = "pmc_bioc"
            elif "xml" in content_type or method == "europe_pmc_xml":
                sections = _jats_sections(response.text)
                text = clean_space(" ".join(sections.values()))
                parse_method = "jats_xml"
            else:
                raw = response.text
                extracted = trafilatura.extract(raw, include_comments=False, include_tables=True, favor_recall=True) or ""
                soup = BeautifulSoup(raw, "lxml")
                abstract = _meta_content(soup, [("name", "citation_abstract"), ("name", "description"), ("property", "og:description")])
                text = clean_space(extracted or abstract)
                sections = {"full_text": text} if text else {}
                parse_method = "publisher_html"
            accepted, identity = _identity_score(work, text, final_url)
            attempt.update({"status": "accepted" if accepted else "identity_rejected", "identity": identity, "chars": len(text), "parse_method": parse_method})
            audit["attempts"].append(attempt)
            if not accepted or len(text) < 400:
                continue
            if len(text) > len(best_text):
                best_text = text
                best_sections = sections
                best_method = parse_method
                best_url = final_url
            if len(best_text) >= 6000:
                break
        except Exception as exc:
            attempt.update({"status": "failed", "error": clean_space(exc)[:300]})
            audit["attempts"].append(attempt)

    work["full_text"] = truncate(best_text, max_chars) if len(best_text) > len(clean_space(work.get("abstract"))) else ""
    work["full_text_sections"] = {k: truncate(v, 8000) for k, v in best_sections.items()}
    work["full_text_method"] = best_method
    work["full_text_url"] = best_url
    work["evidence_level"] = "E2" if work.get("full_text") else ("E1" if work.get("abstract") else "E0")
    work["content_audit"] = audit
    return work


def _meta_content(soup: BeautifulSoup, names: list[tuple[str, str]]) -> str:
    for attr, value in names:
        tag = soup.find("meta", attrs={attr: value})
        if tag and tag.get("content"):
            return clean_space(tag.get("content"))
    return ""
