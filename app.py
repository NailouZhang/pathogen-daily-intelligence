from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

st.set_page_config(page_title="病原每日情报", layout="wide")
path = Path("data/latest.json")
if not path.exists():
    st.info("尚未在本地找到 data/latest.json。GitHub Pages 是默认部署方式；运行一次日报后即可在 Streamlit 中预览。")
    st.stop()
issue = json.loads(path.read_text(encoding="utf-8"))
st.title(issue.get("title_zh", "病原每日情报"))
st.caption(f"{issue.get('window_start')} — {issue.get('window_end')}")
research = [x for x in issue.get("papers", []) if x.get("paper_type") == "research"]
reviews = [x for x in issue.get("papers", []) if x.get("paper_type") == "review"]
for heading, items in (("研究型文献", research), ("综述与观点", reviews), ("新闻与公共卫生", issue.get("news", []))):
    st.header(heading)
    for item in items:
        with st.container(border=True):
            st.subheader(item.get("title_zh") or item.get("title"))
            st.write(item.get("summary_zh") or "暂无中文总结")
            with st.expander("英文原文与审计"):
                st.write(item.get("title"))
                st.json({"analysis": item.get("analysis"), "translation_audit": item.get("translation_audit")})
