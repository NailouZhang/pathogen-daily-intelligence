# Pathogen Daily Intelligence v1.1

A GitHub-managed, bilingual (English/中文) pathogen daily intelligence system. GitHub Actions performs scheduled retrieval and publication; Streamlit Community Cloud is the production frontend. The first profile is Hantavirus, while retrieval, deduplication, event clustering, LLM routing, rendering, and storage remain profile-driven.

## v1.1 release change

GitHub Pages deployment has been removed completely. This prevents a private GitHub Free repository from producing a false failed workflow after the intelligence data were generated successfully.

The production path is now:

```text
GitHub Actions
  → generate DailyIssue, entities, HTML email, static HTML and RSS
  → persist data/ and site/ to intelligence-data
  → Streamlit reads intelligence-data through the GitHub API
```

Static newspaper HTML is still generated and retained in:

- the `intelligence-data/site/` tree;
- the complete GitHub Actions recovery artifact;
- the Streamlit “静态日报与下载” page.

It is no longer deployed by GitHub Pages.

## What this release contains

- GitHub Actions workflows for profile bootstrap, daily production, monthly source/profile refresh, and CI.
- Node.js 24-compatible GitHub actions (`checkout@v6`, `setup-python@v6`, `upload-artifact@v7`).
- PubMed, Europe PMC, Crossref, and Semantic Scholar scholarly adapters.
- WHO listing-page discovery, ReliefWeb, GDELT, and Google News RSS discovery adapters.
- Five logical data objects: `PathogenProfile`, `ScholarlyWork`, `NewsArticle`, `PublicHealthEvent`, and `DailyIssue`.
- Identifier/bibliographic literature deduplication, URL/title news deduplication, and conservative public-health event clustering.
- Hard separation between human-case events and host/reservoir surveillance.
- Rule-first editorial classification: `headline`, `brief`, `archive`, `duplicate`, and `review`.
- Sequential model routing: Gemini → GitHub Models → Groq → deterministic/no-AI fallback.
- Evidence IDs, JSON validation, unsupported-number checks, and audit fields.
- Independent table-based HTML email, static newspaper HTML, RSS, JSON, CSV, and Streamlit dashboards.
- Durable generated data in `intelligence-data`, so scheduled data updates do not modify `main` or trigger a Streamlit code redeploy.
- Streamlit data origin display, branch commit status, optional Workflow status, manual refresh, local cache fallback, Demo fallback, and HTML/JSON downloads.

## Important scope boundary

The included Hantavirus terms are manually approved **search seeds**, not a claim that the package contains a complete, current ICTV taxonomy. `bootstrap-pathogen` produces an audit artifact and never automatically promotes generated candidates into the production lexicon.

Current production retrieval languages are English and Chinese only.

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

An unavailable source is recorded as failed or partial; remaining sources continue. When all LLM providers fail, the system still publishes a deterministic issue.

## Repository branches

- `main`: application code, profiles, schemas, prompts, tests, and workflows.
- `intelligence-data`: generated `data/` and `site/` trees, including the latest issue, state, archives, RSS, and static HTML.

The daily workflow creates `intelligence-data` automatically on its first successful run.

## Streamlit Community Cloud

Deploy `app.py` from `main`.

For a private repository, grant Streamlit access to the repository, then configure the Streamlit Secrets editor:

```toml
PDI_GITHUB_REPO = "NailouZhang/pathogen-daily-intelligence"
PDI_DATA_BRANCH = "intelligence-data"
GITHUB_DATA_TOKEN = "YOUR_FINE_GRAINED_READ_ONLY_TOKEN"
```

Recommended fine-grained token permissions for this single repository:

- **Contents: Read** — required to read `intelligence-data` files and branch commit metadata.
- **Actions: Read** — optional, only for showing the latest Daily Workflow state in the sidebar.

The application never uses literature or LLM API keys during ordinary page visits.

## Data fallback order

```text
GitHub intelligence-data
  → Streamlit runtime cache
  → bundled Demo
  → explicit missing-data error
```

The current source is always displayed. Demo data are never silently presented as production data.

## Main directories

```text
.github/workflows/      GitHub automation
profiles/               Pathogen profiles and source registry
schemas/                JSON Schemas for the five core objects
prompts/                Five evidence-restricted internal prompts
src/pdi/                Retrieval, normalization, dedup, events, LLM, rendering
scripts/                CLI and Git synchronization templates
pages/                  Streamlit pages
runtime/                Ephemeral Streamlit cache; never committed
data/demo/              Offline demonstration issue
docs/                   Deployment, audit, and object documentation
```

Read `docs/部署与Secrets说明.md`, `docs/Streamlit私有仓库部署.md`, and `docs/实现与审计注意事项.md` before production use.
