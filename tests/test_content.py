from src.pdi2.content import remove_boilerplate


def test_google_news_boilerplate_removed():
    text = "Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News."
    assert remove_boilerplate(text) == ""
