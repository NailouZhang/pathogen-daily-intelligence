from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

from src.pdi.dashboard import latest_issue_result, setup_page, source_label, static_report_result

setup_page("静态日报与下载", "🗞️")
st.title("静态报纸预览与下载")

html_result = static_report_result()
issue_result = latest_issue_result()
issue = issue_result.payload if isinstance(issue_result.payload, dict) else {}

st.markdown(
    f'<div class="pdi-note">HTML 来源：<b>{source_label(html_result.source)}</b>　DailyIssue 来源：<b>{source_label(issue_result.source)}</b></div>',
    unsafe_allow_html=True,
)

if html_result.source == "demo":
    st.warning("当前预览的是包内 Demo 静态日报。")
elif html_result.source == "cache":
    st.warning("GitHub 暂时不可用，当前预览上一次缓存的静态日报。")

if html_result.payload:
    components.html(str(html_result.payload), height=1200, scrolling=True)
else:
    st.error("未读取到静态 HTML。")

c1, c2 = st.columns(2)
c1.download_button(
    "下载静态 HTML",
    str(html_result.payload or ""),
    file_name=f"pathogen_daily_issue_{issue.get('issue_date', 'latest')}.html",
    mime="text/html",
    use_container_width=True,
    disabled=not bool(html_result.payload),
)
c2.download_button(
    "下载 DailyIssue JSON",
    json.dumps(issue, ensure_ascii=False, indent=2),
    file_name=f"pathogen_daily_issue_{issue.get('issue_date', 'latest')}.json",
    mime="application/json",
    use_container_width=True,
    disabled=not bool(issue),
)

st.caption("v1.1 不再部署 GitHub Pages；静态 HTML 继续保存在 intelligence-data/site 和 Workflow recovery artifact 中。")
