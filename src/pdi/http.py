from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests


@dataclass
class HttpAudit:
    url: str
    status_code: int | None = None
    elapsed_ms: int | None = None
    attempts: int = 0
    error: str | None = None
    response_headers: dict[str, str] = field(default_factory=dict)


class HttpClient:
    def __init__(self, timeout: int = 20, user_agent: str = "PathogenDailyIntelligence/1.0") -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent, "Accept": "application/json, application/xml, text/xml, text/html, */*"})

    def request(self, method: str, url: str, *, max_attempts: int = 3, **kwargs: Any) -> tuple[requests.Response | None, HttpAudit]:
        audit = HttpAudit(url=url)
        for attempt in range(1, max_attempts + 1):
            audit.attempts = attempt
            started = time.monotonic()
            try:
                response = self.session.request(method, url, timeout=self.timeout, **kwargs)
                audit.elapsed_ms = int((time.monotonic() - started) * 1000)
                audit.status_code = response.status_code
                audit.response_headers = {k: v for k, v in response.headers.items() if k.lower() in {"retry-after", "content-type", "etag", "last-modified"}}
                if response.status_code == 429 and attempt < max_attempts:
                    wait = min(30, int(response.headers.get("Retry-After", "2") or 2))
                    time.sleep(max(1, wait))
                    continue
                if response.status_code >= 500 and attempt < max_attempts:
                    time.sleep(min(8, 2 ** (attempt - 1)))
                    continue
                response.raise_for_status()
                return response, audit
            except requests.RequestException as exc:
                audit.elapsed_ms = int((time.monotonic() - started) * 1000)
                audit.error = str(exc)
                if attempt < max_attempts:
                    time.sleep(min(8, 2 ** (attempt - 1)))
                    continue
        return None, audit

    def get_json(self, url: str, **kwargs: Any) -> tuple[Any | None, HttpAudit]:
        response, audit = self.request("GET", url, **kwargs)
        if response is None:
            return None, audit
        try:
            return response.json(), audit
        except ValueError as exc:
            audit.error = f"Invalid JSON: {exc}"
            return None, audit

    def post_json(self, url: str, **kwargs: Any) -> tuple[Any | None, HttpAudit]:
        response, audit = self.request("POST", url, **kwargs)
        if response is None:
            return None, audit
        try:
            return response.json(), audit
        except ValueError as exc:
            audit.error = f"Invalid JSON: {exc}"
            return None, audit
