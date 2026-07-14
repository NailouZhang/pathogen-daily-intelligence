from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceResult:
    source_id: str
    status: str
    records: list[dict[str, Any]] = field(default_factory=list)
    audits: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    query_count: int = 0

    def health(self) -> dict[str, Any]:
        if self.status == "success" and not self.records:
            state = "success_no_results"
        elif self.status == "success":
            state = "success_with_results"
        else:
            state = self.status
        return {"source_id": self.source_id, "status": state, "record_count": len(self.records), "query_count": self.query_count, "errors": self.errors[:5], "audits": self.audits[:20]}
