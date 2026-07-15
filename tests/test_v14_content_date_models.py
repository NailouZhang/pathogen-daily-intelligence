from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.pdi.dates import choose_current_availability_date, coverage_window
from src.pdi.dedup import deduplicate_scholarly
from src.pdi.entities import annotate_article
from src.pdi.filters import classify_article
from src.pdi.llm import ModelRouter
from src.pdi.translation import validate_translation_fields


def _profile():
    return {
        "lexicon": [
            {"term": "hantavirus", "status": "accepted_for_search", "term_type": "pathogen_name"},
            {"term": "hantavirus pulmonary syndrome", "status": "accepted_for_search", "term_type": "disease_name"},
        ],
        "ambiguous_terms": [],
    }


def test_current_availability_date_beats_future_issue_date():
    window = coverage_window(7, "Asia/Shanghai", datetime(2026, 7, 15, 8, tzinfo=ZoneInfo("Asia/Shanghai")))
    value, precision, basis = choose_current_availability_date(
        [
            ("source_created_date", "2026-07-12", "day"),
            ("issue_date", "2027-02-01", "day"),
        ],
        window,
    )
    assert value == "2026-07-12"
    assert precision == "day"
    assert basis == "source_created_date"


def test_background_hantavirus_mention_does_not_take_ebola_counts_or_location():
    article = {
        "title": {"original": "US citizen tests positive for Ebola", "language": "en"},
        "content": {
            "analysis_text": (
                "The Ebola outbreak in the Democratic Republic of the Congo reached 1,830 confirmed cases and 648 deaths. "
                "Two isolation facilities were previously used for people from a cruise ship on which a hantavirus outbreak occurred."
            ),
            "excerpt": None,
            "coverage_level": "full_relevant_extract",
        },
        "source": {"reliability_tier": "B"},
        "entities": {},
        "published_at": "2026-07-10",
    }
    article = annotate_article(article, _profile()["lexicon"])
    article = classify_article(article, _profile())
    assert article["entities"]["confirmed_cases"] is None
    assert article["entities"]["deaths"] is None
    assert article["entities"]["country"] is None
    assert article["classification"]["decision"] == "archive"


def test_shared_bad_doi_does_not_merge_unrelated_titles():
    records = [
        {
            "source_id": "pubmed",
            "source_record_id": "1",
            "identifiers": {"doi": "10.1000/same"},
            "title": "Hantavirus neurological manifestations",
            "authors": ["A Author"],
            "published_date": "2026-07-10",
            "retrieved_at": "2026-07-15T00:00:00Z",
        },
        {
            "source_id": "crossref",
            "source_record_id": "2",
            "identifiers": {"doi": "10.1000/same"},
            "title": "Unrelated bacterial vaccine trial",
            "authors": ["B Author"],
            "published_date": "2010-01-01",
            "retrieved_at": "2026-07-15T00:00:00Z",
        },
    ]
    works, counts = deduplicate_scholarly(records)
    assert len(works) == 2
    assert counts["identifier_conflicts"] == 1


def test_translation_number_validation_accepts_removed_thousands_separator():
    result = validate_translation_fields(
        "Report of 1,600 cases",
        "There were 1,600 cases and 50% recovered.",
        {
            "translated_title_zh": "1,600 例病例报告",
            "translated_text_zh": "共有 1600 例病例，50% 已康复。",
            "display_summary_zh": "共有 1600 例病例，50% 已康复。",
            "display_summary_en": "There were 1,600 cases and 50% recovered.",
        },
        {},
    )
    assert result["valid"] is True


def test_groq_dynamic_discovery_filters_non_chat_and_ranks_multiple_models():
    router = ModelRouter(Path("."), {"max_models_per_provider": {"groq": 8}})
    router._model_lists["groq"] = [
        "whisper-large-v3",
        "llama-3.3-70b-versatile",
        "qwen/qwen3-32b",
        "openai/gpt-oss-20b",
    ]
    # Credentials are only needed by _model_candidates; use a harmless test value.
    import os
    old = os.environ.get("GROQ_API_KEY")
    os.environ["GROQ_API_KEY"] = "test"
    try:
        _, models = router._model_candidates("groq", "bilingual_translation_batch")
    finally:
        if old is None:
            os.environ.pop("GROQ_API_KEY", None)
        else:
            os.environ["GROQ_API_KEY"] = old
    assert "whisper-large-v3" not in models
    assert len(models) == 3
    assert models[0] in {"qwen/qwen3-32b", "openai/gpt-oss-20b"}
