from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .config import Settings, load_seed
from .http import HttpClient
from .llm import LLMError, LLMRouter
from .utils import clean_space, dump_json, sha256_text, unique_strings, utc_now_iso


def _html_to_text(raw: str, limit: int = 18000) -> str:
    soup = BeautifulSoup(raw, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "form"]):
        tag.decompose()
    text = clean_space(soup.get_text(" "))
    return text[:limit]


def _fetch_authoritative_context(settings: Settings, seed: dict[str, Any], http: HttpClient) -> list[dict[str, Any]]:
    profile_id = settings.profile_id
    urls = list(seed.get("authoritative_urls") or [])
    generic = [
        f"https://viralzone.expasy.org/search?query={quote_plus(profile_id)}",
        f"https://ictv.global/search?search_api_fulltext={quote_plus(profile_id)}",
        f"https://ictv.global/search?keys={quote_plus(profile_id)}",
    ]
    urls.extend(generic)
    records: list[dict[str, Any]] = []
    for url in unique_strings(urls):
        try:
            raw = http.get_text(url)
            text = _html_to_text(raw)
            if len(text) < 200:
                continue
            records.append({
                "url": url,
                "retrieved_at": utc_now_iso(),
                "text": text,
                "content_hash": sha256_text(text),
            })
        except Exception as exc:
            records.append({"url": url, "error": clean_space(exc)[:300]})
    return records


def _fallback_profile(seed: dict[str, Any], sources: list[dict[str, Any]]) -> dict[str, Any]:
    terms = unique_strings(
        list(seed.get("seed_terms") or [])
        + list(seed.get("virus_names") or [])
        + list(seed.get("disease_names_en") or [])
        + list(seed.get("disease_names_zh") or [])
    )
    en_terms = [t for t in terms if not re.search(r"[\u4e00-\u9fff]", t)]
    zh_terms = [t for t in terms if re.search(r"[\u4e00-\u9fff]", t)]
    anchor = en_terms[0] if en_terms else seed.get("profile_id", "pathogen")
    return {
        "schema_version": "2.0",
        "profile_id": seed.get("profile_id") or anchor.lower().replace(" ", "_"),
        "display_name_en": seed.get("display_name_en") or anchor,
        "display_name_zh": seed.get("display_name_zh") or anchor,
        "taxonomy": seed.get("taxonomy") or {},
        "english_terms": en_terms,
        "chinese_terms": zh_terms,
        "virus_names": unique_strings(seed.get("virus_names") or en_terms),
        "disease_names_en": unique_strings(seed.get("disease_names_en") or []),
        "disease_names_zh": unique_strings(seed.get("disease_names_zh") or []),
        "hosts": unique_strings(seed.get("hosts") or []),
        "transmission_terms": unique_strings(seed.get("transmission_terms") or []),
        "negative_terms": unique_strings(seed.get("negative_terms") or []),
        "translation_glossary": seed.get("translation_glossary") or [],
        "query_groups": seed.get("query_groups") or [
            {"id": "core", "terms": en_terms[:8], "topics": []},
            {"id": "clinical", "terms": en_terms[:5], "topics": ["infection", "disease", "diagnosis", "treatment"]},
            {"id": "epidemiology", "terms": en_terms[:5], "topics": ["outbreak", "surveillance", "epidemiology", "case"]},
            {"id": "ecology", "terms": en_terms[:5], "topics": ["reservoir", "host", "ecology", "spillover"]},
        ],
        "authoritative_sources": sources,
        "generated_by": "deterministic_seed_fallback",
        "generated_at": utc_now_iso(),
    }


def build_profile(settings: Settings, http: HttpClient, llm: LLMRouter) -> dict[str, Any]:
    seed = load_seed(settings.project_root, settings.profile_id)
    sources = _fetch_authoritative_context(settings, seed, http)
    fallback = _fallback_profile(seed, sources)
    usable = [record for record in sources if record.get("text")]
    if not usable or not llm.available:
        profile = fallback
    else:
        prompt_path = settings.project_root / "prompts" / "profile_bootstrap.md"
        system = prompt_path.read_text(encoding="utf-8")
        context = [{"url": r["url"], "text": r["text"][:10000]} for r in usable]
        prompt = json.dumps({
            "profile_id_seed": settings.profile_id,
            "manual_seed": seed,
            "authoritative_page_text": context,
            "required_output_language": "Bilingual English and Simplified Chinese",
        }, ensure_ascii=False)

        def validator(data: Any) -> tuple[bool, str]:
            if not isinstance(data, dict):
                return False, "not an object"
            if not data.get("english_terms") or not data.get("query_groups"):
                return False, "missing english_terms or query_groups"
            return True, "ok"

        try:
            result = llm.json_task(system=system, prompt=prompt, validator=validator, max_models_per_provider=2)
            profile = dict(fallback)
            profile.update(result.data)
            profile["profile_id"] = settings.profile_id
            profile["authoritative_sources"] = sources
            profile["generated_by"] = f"{result.provider}:{result.model}"
            profile["llm_attempts"] = result.attempts
            profile["generated_at"] = utc_now_iso()
        except LLMError:
            profile = fallback
    target = settings.state_dir.parent / "profiles" / settings.profile_id / "profile.json"
    dump_json(target, profile)
    return profile
