#!/usr/bin/env bash
set -euo pipefail

OLD_DIR="$HOME/下载/pathogen-daily-intelligence"
ZIP_FILE="$HOME/下载/pathogen_daily_intelligence_v1_github_streamlit_bilingual_mature.zip"
COMMIT_MSG="v1.0：同步中英双语病原每日情报成熟首版工程"

cd "$OLD_DIR"
git pull --rebase --autostash origin main

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -type f -name app.py -printf '%h\n' | head -n 1)"

if [ -z "$SRC_DIR" ] || [ ! -f "$SRC_DIR/app.py" ]; then
  echo "错误：未在 ZIP 中找到 app.py"
  exit 1
fi

rsync -av --delete \
  --exclude '.git/' \
  --exclude '.streamlit/secrets.toml' \
  --exclude 'runtime/*' \
  --exclude '.env' \
  "$SRC_DIR"/ "$OLD_DIR"/

echo
echo "当前文件变化："
git status --short

git add -A
git reset .streamlit/secrets.toml 2>/dev/null || true
git reset runtime 2>/dev/null || true
git reset .env 2>/dev/null || true

echo
echo "即将提交到 GitHub 的文件："
git diff --cached --stat

if git diff --cached --quiet; then
  echo "没有检测到需要提交的代码变更。"
  exit 0
fi

git commit -m "$COMMIT_MSG"
git pull --rebase --autostash origin main
git push origin main

echo "代码已推送到 main。"
echo "可运行：gh workflow run ci.yml"
echo "可运行 demo 日报：gh workflow run daily-intelligence.yml -f profile_id=hantavirus -f demo_mode=true -f disable_llm=true"
