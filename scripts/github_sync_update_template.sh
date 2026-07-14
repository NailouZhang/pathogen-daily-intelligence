#!/usr/bin/env bash

OLD_DIR="$HOME/下载/pathogen-daily-intelligence"
ZIP_FILE="$HOME/下载/pathogen_daily_intelligence_v1_1_github_actions_streamlit_private_repo.zip"
REMOTE_URL="git@github.com:NailouZhang/pathogen-daily-intelligence.git"
COMMIT_MSG="v1.1：移除 GitHub Pages 并强化 GitHub Actions + Streamlit 私有仓库发布"

set -euo pipefail

cd "$OLD_DIR"

if [ ! -d ".git" ]; then
    echo "错误：$OLD_DIR 不是 Git 仓库。"
    exit 1
fi

if [ ! -f "$ZIP_FILE" ]; then
    echo "错误：未找到更新包：$ZIP_FILE"
    exit 1
fi

if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
    echo "错误：本地仓库还没有提交。"
    exit 1
fi

if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$REMOTE_URL"
else
    git remote add origin "$REMOTE_URL"
fi

git branch -M main

echo "本地提交："
git log -1 --oneline

echo
echo "远程地址："
git remote -v

echo
echo "检测 GitHub SSH："
SSH_OUTPUT="$(ssh -o BatchMode=yes -o ConnectTimeout=20 -T git@github.com 2>&1 || true)"
echo "$SSH_OUTPUT"
if ! grep -qi "successfully authenticated" <<< "$SSH_OUTPUT"; then
    echo "错误：当前 SSH Key 未能通过 GitHub 认证。"
    echo "请先运行：ssh -T git@github.com"
    exit 1
fi

echo
echo "同步远程 main："
git pull --rebase --autostash origin main

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

unzip -q "$ZIP_FILE" -d "$TMP_DIR"
SRC_DIR="$(find "$TMP_DIR" -type f -name "app.py" -printf '%h\n' | head -n 1)"

if [ -z "$SRC_DIR" ] || [ ! -f "$SRC_DIR/app.py" ]; then
    echo "错误：未在更新包中找到 app.py。"
    exit 1
fi

if ! grep -q 'version = "1.1.0"' "$SRC_DIR/pyproject.toml"; then
    echo "错误：ZIP 不是预期的 v1.1 工程包。"
    exit 1
fi

echo
echo "更新包工程根目录：$SRC_DIR"
echo "本地 GitHub 仓库：$OLD_DIR"

# main 只保存代码和包内 Demo。保留本机 Secret、运行缓存和 .env。
# 正式日报数据位于远程 intelligence-data 分支，不受此 rsync 影响。
rsync -av --delete \
  --exclude ".git/" \
  --exclude ".streamlit/secrets.toml" \
  --exclude "runtime/*" \
  --exclude ".env" \
  "$SRC_DIR"/ "$OLD_DIR"/

echo
echo "当前文件变化："
git status --short

git add -A

# 双重保护：真实 Secret 和本机运行缓存不得进入提交。
git reset .streamlit/secrets.toml 2>/dev/null || true
git reset runtime 2>/dev/null || true
git reset .env 2>/dev/null || true

echo
echo "即将提交到 GitHub 的文件："
git diff --cached --stat

if git diff --cached --quiet; then
    echo "没有检测到需要提交的代码变更。"
else
    git commit -m "$COMMIT_MSG"
fi

# 提交之后再次同步，避免远程 main 在更新期间发生变化。
git pull --rebase --autostash origin main

for attempt in 1 2 3; do
    echo
    echo "第 $attempt 次推送……"

    if git push -u origin main; then
        echo
        echo "main 分支已成功推送。"
        echo "仓库地址：https://github.com/NailouZhang/pathogen-daily-intelligence"
        break
    fi

    if [ "$attempt" -eq 3 ]; then
        echo "错误：连续三次推送失败。"
        exit 1
    fi

    sleep $((attempt * 10))
done

echo
echo "触发并检查 CI："
gh workflow run ci.yml
sleep 3
RUN_ID="$(gh run list --workflow ci.yml --limit 1 --json databaseId --jq '.[0].databaseId')"

if [ -z "$RUN_ID" ] || [ "$RUN_ID" = "null" ]; then
    echo "警告：未获取到 CI Run ID，请在 GitHub Actions 页面手动查看。"
    exit 0
fi

gh run watch "$RUN_ID" --exit-status

echo
echo "v1.1 已同步完成，CI 已通过。"
echo "下一步：重新运行 Daily Pathogen Intelligence Demo。"
