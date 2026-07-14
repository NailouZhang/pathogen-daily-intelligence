from __future__ import annotations

import streamlit as st

from src.pdi.dashboard import all_articles, render_bilingual_card, setup_page
from src.pdi.markup import strip_scientific_markup

setup_page("新闻文章", "🗞️")
st.title("新闻与官方通报原文")
st.caption("这里展示事件聚类前的独立新闻文章。默认中文，每篇文章可切换英文原文。")

articles = all_articles()
if not articles:
    st.info("暂无新闻文章记录。")
    st.stop()

sources = sorted({article.get("source", {}).get("name") for article in articles if article.get("source", {}).get("name")})
decisions = sorted({article.get("classification", {}).get("decision") for article in articles if article.get("classification", {}).get("decision")})
c1, c2, c3 = st.columns(3)
source_filter = c1.multiselect("来源", sources)
decision_filter = c2.multiselect("版面决策", decisions)
keyword = c3.text_input("中英文标题或摘要关键词")

filtered = []
for article in articles:
    if source_filter and article.get("source", {}).get("name") not in source_filter:
        continue
    if decision_filter and article.get("classification", {}).get("decision") not in decision_filter:
        continue
    haystack = " ".join(
        strip_scientific_markup(value)
        for value in [
            article.get("title", {}).get("translated_zh"),
            article.get("title", {}).get("original"),
            (article.get("display_summary") or {}).get("zh"),
            (article.get("display_summary") or {}).get("en"),
        ]
        if value
    )
    if keyword and keyword.casefold() not in haystack.casefold():
        continue
    filtered.append(article)

st.caption(f"显示 {len(filtered)} / {len(articles)} 篇文章")
for article in filtered:
    render_bilingual_card(article, "article", "article_page")
