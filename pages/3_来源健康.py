from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from src.pdi.dashboard import github_repository_status, latest_issue_result, setup_page, source_label

setup_page("来源健康", "🩺")
st.title("来源健康与运行状态")

issue_result = latest_issue_result()
issue = issue_result.payload if isinstance(issue_result.payload, dict) else {}
repo = github_repository_status()

c1, c2, c3, c4 = st.columns(4)
c1.metric("当前数据来源", source_label(issue_result.source))
c2.metric("数据分支", repo.get("branch") or "未配置")
c3.metric("数据提交", (repo.get("commit_sha") or "未知")[:10])
c4.metric("日报 Workflow", repo.get("workflow_conclusion") or repo.get("workflow_status") or "未知")

st.caption(issue_result.message)
if repo.get("message"):
    st.caption(repo["message"])

links = st.columns(2)
if repo.get("workflow_url"):
    links[0].link_button("打开最近 Daily Workflow", repo["workflow_url"], use_container_width=True)
if repo.get("commit_url"):
    links[1].link_button("查看 intelligence-data 提交", repo["commit_url"], use_container_width=True)

st.subheader("检索来源状态")
df = pd.DataFrame(issue.get("source_health", []))
if df.empty:
    st.info("暂无来源状态。")
    st.stop()

columns = [c for c in ["source_id", "status", "record_count", "query_count", "errors"] if c in df.columns]
st.dataframe(df[columns], use_container_width=True, hide_index=True)
counts = df.groupby("status", dropna=False).size().reset_index(name="sources")
st.plotly_chart(px.bar(counts, x="status", y="sources", text="sources", title="来源状态分布"), use_container_width=True)
st.markdown("成功但无新内容、部分完成、失败和未启用均被明确区分；媒体发现器失败不会被表述为官方来源正常。")
