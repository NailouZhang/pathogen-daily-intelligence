from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_research_prompt_has_five_required_elements():
    text = (ROOT / "prompts/research_analysis.md").read_text(encoding="utf-8")
    for key in ("background", "methods", "results", "contribution", "limitations"):
        assert key in text


def test_review_prompt_has_five_required_elements():
    text = (ROOT / "prompts/review_analysis.md").read_text(encoding="utf-8")
    for key in ("background", "main_directions", "current_state", "gaps", "future_research"):
        assert key in text


def test_news_prompt_has_five_required_elements():
    text = (ROOT / "prompts/news_analysis.md").read_text(encoding="utf-8")
    for key in ("time", "location", "event", "impact", "status"):
        assert key in text
