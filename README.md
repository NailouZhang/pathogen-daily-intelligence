# Pathogen Daily Intelligence v1.2

GitHub Actions + GitHub Pages + Streamlit 的中英双语病原每日情报系统。当前病原配置为汉坦病毒，生产检索语言为英文和中文；检索、去重、事件聚类、模型路由、翻译缓存、排版和发布均由 `PathogenProfile` 驱动。

## v1.2 重点更新

- 所有规范化 `ScholarlyWork` 与 `NewsArticle` 都具有中英双语展示字段。
- GitHub Pages 和 Streamlit 默认显示中文标题及中文摘要。
- 每个文献、事件和新闻卡片均可独立点击“显示英文”。
- GitHub Pages 另提供“全部显示英文 / 全部显示中文”。
- 新增科学文本安全渲染，仅允许 `sub`、`sup`、`i`、`em`、`strong`、`b`、`br`，因此 `G<sub>N</sub>/G<sub>C</sub>` 会真正显示为上下标，而不会暴露任意 HTML。
- 翻译前将科学标签替换为不可修改的占位符；翻译后恢复并校验。
- 标题和完整摘要翻译必须保留数字、百分数、单位及科学符号。
- 文献分析、官方通报分析和媒体新闻分析在一次模型调用中同时输出中文标题、忠实中文全文翻译、中文卡片摘要和英文卡片摘要。
- 未进入分析任务的记录使用批量翻译提示词，减少 API 调用次数。
- 翻译和分析结果写入 `data/state/seen_items.json` 的 LLM 缓存；7 天重叠窗口不会反复翻译相同内容。
- 没有通过校验的翻译不会伪装为中文，页面显示“中文翻译暂不可用”，英文按钮仍可查看原文。
- 新增 Streamlit “新闻与官方通报原文”页面。

## 模型与 Python 的职责

Python 负责：

- 提取和规范化原文；
- 科学标签保护；
- 模型调用和顺序降级；
- 批量翻译；
- 数字、占位符和 Schema 校验；
- 翻译缓存；
- GitHub Pages、邮件和 Streamlit 展示。

真正的专业英译中由现有模型路由完成：

```text
Gemini → GitHub Models → Groq → deterministic/no-AI
```

全部模型失败时仍发布日报，但不会根据英文标题伪造中文摘要。

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

Demo 已内置经过人工给定的中英文内容，可在禁用模型时验证：

- 中文默认；
- 单卡片英文切换；
- `G<sub>N</sub>/G<sub>C</sub>` 科学下标；
- 文献、新闻和事件双语字段。

## 分支职责

- `main`：代码、Profile、Schema、提示词、测试和 Workflows。
- `intelligence-data`：生成的 `data/`、`site/`、历史日报和 LLM 缓存。

## GitHub Pages

仓库必须为 public，或账户方案支持私有仓库 Pages。设置：

```text
Settings → Pages → Source → GitHub Actions
```

## Streamlit

从 `main/app.py` 部署。公共仓库通常只需要：

```toml
PDI_GITHUB_REPO = "NailouZhang/pathogen-daily-intelligence"
PDI_DATA_BRANCH = "intelligence-data"
GITHUB_DATA_TOKEN = ""
```

Streamlit 只读取生成数据，不在访客打开页面时调用模型。

## 重要边界

- 当前词表是生产检索种子，不是完整 ICTV 分类清单。
- 无摘要文献只能翻译标题并显示“摘要暂不可用”。
- 媒体使用“暴发”不等于官方确认暴发。
- 模型不得猜 DOI、病例数、日期、地点、宿主或研究结果。
