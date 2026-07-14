from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .http import HttpClient
from .utils import canonicalize_url, ensure_dict_field, normalize_space, sentence_split, utc_now_iso

_PAYWALL_MARKERS = (
    "subscribe to continue",
    "sign in to continue",
    "register to continue",
    "this content is for subscribers",
    "enable javascript and cookies",
    "access denied",
)


def _iter_jsonld(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_jsonld(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_jsonld(child)


def _jsonld_article(soup: BeautifulSoup) -> tuple[str | None, dict[str, Any]]:
    best_text: str | None = None
    metadata: dict[str, Any] = {}
    for node in soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        raw = node.string or node.get_text(" ")
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        for obj in _iter_jsonld(data):
            type_value = obj.get("@type")
            types = type_value if isinstance(type_value, list) else [type_value]
            if not any(str(t or "").casefold() in {"article", "newsarticle", "report", "medicalscholarlyarticle"} for t in types):
                continue
            article_body = normalize_space(obj.get("articleBody"))
            if article_body and (best_text is None or len(article_body) > len(best_text)):
                best_text = article_body
                metadata = {
                    "headline": normalize_space(obj.get("headline")) or None,
                    "date_published": obj.get("datePublished"),
                    "date_modified": obj.get("dateModified"),
                }
    return best_text, metadata


def _paragraph_text(container: Any) -> str:
    paragraphs: list[str] = []
    seen: set[str] = set()
    for paragraph in container.find_all("p"):
        text = normalize_space(paragraph.get_text(" ", strip=True))
        if len(text) < 45 or text in seen:
            continue
        low = text.casefold()
        if any(marker in low for marker in ("cookie policy", "privacy policy", "all rights reserved", "sign up for")):
            continue
        seen.add(text)
        paragraphs.append(text)
    return " ".join(paragraphs)


def _extract_main_text(html_text: str) -> tuple[str | None, str, dict[str, Any]]:
    soup = BeautifulSoup(html_text, "lxml")
    jsonld_text, jsonld_meta = _jsonld_article(soup)
    if jsonld_text and len(jsonld_text) >= 250:
        return jsonld_text, "jsonld_articleBody", jsonld_meta

    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside"]):
        tag.decompose()

    selectors = [
        "article",
        "main",
        "[role='main']",
        "[itemprop='articleBody']",
        ".article-body",
        ".story-body",
        ".post-content",
        ".entry-content",
        ".content-body",
    ]
    candidates: list[tuple[int, str, str]] = []
    for selector in selectors:
        for node in soup.select(selector):
            text = _paragraph_text(node)
            if len(text) >= 250:
                candidates.append((len(text), text, f"css:{selector}"))
    if candidates:
        _, text, method = max(candidates, key=lambda row: row[0])
        return text, method, jsonld_meta

    text = _paragraph_text(soup)
    return (text if len(text) >= 250 else None), "paragraph_fallback", jsonld_meta


def _fetch_one_news(article: dict[str, Any], timeout: int, user_agent: str, max_chars: int, max_sentences: int) -> dict[str, Any]:
    url = article.get("canonical_url") or article.get("original_url")
    audit: dict[str, Any] = {
        "status": "skipped",
        "requested_url": url,
        "final_url": None,
        "method": None,
        "retrieved_at": utc_now_iso(),
        "char_count": 0,
        "sentence_count": 0,
        "error": None,
        "copyright_policy": "bounded evidence extraction; full publisher page is not persisted",
    }
    if not url or urlsplit(str(url)).scheme not in {"http", "https"}:
        audit["error"] = "UNSUPPORTED_OR_MISSING_URL"
        return {"article_id": article.get("article_id"), "audit": audit}

    client = HttpClient(timeout=timeout, user_agent=user_agent)
    response, http_audit = client.request("GET", str(url), max_attempts=2, allow_redirects=True)
    audit["http"] = http_audit.__dict__.copy()
    if response is None:
        audit.update({"status": "failed", "error": http_audit.error or "HTTP_FETCH_FAILED"})
        return {"article_id": article.get("article_id"), "audit": audit}
    content_type = response.headers.get("Content-Type", "")
    audit["final_url"] = response.url
    audit["content_type"] = content_type
    if "html" not in content_type.casefold() and not response.text.lstrip().startswith("<"):
        audit.update({"status": "failed", "error": "NON_HTML_CONTENT"})
        return {"article_id": article.get("article_id"), "audit": audit}

    text, method, metadata = _extract_main_text(response.text)
    if not text:
        audit.update({"status": "failed", "method": method, "error": "NO_MAIN_ARTICLE_TEXT"})
        return {"article_id": article.get("article_id"), "audit": audit}
    low = text.casefold()
    if len(text) < 500 and any(marker in low for marker in _PAYWALL_MARKERS):
        audit.update({"status": "failed", "method": method, "error": "PAYWALL_OR_ACCESS_INTERSTITIAL"})
        return {"article_id": article.get("article_id"), "audit": audit}

    bounded = normalize_space(text)[:max_chars]
    sentences = sentence_split(bounded, "N")[:max_sentences]
    if not sentences:
        audit.update({"status": "failed", "method": method, "error": "NO_EVIDENCE_SENTENCES"})
        return {"article_id": article.get("article_id"), "audit": audit}
    evidence_text = " ".join(row["text"] for row in sentences)
    audit.update(
        {
            "status": "success",
            "method": method,
            "char_count": len(evidence_text),
            "sentence_count": len(sentences),
            "truncated": len(text) > len(evidence_text),
            "metadata": metadata,
        }
    )
    return {
        "article_id": article.get("article_id"),
        "analysis_text": evidence_text,
        "sentences": sentences,
        "final_url": response.url,
        "audit": audit,
    }


def enrich_news_articles(articles: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    policy = profile.get("content_policy") or {}
    maximum = int(policy.get("max_news_fetches_per_issue", 24))
    max_chars = int(policy.get("max_news_analysis_chars", 12000))
    max_sentences = int(policy.get("max_news_evidence_sentences", 40))
    max_translation_chars = int(policy.get("max_news_translation_chars", 4500))
    workers = max(1, int(policy.get("news_fetch_workers", 4)))
    timeout = int(profile.get("search_policy", {}).get("request_timeout_seconds", 20))
    user_agent = str(policy.get("user_agent") or "PathogenDailyIntelligence/1.3 (research monitoring)")

    tier_rank = {"A": 0, "B": 1, "C": 2, "D": 3, "unknown": 4}
    candidates = sorted(
        articles,
        key=lambda article: (
            tier_rank.get(article.get("source", {}).get("reliability_tier", "unknown"), 4),
            0 if not article.get("content", {}).get("excerpt") else 1,
            article.get("published_at") or "9999",
        ),
    )[:maximum]
    article_map = {article.get("article_id"): article for article in articles}
    audits: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_one_news, article, timeout, user_agent, max_chars, max_sentences): article.get("article_id")
            for article in candidates
        }
        for future in as_completed(futures):
            article_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # defensive boundary: one page cannot stop the issue
                result = {
                    "article_id": article_id,
                    "audit": {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "retrieved_at": utc_now_iso(),
                    },
                }
            article = article_map.get(article_id)
            if not article:
                continue
            audit = result.get("audit") or {}
            ensure_dict_field(article, "retrieval_audit")["content_fetch"] = audit
            audits.append({"object_type": "news_article", "object_id": article_id, **audit})
            if audit.get("status") != "success":
                continue
            content = ensure_dict_field(article, "content")
            content.setdefault("original_excerpt", content.get("excerpt"))
            content["analysis_text"] = result.get("analysis_text")
            content["translation_text"] = str(result.get("analysis_text") or "")[:max_translation_chars] or content.get("excerpt")
            content["sentences"] = result.get("sentences") or content.get("sentences") or []
            if result.get("analysis_text"):
                content["excerpt"] = str(result["analysis_text"])[:2200].rstrip() + (
                    "…" if len(str(result["analysis_text"])) > 2200 else ""
                )
            final_url = canonicalize_url(result.get("final_url"))
            if final_url:
                article["canonical_url"] = final_url
                ensure_dict_field(article, "fingerprints")["resolved_url"] = final_url

    return {
        "attempted": len(candidates),
        "success": sum(a.get("status") == "success" for a in audits),
        "failed": sum(a.get("status") == "failed" for a in audits),
        "audits": audits,
    }


def _section_title(section: Any) -> str:
    title = section.find("title", recursive=False)
    return normalize_space(title.get_text(" ", strip=True)) if title else ""


def _section_text(section: Any) -> str:
    paragraphs = [normalize_space(p.get_text(" ", strip=True)) for p in section.find_all("p")]
    return " ".join(p for p in paragraphs if len(p) >= 35)


def _fetch_fulltext_work(work: dict[str, Any], timeout: int, user_agent: str, max_chars: int, max_sentences: int) -> dict[str, Any]:
    pmcid = work.get("identifiers", {}).get("pmcid")
    audit: dict[str, Any] = {
        "status": "skipped",
        "pmcid": pmcid,
        "source": "Europe PMC Open Access fullTextXML",
        "retrieved_at": utc_now_iso(),
        "error": None,
    }
    if not pmcid:
        audit["error"] = "NO_PMCID"
        return {"work_id": work.get("work_id"), "audit": audit}
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    client = HttpClient(timeout=timeout, user_agent=user_agent)
    response, http_audit = client.request("GET", url, max_attempts=2)
    audit["http"] = http_audit.__dict__.copy()
    if response is None:
        audit.update({"status": "failed", "error": http_audit.error or "FULLTEXT_FETCH_FAILED"})
        return {"work_id": work.get("work_id"), "audit": audit}
    soup = BeautifulSoup(response.content, "xml")
    abstract_node = soup.find("abstract")
    abstract_text = normalize_space(abstract_node.get_text(" ", strip=True)) if abstract_node else None

    key_terms = (
        "method",
        "material",
        "result",
        "finding",
        "discussion",
        "conclusion",
        "limitation",
        "方法",
        "结果",
        "讨论",
        "结论",
    )
    section_rows: list[dict[str, Any]] = []
    used_chars = 0
    evidence_counter = 1
    for section in soup.find_all("sec"):
        title = _section_title(section)
        if not title or not any(term in title.casefold() for term in key_terms):
            continue
        text = _section_text(section)
        if len(text) < 120:
            continue
        remaining = max_chars - used_chars
        if remaining <= 0:
            break
        text = text[:remaining]
        rows = sentence_split(text, "X")
        evidence: list[dict[str, str]] = []
        for row in rows:
            if evidence_counter > max_sentences:
                break
            evidence.append({"id": f"F{evidence_counter}", "text": row["text"]})
            evidence_counter += 1
        if not evidence:
            continue
        retained = " ".join(row["text"] for row in evidence)
        used_chars += len(retained)
        section_rows.append({"title": title, "text": retained, "sentences": evidence})
        if evidence_counter > max_sentences:
            break

    if not abstract_text and not section_rows:
        audit.update({"status": "failed", "error": "NO_USABLE_OPEN_FULLTEXT"})
        return {"work_id": work.get("work_id"), "audit": audit}
    audit.update(
        {
            "status": "success",
            "abstract_chars": len(abstract_text or ""),
            "section_count": len(section_rows),
            "evidence_sentence_count": sum(len(row["sentences"]) for row in section_rows),
            "copyright_policy": "Europe PMC open-access XML; only bounded analytical evidence is retained",
        }
    )
    return {
        "work_id": work.get("work_id"),
        "abstract": abstract_text,
        "sections": section_rows,
        "url": url,
        "audit": audit,
    }


def enrich_scholarly_works(works: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    policy = profile.get("content_policy") or {}
    maximum = int(policy.get("max_open_fulltext_fetches_per_issue", 6))
    max_chars = int(policy.get("max_open_fulltext_analysis_chars", 14000))
    max_sentences = int(policy.get("max_open_fulltext_evidence_sentences", 45))
    timeout = int(profile.get("search_policy", {}).get("request_timeout_seconds", 20))
    user_agent = str(policy.get("user_agent") or "PathogenDailyIntelligence/1.3 (research monitoring)")

    candidates = [work for work in works if work.get("identifiers", {}).get("pmcid")]
    candidates.sort(key=lambda work: (bool(work.get("abstract", {}).get("original")), work.get("bibliography", {}).get("published_date") or "9999"))
    candidates = candidates[:maximum]
    work_map = {work.get("work_id"): work for work in works}
    audits: list[dict[str, Any]] = []

    for work in candidates:
        result = _fetch_fulltext_work(work, timeout, user_agent, max_chars, max_sentences)
        audit = result.get("audit") or {}
        audits.append({"object_type": "scholarly_work", "object_id": work.get("work_id"), **audit})
        current = work_map.get(result.get("work_id"))
        if not current:
            continue
        ensure_dict_field(current, "processing_audit")["open_fulltext"] = audit
        if audit.get("status") != "success":
            continue
        if not current.get("abstract", {}).get("original") and result.get("abstract"):
            ensure_dict_field(current, "abstract")["original"] = result["abstract"]
            current["abstract"]["source"] = "europe_pmc_fulltext_xml"
            current["abstract"]["sentences"] = sentence_split(result["abstract"], "A")
        current["full_text"] = {
            "available": True,
            "source": "Europe PMC Open Access",
            "url": result.get("url"),
            "sections": result.get("sections") or [],
            "retrieved_at": audit.get("retrieved_at"),
        }
        ensure_dict_field(current, "quality")["full_text_enriched"] = True
        current["quality"]["has_abstract"] = bool(current.get("abstract", {}).get("original"))

    return {
        "attempted": len(candidates),
        "success": sum(a.get("status") == "success" for a in audits),
        "failed": sum(a.get("status") == "failed" for a in audits),
        "audits": audits,
    }
