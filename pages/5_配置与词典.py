from __future__ import annotations

import pandas as pd
import streamlit as st

from src.pdi.config import load_profile
from src.pdi.dashboard import setup_page

setup_page("配置与词典", "⚙️")
st.title("PathogenProfile 与中英词典")
profile = load_profile("hantavirus")
st.markdown('<div class="pdi-note">当前词典仅以英文和中文为生产检索语言。人工种子已批准用于检索，但不等同于完整 ICTV 验证清单。</div>', unsafe_allow_html=True)

st.subheader("基础配置")
st.json({k: profile.get(k) for k in ["profile_id", "profile_version", "lifecycle_status", "display_names", "priority_languages", "search_policy", "llm_policy"]}, expanded=False)
st.subheader("词典")
st.dataframe(pd.DataFrame(profile.get("lexicon", [])), use_container_width=True, hide_index=True)
st.subheader("查询组")
st.dataframe(pd.DataFrame(profile.get("query_groups", {}).get("groups", [])), use_container_width=True, hide_index=True)
st.subheader("来源注册表")
st.dataframe(pd.DataFrame(profile.get("source_registry", {}).get("sources", [])), use_container_width=True, hide_index=True)
