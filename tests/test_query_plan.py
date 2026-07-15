from src.pdi2.query_plan import build_query_plan


def test_query_plan_contains_profile_terms():
    profile = {"profile_id": "hantavirus", "query_groups": [{"id": "core", "terms": ["hantavirus", "Andes virus"], "topics": ["outbreak"]}]}
    plan = build_query_plan(profile)
    assert "hantavirus" in plan[0]["scholarly_query"]
    assert "outbreak" in plan[0]["news_query"]
