from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .utils import read_json


@dataclass(frozen=True)
class ProjectPaths:
    root: Path
    profile_dir: Path
    manifest: Path
    lexicon: Path
    query_groups: Path
    source_registry: Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def profile_paths(profile_id: str = "hantavirus", root: Path | None = None) -> ProjectPaths:
    root = root or project_root()
    pdir = root / "profiles" / profile_id
    return ProjectPaths(root, pdir, pdir / "manifest.yaml", pdir / "lexicon.json", pdir / "query_groups.json", pdir / "source_registry.json")


def load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be an object: {path}")
    return data


def load_profile(profile_id: str = "hantavirus", root: Path | None = None) -> dict[str, Any]:
    paths = profile_paths(profile_id, root)
    profile = load_yaml(paths.manifest)
    profile["lexicon"] = read_json(paths.lexicon, []) or []
    profile["query_groups"] = read_json(paths.query_groups, {}) or {}
    profile["source_registry"] = read_json(paths.source_registry, {}) or {}
    glossary_path = paths.profile_dir / "translation_glossary.json"
    profile["translation_glossary"] = read_json(glossary_path, {}) or {}
    return profile


def env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def env_bool(name: str, default: bool = False) -> bool:
    value = env(name)
    if value is None:
        return default
    return value.strip().casefold() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(env(name, str(default)) or default)
    except ValueError:
        return default
