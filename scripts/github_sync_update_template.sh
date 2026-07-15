#!/usr/bin/env bash
OLD_DIR="$HOME/下载/pathogen-daily-intelligence"
ZIP_FILE="$HOME/下载/pathogen_daily_intelligence_v1_6_local_mt_translation_quality.zip"
REMOTE_URL="git@github.com:NailouZhang/pathogen-daily-intelligence.git"
COMMIT_MSG="v1.6：增加本地Marian翻译兜底、翻译质量交叉校验与证据身份修复"
RUN_REAL_AFTER_SYNC="true"

set -euo pipefail

cd "$OLD_DIR"

[ -d .git ] || { echo "错误：$OLD_DIR 不是 Git 仓库。"; exit 1; }
[ -f "$ZIP_FILE" ] || { echo "错误：未找到更新包：$ZIP_FILE"; exit 1; }

if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$REMOTE_URL"
else
    git remote add origin "$REMOTE_URL"
fi

git branch -M main
git pull --rebase --autostash origin main

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
unzip -q "$ZIP_FILE" -d "$TMP_DIR"

SRC_DIR="$(find "$TMP_DIR" -type f -name app.py -printf '%h\n' | head -n 1)"
[ -n "$SRC_DIR" ] && [ -f "$SRC_DIR/app.py" ] || { echo "错误：ZIP 中未找到 app.py。"; exit 1; }
grep -q 'version = "1.6.0"' "$SRC_DIR/pyproject.toml" || { echo "错误：不是 v1.6.0 工程包。"; exit 1; }

echo "v1.6 工程目录：$SRC_DIR"
echo "本地仓库目录：$OLD_DIR"

rsync -av --delete \
  --exclude '.git/' \
  --exclude '.streamlit/secrets.toml' \
  --exclude 'runtime/*' \
  --exclude '.env' \
  "$SRC_DIR"/ "$OLD_DIR"/

git add -A
git reset .streamlit/secrets.toml runtime .env 2>/dev/null || true

echo
echo "即将提交的变化："
git diff --cached --stat

if git diff --cached --quiet; then
    echo "当前 main 已经与 v1.6 相同。"
else
    git commit -m "$COMMIT_MSG"
fi

git pull --rebase --autostash origin main

for attempt in 1 2 3; do
    echo "第 $attempt 次推送……"
    if git push -u origin main; then break; fi
    [ "$attempt" -lt 3 ] || { echo "错误：连续三次推送失败。"; exit 1; }
    sleep $((attempt * 10))
done

echo "运行 CI……"
gh workflow run ci.yml --ref main
sleep 5
CI_RUN_ID="$(gh run list --workflow ci.yml --branch main --limit 1 --json databaseId --jq '.[0].databaseId')"
[ -n "$CI_RUN_ID" ] && [ "$CI_RUN_ID" != null ] || { echo "错误：未获得 CI Run ID。"; exit 1; }
gh run watch "$CI_RUN_ID" --exit-status

if [ "$RUN_REAL_AFTER_SYNC" = true ]; then
    echo "运行真实日报……"
    gh workflow run daily-intelligence.yml --ref main \
      -f profile_id=hantavirus \
      -f demo_mode=false \
      -f disable_llm=false
    sleep 5
    RUN_ID="$(gh run list --workflow daily-intelligence.yml --branch main --event workflow_dispatch --limit 1 --json databaseId --jq '.[0].databaseId')"
    [ -n "$RUN_ID" ] && [ "$RUN_ID" != null ] || { echo "错误：未获得 Daily Workflow Run ID。"; exit 1; }
    echo "Daily Workflow Run ID：$RUN_ID"
    gh run watch "$RUN_ID" --exit-status
fi

echo
echo "v1.6 更新完成。"
echo "GitHub Pages：https://nailouzhang.github.io/pathogen-daily-intelligence/"
