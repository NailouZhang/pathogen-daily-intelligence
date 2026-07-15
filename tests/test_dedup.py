from src.pdi2.dedup import attach_news_to_papers, dedup_news, dedup_papers


def test_paper_doi_dedup_prefers_longer_abstract():
    records = [
        {"source": "A", "doi": "10.1/demo", "title": "A study", "abstract": "short", "authors": ["Smith"]},
        {"source": "B", "doi": "10.1/demo", "title": "A study", "abstract": "a much longer abstract", "authors": ["Smith"]},
    ]
    out = dedup_papers(records)
    assert len(out) == 1
    assert out[0]["abstract"] == "a much longer abstract"


def test_news_dedup():
    records = [
        {"source": "A", "title": "WHO declares hantavirus outbreak over", "url": "a"},
        {"source": "B", "title": "WHO says hantavirus outbreak is over", "url": "b"},
    ]
    assert len(dedup_news(records)) == 1


def test_news_about_paper_can_attach():
    papers = dedup_papers([{"source": "PubMed", "title": "Neurological manifestations of hantavirus infection", "authors": ["Nath"], "doi": "10.1/x"}])
    news = dedup_news([{"source": "News", "title": "Neurological manifestations of hantavirus infection", "url": "x", "excerpt": "A new paper"}])
    remaining, papers = attach_news_to_papers(news, papers)
    assert remaining == []
    assert papers[0]["media_mentions"]
