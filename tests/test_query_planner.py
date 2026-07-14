from src.pdi.config import load_profile
from src.pdi.query_planner import build_query_tasks


def test_query_plan_is_bilingual_and_budgeted():
    profile = load_profile("hantavirus")
    source = next(s for s in profile["source_registry"]["sources"] if s["source_id"] == "gdelt")
    tasks = build_query_tasks(profile, source)
    assert tasks
    assert len(tasks) <= profile["search_policy"]["max_query_groups_per_source"]
    assert {x.language for x in tasks} <= {"en", "zh"}
    assert all(" OR " in x.query or x.query for x in tasks)
