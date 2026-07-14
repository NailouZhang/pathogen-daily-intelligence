#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pdi.config import load_profile, profile_paths
from src.pdi.query_planner import build_query_tasks
from src.pdi.utils import content_hash, utc_now_iso, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Compile and audit a pathogen profile without auto-approving new taxonomy terms.")
    parser.add_argument("--profile", default="hantavirus")
    parser.add_argument("--output-dir", default="build/bootstrap")
    args = parser.parse_args()
    out = Path(args.output_dir).resolve()
    profile = load_profile(args.profile, ROOT)
    schema = json.loads((ROOT / "schemas/pathogen_profile.schema.json").read_text(encoding="utf-8"))
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(profile)]
    query_plan = {}
    for source in profile.get("source_registry", {}).get("sources", []):
        if source.get("enabled"):
            query_plan[source["source_id"]] = [x.as_dict() for x in build_query_tasks(profile, source)]
    accepted = [x for x in profile.get("lexicon", []) if x.get("status") == "accepted_for_search"]
    candidates = [x for x in profile.get("lexicon", []) if x.get("status") == "candidate"]
    report = {
        "schema_version": "1.0",
        "profile_id": args.profile,
        "generated_at": utc_now_iso(),
        "status": "ready_for_manual_review" if not errors else "invalid",
        "schema_errors": errors,
        "taxonomy_status": {
            "ictv_release": profile.get("taxonomy", {}).get("ictv_release"),
            "notice": "The bundled seed profile is approved for search, not represented as a complete ICTV-verified taxonomy list.",
        },
        "accepted_search_terms": accepted,
        "candidate_terms": candidates,
        "query_plan": query_plan,
        "source_registry": profile.get("source_registry", {}),
        "profile_hash": content_hash(profile),
        "manual_approval_required": True,
    }
    write_json(out / f"{args.profile}_bootstrap_report.json", report)
    (out / f"{args.profile}_bootstrap_report.md").write_text(
        "# Pathogen bootstrap audit\n\n"
        f"- Profile: `{args.profile}`\n"
        f"- Status: `{report['status']}`\n"
        f"- Accepted search terms: {len(accepted)}\n"
        f"- Candidate terms: {len(candidates)}\n"
        f"- ICTV release: `{report['taxonomy_status']['ictv_release']}`\n\n"
        "No generated candidate is automatically promoted into production. Review the JSON report before changing the approved profile.\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
