#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pdi.config import load_profile
from src.pdi.http import HttpClient
from src.pdi.utils import content_hash, read_json, utc_now_iso, write_json


def check_source(source: dict[str, Any]) -> dict[str, Any]:
    url = source.get("list_url") or source.get("base_url")
    if not url:
        return {"source_id": source.get("source_id"), "status": "not_checked", "reason": "no_health_url"}
    client = HttpClient(timeout=12)
    response, audit = client.request("GET", url, max_attempts=1)
    return {
        "source_id": source.get("source_id"),
        "status": "reachable" if response is not None else "failed",
        "url": url,
        "http_status": audit.status_code,
        "etag": audit.response_headers.get("ETag") or audit.response_headers.get("etag"),
        "last_modified": audit.response_headers.get("Last-Modified") or audit.response_headers.get("last-modified"),
        "content_hash": content_hash(response.text[:500000]) if response is not None else None,
        "error": audit.error,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check source health and create a non-destructive pathogen profile refresh diff.")
    parser.add_argument("--profile", default="hantavirus")
    parser.add_argument("--output-dir", default="build/profile-refresh")
    args = parser.parse_args()
    out = Path(args.output_dir).resolve()
    profile = load_profile(args.profile, ROOT)
    sources = profile.get("source_registry", {}).get("sources", [])
    checks: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(6, max(1, len(sources)))) as pool:
        futures = {pool.submit(check_source, source): source for source in sources}
        for future in as_completed(futures):
            source = futures[future]
            try:
                checks.append(future.result())
            except Exception as exc:
                checks.append({"source_id": source.get("source_id"), "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
    checks.sort(key=lambda x: str(x.get("source_id")))

    previous = read_json(out / "latest_refresh.json", {}) or {}
    previous_map = {x.get("source_id"): x for x in previous.get("source_checks", [])}
    changes = []
    for current in checks:
        old = previous_map.get(current.get("source_id"))
        if old and old.get("content_hash") != current.get("content_hash"):
            changes.append({"source_id": current.get("source_id"), "change": "content_hash_changed", "old": old.get("content_hash"), "new": current.get("content_hash")})
        elif old and old.get("status") != current.get("status"):
            changes.append({"source_id": current.get("source_id"), "change": "health_status_changed", "old": old.get("status"), "new": current.get("status")})
    report = {
        "schema_version": "1.0",
        "profile_id": args.profile,
        "generated_at": utc_now_iso(),
        "profile_version": profile.get("profile_version"),
        "ictv_release": profile.get("taxonomy", {}).get("ictv_release"),
        "source_checks": checks,
        "changes": changes,
        "auto_applied": False,
        "manual_review_required": bool(changes),
        "notice": "This workflow never overwrites the approved lexicon or taxonomy automatically.",
    }
    write_json(out / "latest_refresh.json", report)
    write_json(out / f"refresh_{report['generated_at'].replace(':','-')}.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
