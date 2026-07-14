from __future__ import annotations

import pandas as pd
import streamlit as st

from src.pdi.dashboard import history_index_result, latest_issue_result, setup_page, source_label

setup_page("历史审计", "🧾")
st.title("历史与生成审计")

history_result = history_index_result()
history = history_result.payload if isinstance(history_result.payload, list) else []
st.caption(f"历史索引来源：{source_label(history_result.source)}；{history_result.message}")
if history:
    st.subheader("历史索引")
    st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
else:
    st.info("尚无历史索引。")

issue_result = latest_issue_result()
issue = issue_result.payload if isinstance(issue_result.payload, dict) else {}
st.subheader("本期生成审计")
st.caption(f"DailyIssue 来源：{source_label(issue_result.source)}")
st.json(issue.get("generation_audit", {}), expanded=False)
st.subheader("数据质量说明")
for note in issue.get("data_quality_notes", []):
    st.markdown(f"- {note}")
