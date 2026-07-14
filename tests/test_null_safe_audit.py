from pathlib import Path

from src.pdi.normalization import make_scholarly_work, normalize_news_article
from src.pdi.pipeline import _translate_and_analyse, run_daily_pipeline
from src.pdi.translation import ensure_bilingual_placeholders
from src.pdi.utils import ensure_dict_field

ROOT = Path(__file__).resolve().parents[1]


def test_ensure_dict_field_replaces_null_and_invalid_legacy_values():
    for legacy_value in (None, [], "invalid", 7):
        item = {"translation_audit": legacy_value}
        audit = ensure_dict_field(item, "translation_audit", {"status": "new"})
        assert isinstance(audit, dict)
        assert item["translation_audit"] is audit
        assert audit == {"status": "new"}


def test_placeholder_fallback_accepts_null_audit_fields():
    work = {
        "work_id": "work-null-audit",
        "title": {"original": "Report of 2 cases", "translated_zh": None, "language": "en"},
        "abstract": {"original": "The report included 2 cases.", "translated_zh": None},
        "display_summary": None,
        "translation_audit": None,
        "processing_audit": None,
    }
    ensure_bilingual_placeholders(work, "work")
    assert isinstance(work["display_summary"], dict)
    assert isinstance(work["translation_audit"], dict)
    assert work["translation_audit"]["validation_status"] == "translation_unavailable"


def test_translate_and_analyse_survives_all_provider_unavailable(monkeypatch):
    from src.pdi import pipeline
    from src.pdi.config import load_profile

    class DeterministicOnlyRouter:
        def __init__(self, *_args, **_kwargs):
            pass

        def provider_sequence(self, _task_name):
            return ["deterministic"]

        def run_provider(self, *_args, **_kwargs):  # pragma: no cover - should never run
            raise AssertionError("deterministic provider must not call a remote model")

    monkeypatch.setattr(pipeline, "ModelRouter", DeterministicOnlyRouter)
    profile = load_profile("hantavirus", ROOT)
    work = {
        "work_id": "work-fallback-null",
        "title": {"original": "Hantavirus report", "translated_zh": None, "language": "en"},
        "abstract": {"original": "A report without a validated translation.", "translated_zh": None, "sentences": []},
        "display_summary": None,
        "translation_audit": None,
        "processing_audit": None,
        "filter_result": {"decision": "archive"},
        "entities": {},
        "ai_analysis": None,
    }
    article = {
        "article_id": "article-fallback-null",
        "title": {"original": "Public health report", "translated_zh": None, "language": "en"},
        "content": {"excerpt": "No validated translation is available.", "translation_text": None, "sentences": []},
        "display_summary": None,
        "translation_audit": None,
        "processing_audit": None,
        "classification": {"decision": "archive"},
        "source": {"reliability_tier": "B"},
        "ai_analysis": None,
    }

    synthesis, audits, cache = _translate_and_analyse(
        ROOT, profile, [work], [article], [], previous_cache={}
    )
    assert synthesis is None
    assert isinstance(cache, dict)
    assert isinstance(audits, list)
    for item in (work, article):
        assert isinstance(item["translation_audit"], dict)
        assert item["translation_audit"]["validation_status"] == "translation_unavailable_after_all_providers"
        assert isinstance(item["processing_audit"], dict)
        assert isinstance(item["processing_audit"]["translation"], dict)


def test_demo_pipeline_with_llm_enabled_and_no_credentials_still_publishes(tmp_path, monkeypatch):
    for name in (
        "GITHUB_MODELS_TOKEN",
        "GITHUB_TOKEN",
        "GEMINI_API_KEY",
        "GOOGLE_AI_STUDIO_API_KEY",
        "GROQ_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)

    out = tmp_path / "out"
    result = run_daily_pipeline(
        ROOT,
        "hantavirus",
        out,
        demo_mode=True,
        disable_llm=False,
    )
    assert result["issue"]["issue_id"]
    assert (out / "data/latest.json").stat().st_size > 0
    assert (out / "site/index.html").stat().st_size > 0
    assert all(isinstance(item.get("translation_audit"), dict) for item in result["works"])
    # Demo records may already include reviewed Chinese translations. The
    # regression assertion is that enabling the LLM path with no credentials
    # still completes and all mutable audit fields remain dictionaries.
    assert all(isinstance(item.get("processing_audit"), dict) for item in result["works"])


def test_new_normalized_entities_start_with_mutable_audit_objects():
    work = make_scholarly_work(
        [
            {
                "source_id": "crossref",
                "source_record_id": "10.0000/test",
                "title": "Hantavirus test",
                "abstract": None,
                "language": "en",
                "authors": [],
                "published_date": "2026-07-15",
                "published_date_precision": "day",
                "retrieved_at": "2026-07-15T00:00:00+00:00",
                "identifiers": {"doi": "10.0000/test"},
            }
        ]
    )
    article = normalize_news_article(
        {
            "source_id": "test",
            "source_name": "Test source",
            "url": "https://example.org/report",
            "title": "Hantavirus report",
            "language": "en",
            "retrieved_at": "2026-07-15T00:00:00+00:00",
        }
    )
    assert work["translation_audit"] == {}
    assert article["translation_audit"] == {}


def test_exact_regression_all_remote_providers_fail_with_null_translation_audit():
    from src.pdi.pipeline import _translate_remaining_items

    class FailedRun:
        output = None

        def __init__(self, provider):
            self.provider = provider

        def audit(self):
            return {
                "provider": self.provider,
                "model": None,
                "status": "unavailable",
                "error": "test provider unavailable",
                "retry_count": 0,
                "fallback_used": self.provider != "github_models",
                "input_hash": "test",
                "generated_at": "2026-07-15T00:00:00+00:00",
                "task_name": "bilingual_translation_batch",
            }

    class AllFailRouter:
        def provider_sequence(self, _task_name):
            return ["github_models", "gemini", "groq", "deterministic"]

        def run_provider(self, _task_name, _payload, provider, fallback_used=False):
            return FailedRun(provider)

    work = {
        "work_id": "work-exact-regression",
        "title": {"original": "Hantavirus report", "translated_zh": None, "language": "en"},
        "abstract": {"original": "No validated translation is available.", "translated_zh": None},
        "display_summary": {"zh": None, "en": None},
        "translation_audit": None,
        "processing_audit": None,
    }
    audits = []
    _translate_remaining_items(
        AllFailRouter(),
        [(work, "work")],
        {},
        audits,
        {"translation_provider_batch_sizes": {"github_models": 6, "gemini": 2, "groq": 1}},
    )
    assert work["translation_audit"]["validation_status"] == "translation_unavailable_after_all_providers"
    assert [x["provider"] for x in work["translation_audit"]["attempt_chain"]] == [
        "github_models",
        "gemini",
        "groq",
    ]
    assert work["processing_audit"]["translation"]["validation_status"] == "translation_unavailable_after_all_providers"


def test_unexpected_llm_orchestration_exception_does_not_block_publication(tmp_path, monkeypatch):
    from src.pdi import pipeline

    def boom(*_args, **_kwargs):
        raise AttributeError("simulated orchestration bug")

    monkeypatch.setattr(pipeline, "_translate_and_analyse", boom)
    out = tmp_path / "orchestration-fallback"
    result = pipeline.run_daily_pipeline(
        ROOT,
        "hantavirus",
        out,
        demo_mode=True,
        disable_llm=False,
    )
    assert result["issue"]["issue_id"]
    assert (out / "data/latest.json").stat().st_size > 0
    assert (out / "site/index.html").stat().st_size > 0
    assert any(
        row.get("validation_status") == "orchestration_failed_fallback_published"
        for row in result["issue"]["generation_audit"]["llm_runs"]
    )
    assert all(isinstance(item.get("translation_audit"), dict) for item in result["works"])
