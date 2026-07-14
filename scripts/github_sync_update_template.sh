#!/usr/bin/env bash
OLD_DIR="$HOME/下载/pathogen-daily-intelligence"
ZIP_FILE="$HOME/下载/pathogen_daily_intelligence_v1_3_multimodel_deep_content_audit.zip"
REMOTE_URL="git@github.com:NailouZhang/pathogen-daily-intelligence.git"
COMMIT_MSG="v1.3：增加新闻正文理解、文献深度审计与多模型顺序兜底"

set -euo pipefail

cd "$OLD_DIR"
[ -d .git ] || { echo "错误：$OLD_DIR 不是 Git 仓库。"; exit 1; }
[ -f "$ZIP_FILE" ] || { echo "错误：未找到 $ZIP_FILE"; exit 1; }

git remote set-url origin "$REMOTE_URL"
git branch -M main
git pull --rebase --autostash origin main

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -type f -name app.py -printf '%h\n' | head -n 1)"
[ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/app.py" ] || { echo "错误：ZIP 中没有 app.py。"; exit 1; }
grep -q 'version = "1.3.0"' "$SRC_DIR/pyproject.toml" || { echo "错误：不是 v1.3 工程包。"; exit 1; }

rsync -av --delete \
  --exclude '.git/' \
  --exclude '.streamlit/secrets.toml' \
  --exclude 'runtime/*' \
  --exclude '.env' \
  "$SRC_DIR"/ "$OLD_DIR"/

git add -A
git reset .streamlit/secrets.toml runtime .env 2>/dev/null || true

echo "即将提交的变化："
git diff --cached --stat

if git diff --cached --quiet; then
    echo "当前 main 已经是 v1.3。"
else
    git commit -m "$COMMIT_MSG"
fi

git pull --rebase --autostash origin main
git push -u origin main

echo "运行 CI……"
gh workflow run ci.yml --ref main
sleep 4
CI_RUN_ID="$(gh run list --workflow ci.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$CI_RUN_ID" --exit-status

echo "运行无模型 Demo，验证页面、审计和 Pages……"
gh workflow run daily-intelligence.yml \
  --ref main \
  -f profile_id=hantavirus \
  -f demo_mode=true \
  -f disable_llm=true
sleep 5
RUN_ID="$(gh run list --workflow daily-intelligence.yml --branch main --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
gh run watch "$RUN_ID" --exit-status

echo "v1.3 更新完成："
echo "https://nailouzhang.github.io/pathogen-daily-intelligence/"
