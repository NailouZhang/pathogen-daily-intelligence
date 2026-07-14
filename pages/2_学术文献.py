from __future__ import annotations

import streamlit as st

from src.pdi.dashboard import setup_page, works_dataframe

setup_page("学术文献", "📚")
st.title("学术文献")
df = works_dataframe()
if df.empty:
    st.info("暂无文献记录。")
    st.stop()

c1, c2 = st.columns(2)
decision = c1.multiselect("版面决策", sorted(x for x in df["decision"].dropna().unique()), default=[x for x in ["headline", "brief"] if x in set(df["decision"].dropna())])
keyword = c2.text_input("标题/期刊关键词")
filtered = df.copy()
if decision:
    filtered = filtered[filtered["decision"].isin(decision)]
if keyword:
    mask = filtered.fillna("").astype(str).apply(lambda col: col.str.contains(keyword, case=False, regex=False)).any(axis=1)
    filtered = filtered[mask]
st.dataframe(filtered, use_container_width=True, hide_index=True)
