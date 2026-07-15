from src.pdi2.relevance import filter_relevant_papers


def test_negative_only_unrelated_paper_is_filtered():
    profile = {"profile_id": "hantavirus", "english_terms": ["hantavirus", "Orthohantavirus"]}
    records = [{"title": "Detection of porcine circovirus", "abstract": "All samples tested negative for hantavirus."}]
    assert filter_relevant_papers(records, profile) == []
