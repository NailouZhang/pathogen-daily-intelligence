from __future__ import annotations

import streamlit as st

from src.pdi.dashboard import all_events, all_works, latest_issue, render_bilingual_card, setup_page

setup_page("汉坦病毒每日情报", "📰")
issue = latest_issue()
works = all_works()
events = all_events()
work_map = {work.get("work_id"): work for work in works}
event_map = {event.get("event_id"): event for event in events}

st.title("汉坦病毒每日情报")
st.caption("Hantavirus Daily Intelligence · 中文默认 · 每卡片可切换英文")

if not issue:
    st.warning("尚未读取到日报。请先运行 GitHub Actions 的 daily-intelligence workflow，或使用包内 Demo 数据。")
    st.stop()

window = issue.get("coverage_window", {})
st.markdown(
    f'<div class="pdi-note">本期日期：<b>{issue.get("issue_date")}</b>　覆盖窗口：{window.get("start")}—{window.get("end")}（{window.get("timezone")}）</div>',
    unsafe_allow_html=True,
)

stats = issue.get("statistics", {})
cols = st.columns(4)
cols[0].metric("入选文献", stats.get("scholarly_selected", 0))
cols[1].metric("公共卫生事件", stats.get("public_health_events", 0))
cols[2].metric("已译文献", stats.get("translated_works", 0))
cols[3].metric("已译事件", stats.get("translated_events", 0))

lead = issue.get("lead_story")
if lead:
    st.subheader("今日头条")
    item_id = lead.get("item_id")
    if item_id in event_map:
        render_bilingual_card(event_map[item_id], "event", "lead")
    elif item_id in work_map:
        render_bilingual_card(work_map[item_id], "work", "lead")
    else:
        st.markdown(f"### {lead.get('title') or '中文标题暂不可用'}")
else:
    st.info("今日未发现符合头条条件的新记录。")

for section in issue.get("sections", []):
    if section.get("section_id") == "lead":
        continue
    st.subheader(section.get("title") or "本期栏目")
    found = False
    for item_id in section.get("item_ids", []):
        if item_id in event_map:
            render_bilingual_card(event_map[item_id], "event", f"section_{section.get('section_id')}")
            found = True
        elif item_id in work_map:
            render_bilingual_card(work_map[item_id], "work", f"section_{section.get('section_id')}")
            found = True
    if not found:
        st.info("该栏目当前没有可显示的实体记录。")

observations = issue.get("daily_observations", [])
if observations:
    st.subheader("每日观察")
    for observation in observations:
        st.write(observation.get("text"))
        st.caption("支持对象：" + ", ".join(observation.get("supporting_item_ids", [])))

st.subheader("数据质量")
for note in issue.get("data_quality_notes", []):
    st.markdown(f"- {note}")

st.caption("本简报仅用于信息跟踪和科研参考，不替代官方公共卫生通报、临床诊断或专业决策。")
