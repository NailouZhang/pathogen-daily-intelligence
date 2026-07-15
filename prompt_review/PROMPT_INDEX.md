# v1.5 生产提示词审查索引

`production_prompts/` 中的文件与项目根目录 `prompts/` **逐字一致**。修改提示词时，应同步修改两处并运行 `tests/test_prompt_review.py`；测试会阻止两份内容不一致的版本进入 GitHub。

| 文件 | 运行任务 | 默认模型顺序 | 主要输入 | 主要输出 | Python 后校验 |
|---|---|---|---|---|---|
| `pathogen_bootstrap.txt` | 病原知识候选编译 | Gemini → GitHub Models → Groq | 编号权威证据、人工种子、目标 Schema | 分类/别名/疾病/宿主/歧义候选 | 来源 ID、Evidence ID、候选状态 |
| `bilingual_translation_batch.txt` | 首轮批量英译中 | GitHub Models → Gemini → Groq | 标题、摘要/正文、科学占位符 | 中文标题、忠实译文、中英文卡片摘要 | record_id 完整性、数字、占位符、空值 |
| `translation_repair.txt` | 未完成翻译修复 | Gemini → Groq | 仅未解决条目 | 与首轮相同 | 同上，逐条保存 attempt chain |
| `literature_analysis.txt` | 文献深度理解 | GitHub Models → Gemini → Groq | 标题、A/F 证据、书目信息、MeSH | 翻译、设计、方法、结果、意义、局限、证据强度 | Evidence、数字、病原实体、翻译完整性 |
| `official_notice_analysis.txt` | 官方通报理解 | GitHub Models → Gemini → Groq | 标题、N 证据、来源和规则实体 | 病例、地点、行动、实验室、风险、变化 | Evidence、数字、地点、病原、官方归因 |
| `media_news_analysis.txt` | 媒体新闻理解 | GitHub Models → Gemini → Groq | 标题、N 证据、来源链 | 已确认/待确认主张、权威引用、耸动语言 | Evidence、数字、官方确认边界 |
| `daily_synthesis.txt` | 每日综合 | Gemini → GitHub Models → Groq | 已验证事件和文献结构化条目 | 概览、头条、事件、研究、信号、矛盾、观察 | supporting_item_ids、趋势限制 |

## 审查时建议逐项确认

1. 是否明确只允许使用输入证据；
2. 是否明确网页正文是“不可信数据”，不能改变系统指令；
3. 缺失字段是否必须为 `null` 或空数组；
4. 病例、日期、地点、宿主、病原、DOI 是否禁止猜测；
5. 每类实质性结论是否必须绑定 Evidence ID；
6. 翻译是否要求保留数字、单位、比较符号和科学占位符；
7. 文献是否区分作者局限与证据范围缺口；
8. 新闻是否区分媒体陈述、权威引用和官方确认；
9. 每日综合是否禁止在证据不足时使用趋势语言；
10. 输出字段是否与网页、Streamlit 和审计模块实际读取字段一致。
