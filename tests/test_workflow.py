from pathlib import Path


def test_workflow_only_requires_profile_id():
    root = Path(__file__).resolve().parents[1]
    text = (root / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert "profile_id:" in text
    assert "deploy-pages@v4" in text
    assert "intelligence-data" in text
