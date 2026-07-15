from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .http import HttpClient
from .markup import contains_cjk
from .utils import canonicalize_url, ensure_dict_field, normalize_space, sentence_split, utc_now_iso
from .scholarly_recovery import enrich_scholarly_works_with_fallbacks

_PAYWALL_MARKERS = (
    "subscribe to continue",
    "already a subscriber",
    "sign in to continue",
    "enable javascript and cookies",
    "access denied",
    "verify you are human",
    "付费订阅",
    "登录后阅读",
)
_NOISE_MARKERS = (
    "cookie policy",
    "privacy policy",
    "all rights reserved",
    "sign up for",
    "subscribe now",
    "newsletter",
    "advertisement",
    "recommended for you",
    "terms of use",
)


def _iter_jsonld(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        graph = value.get("@graph")
        if isinstance(graph, list):
            for child in graph:
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
            if not any(str(t or "").casefold() in {"article", "newsarticle", "report", "medicalscholarlyarticle", "scholarlyarticle"} for t in types):
                continue
            article_body = normalize_space(obj.get("articleBody"))
            description = normalize_space(obj.get("description"))
            candidate = article_body or description
            if candidate and (best_text is None or len(candidate) > len(best_text)):
                best_text = candidate
                metadata = {
                    "headline": normalize_space(obj.get("headline")) or None,
                    "date_published": obj.get("datePublished"),
                    "date_modified": obj.get("dateModified"),
                    "canonical_url": obj.get("url") or obj.get("mainEntityOfPage"),
                    "jsonld_field": "articleBody" if article_body else "description",
                }
    return best_text, metadata


def _paragraphs(container: Any) -> list[str]:
    paragraphs: list[str] = []
    seen: set[str] = set()
    for paragraph in container.find_all(["p", "li"]):
        text = normalize_space(paragraph.get_text(" ", strip=True))
        if len(text) < 40 or text in seen:
            continue
        low = text.casefold()
        if any(marker in low for marker in _NOISE_MARKERS):
            continue
        if re.fullmatch(r"[\W\d_]+", text):
            continue
        seen.add(text)
        paragraphs.append(text)
    return paragraphs


def _meta_description(soup: BeautifulSoup) -> str | None:
    for attrs in (
        {"name": "citation_abstract"},
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
        {"name": "dc.description"},
    ):
        node = soup.find("meta", attrs=attrs)
        value = normalize_space(node.get("content")) if node else ""
        if len(value) >= 80:
            return value
    return None


def _candidate_links(soup: BeautifulSoup, base_url: str) -> dict[str, str | None]:
    out: dict[str, str | None] = {"canonical": None, "amp": None}
    for rel, key in (("canonical", "canonical"), ("amphtml", "amp")):
        node = soup.find("link", rel=lambda value: value and rel in (value if isinstance(value, list) else [value]))
        if node and node.get("href"):
            out[key] = urljoin(base_url, str(node.get("href")))
    return out


def _extract_main_text(html_text: str, base_url: str = "") -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "lxml")
    links = _candidate_links(soup, base_url)
    jsonld_text, jsonld_meta = _jsonld_article(soup)
    meta_description = _meta_description(soup)
    candidates: list[tuple[int, str, str]] = []
    if jsonld_text:
        candidates.append((len(jsonld_text) + 500, jsonld_text, "jsonld_article"))

    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "iframe"]):
        tag.decompose()
    selectors = [
        "article",
        "main",
        "[role='main']",
        "[itemprop='articleBody']",
        ".article-body",
        ".article__body",
        ".story-body",
        ".story__body",
        ".post-content",
        ".entry-content",
        ".content-body",
        ".article-content",
        ".news-content",
        "#article-body",
        "#story-body",
    ]
    for selector in selectors:
        for node in soup.select(selector):
            paragraphs = _paragraphs(node)
            text = " ".join(paragraphs)
            if len(text) >= 180:
                # Reward coherent paragraph count, not only raw page length.
                score = len(text) + min(2000, len(paragraphs) * 80)
                candidates.append((score, text, f"css:{selector}"))
    fallback_paragraphs = _paragraphs(soup)
    fallback = " ".join(fallback_paragraphs)
    if len(fallback) >= 180:
        candidates.append((len(fallback), fallback, "paragraph_fallback"))
    if meta_description:
        candidates.append((len(meta_description), meta_description, "meta_description"))

    if not candidates:
        return {"text": None, "method": "none", "metadata": jsonld_meta, "links": links, "meta_description": meta_description}
    _, text, method = max(candidates, key=lambda row: row[0])
    return {"text": normalize_space(text), "method": method, "metadata": jsonld_meta, "links": links, "meta_description": meta_description}


def _term_positions(text: str, terms: list[str]) -> list[int]:
    low = text.casefold()
    positions: list[int] = []
    for term in terms:
        token = normalize_space(term).casefold()
        if not token or len(token) < 4:
            continue
        start = 0
        while True:
            pos = low.find(token, start)
            if pos < 0:
                break
            positions.append(pos)
            start = pos + max(1, len(token))
    return sorted(set(positions))


def _focus_evidence(
    title: str,
    text: str,
    terms: list[str],
    max_chars: int,
    max_sentences: int,
) -> dict[str, Any]:
    sentences = sentence_split(text, "N")
    title_low = title.casefold()
    title_direct = any(term.casefold() in title_low for term in terms if len(term) >= 4)
    matching_indices = [
        i
        for i, row in enumerate(sentences)
        if any(term.casefold() in row["text"].casefold() for term in terms if len(term) >= 4)
    ]
    selected: set[int] = set()
    if title_direct:
        selected.update(range(min(8, len(sentences))))
    for index in matching_indices:
        selected.update(i for i in range(max(0, index - 1), min(len(sentences), index + 2)))
    if not selected and sentences:
        selected.update(range(min(3, len(sentences))))
    rows: list[dict[str, str]] = []
    chars = 0
    for index in sorted(selected):
        text_value = sentences[index]["text"]
        if chars + len(text_value) > max_chars or len(rows) >= max_sentences:
            break
        rows.append({"id": f"N{len(rows) + 1}", "text": text_value})
        chars += len(text_value) + 1
    focused = " ".join(row["text"] for row in rows)
    positions = _term_positions(text, terms)
    first_position = positions[0] if positions else None
    if title_direct and len(focused) >= 500:
        coverage = "full_relevant_extract"
    elif matching_indices and len(focused) >= 220:
        coverage = "focused_partial"
    elif focused:
        coverage = "title_or_snippet_only"
    else:
        coverage = "unavailable"
    return {
        "text": focused or None,
        "sentences": rows,
        "coverage": coverage,
        "title_direct": title_direct,
        "mention_count": len(positions),
        "first_mention_position": first_position,
        "raw_sentence_count": len(sentences),
    }


def _fetch_page(client: HttpClient, url: str) -> tuple[Any | None, dict[str, Any]]:
    response, http_audit = client.request("GET", url, max_attempts=2, allow_redirects=True)
    return response, http_audit.__dict__.copy()


def _fetch_one_news(
    article: dict[str, Any],
    timeout: int,
    user_agent: str,
    max_chars: int,
    max_sentences: int,
    terms: list[str],
) -> dict[str, Any]:
    url = article.get("canonical_url") or article.get("original_url")
    audit: dict[str, Any] = {
        "status": "skipped",
        "requested_url": url,
        "final_url": None,
        "method": None,
        "attempted_urls": [],
        "retrieved_at": utc_now_iso(),
        "raw_char_count": 0,
        "focused_char_count": 0,
        "sentence_count": 0,
        "coverage_level": "unavailable",
        "error": None,
        "copyright_policy": "bounded evidence extraction; full publisher page is not persisted",
    }
    if not url or urlsplit(str(url)).scheme not in {"http", "https"}:
        audit["error"] = "UNSUPPORTED_OR_MISSING_URL"
        return {"article_id": article.get("article_id"), "audit": audit}

    client = HttpClient(timeout=timeout, user_agent=user_agent)
    queue = [str(url)]
    visited: set[str] = set()
    best: dict[str, Any] | None = None
    while queue and len(visited) < 3:
        current_url = queue.pop(0)
        if current_url in visited:
            continue
        visited.add(current_url)
        audit["attempted_urls"].append(current_url)
        response, http = _fetch_page(client, current_url)
        if response is None:
            audit.setdefault("http_failures", []).append(http)
            continue
        content_type = response.headers.get("Content-Type", "")
        if "html" not in content_type.casefold() and not response.text.lstrip().startswith("<"):
            audit.setdefault("http_failures", []).append({**http, "error": "NON_HTML_CONTENT"})
            continue
        extracted = _extract_main_text(response.text, response.url)
        text = extracted.get("text")
        if text:
            focused = _focus_evidence(
                (article.get("title") or {}).get("original") or "",
                text,
                terms,
                max_chars,
                max_sentences,
            )
            candidate = {
                "raw_text": text,
                "analysis_text": focused.get("text"),
                "sentences": focused.get("sentences"),
                "coverage": focused.get("coverage"),
                "method": extracted.get("method"),
                "metadata": extracted.get("metadata"),
                "final_url": response.url,
                "http": http,
                "focus": focused,
            }
            if best is None or len(candidate.get("analysis_text") or "") > len(best.get("analysis_text") or ""):
                best = candidate
        links = extracted.get("links") or {}
        for link in (links.get("canonical"), links.get("amp")):
            if link and link not in visited and link not in queue:
                queue.append(link)

    if not best or not best.get("analysis_text"):
        snippet = normalize_space((article.get("content") or {}).get("original_excerpt") or (article.get("content") or {}).get("excerpt"))
        if snippet:
            focused = _focus_evidence((article.get("title") or {}).get("original") or "", snippet, terms, min(max_chars, 2500), min(max_sentences, 12))
            audit.update(
                {
                    "status": "partial",
                    "method": "source_snippet_fallback",
                    "focused_char_count": len(focused.get("text") or ""),
                    "sentence_count": len(focused.get("sentences") or []),
                    "coverage_level": "title_or_snippet_only",
                    "error": "FULL_PAGE_CONTENT_NOT_RETRIEVED",
                }
            )
            return {
                "article_id": article.get("article_id"),
                "analysis_text": focused.get("text"),
                "sentences": focused.get("sentences") or [],
                "final_url": None,
                "audit": audit,
            }
        audit.update({"status": "failed", "error": "NO_USABLE_PAGE_OR_SNIPPET"})
        return {"article_id": article.get("article_id"), "audit": audit}

    raw = best.get("raw_text") or ""
    low = raw.casefold()
    if len(raw) < 500 and any(marker in low for marker in _PAYWALL_MARKERS):
        audit.update({"status": "failed", "method": best.get("method"), "error": "PAYWALL_OR_ACCESS_INTERSTITIAL"})
        return {"article_id": article.get("article_id"), "audit": audit}

    focus = best.get("focus") or {}
    audit.update(
        {
            "status": "success" if focus.get("coverage") != "title_or_snippet_only" else "partial",
            "final_url": best.get("final_url"),
            "method": best.get("method"),
            "raw_char_count": len(raw),
            "focused_char_count": len(best.get("analysis_text") or ""),
            "sentence_count": len(best.get("sentences") or []),
            "coverage_level": focus.get("coverage"),
            "title_direct_pathogen_match": focus.get("title_direct"),
            "pathogen_mention_count": focus.get("mention_count"),
            "first_pathogen_mention_position": focus.get("first_mention_position"),
            "metadata": best.get("metadata"),
            "http": best.get("http"),
            "truncated": len(raw) > len(best.get("analysis_text") or ""),
        }
    )
    return {
        "article_id": article.get("article_id"),
        "analysis_text": best.get("analysis_text"),
        "sentences": best.get("sentences") or [],
        "final_url": best.get("final_url"),
        "audit": audit,
    }


def _accepted_terms(profile: dict[str, Any]) -> list[str]:
    terms = [
        str(term.get("term"))
        for term in profile.get("lexicon", [])
        if term.get("status") == "accepted_for_search"
        and term.get("term_type") in {"pathogen_name", "virus_entity_name", "taxonomy_family", "taxonomy_genus", "disease_name", "clinical_syndrome"}
        and term.get("term")
    ]
    return sorted(set(terms), key=len, reverse=True)


def _title_focus_score(article: dict[str, Any], terms: list[str]) -> int:
    title = ((article.get("title") or {}).get("original") or "").casefold()
    return sum(1 for term in terms if len(term) >= 4 and term.casefold() in title)


def enrich_news_articles(articles: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    policy = profile.get("content_policy") or {}
    maximum = int(policy.get("max_news_fetches_per_issue", 45))
    max_chars = int(policy.get("max_news_analysis_chars", 10000))
    max_sentences = int(policy.get("max_news_evidence_sentences", 36))
    max_translation_chars = int(policy.get("max_news_translation_chars", 2800))
    workers = max(1, int(policy.get("news_fetch_workers", 6)))
    timeout = int(profile.get("search_policy", {}).get("request_timeout_seconds", 20))
    user_agent = str(policy.get("user_agent") or "PathogenDailyIntelligence/1.5 (research monitoring)")
    terms = _accepted_terms(profile)

    tier_rank = {"A": 0, "B": 1, "C": 2, "D": 3, "unknown": 4}
    candidates = sorted(
        articles,
        key=lambda article: (
            -_title_focus_score(article, terms),
            tier_rank.get((article.get("source") or {}).get("reliability_tier", "unknown"), 4),
            0 if not (article.get("content") or {}).get("excerpt") else 1,
            article.get("published_at") or "9999",
        ),
    )[:maximum]
    article_map = {article.get("article_id"): article for article in articles}
    audits: list[dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_one_news, article, timeout, user_agent, max_chars, max_sentences, terms): article.get("article_id")
            for article in candidates
        }
        for future in as_completed(futures):
            article_id = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"article_id": article_id, "audit": {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "retrieved_at": utc_now_iso(), "coverage_level": "unavailable"}}
            article = article_map.get(article_id)
            if not article:
                continue
            audit = result.get("audit") or {}
            ensure_dict_field(article, "retrieval_audit")["content_fetch"] = audit
            audits.append({"object_type": "news_article", "object_id": article_id, **audit})
            content = ensure_dict_field(article, "content")
            content["availability_status"] = "available" if audit.get("status") == "success" else ("partial" if audit.get("status") == "partial" else "not_retrieved")
            content["coverage_level"] = audit.get("coverage_level") or "unavailable"
            if audit.get("status") not in {"success", "partial"}:
                content["analysis_text"] = None
                content["sentences"] = sentence_split(content.get("original_excerpt"), "N")[:8]
                continue
            content.setdefault("original_excerpt", content.get("excerpt"))
            content["analysis_text"] = result.get("analysis_text")
            content["translation_text"] = str(result.get("analysis_text") or "")[:max_translation_chars] or content.get("original_excerpt")
            content["sentences"] = result.get("sentences") or []
            if result.get("analysis_text"):
                content["excerpt"] = str(result["analysis_text"])[:2200].rstrip() + ("…" if len(str(result["analysis_text"])) > 2200 else "")
            final_url = canonicalize_url(result.get("final_url"))
            if final_url:
                article["canonical_url"] = final_url
                ensure_dict_field(article, "fingerprints")["resolved_url"] = final_url

    # Items outside the fetch budget must be explicitly labelled, not silently
    # treated as full-content records.
    attempted_ids = {row.get("object_id") for row in audits}
    for article in articles:
        if article.get("article_id") in attempted_ids:
            continue
        content = ensure_dict_field(article, "content")
        content["availability_status"] = "not_attempted_budget"
        content["coverage_level"] = "title_or_snippet_only" if content.get("original_excerpt") else "title_only"
        ensure_dict_field(article, "retrieval_audit")["content_fetch"] = {
            "status": "skipped",
            "error": "FETCH_BUDGET_EXHAUSTED",
            "coverage_level": content["coverage_level"],
            "retrieved_at": utc_now_iso(),
        }

    return {
        "attempted": len(candidates),
        "success": sum(a.get("status") == "success" for a in audits),
        "partial": sum(a.get("status") == "partial" for a in audits),
        "failed": sum(a.get("status") == "failed" for a in audits),
        "skipped_budget": max(0, len(articles) - len(candidates)),
        "audits": audits,
    }


def _section_title(section: Any) -> str:
    title = section.find("title", recursive=False)
    return normalize_space(title.get_text(" ", strip=True)) if title else ""


def _section_text(section: Any) -> str:
    paragraphs = [normalize_space(p.get_text(" ", strip=True)) for p in section.find_all("p")]
    return " ".join(p for p in paragraphs if len(p) >= 35)


def _fetch_fulltext_work(work: dict[str, Any], timeout: int, user_agent: str, max_chars: int, max_sentences: int) -> dict[str, Any]:
    pmcid = (work.get("identifiers") or {}).get("pmcid")
    audit: dict[str, Any] = {"status": "skipped", "pmcid": pmcid, "source": "Europe PMC Open Access fullTextXML", "retrieved_at": utc_now_iso(), "error": None}
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
    key_terms = ("method", "material", "result", "finding", "discussion", "conclusion", "limitation", "方法", "结果", "讨论", "结论")
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
        rows = sentence_split(text[:remaining], "X")
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
    audit.update({"status": "success", "abstract_chars": len(abstract_text or ""), "section_count": len(section_rows), "evidence_sentence_count": sum(len(row["sentences"]) for row in section_rows), "copyright_policy": "Europe PMC open-access XML; only bounded analytical evidence is retained"})
    return {"work_id": work.get("work_id"), "abstract": abstract_text, "sections": section_rows, "url": url, "audit": audit}


def _fetch_europe_pmc_abstract(work: dict[str, Any], timeout: int, user_agent: str) -> dict[str, Any]:
    ids = work.get("identifiers") or {}
    queries: list[str] = []
    if ids.get("pmid"):
        queries.append(f'EXT_ID:{ids["pmid"]} AND SRC:MED')
    if ids.get("doi"):
        queries.append(f'DOI:"{ids["doi"]}"')
    audit: dict[str, Any] = {"status": "skipped", "source": "Europe PMC exact metadata lookup", "queries": queries, "retrieved_at": utc_now_iso(), "error": None}
    if not queries:
        audit["error"] = "NO_EXACT_IDENTIFIER"
        return {"work_id": work.get("work_id"), "audit": audit}
    client = HttpClient(timeout=timeout, user_agent=user_agent)
    for query in queries:
        data, http_audit = client.get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search", params={"query": query, "format": "json", "resultType": "core", "pageSize": 3}, max_attempts=2)
        audit.setdefault("http", []).append(http_audit.__dict__.copy())
        for rec in ((data or {}).get("resultList") or {}).get("result", []):
            abstract = normalize_space(rec.get("abstractText"))
            if len(abstract) >= 80:
                audit.update({"status": "success", "matched_id": rec.get("id"), "abstract_chars": len(abstract), "query": query})
                return {"work_id": work.get("work_id"), "abstract": abstract, "audit": audit}
    audit.update({"status": "failed", "error": "NO_ABSTRACT_IN_EXACT_LOOKUP"})
    return {"work_id": work.get("work_id"), "audit": audit}


def _fetch_publisher_metadata_abstract(work: dict[str, Any], timeout: int, user_agent: str) -> dict[str, Any]:
    doi = (work.get("identifiers") or {}).get("doi")
    audit = {"status": "skipped", "source": "publisher metadata", "doi": doi, "retrieved_at": utc_now_iso(), "error": None}
    if not doi:
        audit["error"] = "NO_DOI"
        return {"work_id": work.get("work_id"), "audit": audit}
    client = HttpClient(timeout=timeout, user_agent=user_agent)
    response, http_audit = client.request("GET", f"https://doi.org/{doi}", max_attempts=2, allow_redirects=True)
    audit["http"] = http_audit.__dict__.copy()
    if response is None:
        audit.update({"status": "failed", "error": http_audit.error or "DOI_LANDING_FETCH_FAILED"})
        return {"work_id": work.get("work_id"), "audit": audit}
    extracted = _extract_main_text(response.text, response.url)
    # Only metadata descriptions are accepted here; article body may be copyrighted
    # or paywalled and is not used as a substitute for an abstract.
    abstract = normalize_space(extracted.get("meta_description"))
    if len(abstract) < 80:
        audit.update({"status": "failed", "error": "NO_METADATA_ABSTRACT", "final_url": response.url})
        return {"work_id": work.get("work_id"), "audit": audit}
    audit.update({"status": "success", "final_url": response.url, "abstract_chars": len(abstract), "method": "meta_description"})
    return {"work_id": work.get("work_id"), "abstract": abstract, "audit": audit}


def enrich_scholarly_works(works: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    """Recover abstracts and analysable full text through a layered, audited chain.

    Metadata-only works are retained.  Missing evidence disables content claims,
    not discovery or publication of the bibliographic record.
    """
    return enrich_scholarly_works_with_fallbacks(works, profile)
