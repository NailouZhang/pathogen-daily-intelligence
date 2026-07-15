from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from src.pdi.config import project_root
from src.pdi.dashboard import fetch_json, fetch_text, latest_issue, setup_page

setup_page("LLM 提示词与审计", "🧠")
st.title("LLM 提示词、回退链与证据审计")
st.caption("生产提示词以独立文本文件保存。模型只按顺序兜底，不对同一任务并行投票。")

root = project_root()
prompt_dir = root / "prompts"
prompt_paths = sorted(prompt_dir.glob("*.txt"))

route_rows = [
    {"任务": "批量翻译", "顺序": "GitHub Models → Gemini → Groq → deterministic"},
    {"任务": "文献深度分析", "顺序": "GitHub Models → Gemini → Groq → deterministic"},
    {"任务": "官方通报分析", "顺序": "GitHub Models → Gemini → Groq → deterministic"},
    {"任务": "媒体新闻分析", "顺序": "GitHub Models → Gemini → Groq → deterministic"},
    {"任务": "每日综合", "顺序": "Gemini → GitHub Models → Groq → deterministic"},
]
st.subheader("模型路由")
st.dataframe(pd.DataFrame(route_rows), use_container_width=True, hide_index=True)

st.subheader("生产提示词")
if not prompt_paths:
    st.warning("工程中没有找到 prompts/*.txt。")
else:
    selected = st.selectbox("选择提示词", [path.name for path in prompt_paths])
    selected_path = prompt_dir / selected
    prompt_text = selected_path.read_text(encoding="utf-8")
    st.download_button(
        "下载当前提示词",
        prompt_text,
        file_name=selected,
        mime="text/plain",
        use_container_width=False,
    )
    st.code(prompt_text, language="text")
    combined_path = root / "prompt_review" / "PRODUCTION_PROMPTS_COMBINED.md"
    if combined_path.exists():
        st.download_button(
            "下载全部生产提示词合订本",
            combined_path.read_text(encoding="utf-8"),
            file_name="PDI_v1.5_生产提示词合订本.md",
            mime="text/markdown",
        )

st.subheader("全流程设计文档")
flow_path = root / "docs" / "全流程与LLM审计逻辑.md"
if flow_path.exists():
    with st.expander("展开收集、处理、理解、总结与审计逻辑", expanded=False):
        st.markdown(flow_path.read_text(encoding="utf-8"))

issue = latest_issue()
st.subheader("本期汇总审计")
st.json(issue.get("generation_audit", {}), expanded=False)

llm_text = fetch_text(
    "data/audit/llm_runs.jsonl",
    root / "data" / "demo" / "audit" / "llm_runs.jsonl",
)
llm_rows = []
for line in llm_text.splitlines():
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    llm_rows.append(
        {
            "任务": row.get("task_name"),
            "记录": row.get("record_id"),
            "最终提供商": row.get("provider"),
            "模型": row.get("model"),
            "状态": row.get("status"),
            "校验": row.get("validation_status"),
            "是否回退": row.get("fallback_used"),
            "未支持主张": row.get("unsupported_claim_count"),
            "错误": "; ".join(row.get("validation_errors") or [])[:500],
        }
    )

st.subheader("本期模型调用审计")
if llm_rows:
    st.dataframe(pd.DataFrame(llm_rows), use_container_width=True, hide_index=True)
    st.download_button(
        "下载原始 LLM JSONL 审计",
        llm_text,
        file_name="llm_runs.jsonl",
        mime="application/x-ndjson",
    )
else:
    st.info("尚未从 intelligence-data 读取到本期 LLM 审计；Demo 或禁用模型运行可能只有确定性记录。")

content_audit = fetch_json(
    "data/audit/content_enrichment.json",
    root / "data" / "demo" / "audit" / "content_enrichment.json",
)
st.subheader("正文与开放全文抓取审计")
if content_audit:
    st.json(content_audit, expanded=False)
else:
    st.info("尚未读取到正文抓取审计。")
