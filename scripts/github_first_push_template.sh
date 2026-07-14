#!/usr/bin/env bash
set -euo pipefail

ZIP_FILE="$HOME/下载/pathogen_daily_intelligence_v1_github_streamlit_bilingual_mature.zip"
NEW_DIR="$HOME/下载/pathogen-daily-intelligence"
GITHUB_OWNER="请替换为你的GitHub用户名"
REPO_NAME="pathogen-daily-intelligence"
COMMIT_MSG="v1.0：上线中英双语病原每日情报 GitHub Actions + Streamlit 成熟首版"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -type f -name app.py -printf '%h\n' | head -n 1)"

if [ -z "$SRC_DIR" ] || [ ! -f "$SRC_DIR/app.py" ]; then
  echo "错误：ZIP 中未找到 app.py"
  exit 1
fi

mkdir -p "$NEW_DIR"
rsync -av --delete \
  --exclude '.git/' \
  --exclude '.streamlit/secrets.toml' \
  --exclude 'runtime/*' \
  "$SRC_DIR"/ "$NEW_DIR"/

cd "$NEW_DIR"
git init -b main
git config user.name "${GIT_AUTHOR_NAME:-Buildingzhang}"
git config user.email "${GIT_AUTHOR_EMAIL:-buildingzhang@outlook.com}"
git add -A
git commit -m "$COMMIT_MSG"

if command -v gh >/dev/null 2>&1; then
  gh auth status
  gh repo create "$GITHUB_OWNER/$REPO_NAME" --private --source=. --remote=origin --push
else
  echo "未检测到 gh。请先在 GitHub 网页创建空仓库：$GITHUB_OWNER/$REPO_NAME"
  echo "然后运行："
  echo "git remote add origin https://github.com/$GITHUB_OWNER/$REPO_NAME.git"
  echo "git push -u origin main"
fi
