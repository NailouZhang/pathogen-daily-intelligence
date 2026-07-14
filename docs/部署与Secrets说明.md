# 部署与 Secrets 说明

## 1. GitHub 仓库

建议创建私有仓库 `pathogen-daily-intelligence`，将本工程根目录完整推送到 `main`。GitHub Actions 需要允许工作流读取代码、写入 `intelligence-data` 分支、调用 GitHub Models（可选）以及部署 Pages。

## 2. GitHub Pages

仓库设置中将 Pages 的发布源选择为 **GitHub Actions**。`daily-intelligence.yml` 会：

1. 从 `main` 读取代码；
2. 从 `intelligence-data` 恢复历史 `data/` 和 `site/`；
3. 在 `/tmp/pdi_out` 生成新一期；
4. 验证 `latest.json`、`index.html` 和 `feed.xml` 非空；
5. 上传完整恢复 artifact；
6. 上传 Pages artifact；
7. 将 `data/` 和 `site/` 提交到 `intelligence-data`；
8. 部署 Pages；
9. 可选发送邮件。

生成目录位于 `/tmp` 时，必须复制回 Git 工作区并先 `git add`，再检查 `git diff --cached`。新目录属于未跟踪文件，普通未暂存 diff 不会可靠识别它。

## 3. GitHub Actions Secrets

### 推荐设置

- `NCBI_API_KEY`：可选，提高 NCBI E-utilities 请求额度。
- `SEMANTIC_SCHOLAR_API_KEY`：可选。
- `CROSSREF_MAILTO`：推荐填写联系邮箱。
- `GEMINI_API_KEY` 或 `GOOGLE_AI_STUDIO_API_KEY`：可选首选模型。
- `GROQ_API_KEY`：可选第二备用模型。

GitHub Models 默认使用 workflow 的 `github.token`，工作流声明 `models: read`。如果账户或仓库没有 GitHub Models 权限，系统继续降级。

### 可选邮件

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

邮件步骤设置为 `continue-on-error`，不会阻断网页、JSON 和 Pages 发布。

## 4. GitHub Actions Variables

可选：

- `GEMINI_MODEL`
- `GITHUB_MODELS_MODEL`
- `GROQ_MODEL`
- `PDI_USER_AGENT`

模型名留空时，适配器先查询账户当前可用模型，再按能力关键词选择。动态模型发现能力仍取决于提供商接口和账户权限。

## 5. Streamlit Community Cloud Secrets

```toml
PDI_GITHUB_REPO = "YOUR_GITHUB_USERNAME/pathogen-daily-intelligence"
PDI_DATA_BRANCH = "intelligence-data"

# 私有仓库读取 intelligence-data 时需要；只授予该仓库 Contents: Read。
GITHUB_DATA_TOKEN = ""
```

Streamlit 只读取已生成数据，不在网页访问时抓取文献或调用 LLM，避免访问者触发外部 API 消耗。

## 6. 首次运行顺序

1. 推送 `main`。
2. 在 Actions 手动运行 `CI`。
3. 手动运行 `Bootstrap Pathogen`，下载并审查 artifact。
4. 手动运行 `Daily Pathogen Intelligence`，首次可将 `demo_mode=true`、`disable_llm=true` 验证分支与 Pages。
5. 再以 `demo_mode=false` 运行真实接口。
6. 部署 Streamlit `app.py`。

## 7. 定时逻辑

日报 cron 为 `20 22 * * *`，即北京时间次日 06:20。GitHub cron 使用 UTC，实际开始时间可能因 GitHub 队列稍有延迟。
