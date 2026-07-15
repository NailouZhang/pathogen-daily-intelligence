from __future__ import annotations

from src.pdi.local_mt import LocalMTResult
from src.pdi.pipeline import _translate_remaining_items
from src.pdi.scholarly_recovery import _identity_check
from src.pdi.translation import review_translation_candidate
from src.pdi.translation_quality import clean_translation_source, repair_translation_text


GLOSSARY = {
    "terms": [
        {"source_patterns": ["hantavirus"], "target": "汉坦病毒"},
        {"source_patterns": ["Seoul virus"], "target": "首尔病毒"},
    ],
    "forbidden": ["宋病毒", "汉塔病毒"],
    "repairs": {"宋病毒": "汉坦病毒", "汉塔病毒": "汉坦病毒"},
}


class FakeLocalMT:
    enabled = True
    verify_remote = True

    def reference_title(self, source_title):
        return "世卫组织宣布汉坦病毒疫情结束", {"status": "success", "provider": "fake_local_reference"}

    def translate_record(self, item, kind):
        title = item["title"]["original"]
        return LocalMTResult(
            "success",
            {
                "translated_title_zh": "世卫组织宣布汉坦病毒疫情结束" if "WHO" in title else "汉坦病毒防控指南",
                "translated_text_zh": None,
                "display_summary_zh": None,
                "display_summary_en": None,
                "uncertainties": [],
            },
            {
                "task_name": "local_machine_translation",
                "provider": "local_marian",
                "model": "fake",
                "status": "success",
                "validation_status": "passed_local_mt",
                "fallback_used": True,
            },
        )


def test_google_news_boilerplate_is_not_translation_content():
    assert clean_translation_source(
        "Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News.",
        kind="article",
    ) is None


def test_glossary_repairs_known_bad_hantavirus_translations():
    value, repairs = repair_translation_text("WHO 宣布宋病毒爆发结束", "WHO declares hantavirus outbreak over", GLOSSARY)
    assert value == "WHO 宣布汉坦病毒爆发结束"
    assert repairs


def test_remote_translation_rejected_when_uncertainty_is_lost():
    review = review_translation_candidate(
        "Apparent hantavirus outbreak kills 3",
        None,
        {
            "translated_title_zh": "汉坦病毒疫情导致 3 人死亡",
            "translated_text_zh": None,
            "display_summary_zh": None,
            "display_summary_en": None,
        },
        {},
        glossary=GLOSSARY,
        local_translator=None,
    )
    assert not review["valid"]
    assert "UNCERTAINTY_MARKER_DROPPED" in review["errors"]


def test_bad_phrase_is_repaired_before_acceptance():
    review = review_translation_candidate(
        "Five passengers from hantavirus-stricken cruise ship depart quarantine",
        None,
        {"translated_title_zh": "五名来自汉坦病毒肆虐游轮的乘客解除隔离"},
        {},
        glossary=GLOSSARY,
        local_translator=FakeLocalMT(),
    )
    assert review["valid"]
    assert "发生汉坦病毒疫情的邮轮" in review["fields"]["translated_title_zh"]


def test_local_mt_final_fallback_translates_title_only_without_fake_summary():
    item = {
        "article_id": "a1",
        "title": {"original": "WHO declares hantavirus outbreak over", "translated_zh": None, "language": "en"},
        "content": {
            "excerpt": "Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News.",
            "translated_excerpt_zh": None,
            "availability_status": "content_unavailable",
        },
        "display_summary": {"zh": None, "en": None},
        "translation_audit": {},
        "processing_audit": {},
    }

    class EmptyRouter:
        def provider_sequence(self, task):
            return []

        def run_provider(self, *args, **kwargs):
            raise AssertionError("no provider should be called")

    audits = []
    _translate_remaining_items(
        EmptyRouter(),
        [(item, "article")],
        {},
        audits,
        {},
        FakeLocalMT(),
        GLOSSARY,
    )
    assert item["title"]["translated_zh"] == "世卫组织宣布汉坦病毒疫情结束"
    assert item["content"].get("translated_excerpt_zh") is None
    assert item["display_summary"]["zh"] is None
    assert item["translation_audit"]["provider"] == "local_marian"


def test_doi_presence_alone_cannot_accept_wrong_pdf():
    work = {
        "title": {"original": "2026 Cruise Ship-associated Andes Hantavirus Outbreak and Public Health Response"},
        "identifiers": {"doi": "10.1007/s12539-020-00413-4"},
        "authors": ["Jin Yong Kim"],
    }
    wrong_pdf = "10.1007/s12539-020-00413-4 Frontiers in Microbiology. Completely unrelated coronavirus article by Smith."
    result = _identity_check(work, wrong_pdf, "Completely unrelated coronavirus article")
    assert not result["accepted"]
    assert result["identifier_conflict"]
