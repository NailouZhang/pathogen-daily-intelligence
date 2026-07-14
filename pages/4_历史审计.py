from __future__ import annotations

import pandas as pd
import streamlit as st

from src.pdi.dashboard import history_index, latest_issue, setup_page

setup_page("历史审计", "🧾")
st.title("历史与生成审计")
history = history_index()
if history:
    st.subheader("历史索引")
    st.dataframe(pd.DataFrame(history), use_container_width=True, hide_index=True)
else:
    st.info("尚无远程历史索引；当前展示包内 demo 或最新一期。")

issue = latest_issue()
st.subheader("本期生成审计")
st.json(issue.get("generation_audit", {}), expanded=False)
st.subheader("数据质量说明")
for note in issue.get("data_quality_notes", []):
    st.markdown(f"- {note}")
