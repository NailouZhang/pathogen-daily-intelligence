#!/usr/bin/env bash

ZIP_FILE="$HOME/下载/pathogen_daily_intelligence_v1_1_github_actions_streamlit_private_repo.zip"
NEW_DIR="$HOME/下载/pathogen-daily-intelligence"
REMOTE_URL="git@github.com:NailouZhang/pathogen-daily-intelligence.git"
COMMIT_MSG="v1.1：初始化 GitHub Actions + Streamlit 私有仓库病原每日情报系统"

set -euo pipefail

if [ ! -f "$ZIP_FILE" ]; then
    echo "错误：未找到工程包：$ZIP_FILE"
    exit 1
fi

if [ -e "$NEW_DIR/.git" ]; then
    echo "错误：$NEW_DIR 已经是 Git 仓库，请改用 github_sync_update_template.sh。"
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -type f -name "app.py" -printf '%h\n' | head -n 1)"

if [ -z "$SRC_DIR" ] || [ ! -f "$SRC_DIR/app.py" ]; then
    echo "错误：未在 ZIP 中找到 app.py。"
    exit 1
fi

mkdir -p "$NEW_DIR"
rsync -av --delete \
  --exclude ".git/" \
  --exclude ".streamlit/secrets.toml" \
  --exclude "runtime/*" \
  --exclude ".env" \
  "$SRC_DIR"/ "$NEW_DIR"/

cd "$NEW_DIR"
git init -b main
git config user.name >/dev/null 2>&1 || git config user.name "NailouZhang"
git config user.email >/dev/null 2>&1 || git config user.email "buildingzhang@outlook.com"
git add -A
git commit -m "$COMMIT_MSG"
git remote add origin "$REMOTE_URL"
git push -u origin main
