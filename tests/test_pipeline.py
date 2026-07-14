import json
from pathlib import Path

from src.pdi.pipeline import run_daily_pipeline


def test_demo_pipeline_produces_all_outputs(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "out"
    result = run_daily_pipeline(root, "hantavirus", out, demo_mode=True, disable_llm=True)
    assert result["issue"]["profile_id"] == "hantavirus"
    assert result["issue"]["statistics"]["scholarly_unique"] == 2
    assert result["issue"]["statistics"]["public_health_events"] == 2
    for path in [
        out / "data/latest.json",
        out / "data/latest_items.csv",
        out / "site/index.html",
        out / "site/feed.xml",
        out / "email/latest.html",
        out / "output_manifest.json",
    ]:
        assert path.is_file() and path.stat().st_size > 0
    issue = json.loads((out / "data/latest.json").read_text(encoding="utf-8"))
    assert issue["outputs"]["site_index"]


def test_second_run_retains_event_ids_and_archives_unchanged_event(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    first = tmp_path / "first"
    second = tmp_path / "second"
    run1 = run_daily_pipeline(root, "hantavirus", first, demo_mode=True, disable_llm=True)
    run2 = run_daily_pipeline(root, "hantavirus", second, state_dir=first / "data/state", demo_mode=True, disable_llm=True)
    ids1 = {e["event_id"] for e in run1["events"]}
    ids2 = {e["event_id"] for e in run2["events"]}
    assert ids1 == ids2
    assert all(e["display_decision"] == "archive" for e in run2["events"])
