from __future__ import annotations

import streamlit as st

from src.pdi.dashboard import all_works, render_bilingual_card, setup_page
from src.pdi.markup import strip_scientific_markup

setup_page("学术文献", "📚")
st.title("学术文献")
st.caption("默认显示中文标题和中文摘要；每篇文献可独立切换为英文原题和原始摘要。")

works = all_works()
if not works:
    st.info("暂无文献记录。")
    st.stop()

decisions = sorted({work.get("filter_result", {}).get("decision") for work in works if work.get("filter_result", {}).get("decision")})
c1, c2 = st.columns(2)
decision_filter = c1.multiselect(
    "版面决策",
    decisions,
    default=[value for value in ["headline", "brief"] if value in decisions],
)
keyword = c2.text_input("中英文标题、摘要或期刊关键词")

filtered = []
for work in works:
    if decision_filter and work.get("filter_result", {}).get("decision") not in decision_filter:
        continue
    haystack = " ".join(
        strip_scientific_markup(value)
        for value in [
            work.get("title", {}).get("translated_zh"),
            work.get("title", {}).get("original"),
            (work.get("display_summary") or {}).get("zh"),
            (work.get("display_summary") or {}).get("en"),
            work.get("bibliography", {}).get("journal"),
        ]
        if value
    )
    if keyword and keyword.casefold() not in haystack.casefold():
        continue
    filtered.append(work)

st.caption(f"显示 {len(filtered)} / {len(works)} 篇文献")
for work in filtered:
    render_bilingual_card(work, "work", "work_page")
