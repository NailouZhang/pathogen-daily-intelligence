import json
from pathlib import Path

from src.pdi2.config import Settings
from src.pdi2.pipeline import run_pipeline


def test_demo_pipeline(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    settings = Settings("hantavirus", root, tmp_path, tmp_path / "data/state")
    issue = run_pipeline(settings, demo=True)
    assert issue["metrics"]["papers"] == 2
    assert (tmp_path / "data/latest.json").exists()
    assert (tmp_path / "site/index.html").exists()
    html = (tmp_path / "site/index.html").read_text(encoding="utf-8")
    assert 'class="language-toggle"' in html
    assert ">en</button>" in html
