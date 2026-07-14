from src.pdi.config import load_profile
from src.pdi.dedup import deduplicate_news
from src.pdi.demo import demo_source_results
from src.pdi.entities import annotate_article
from src.pdi.events import cluster_events


def test_event_cluster_keeps_human_cases_separate_from_host_surveillance():
    profile = load_profile("hantavirus")
    _, news, _ = demo_source_results("2026-07-14")
    articles, _ = deduplicate_news(news)
    articles = [annotate_article(a, profile["lexicon"]) for a in articles]
    events, state = cluster_events(articles)
    assert len(events) == 2
    assert {x["event_type"] for x in events} == {"human_case", "host_surveillance"}
    human = next(x for x in events if x["event_type"] == "human_case")
    assert human["case_counts"]["confirmed"] == 2
    assert len(human["source_articles"]) == 2
