from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .llm import LLMError, LLMRouter
from .utils import clean_space, split_sentences, truncate


REVIEW_HINTS = re.compile(
    r"\b(review|systematic review|meta-analysis|narrative review|scoping review|viewpoint|perspective|commentary)\b",
    flags=re.I,
)


def classify_paper(work: dict[str, Any]) -> str:
    types = " ".join(str(x) for x in work.get("publication_types") or [])
    title = clean_space(work.get("title"))
    abstract = clean_space(work.get("abstract"))[:500]
    if REVIEW_HINTS.search(" ".join([types, title, abstract])):
        return "review"
    return "research"


def _evidence_payload(text: str, prefix: str, max_sentences: int) -> list[dict[str, str]]:
    return [{"id": f"{prefix}{idx}", "text": sentence} for idx, sentence in enumerate(split_sentences(text, max_sentences), 1)]


def build_paper_evidence(work: dict[str, Any]) -> dict[str, Any]:
    abstract = clean_space(work.get("abstract"))
    full = clean_space(work.get("full_text"))
    evidence = _evidence_payload(abstract, "A", 24)
    if full:
        evidence.extend(_evidence_payload(full, "F", 36))
    return {
        "paper_id": work.get("paper_id"),
        "title": work.get("title"),
        "bibliography": {
            "authors": (work.get("authors") or [])[:12],
            "journal": work.get("journal"),
            "year": work.get("year"),
            "doi": work.get("doi"),
            "publication_types": work.get("publication_types") or [],
            "evidence_level": work.get("evidence_level"),
        },
        "evidence": evidence,
        "evidence_scope": "abstract_and_partial_or_full_text" if full else ("abstract_only" if abstract else "metadata_only"),
    }


def build_news_evidence(article: dict[str, Any]) -> dict[str, Any]:
    content = clean_space(article.get("content"))
    excerpt = clean_space(article.get("excerpt"))
    evidence_text = content or excerpt
    return {
        "news_id": article.get("news_id"),
        "title": article.get("title"),
        "publisher": article.get("publisher"),
        "published_date": article.get("published_date"),
        "url": article.get("resolved_url") or article.get("url"),
        "content_status": article.get("content_status"),
        "evidence": _evidence_payload(evidence_text, "N", 45),
    }


def _paper_validator(kind: str):
    required = (
        ["background", "methods", "results", "contribution", "limitations"]
        if kind == "research"
        else ["background", "main_directions", "current_state", "gaps", "future_research"]
    )

    def validator(data: Any) -> tuple[bool, str]:
        if not isinstance(data, dict):
            return False, "not object"
        analysis = data.get("analysis")
        if not isinstance(analysis, dict):
            return False, "analysis missing"
        populated = sum(bool(clean_space(analysis.get(key))) for key in required)
        if populated < 3:
            return False, "fewer than three required analytical elements"
        return True, "ok"

    return validator


def analyze_paper(work: dict[str, Any], llm: LLMRouter, prompts_dir: Path) -> dict[str, Any]:
    kind = classify_paper(work)
    work["paper_type"] = kind
    payload = build_paper_evidence(work)
    if not payload["evidence"]:
        work["analysis"] = {
            "status": "not_run_no_abstract_or_fulltext",
            "kind": kind,
            "analysis": {},
            "summary_en": "",
        }
        return work
    prompt_file = "research_analysis.md" if kind == "research" else "review_analysis.md"
    system = (prompts_dir / prompt_file).read_text(encoding="utf-8")
    try:
        result = llm.json_task(
            system=system,
            prompt=json.dumps(payload, ensure_ascii=False),
            validator=_paper_validator(kind),
            max_models_per_provider=3,
        )
        data = result.data if isinstance(result.data, dict) else {}
        data.update({"status": "passed", "kind": kind, "provider": result.provider, "model": result.model, "attempts": result.attempts})
        work["analysis"] = data
    except LLMError as exc:
        # A failed model must not erase the source evidence. Produce a small deterministic
        # evidence description rather than showing an empty internal-error card.
        sentences = [item["text"] for item in payload["evidence"][:5]]
        work["analysis"] = {
            "status": "fallback_source_extract",
            "kind": kind,
            "analysis": {
                "background": truncate(sentences[0] if sentences else "", 260),
                "methods": truncate(sentences[1] if len(sentences) > 1 else "未从现有证据中明确报告。", 220),
                "results": truncate(" ".join(sentences[2:4]), 320) if len(sentences) > 2 else "未从现有证据中明确报告。",
                "contribution": truncate(sentences[4] if len(sentences) > 4 else "需阅读全文进一步判断。", 220),
                "limitations": "当前仅依据已获得的摘要或正文片段，未进行超出证据的推断。",
            } if kind == "research" else {
                "background": truncate(sentences[0] if sentences else "", 260),
                "main_directions": truncate(" ".join(sentences[1:3]), 320) if len(sentences) > 1 else "未从现有证据中明确报告。",
                "current_state": truncate(sentences[3] if len(sentences) > 3 else "需阅读全文进一步判断。", 220),
                "gaps": "当前仅依据已获得的摘要或正文片段，未进行超出证据的推断。",
                "future_research": truncate(sentences[4] if len(sentences) > 4 else "需根据全文进一步判断。", 220),
            },
            "summary_en": "",
            "error": clean_space(exc)[:600],
        }
    return work


def _news_validator(data: Any) -> tuple[bool, str]:
    if not isinstance(data, dict) or not isinstance(data.get("analysis"), dict):
        return False, "analysis missing"
    analysis = data["analysis"]
    populated = sum(bool(clean_space(analysis.get(key))) for key in ("time", "location", "event", "impact", "status"))
    if populated < 3:
        return False, "fewer than three news elements"
    return True, "ok"


def analyze_news(article: dict[str, Any], llm: LLMRouter, prompts_dir: Path) -> dict[str, Any]:
    payload = build_news_evidence(article)
    if not payload["evidence"]:
        article["analysis"] = {
            "status": "not_run_no_content",
            "analysis": {
                "time": article.get("published_date") or "未报告",
                "location": "未从标题或来源元数据中可靠确定",
                "event": "未抓获新闻正文，不能根据标题扩写事件内容",
                "impact": "未获得正文，无法判断",
                "status": "仅保留标题和来源元数据",
            },
        }
        return article
    system = (prompts_dir / "news_analysis.md").read_text(encoding="utf-8")
    try:
        result = llm.json_task(
            system=system,
            prompt=json.dumps(payload, ensure_ascii=False),
            validator=_news_validator,
            max_models_per_provider=3,
        )
        data = result.data if isinstance(result.data, dict) else {}
        data.update({"status": "passed", "provider": result.provider, "model": result.model, "attempts": result.attempts})
        article["analysis"] = data
    except LLMError as exc:
        sentences = [item["text"] for item in payload["evidence"][:5]]
        article["analysis"] = {
            "status": "fallback_source_extract",
            "analysis": {
                "time": article.get("published_date") or "未报告",
                "location": "未从现有证据中明确报告",
                "event": truncate(sentences[0] if sentences else article.get("title"), 260),
                "impact": truncate(" ".join(sentences[1:3]), 300) if len(sentences) > 1 else "未从现有证据中明确报告",
                "status": truncate(" ".join(sentences[3:5]), 280) if len(sentences) > 3 else "需进一步核实",
            },
            "error": clean_space(exc)[:600],
        }
    return article
