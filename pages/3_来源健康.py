from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from src.pdi.dashboard import latest_issue, setup_page

setup_page("来源健康", "🩺")
st.title("来源健康与覆盖状态")
issue = latest_issue()
df = pd.DataFrame(issue.get("source_health", []))
if df.empty:
    st.info("暂无来源状态。")
    st.stop()
st.dataframe(df[[c for c in ["source_id", "status", "record_count", "query_count", "errors"] if c in df.columns]], use_container_width=True, hide_index=True)
counts = df.groupby("status", dropna=False).size().reset_index(name="sources")
st.plotly_chart(px.bar(counts, x="status", y="sources", text="sources", title="来源状态分布"), use_container_width=True)
st.markdown("成功但无新内容、部分完成、失败和未启用均被明确区分；媒体发现器失败不会被表述为官方来源正常。")
