from src.pdi.pipeline import _translate_remaining_items


class FakeRun:
    def __init__(self, provider, output):
        self.provider = provider
        self.output = output

    def audit(self):
        return {
            "provider": self.provider,
            "model": f"{self.provider}-model",
            "status": "success",
            "retry_count": 0,
            "fallback_used": self.provider != "github_models",
            "input_hash": "fake",
            "generated_at": "2026-07-14T00:00:00+00:00",
            "task_name": "translation",
        }


class FakeRouter:
    def __init__(self):
        self.calls = []

    def provider_sequence(self, task_name):
        return ["github_models", "gemini", "groq", "deterministic"]

    def run_provider(self, task_name, payload, provider, fallback_used=False):
        self.calls.append(provider)
        rows = []
        for item in payload["items"]:
            if provider == "github_models":
                # Deliberately invalid: source contains 2 but the first model drops it.
                rows.append(
                    {
                        "record_id": item["record_id"],
                        "translated_title_zh": "病例报告",
                        "translated_text_zh": "该报告描述病例。",
                        "display_summary_zh": "病例报告。",
                        "display_summary_en": "Case report.",
                    }
                )
            else:
                rows.append(
                    {
                        "record_id": item["record_id"],
                        "translated_title_zh": "2 例病例报告",
                        "translated_text_zh": "该报告纳入 2 例病例。",
                        "display_summary_zh": "报告涉及 2 例病例。",
                        "display_summary_en": "The report included 2 cases.",
                    }
                )
        return FakeRun(provider, {"items": rows})


def test_failed_github_translation_falls_back_only_for_unresolved_item():
    work = {
        "work_id": "work-fallback",
        "title": {"original": "Report of 2 cases", "translated_zh": None, "language": "en"},
        "abstract": {"original": "The report included 2 cases.", "translated_zh": None, "sentences": []},
        "display_summary": {"zh": None, "en": None},
    }
    audits = []
    cache = {}
    policy = {
        "translation_provider_batch_sizes": {"github_models": 6, "gemini": 2, "groq": 1}
    }
    router = FakeRouter()
    _translate_remaining_items(router, [(work, "work")], cache, audits, policy)
    assert work["title"]["translated_zh"] == "2 例病例报告"
    assert work["translation_audit"]["provider"] == "gemini"
    chain = work["translation_audit"]["attempt_chain"]
    assert [row["provider"] for row in chain] == ["github_models", "gemini"]
    assert chain[0]["validation_status"] == "failed"
    assert chain[1]["validation_status"] == "passed"
    assert router.calls == ["github_models", "gemini"]


def test_analysis_stops_after_first_valid_fallback_provider():
    from src.pdi.pipeline import _run_validated_analysis

    class AnalysisRouter:
        def __init__(self):
            self.calls = []

        def provider_sequence(self, task_name):
            return ["github_models", "gemini", "groq", "deterministic"]

        def run_provider(self, task_name, payload, provider, fallback_used=False):
            self.calls.append(provider)
            if provider == "github_models":
                return FakeRun(provider, None)
            valid = {
                "translated_title_zh": "汉坦病毒综述",
                "translated_abstract_zh": "该综述描述汉坦病毒进入。",
                "display_summary_zh": "该综述描述汉坦病毒进入。",
                "display_summary_en": "This review describes hantavirus entry.",
                "one_sentence_takeaway": {"text": "该综述描述汉坦病毒进入。", "evidence_ids": ["A1"]},
                "study": {
                    "research_question": {"text": "综述关注汉坦病毒进入。", "evidence_ids": ["A1"]},
                    "study_type": "review",
                    "design": {"text": "综述性描述。", "evidence_ids": ["A1"]},
                    "sample_or_dataset": {"text": None, "evidence_ids": []},
                    "methods": [],
                },
                "entities": {"viruses": [], "hosts": [], "countries": [], "populations": []},
                "key_findings": [{"finding": "综述描述汉坦病毒进入。", "evidence_ids": ["A1"], "quantitative": False}],
                "quantitative_results": [],
                "significance": {"statement": "提供综述性信息。", "evidence_ids": ["A1"], "scope": "review"},
                "limitations": {"author_reported": [], "evidence_gaps": []},
                "evidence_strength": {"level": "low", "basis": "仅有摘要。", "evidence_ids": ["A1"]},
                "evidence_coverage": {"level": "abstract", "sections_used": ["abstract"], "note": "Only abstract evidence."},
                "categories": [],
                "audience_tags": [],
                "display_priority": "medium",
                "uncertainties": [],
            }
            return FakeRun(provider, valid)

    work = {
        "work_id": "work-analysis",
        "title": {"original": "Hantavirus review", "translated_zh": None, "language": "en"},
        "abstract": {
            "original": "This review describes hantavirus entry.",
            "translated_zh": None,
            "sentences": [{"id": "A1", "text": "This review describes hantavirus entry."}],
        },
        "display_summary": {"zh": None, "en": None},
        "processing_audit": {},
    }
    router = AnalysisRouter()
    output, audit = _run_validated_analysis(
        router,
        "literature_analysis",
        {"record_id": "work-analysis"},
        work,
        "work",
        work["abstract"]["sentences"],
        ["hantavirus"],
        {},
    )
    assert output
    assert audit["provider"] == "gemini"
    assert router.calls == ["github_models", "gemini"]
    assert work["ai_analysis"]
