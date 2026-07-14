from __future__ import annotations

import streamlit as st

from src.pdi.dashboard import latest_issue, setup_page

setup_page("汉坦病毒每日情报", "📰")
issue = latest_issue()

st.title("汉坦病毒每日情报")
st.caption("Hantavirus Daily Intelligence · GitHub Actions + Streamlit · English/中文")

if not issue:
    st.warning("尚未读取到日报。请先运行 GitHub Actions 的 daily-intelligence workflow，或使用包内 demo 数据。")
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
cols[2].metric("官方事件", stats.get("official_events", 0))
cols[3].metric("涉及国家", stats.get("countries", 0))

lead = issue.get("lead_story")
if lead:
    st.subheader("今日头条")
    st.markdown(f'<div class="pdi-kicker">{lead.get("item_type")}</div><div class="pdi-card"><h3>{lead.get("title")}</h3><span class="small-muted">证据对象：{lead.get("item_id")}</span></div>', unsafe_allow_html=True)
else:
    st.info("今日未发现符合头条条件的新记录。")

st.subheader("本期栏目")
for section in issue.get("sections", []):
    with st.expander(f"{section.get('title')}（{len(section.get('item_ids', []))}）", expanded=True):
        for item_id in section.get("item_ids", []):
            st.markdown(f"- `{item_id}`")

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
