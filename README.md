# Pathogen Daily Intelligence v2.0

A GitHub Actions + GitHub Pages pipeline that accepts one `profile_id` seed and publishes a seven-day bilingual pathogen intelligence report.

## What it does

1. Uses `profile_id` as a seed and collects authoritative context from ICTV, ViralZone, and NCBI-compatible sources.
2. Uses Gemini first and Groq as fallback to build a bilingual professional dictionary and semantic query plan. A deterministic seed profile remains available if both models fail.
3. Searches the last seven days across PubMed, Europe PMC, Crossref, Semantic Scholar, and OpenAlex.
4. Searches Google News RSS in English and Chinese, Bing News RSS, GDELT, ReliefWeb, and WHO website results.
5. Merges DOI/PMID/PMCID records, performs title-author deduplication, asks an LLM only to review ambiguous duplicate groups, and links news items that merely report a scholarly paper.
6. Recovers abstracts and legal/open full text through Europe PMC XML, PMC BioC, Crossref links, Unpaywall, Semantic Scholar/OpenAlex PDF links, DOI landing pages, publisher HTML, and PDF text extraction.
7. Separates primary research from reviews/viewpoints.
8. Produces mandatory five-part evidence-based analyses:
   - Research: background, methods, results, contribution, limitations.
   - Review: background, main directions, current state, gaps, future research.
   - News: time, location, event, impact, current status.
9. Translates the title and five analytical elements to professional Chinese. Gemini is primary, dynamically discovered Groq models are fallback, and Python `deep-translator` Google/MyMemory translation is the final fallback.
10. Publishes a newspaper-style GitHub Pages site with a compact `en/zh` control in the upper-right corner of every card.

## Existing secrets

The workflow directly uses the secrets already configured in the repository:

```bash
gh secret set CROSSREF_MAILTO
gh secret set NCBI_API_KEY
gh secret set GEMINI_API_KEY
gh secret set GROQ_API_KEY
```

Optional:

```bash
gh secret set SEMANTIC_SCHOLAR_API_KEY
```

## Run

```bash
gh workflow run daily-intelligence.yml \
  --ref main \
  -f profile_id=hantavirus
```

Then watch the newest run:

```bash
RUN_ID="$(gh run list --workflow daily-intelligence.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$RUN_ID" --exit-status
```

## Output branches

- `main`: executable code and reviewed prompts.
- `intelligence-data`: `data/latest.json`, history, state/profile cache, audit files, and `site/`.
- GitHub Pages deploys the `site/` artifact from each successful run.

## Evidence policy

A record is not discarded merely because an abstract or full text is missing. It remains as `E0 metadata_only`; however, research findings are not inferred from the title. The workflow does not bypass paywalls, login barriers, robots restrictions, or access controls. PDFs are processed temporarily and are not committed to GitHub.
