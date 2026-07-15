from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class HttpClient:
    user_agent: str
    timeout: int = 25
    retries: int = 3

    def __post_init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.6",
        })

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        timeout = kwargs.pop("timeout", self.timeout)
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(f"retryable status {response.status_code}", response=response)
                response.raise_for_status()
                return response
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep((2 ** attempt) + random.random())
        raise RuntimeError(f"HTTP request failed: {url}: {last_error}")

    def get_json(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs).json()

    def get_text(self, url: str, **kwargs: Any) -> str:
        response = self.request("GET", url, **kwargs)
        response.encoding = response.apparent_encoding or response.encoding
        return response.text
