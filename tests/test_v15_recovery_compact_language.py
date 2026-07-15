from __future__ import annotations

from pathlib import Path

import pytest

from src.pdi.pipeline import _build_recovery_queue, _recovery_queue_records, run_daily_pipeline
from src.pdi import scholarly_recovery as recovery


def _work() -> dict:
    return {
        "work_id": "work-test",
        "identifiers": {"doi": "10.1234/example.2026.1", "pmid": "12345678"},
        "title": {"original": "Hantavirus reservoir surveillance in rural communities", "language": "en"},
        "authors": ["Alice Smith", "Bo Chen"],
        "abstract": {"original": None, "sentences": []},
        "full_text": {"available": False, "sections": []},
        "quality": {},
        "source_records": [],
        "bibliography": {"availability_date": "2026-07-15", "journal": "Example Journal"},
    }


def _failed(stage: str) -> dict:
    return {"audit": {"stage": stage, "status": "failed", "error": "TEST_UNAVAILABLE"}, "candidates": []}


def test_metadata_only_work_is_retained_and_marked_for_retry(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(recovery, "_exact_pubmed", lambda *args: _failed("pubmed_exact"))
    monkeypatch.setattr(recovery, "_exact_europe_pmc", lambda *args: _failed("europe_pmc_exact"))
    monkeypatch.setattr(recovery, "_exact_crossref", lambda *args: _failed("crossref_exact"))
    monkeypatch.setattr(recovery, "_exact_semantic_scholar", lambda *args: _failed("semantic_scholar_exact"))
    monkeypatch.setattr(recovery, "_pmc_fulltext_xml", lambda *args: _failed("europe_pmc_fulltext_xml"))
    monkeypatch.setattr(recovery, "_pmc_bioc", lambda *args: _failed("pmc_bioc_json"))
    monkeypatch.setattr(recovery, "_unpaywall", lambda *args: _failed("unpaywall"))
    monkeypatch.setattr(recovery, "_doi_landing", lambda *args: _failed("doi_landing"))

    work = _work()
    result = recovery.recover_scholarly_work(work, {"search_policy": {}, "content_policy": {}})

    assert result["status"] == "metadata_only"
    assert result["evidence_level"] == "E0"
    assert work["evidence_acquisition"]["analysis_eligible"] is False
    assert work["evidence_acquisition"]["retry_recommended"] is True
    assert "ABSTRACT_NOT_RETRIEVED" in work["evidence_acquisition"]["reason_codes"]
    assert work["title"]["original"]  # discovery record remains intact


def test_recovery_queue_rehydrates_due_metadata_record():
    work = _work()
    work["evidence_acquisition"] = {
        "evidence_level": "E0",
        "attempt_count": 1,
        "reason_codes": ["ABSTRACT_NOT_RETRIEVED"],
    }
    profile = {
        "content_policy": {
            "scholarly_recovery_retry_days": [1, 3, 7, 14],
            "scholarly_recovery_queue_max_age_days": 30,
            "scholarly_recovery_queue_max_items": 200,
        }
    }
    queue = _build_recovery_queue([work], {}, "2026-07-15", profile)
    assert len(queue) == 1
    assert queue[0]["next_retry_date"] == "2026-07-16"

    due = _recovery_queue_records({"scholarly_recovery_queue": queue}, "2026-07-16")
    assert len(due) == 1
    assert due[0]["source_id"] == "recovery_queue"
    assert due[0]["identifiers"]["doi"] == "10.1234/example.2026.1"


def test_jats_fulltext_yields_structured_sections():
    xml = """
    <article><front><article-meta><title-group><article-title>Hantavirus reservoir surveillance in rural communities</article-title></title-group>
    <abstract><p>We assessed hantavirus exposure in rural communities.</p></abstract></article-meta></front>
    <body>
      <sec><title>Methods</title><p>We collected serum samples from rural residents and tested them with an immunoassay.</p></sec>
      <sec><title>Results</title><p>Antibodies were detected in the analysed sample set, supporting prior exposure.</p></sec>
      <sec><title>Conclusion</title><p>The findings support continued surveillance while not establishing causation.</p></sec>
    </body></article>
    """
    abstract, sections, title = recovery._sections_from_jats(xml, 10000, 30)
    assert title == "Hantavirus reservoir surveillance in rural communities"
    assert "assessed hantavirus exposure" in abstract
    assert {row["title"] for row in sections} >= {"Methods", "Results", "Conclusion"}
    assert all(sentence["id"].startswith("F") for row in sections for sentence in row["sentences"])


def test_pdf_text_is_parsed_only_after_identity_check():
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    page = document.new_page()
    paragraph = (
        "Hantavirus reservoir surveillance in rural communities\n"
        "Alice Smith and Bo Chen\nDOI: 10.1234/example.2026.1\n"
        "Methods\nWe collected serum samples from rural residents and tested them with an immunoassay. "
        "The sampling protocol and laboratory quality controls were documented for every participant.\n"
        "Results\nAntibodies were detected in the analysed sample set, supporting prior exposure without proving causation. "
        "The result was assessed against predefined controls and reviewed by two analysts.\n"
        "Discussion\nThe findings support continued surveillance and additional prospective research. "
        "Limitations include incomplete geographic coverage and the use of a cross-sectional design.\n"
    ) * 4
    page.insert_textbox(fitz.Rect(40, 40, 555, 800), paragraph, fontsize=8)
    content = document.tobytes()
    document.close()

    parsed = recovery._extract_pdf(content, _work(), 12000, 40, 20, False, 0)
    assert parsed["status"] == "success"
    assert parsed["engine"] == "pymupdf"
    assert parsed["sections"]
    assert len(parsed["sha256"]) == 64

    wrong = _work()
    wrong["identifiers"] = {"doi": "10.9999/wrong"}
    wrong["title"]["original"] = "Completely unrelated plant genome"
    rejected = recovery._extract_pdf(content, wrong, 12000, 40, 20, False, 0)
    assert rejected["status"] == "failed"
    assert rejected["error"] == "PDF_IDENTITY_MISMATCH"


def test_static_cards_use_compact_top_right_en_zh_control(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    out = tmp_path / "out"
    run_daily_pipeline(root, "hantavirus", out, demo_mode=True, disable_llm=True)
    html = (out / "site/index.html").read_text(encoding="utf-8")

    assert '>en</button>' in html
    assert '>显示英文</button>' not in html
    assert '.language-toggle{position:absolute;top:0;right:0' in html
    assert "button.textContent = showEnglish ? 'zh' : 'en';" in html
    assert '点击右上角“en”' in html
