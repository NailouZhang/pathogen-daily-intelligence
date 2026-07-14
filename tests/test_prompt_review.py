from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_prompt_review_copies_match_production_prompts():
    production = ROOT / "prompts"
    review = ROOT / "prompt_review" / "production_prompts"
    assert (ROOT / "prompt_review" / "PROMPT_INDEX.md").is_file()
    assert (ROOT / "prompt_review" / "PRODUCTION_PROMPTS_COMBINED.md").is_file()
    for path in production.glob("*.txt"):
        review_path = review / path.name
        assert review_path.is_file()
        assert review_path.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")


def test_all_required_prompt_types_are_independent_files():
    names = {path.name for path in (ROOT / "prompts").glob("*.txt")}
    assert {
        "pathogen_bootstrap.txt",
        "bilingual_translation_batch.txt",
        "translation_repair.txt",
        "literature_analysis.txt",
        "official_notice_analysis.txt",
        "media_news_analysis.txt",
        "daily_synthesis.txt",
    } <= names
