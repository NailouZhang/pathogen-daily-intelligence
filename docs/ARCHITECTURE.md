# v2.0 architecture and audit logic

## Profile bootstrap

The first real run reads `profiles/<profile_id>/seed.yaml`, fetches supplied ICTV/ViralZone URLs and generic search pages, and asks Gemini/Groq to return a bilingual profile. The generated profile is persisted at `data/profiles/<profile_id>/profile.json` on `intelligence-data`. Subsequent runs reuse it.

## Date policy

The report window is seven calendar days. A scholarly item is included using the first available date within the window in this order: online publication, first publication, database creation, database indexing, publication, print. A future print issue does not postpone an already-online paper.

## Evidence levels

- E0: metadata only. Translate title; no research conclusion.
- E1: abstract available. Abstract-level analysis.
- E2: verified public HTML/XML/PDF evidence available. Expanded analysis.

## Translation

Translation is deliberately separate from scientific analysis. It preserves numbers, uncertainty, technical names, and glossary tokens. The final Python fallback uses independent translation services through `deep-translator`, so a model outage does not automatically leave every title untranslated.

## Failure behavior

An API, webpage, model, or translation failure is recorded in `data/audit/`. A single failed record never aborts the daily publication. When model analysis fails but source evidence exists, the system produces a conservative source-extract fallback instead of an empty card.
