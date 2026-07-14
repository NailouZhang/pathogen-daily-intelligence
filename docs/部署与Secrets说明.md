# v1.3 部署与 Secrets

## GitHub Pages

仓库为 public 后，在：

```text
Settings → Pages → Source → GitHub Actions
```

每日 Workflow 会生成 `/tmp/pdi_out`，上传恢复 artifact 和 Pages artifact，再将 `data/`、`site/` 写入 `intelligence-data`。

## GitHub Actions Secrets

推荐：

- `NCBI_API_KEY`
- `SEMANTIC_SCHOLAR_API_KEY`
- `CROSSREF_MAILTO`
- `GEMINI_API_KEY` 或 `GOOGLE_AI_STUDIO_API_KEY`
- `GROQ_API_KEY`

GitHub Models 使用工作流 `github.token` 和 `models: read`。

可选 Variables：

- `GEMINI_MODEL`
- `GITHUB_MODELS_MODEL`
- `GROQ_MODEL`
- `PDI_USER_AGENT`

## 翻译策略

未设置 Gemini/Groq Key 时仍会尝试 GitHub Models；全部不可用时进入无模型模式。由于 v1.3 要求中文默认，建议至少配置一个可用模型提供商。

模型在 GitHub Actions 中运行，访客打开 Pages 或 Streamlit 不会产生模型费用。

## Streamlit

公共仓库：

```toml
PDI_GITHUB_REPO = "NailouZhang/pathogen-daily-intelligence"
PDI_DATA_BRANCH = "intelligence-data"
GITHUB_DATA_TOKEN = ""
```

私有读取才需要只读 Token。

## 首次升级后的验证顺序

1. 推送 v1.3 到 `main`。
2. 运行 CI。
3. 运行 `demo_mode=true`、`disable_llm=true`，验证双语按钮和上下标。
4. 运行真实模式并启用模型。
5. 检查 `intelligence-data/data/state/seen_items.json` 中存在 `llm_cache`。
6. 第二次运行相同 Demo/重叠数据，检查审计中出现 `cache_hit`。

## v1.3 推荐模型路由

```text
翻译、文献、官方通报、媒体分析：
GitHub Models → Gemini → Groq → deterministic

每日综合：
Gemini → GitHub Models → Groq → deterministic
```

缺少某个 Secret 时该提供商会被记为 `unavailable` 并进入下一提供商。GitHub Models 由 Workflow 的 `models: read` 权限授权；Gemini 与 Groq 需要相应 Secret。

运行后可在 Streamlit 的“LLM 提示词与审计”页面检查最终模型、回退链、校验错误和正文抓取状态。
