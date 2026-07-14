# GitHub Actions + Streamlit 部署与 Secrets 说明

## 1. 发布架构

v1.1 面向私有 GitHub 仓库，不再使用 GitHub Pages。

```text
main：代码、Profile、Schema、Prompt、测试和 Workflow
intelligence-data：DailyIssue、实体、状态、静态 HTML、RSS 和历史归档
Streamlit：正式网页入口，运行时读取 intelligence-data
Actions artifact：每次运行的完整恢复包
```

GitHub Actions 只需要：

- 读取 `main`；
- 写入 `intelligence-data`；
- 可选调用 GitHub Models；
- 上传 recovery artifact。

不再需要 `pages: write`、`id-token: write`、Pages environment 或 deploy-pages job。

## 2. 每日工作流

`daily-intelligence.yml`：

1. 从 `main` checkout 可执行代码；
2. 从 `intelligence-data` 恢复历史 `data/` 和 `site/`；
3. 在 `/tmp/pdi_out` 生成新一期；
4. 检查 JSON、静态 HTML、RSS 和邮件 HTML 非空；
5. 上传完整 recovery artifact；
6. 可选发送邮件；
7. 切换或创建 `intelligence-data`；
8. 将 `/tmp/pdi_out/data` 和 `/tmp/pdi_out/site` 复制到 Git 工作区；
9. 先执行 `git add -A data site`，再检查 staged diff；
10. commit 和 push 数据分支；
11. 将 issue date、数据 commit 和运行地址写入 Workflow Summary。

生成目录位于 `/tmp` 时，Git 不会自动提交其中的文件。尤其是新目录在 `git add` 之前不属于 staged diff，不能用普通 `git diff` 判断是否生成成功。

## 3. GitHub Actions Secrets

推荐配置：

- `NCBI_API_KEY`：可选，提高 NCBI E-utilities 请求额度；
- `SEMANTIC_SCHOLAR_API_KEY`：可选；
- `CROSSREF_MAILTO`：推荐填写联系邮箱；
- `GEMINI_API_KEY` 或 `GOOGLE_AI_STUDIO_API_KEY`：可选首选模型；
- `GROQ_API_KEY`：可选第二备用模型。

GitHub Models 使用 Workflow 自带的 `github.token` 和 `models: read`。账户没有该能力时，系统继续降级。

### 可选邮件

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`

邮件步骤使用 `continue-on-error`，邮件失败不会阻止数据分支持久化。

## 4. GitHub Actions Variables

可选：

- `GEMINI_MODEL`
- `GITHUB_MODELS_MODEL`
- `GROQ_MODEL`
- `PDI_USER_AGENT`

模型名留空时，适配器按提供商实际能力进行发现或候选探测。模型列表、权限和结构化输出能力属于动态信息，必须以运行时结果为准。

## 5. Streamlit Secrets

正常网页只需要：

```toml
PDI_GITHUB_REPO = "NailouZhang/pathogen-daily-intelligence"
PDI_DATA_BRANCH = "intelligence-data"
GITHUB_DATA_TOKEN = ""
```

私有仓库需要 fine-grained token。建议只授权当前仓库：

- Contents: Read（必需）；
- Actions: Read（可选，用于显示 Workflow 状态）。

不得把模型、文献接口或 SMTP Secret 粘贴到 Streamlit，普通访客访问网页不会触发外部检索或 LLM 调用。

## 6. 首次运行顺序

1. 推送 v1.1 到 `main`；
2. 等待或手动运行 `CI`；
3. 手动运行 `Bootstrap Pathogen` 并下载审核 artifact；
4. 手动运行 Daily Workflow：`demo_mode=true`、`disable_llm=true`；
5. 检查 Workflow 为绿色成功；
6. 检查 `intelligence-data/data/latest.json` 和 `site/index.html`；
7. 部署 Streamlit `app.py`；
8. 配置 Streamlit Secrets；
9. 确认首页显示“GitHub 生产数据”，而不是 Demo；
10. 再运行真实接口日报。

## 7. 定时逻辑

日报 cron 为 `20 22 * * *`，对应北京时间次日 06:20。GitHub cron 使用 UTC，实际启动可能因共享队列延迟。

## 8. 静态 HTML 的用途

虽然不再使用 Pages，`site/index.html`、历史 HTML 和 RSS 仍然有价值：

- 可从 Streamlit 预览和下载；
- 可从 recovery artifact 恢复；
- 可供未来迁移到其他静态托管服务；
- 可用于历史审计和打印。
