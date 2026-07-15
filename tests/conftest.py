from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _disable_real_local_mt_downloads(monkeypatch):
    """Unit tests use fakes and must never download model weights."""
    monkeypatch.setenv("PDI_ENABLE_LOCAL_MT", "false")
    monkeypatch.setenv("PDI_VERIFY_LLM_WITH_LOCAL_MT", "false")
