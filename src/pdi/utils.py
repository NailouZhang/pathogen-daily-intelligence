from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value: str, length: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def content_hash(data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_space(value: str | None) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def normalize_title(value: str | None) -> str:
    text = normalize_space(value).casefold()
    text = re.sub(r"[^\w\u3400-\u9fff]+", " ", text)
    return normalize_space(text)


def canonicalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    return text if text.startswith("10.") and "/" in text else None


def canonicalize_url(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    parts = urlsplit(value)
    query = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        lk = key.casefold()
        if lk.startswith("utm_") or lk in TRACKING_KEYS:
            continue
        query.append((key, val))
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def deep_get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def parse_date_loose(value: Any) -> tuple[str | None, str]:
    if value in (None, ""):
        return None, "unknown"
    if isinstance(value, (list, tuple)):
        value = "-".join(str(x) for x in value if x is not None)
    text = str(value).strip()
    for fmt, precision in (("%Y-%m-%d", "day"), ("%Y/%m/%d", "day"), ("%Y-%m", "month"), ("%Y", "year")):
        try:
            parsed = datetime.strptime(text[:10] if precision == "day" else text, fmt)
            if precision == "day":
                return parsed.date().isoformat(), precision
            if precision == "month":
                return parsed.strftime("%Y-%m"), precision
            return parsed.strftime("%Y"), precision
        except ValueError:
            pass
    match = re.search(r"(19|20)\d{2}(?:[-/]\d{1,2})?(?:[-/]\d{1,2})?", text)
    if match:
        token = match.group(0).replace("/", "-")
        parts = token.split("-")
        if len(parts) == 3:
            try:
                return date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat(), "day"
            except ValueError:
                return None, "unknown"
        if len(parts) == 2:
            return f"{int(parts[0]):04d}-{int(parts[1]):02d}", "month"
        return parts[0], "year"
    return None, "unknown"


def sentence_split(text: str | None, prefix: str) -> list[dict[str, str]]:
    clean = normalize_space(text)
    if not clean:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?。！？])\s+|(?<=[。！？])", clean) if p.strip()]
    return [{"id": f"{prefix}{i}", "text": part} for i, part in enumerate(parts, 1)]
