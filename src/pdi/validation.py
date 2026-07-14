from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def validate_schema(data: dict[str, Any], schema_path: Path) -> list[str]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    return [
        f"{'.'.join(str(x) for x in err.path)}: {err.message}"
        for err in Draft202012Validator(schema).iter_errors(data)
    ]


def _numeric_tokens(value: Any) -> list[str]:
    return re.findall(r"(?<![A-Za-z])\d+(?:[.,]\d+)?%?", str(value or ""))


def validate_ai_output(
    output: dict[str, Any] | None,
    evidence: list[dict[str, str]],
    approved_terms: list[str],
    support_ids: set[str] | None = None,
) -> dict[str, Any]:
    if output is None:
        return {"valid": False, "errors": ["NO_OUTPUT"], "unsupported_claim_count": 0}

    evidence_map = {str(x.get("id")): str(x.get("text", "")) for x in evidence if x.get("id")}
    evidence_text = " ".join(evidence_map.values()).casefold()
    approved = {str(term).casefold() for term in approved_terms if term}
    errors: list[str] = []
    unsupported = 0
    material_keys = {
        "finding",
        "claim",
        "conclusion",
        "significance",
        "risk_assessment",
        "laboratory_finding",
        "official_action",
        "what_changed",
        "takeaway",
    }

    def walk(value: Any, path: str = "", parent_ids: list[str] | None = None) -> None:
        nonlocal unsupported
        if isinstance(value, dict):
            ids_raw = value.get("evidence_ids")
            ids = [str(x) for x in ids_raw] if isinstance(ids_raw, list) else []
            if ids_raw is not None:
                missing = [x for x in ids if x not in evidence_map]
                if missing:
                    errors.append(f"{path}.evidence_ids missing: {missing}")
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else key
                if key in material_keys and isinstance(child, (str, int, float)) and child not in (None, "") and evidence_map and not ids:
                    errors.append(f"{child_path} has no evidence_ids")
                if key == "pathogens" and isinstance(child, list):
                    for pathogen in child:
                        token = str(pathogen or "").casefold()
                        if token and token not in evidence_text and token not in approved:
                            errors.append(f"Unsupported pathogen at {child_path}: {pathogen}")
                if key == "supporting_item_ids" and isinstance(child, list) and support_ids is not None:
                    for supporting_id in child:
                        if supporting_id not in support_ids:
                            errors.append(f"Unknown supporting item id at {child_path}: {supporting_id}")
                walk(child, child_path, ids or parent_ids)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", parent_ids)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            token = str(value)
            cited_text = " ".join(evidence_map.get(i, "") for i in (parent_ids or []))
            search_text = cited_text.casefold() if cited_text else evidence_text
            if evidence_map and token not in search_text:
                unsupported += 1
                errors.append(f"Unsupported numeric value at {path}: {token}")
        elif isinstance(value, str) and evidence_map:
            numbers = _numeric_tokens(value)
            if numbers:
                cited_text = " ".join(evidence_map.get(i, "") for i in (parent_ids or []))
                search_text = cited_text if cited_text else " ".join(evidence_map.values())
                for token in numbers:
                    normalized = token.replace(",", "")
                    haystack = search_text.replace(",", "")
                    if normalized not in haystack:
                        unsupported += 1
                        errors.append(f"Unsupported numeric token at {path}: {token}")

    walk(output)
    return {
        "valid": not errors,
        "errors": errors[:50],
        "unsupported_claim_count": unsupported,
    }
