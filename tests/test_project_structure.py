from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_entrypoint_and_pages_exist():
    assert (ROOT / "app.py").is_file()
    names = {x.name for x in (ROOT / "pages").glob("*.py")}
    assert {
        "1_公共卫生事件.py",
        "2_学术文献.py",
        "3_来源健康.py",
        "4_历史审计.py",
        "5_配置与词典.py",
        "6_静态日报与下载.py",
    } <= names


def test_five_schemas_exist():
    expected = {
        "pathogen_profile.schema.json",
        "scholarly_work.schema.json",
        "news_article.schema.json",
        "public_health_event.schema.json",
        "daily_issue.schema.json",
    }
    assert expected <= {x.name for x in (ROOT / "schemas").glob("*.json")}


def test_streamlit_secrets_example_contains_no_retrieval_or_model_keys():
    text = (ROOT / ".streamlit/secrets.example.toml").read_text(encoding="utf-8")
    assert "PDI_GITHUB_REPO" in text
    assert "GITHUB_DATA_TOKEN" in text
    for forbidden in ["GEMINI_API_KEY", "GROQ_API_KEY", "NCBI_API_KEY", "SMTP_PASSWORD"]:
        assert forbidden not in text
