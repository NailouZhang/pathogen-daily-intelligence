from __future__ import annotations

import streamlit as st

from src.pdi.dashboard import all_events, render_bilingual_card, setup_page

setup_page("公共卫生事件", "🌍")
st.title("公共卫生事件")
st.caption("默认显示中文标题和中文摘要；每个事件卡片可独立切换为英文原文。")

events = all_events()
if not events:
    st.info("暂无事件记录。")
    st.stop()

countries = sorted({event.get("location", {}).get("country") for event in events if event.get("location", {}).get("country")})
statuses = sorted({event.get("official_status") for event in events if event.get("official_status")})
decisions = sorted({event.get("display_decision") for event in events if event.get("display_decision")})

c1, c2, c3 = st.columns(3)
country_filter = c1.multiselect("国家/地区", countries)
status_filter = c2.multiselect("官方状态", statuses)
decision_filter = c3.multiselect(
    "版面决策",
    decisions,
    default=[value for value in ["headline", "brief", "review"] if value in decisions],
)

filtered = []
for event in events:
    if country_filter and event.get("location", {}).get("country") not in country_filter:
        continue
    if status_filter and event.get("official_status") not in status_filter:
        continue
    if decision_filter and event.get("display_decision") not in decision_filter:
        continue
    filtered.append(event)

st.caption(f"显示 {len(filtered)} / {len(events)} 个事件")
for event in filtered:
    render_bilingual_card(event, "event", "event_page")
