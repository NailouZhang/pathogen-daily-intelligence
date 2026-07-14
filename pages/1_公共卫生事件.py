from __future__ import annotations

import streamlit as st

from src.pdi.dashboard import events_dataframe, setup_page

setup_page("公共卫生事件", "🌍")
st.title("公共卫生事件")
df = events_dataframe()
if df.empty:
    st.info("暂无事件记录。")
    st.stop()

c1, c2, c3 = st.columns(3)
country = c1.multiselect("国家/地区", sorted(x for x in df["country"].dropna().unique()))
status = c2.multiselect("官方状态", sorted(x for x in df["official_status"].dropna().unique()))
decision = c3.multiselect("版面决策", sorted(x for x in df["decision"].dropna().unique()), default=[x for x in ["headline", "brief", "review"] if x in set(df["decision"].dropna())])
filtered = df.copy()
if country:
    filtered = filtered[filtered["country"].isin(country)]
if status:
    filtered = filtered[filtered["official_status"].isin(status)]
if decision:
    filtered = filtered[filtered["decision"].isin(decision)]

st.dataframe(filtered, use_container_width=True, hide_index=True, column_config={"source_url": st.column_config.LinkColumn("原始来源")})
