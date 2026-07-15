#!/usr/bin/env bash
set -euo pipefail

OLD_DIR="${OLD_DIR:-$HOME/下载/pathogen-daily-intelligence}"
ZIP_FILE="${ZIP_FILE:-$HOME/下载/pathogen_daily_intelligence_v2_0_robust_evidence_site.zip}"
REMOTE_URL="${REMOTE_URL:-git@github.com:NailouZhang/pathogen-daily-intelligence.git}"
COMMIT_MSG="${COMMIT_MSG:-v2.0：重构七日病原文献新闻抓取、五要素解读与多层翻译兜底}"

cd "$OLD_DIR"
[ -d .git ] || { echo "错误：$OLD_DIR 不是 Git 仓库"; exit 1; }
[ -f "$ZIP_FILE" ] || { echo "错误：未找到 $ZIP_FILE"; exit 1; }

git remote set-url origin "$REMOTE_URL"
git branch -M main
git pull --rebase --autostash origin main

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -type f -name pyproject.toml -printf '%h\n' | head -n 1)"
[ -n "$SRC_DIR" ] || { echo "错误：ZIP 中未找到工程根目录"; exit 1; }
grep -q 'version = "2.0.0"' "$SRC_DIR/pyproject.toml" || { echo "错误：不是 v2.0.0 包"; exit 1; }

rsync -av --delete \
  --exclude .git/ \
  --exclude .streamlit/secrets.toml \
  --exclude runtime/ \
  --exclude .env \
  "$SRC_DIR"/ "$OLD_DIR"/

git add -A
git reset .streamlit/secrets.toml runtime .env 2>/dev/null || true

git diff --cached --stat
if ! git diff --cached --quiet; then
  git commit -m "$COMMIT_MSG"
fi

git pull --rebase --autostash origin main
git push -u origin main

gh workflow run ci.yml --ref main
sleep 5
CI_RUN_ID="$(gh run list --workflow ci.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$CI_RUN_ID" --exit-status

gh workflow run daily-intelligence.yml --ref main -f profile_id=hantavirus
sleep 5
RUN_ID="$(gh run list --workflow daily-intelligence.yml --branch main --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
echo "Daily Workflow Run ID: $RUN_ID"
gh run watch "$RUN_ID" --exit-status

echo "GitHub Pages: https://nailouzhang.github.io/pathogen-daily-intelligence/"
