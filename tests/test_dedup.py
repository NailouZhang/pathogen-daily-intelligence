from src.pdi.dedup import deduplicate_news, deduplicate_scholarly
from src.pdi.demo import demo_source_results


def test_scholarly_identifier_dedup():
    scholarly, _, _ = demo_source_results("2026-07-14")
    works, counts = deduplicate_scholarly(scholarly)
    assert counts["raw"] == 3
    assert len(works) == 2
    merged = next(w for w in works if w["identifiers"].get("pmid") == "99900001")
    assert merged["quality"]["source_count"] == 2


def test_news_url_tracking_removed_and_kept_unique():
    _, news, _ = demo_source_results("2026-07-14")
    articles, counts = deduplicate_news(news)
    assert counts["raw"] == 3
    assert len(articles) == 3
    assert all("utm_" not in a["canonical_url"] for a in articles)


def test_cross_source_doi_links_pubmed_and_crossref_records():
    base = {
        "title": "A shared hantavirus study",
        "abstract": "Hantavirus study.",
        "abstract_sentences": [],
        "authors": ["A Researcher"],
        "published_date": "2026-07-14",
        "published_date_precision": "day",
        "journal": "Journal",
        "retrieved_at": "2026-07-14T00:00:00+00:00",
    }
    records = [
        {**base, "source_id": "pubmed", "source_record_id": "1", "identifiers": {"pmid": "1", "doi": "10.1/shared"}},
        {**base, "source_id": "crossref", "source_record_id": "10.1/shared", "identifiers": {"doi": "10.1/shared"}},
    ]
    works, counts = deduplicate_scholarly(records)
    assert len(works) == 1
    assert counts["merged"] == 1
