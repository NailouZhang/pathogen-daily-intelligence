from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_daily_workflow_uses_tmp_data_branch_and_staging_first_without_pages():
    text = (ROOT / ".github/workflows/daily-intelligence.yml").read_text(encoding="utf-8")
    assert 'cron: "20 22 * * *"' in text
    assert "/tmp/pdi_out" in text
    assert "intelligence-data" in text
    assert "git add -A data site" in text
    assert "git diff --cached --quiet -- data site" in text
    assert "git diff --quiet -- data site" not in text
    assert "actions/upload-pages-artifact" not in text
    assert "actions/deploy-pages" not in text
    assert "pages: write" not in text
    assert "id-token: write" not in text
    assert "actions/checkout@v6" in text
    assert "actions/setup-python@v6" in text
    assert "actions/upload-artifact@v7" in text
    assert text.index("Send optional HTML email") < text.index("Persist data and static history")


def test_three_operational_workflows_exist():
    for name in ["bootstrap-pathogen.yml", "daily-intelligence.yml", "refresh-pathogen-profile.yml"]:
        assert (ROOT / ".github/workflows" / name).is_file()


def test_all_workflows_use_node24_compatible_official_actions():
    texts = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / ".github/workflows").glob("*.yml"))
    assert "actions/checkout@v4" not in texts
    assert "actions/setup-python@v5" not in texts
    assert "actions/upload-artifact@v4" not in texts
