#!/usr/bin/env bash
ZIP_FILE="$HOME/下载/pathogen_daily_intelligence_v1_5_scholarly_recovery_compact_language.zip"
NEW_DIR="$HOME/下载/pathogen-daily-intelligence"
REPO_NAME="pathogen-daily-intelligence"
COMMIT_MSG="v1.5：上线当前可报道日期、多策略正文抓取与动态多模型兜底版本"
set -euo pipefail

gh auth status
OWNER="$(gh api user --jq .login)"
[ ! -e "$NEW_DIR/.git" ] || { echo "错误：$NEW_DIR 已经是 Git 仓库。"; exit 1; }
[ -f "$ZIP_FILE" ] || { echo "错误：未找到 $ZIP_FILE"; exit 1; }
TMP_DIR="$(mktemp -d)"; trap 'rm -rf "$TMP_DIR"' EXIT
unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -type f -name app.py -printf '%h\n' | head -n1)"
[ -n "$SRC_DIR" ] || { echo "错误：ZIP 中未找到 app.py。"; exit 1; }
mkdir -p "$NEW_DIR"
rsync -av --delete --exclude .git/ --exclude .streamlit/secrets.toml --exclude 'runtime/*' --exclude .env "$SRC_DIR"/ "$NEW_DIR"/
cd "$NEW_DIR"
git init -b main
git add -A
git commit -m "$COMMIT_MSG"
gh repo create "$OWNER/$REPO_NAME" --public --source=. --remote=origin --push
