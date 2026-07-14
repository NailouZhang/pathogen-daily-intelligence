#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.pdi.config import load_profile


def main() -> int:
    required = [
        "app.py",
        "requirements.txt",
        ".github/workflows/daily-intelligence.yml",
        ".github/workflows/bootstrap-pathogen.yml",
        ".github/workflows/refresh-pathogen-profile.yml",
        "profiles/hantavirus/manifest.yaml",
        "schemas/daily_issue.schema.json",
    ]
    missing = [x for x in required if not (ROOT / x).exists()]
    if missing:
        raise SystemExit(f"Missing project files: {missing}")
    for path in (ROOT / ".github/workflows").glob("*.yml"):
        yaml.safe_load(path.read_text(encoding="utf-8"))
    profile = load_profile("hantavirus", ROOT)
    schema = json.loads((ROOT / "schemas/pathogen_profile.schema.json").read_text(encoding="utf-8"))
    errors = list(Draft202012Validator(schema).iter_errors(profile))
    if errors:
        raise SystemExit("Profile schema errors: " + "; ".join(e.message for e in errors[:10]))
    print("Project validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
