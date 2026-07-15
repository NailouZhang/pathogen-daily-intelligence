# v1.6 deterministic demo

This directory is generated with `--demo --disable-llm` to validate JSON, HTML, RSS, bilingual controls and audit output without network credentials.

The local MarianMT path is covered by isolated fake-model regression tests. The current packaging environment could install the Python dependencies but could not resolve `huggingface.co`, so live model-weight download was not falsely reported as tested. GitHub Actions will download and cache the configured model during the first real run.
