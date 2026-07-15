from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .utils import load_json


@dataclass
class Settings:
    profile_id: str
    project_root: Path
    output_dir: Path
    state_dir: Path
    window_days: int = 7
    max_papers: int = 24
    max_news: int = 36
    max_news_fetches: int = 50
    max_fulltexts: int = 18
    timezone: str = "Asia/Shanghai"

    @property
    def secrets(self) -> dict[str, str]:
        names = [
            "CROSSREF_MAILTO",
            "NCBI_API_KEY",
            "GEMINI_API_KEY",
            "GROQ_API_KEY",
            "SEMANTIC_SCHOLAR_API_KEY",
        ]
        return {name: os.getenv(name, "").strip() for name in names}

    @property
    def user_agent(self) -> str:
        email = self.secrets.get("CROSSREF_MAILTO") or "contact@example.org"
        return os.getenv("PDI_USER_AGENT", f"PathogenDailyIntelligence/2.0 ({email})")


def load_seed(project_root: Path, profile_id: str) -> dict[str, Any]:
    path = project_root / "profiles" / profile_id / "seed.yaml"
    if not path.exists():
        return {
            "profile_id": profile_id,
            "display_name_en": profile_id,
            "display_name_zh": profile_id,
            "seed_terms": [profile_id],
            "authoritative_urls": [],
        }
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_profile(settings: Settings) -> dict[str, Any] | None:
    persisted = settings.state_dir.parent / "profiles" / settings.profile_id / "profile.json"
    bundled = settings.project_root / "profiles" / settings.profile_id / "profile.json"
    return load_json(persisted) or load_json(bundled)
