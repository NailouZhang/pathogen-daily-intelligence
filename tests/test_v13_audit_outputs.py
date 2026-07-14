import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def _jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_demo_entities_validate_against_five_object_schemas():
    pairs = [
        ("scholarly_works.jsonl", "scholarly_work.schema.json"),
        ("news_articles.jsonl", "news_article.schema.json"),
        ("public_health_events.jsonl", "public_health_event.schema.json"),
    ]
    for data_name, schema_name in pairs:
        schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema)
        for row in _jsonl(ROOT / "data" / "demo" / data_name):
            assert not list(validator.iter_errors(row))
    issue_schema = json.loads((ROOT / "schemas" / "daily_issue.schema.json").read_text(encoding="utf-8"))
    issue = json.loads((ROOT / "data" / "demo" / "latest.json").read_text(encoding="utf-8"))
    assert not list(Draft202012Validator(issue_schema).iter_errors(issue))


def test_demo_exposes_deep_analysis_and_audit_files():
    works = _jsonl(ROOT / "data" / "demo" / "scholarly_works.jsonl")
    articles = _jsonl(ROOT / "data" / "demo" / "news_articles.jsonl")
    assert any(work.get("ai_analysis") for work in works)
    assert any(article.get("ai_analysis") for article in articles)
    audit_dir = ROOT / "data" / "demo" / "audit"
    assert (audit_dir / "content_enrichment.json").is_file()
    assert (audit_dir / "llm_runs.jsonl").is_file()
    assert (audit_dir / "object_audit.jsonl").is_file()
