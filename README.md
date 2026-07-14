# Pathogen Daily Intelligence v1

A GitHub-managed, bilingual (English/中文) pathogen daily intelligence system. The first profile is Hantavirus, while the retrieval, deduplication, event clustering, LLM routing, rendering, and deployment layers remain profile-driven.

## What this release contains

- GitHub Actions workflows for profile bootstrap, daily production, monthly source/profile refresh, and CI.
- PubMed, Europe PMC, Crossref, and Semantic Scholar scholarly adapters.
- WHO listing-page discovery, ReliefWeb, GDELT, and Google News RSS discovery adapters.
- Five logical data objects: `PathogenProfile`, `ScholarlyWork`, `NewsArticle`, `PublicHealthEvent`, and `DailyIssue`.
- Identifier/bibliographic literature deduplication, URL/title news deduplication, and conservative public-health event clustering.
- Rule-first relevance and editorial classification: `headline`, `brief`, `archive`, `duplicate`, and `review`.
- Sequential model routing: Gemini → GitHub Models → Groq → deterministic/no-AI fallback.
- Evidence IDs, JSON validation, unsupported-number checks, and audit fields.
- Newspaper-style GitHub Pages output, independent table-based HTML email, RSS, JSON, CSV, and Streamlit dashboards.
- Durable generated data in the `intelligence-data` branch, so scheduled data publication does not modify `main` or trigger a Streamlit code redeploy.

## Important scope boundary

The included Hantavirus terms are manually approved **search seeds**, not a claim that the package contains a complete, current ICTV taxonomy. `bootstrap-pathogen` produces an audit artifact and never automatically promotes generated candidates into the production lexicon.

## Local deterministic test

```bash
python -m pip install -r requirements.txt
python scripts/validate_project.py
pytest
python scripts/run_daily.py \
  --profile hantavirus \
  --output-dir build/demo \
  --demo \
  --disable-llm
streamlit run app.py
```

The bundled `data/demo/` files let Streamlit start before any GitHub workflow has run.

## Live daily run

```bash
export NCBI_API_KEY=""
export SEMANTIC_SCHOLAR_API_KEY=""
export CROSSREF_MAILTO="your-email@example.com"
export GEMINI_API_KEY=""
export GROQ_API_KEY=""

python scripts/run_daily.py \
  --profile hantavirus \
  --output-dir build/live
```

An unavailable source is recorded as failed or partial; the remaining sources continue. When all LLM providers fail, the system still publishes a deterministic issue.

## Repository branches

- `main`: application code, profiles, schemas, prompts, tests, and workflows.
- `intelligence-data`: generated `data/` and `site/` trees, including latest issue, state, archives, RSS, and static Pages content.

The daily workflow creates `intelligence-data` automatically on its first successful run.

## GitHub Pages

Set **Settings → Pages → Source** to **GitHub Actions**. The daily workflow uploads `/tmp/pdi_out/site` as a Pages artifact and deploys it after data generation.

## Streamlit Community Cloud

Deploy `app.py` from `main`. Copy `.streamlit/secrets.example.toml` into the Streamlit Secrets editor and set `PDI_GITHUB_REPO`. For a private repository, add a fine-grained read-only token as `GITHUB_DATA_TOKEN`; for a public repository no data token is required.

## Main directories

```text
.github/workflows/      GitHub automation
profiles/               Pathogen profiles and source registry
schemas/                JSON Schemas for the five core objects
prompts/                Five evidence-restricted internal prompts
src/pdi/                Retrieval, normalization, dedup, events, LLM, rendering
scripts/                CLI entry points
pages/                  Streamlit pages
data/demo/              Offline demonstration issue
```

See `docs/部署与Secrets说明.md` and `docs/实现与审计注意事项.md` before production use.
