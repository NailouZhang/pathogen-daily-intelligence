# Pathogen Daily Intelligence v1.5.0

GitHub Actions + GitHub Pages + Streamlit 的中英文病原每日情报系统。

## v1.5.0 重点修复

### 文献发现与证据补全分离

- 无摘要或全文时仍建立并保留 `ScholarlyWork`，不会因为内容暂缺而漏掉新文献。
- E0 元数据级文献只翻译标题并显示书目信息，不生成研究结论；自动进入第 1、3、7、14 天补抓队列。
- E1 为摘要级证据；E2 为经身份和质量校验的部分 HTML/PDF 文本；E3 为 PMC/Europe PMC XML 或 NCBI PMC BioC 等结构化全文证据。
- 当前归属日期优先使用 online/electronic、PubMed create/entry/index、Crossref created/indexed/deposited；未来卷期日期只作书目展示。

### 摘要与全文多层兜底

按成本和可靠性依次尝试：

```text
PubMed / Europe PMC / Crossref / Semantic Scholar 摘要
→ Europe PMC fullTextXML
→ NCBI PMC BioC
→ Crossref text-mining/XML/HTML/PDF 链接
→ Unpaywall 合法开放位置
→ Semantic Scholar openAccessPdf
→ DOI 出版商页面的 citation_abstract / JSON-LD / HTML
→ 经身份校验的开放 PDF（PyMuPDF → pypdf）
→ 可选且受限的本机 OCR
→ metadata_only + 后续重试
```

- DOI 只是发现入口，不假设每个 DOI 都能直接下载 PDF。
- PDF 必须通过 DOI、标题或作者身份校验；错误文章、补充材料、登录页和低质量文本会被拒绝。
- 临时 PDF 不写入 GitHub，只保存内容哈希、获取来源、许可/版本、有限证据和审计信息。
- 不绕过登录、付费墙、验证码或访问控制。

### 卡片紧凑语言切换

- 静态 GitHub Pages 卡片右上角显示小型 `en` 字符，不再独占一行。
- 点击 `en` 后显示英文并变为 `zh`；再次点击恢复中文。
- Streamlit 使用卡片右侧窄栏中的 `zh/en` 分段字符控件。
- 打印版固定显示中文并隐藏语言切换字符。

### 新闻、去重与多模型机制继续保留

- 新闻正文继续使用 JSON-LD、article/main、canonical/AMP、段落、元描述和 RSS 多层兜底。
- 背景提及目标病原的无关报道归档；病例数字只从明确涉及目标病原的证据句提取。
- DOI/PMID 合并继续执行标题、作者和日期一致性闸门。
- GitHub Models、Gemini、Groq 继续支持提供商内多模型轮换及跨提供商兜底。
- 页面明确区分“结构化全文”“部分全文”“摘要级”“仅元数据/待补抓”。

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
UNPAYWALL_EMAIL（可省略；未设置时复用 CROSSREF_MAILTO）
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
- 只处理公开可访问或明确提供文本挖掘/开放位置的全文；优先结构化 XML/BioC，其次经身份校验的 HTML/PDF。
- 媒体使用“暴发”不等于官方确认暴发。
- 模型不得猜 DOI、病例数、日期、地点、宿主或研究结果。
- 所有模型失败时仍发布，但明确标记翻译或深度分析不可用。


## v1.3.1 稳定性修复

- 修复历史或新规范化对象中 `translation_audit: null` 导致翻译兜底阶段崩溃。
- 新对象默认使用空审计对象；旧数据中的 `null`、列表或其他异常值会在写入前安全迁移为对象。
- 同步加固 `processing_audit`、`retrieval_audit`、`content`、`abstract` 和 `quality` 等可变字段。
- 所有模型失败时继续生成日报，并记录 `translation_unavailable_after_all_providers`，不再因审计写入失败终止 Workflow。
