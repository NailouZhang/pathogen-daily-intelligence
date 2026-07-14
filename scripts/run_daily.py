#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pdi.pipeline import run_daily_pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a bilingual pathogen daily intelligence issue.")
    parser.add_argument("--profile", default="hantavirus")
    parser.add_argument("--output-dir", default="build/output")
    parser.add_argument("--state-dir", default=None)
    parser.add_argument("--demo", action="store_true", help="Use deterministic built-in demonstration records; no network is required.")
    parser.add_argument("--disable-llm", action="store_true", help="Do not call any LLM provider.")
    args = parser.parse_args()

    result = run_daily_pipeline(
        root=ROOT,
        profile_id=args.profile,
        output_dir=Path(args.output_dir).resolve(),
        state_dir=Path(args.state_dir).resolve() if args.state_dir else None,
        demo_mode=args.demo,
        disable_llm=args.disable_llm,
    )
    print(json.dumps({
        "issue_id": result["issue"]["issue_id"],
        "statistics": result["issue"]["statistics"],
        "manifest": result["manifest"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
