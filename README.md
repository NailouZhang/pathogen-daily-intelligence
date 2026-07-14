# Pathogen Daily Intelligence v1.3.1

GitHub Actions + GitHub Pages + Streamlit 的中英双语病原每日情报系统。当前病原配置为汉坦病毒，检索语言为英文和中文；系统以 `PathogenProfile` 驱动文献检索、新闻发现、正文抓取、文献实体去重、公共卫生事件聚类、多模型理解、证据校验、日报生成和历史审计。

## v1.3.1 重点更新

### 多模型顺序兜底

翻译和单条内容分析默认：

```text
GitHub Models → Gemini → Groq → deterministic
```

- 首先用 GitHub Models 批量处理；
- 只有请求失败、JSON 无效、条目缺失或证据/数字/翻译校验失败的记录，才进入 Gemini；
- 仍未解决的记录再逐条进入 Groq；
- 已成功记录不会重复调用后续模型；
- 全部模型失败时保留英文原文和规则结果，不伪造中文内容。

每日综合默认：

```text
Gemini → GitHub Models → Groq → deterministic
```

同一任务不并行调用多个模型，也不使用模型投票。

### 新闻不再只理解标题

- 对优先新闻和官方通报抓取公开网页正文；
- 支持 JSON-LD `articleBody`、`article`、`main` 和正文段落回退；
- 跟随普通重定向，尽量解析 Google News 等发现链接的最终来源；
- 不绕过登录、付费墙或访问控制；
- 只保存有限长度的证据句，不复制完整出版页面；
- 官方通报与媒体报道使用不同提示词和不同事实边界。

### 文献深度理解

- 默认使用标题和摘要；
- 含 PMCID 的开放获取文献可从 Europe PMC Open Access XML 增强 Methods、Results、Discussion、Conclusion 和 Limitation 证据；
- 输出研究问题、类型、设计、样本、方法、关键发现、定量结果、意义、作者局限、证据缺口和证据强度；
- 每个实质性结论绑定 `A*`、`F*` 或 `T0` Evidence ID；
- 没有摘要或全文证据时，不根据标题编造研究结果。

### 审计

每次运行新增：

```text
data/audit/content_enrichment.json
data/audit/llm_runs.jsonl
data/audit/object_audit.jsonl
```

可以追踪：正文是否抓取成功、是否使用开放全文、每个模型尝试、校验失败原因、最终接受模型、缓存命中、unsupported claims 和对象处理状态。

### 提示词独立审查

生产提示词位于：

```text
prompts/*.txt
```

审查副本与合订本位于：

```text
prompt_review/
```

Streamlit 新增“LLM 提示词与审计”页面，可直接查看和下载提示词、流程文档及本期模型审计。

## 完整流程文档

参见：

```text
docs/全流程与LLM审计逻辑.md
```

## 本地验证

```bash
python -m pip install -r requirements.txt
python scripts/validate_project.py
pytest
python scripts/run_daily.py \
  --profile hantavirus \
  --output-dir build/demo \
  --demo \
  --disable-llm
streamlit run app.py
```

## GitHub Secrets

真实文献接口可选：

```text
NCBI_API_KEY
CROSSREF_MAILTO
SEMANTIC_SCHOLAR_API_KEY
```

模型：

```text
GEMINI_API_KEY 或 GOOGLE_AI_STUDIO_API_KEY
GROQ_API_KEY
```

GitHub Models 使用 Workflow 的 `${{ github.token }}` 和 `models: read` 权限，不需要把个人令牌提交到仓库。

模型名称可通过 GitHub Variables 覆盖：

```text
GITHUB_MODELS_MODEL
GEMINI_MODEL
GROQ_MODEL
```

## 分支职责

- `main`：代码、Profile、Schema、提示词、测试和 Workflows。
- `intelligence-data`：生成的实体、审计、`DailyIssue`、静态网页、RSS 和 LLM 缓存。

## GitHub Pages

```text
Settings → Pages → Source → GitHub Actions
```

## Streamlit

从 `main/app.py` 部署。公共仓库可配置：

```toml
PDI_GITHUB_REPO = "NailouZhang/pathogen-daily-intelligence"
PDI_DATA_BRANCH = "intelligence-data"
GITHUB_DATA_TOKEN = ""
```

Streamlit 只读取生成结果，不在访客打开页面时抓取新闻或调用模型。

## 重要边界

- 当前词表是检索种子，不是完整 ICTV 正式分类清单。
- 新闻正文抓取不绕过网站限制，只保留有限证据。
- 只有开放获取且可通过 Europe PMC 获取的全文才进行有限证据增强。
- 媒体使用“暴发”不等于官方确认暴发。
- 模型不得猜 DOI、病例数、日期、地点、宿主或研究结果。
- 所有模型失败时仍发布，但明确标记翻译或深度分析不可用。


## v1.3.1 稳定性修复

- 修复历史或新规范化对象中 `translation_audit: null` 导致翻译兜底阶段崩溃。
- 新对象默认使用空审计对象；旧数据中的 `null`、列表或其他异常值会在写入前安全迁移为对象。
- 同步加固 `processing_audit`、`retrieval_audit`、`content`、`abstract` 和 `quality` 等可变字段。
- 所有模型失败时继续生成日报，并记录 `translation_unavailable_after_all_providers`，不再因审计写入失败终止 Workflow。
