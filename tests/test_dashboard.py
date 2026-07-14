from pathlib import Path

from src.pdi import dashboard


def test_dashboard_explicitly_falls_back_to_demo(monkeypatch):
    dashboard.clear_dashboard_cache()
    monkeypatch.setattr(dashboard, "_repo_parts", lambda: None)
    result = dashboard.latest_issue_result()
    assert result.source == "demo"
    assert result.payload["profile_id"] == "hantavirus"
    assert "Demo" in result.message


def test_static_report_demo_is_available(monkeypatch):
    dashboard.clear_dashboard_cache()
    monkeypatch.setattr(dashboard, "_repo_parts", lambda: None)
    result = dashboard.static_report_result()
    assert result.source == "demo"
    assert "<!doctype html>" in result.payload.lower()


def test_runtime_cache_directory_is_git_ignored():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".gitignore").read_text(encoding="utf-8")
    assert "runtime/*" in text
