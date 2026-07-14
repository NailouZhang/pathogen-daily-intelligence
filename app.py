from __future__ import annotations

import json

import streamlit as st

from src.pdi.dashboard import (
    clear_dashboard_cache,
    entity_jsonl_result,
    github_repository_status,
    latest_issue_result,
    setup_page,
    source_label,
    static_report_result,
)

setup_page("汉坦病毒每日情报", "📰")

if st.sidebar.button("刷新生产数据", use_container_width=True):
    clear_dashboard_cache()
    st.rerun()

issue_result = latest_issue_result()
issue = issue_result.payload if isinstance(issue_result.payload, dict) else {}
repo_status = github_repository_status()

st.title("汉坦病毒每日情报")
st.caption("Hantavirus Daily Intelligence · GitHub Actions + Streamlit · English/中文")

with st.sidebar:
    st.subheader("数据连接")
    st.write(f"**当前来源：** {source_label(issue_result.source)}")
    repo_name = repo_status.get("repository") or "未配置"
    branch = repo_status.get("branch") or "未配置"
    st.write(f"**GitHub：** `{repo_name}`")
    st.write(f"**数据分支：** `{branch}`")
    if repo_status.get("commit_sha"):
        st.write(f"**数据提交：** `{repo_status['commit_sha'][:10]}`")
    if repo_status.get("workflow_conclusion"):
        st.write(f"**最近日报：** `{repo_status.get('workflow_status')}/{repo_status.get('workflow_conclusion')}`")
    if repo_status.get("workflow_url"):
        st.link_button("打开最近 Workflow", repo_status["workflow_url"], use_container_width=True)
    if repo_status.get("commit_url"):
        st.link_button("查看数据分支提交", repo_status["commit_url"], use_container_width=True)
    st.caption(issue_result.message)

if not issue:
    st.error("尚未读取到日报。请检查 Streamlit Secrets、intelligence-data 分支及 daily-intelligence Workflow。")
    st.stop()

if issue_result.source == "demo":
    st.warning("当前显示的是工程包内 Demo，不是 GitHub Actions 生成的生产日报。请配置 PDI_GITHUB_REPO 和 GITHUB_DATA_TOKEN。")
elif issue_result.source == "cache":
    st.warning("GitHub 生产数据暂时读取失败，当前展示上一次成功读取后的 Streamlit 本地缓存。")
elif issue_result.source == "github":
    st.success("已连接 GitHub intelligence-data 生产数据。")

window = issue.get("coverage_window", {})
st.markdown(
    f'<div class="pdi-note">本期日期：<b>{issue.get("issue_date")}</b>　覆盖窗口：{window.get("start")}—{window.get("end")}（{window.get("timezone")}）　生成时间：{issue.get("generated_at", "-")}</div>',
    unsafe_allow_html=True,
)

stats = issue.get("statistics", {})
cols = st.columns(4)
cols[0].metric("入选文献", stats.get("scholarly_selected", 0))
cols[1].metric("公共卫生事件", stats.get("public_health_events", 0))
cols[2].metric("官方事件", stats.get("official_events", 0))
cols[3].metric("涉及国家", stats.get("countries", 0))

works_result = entity_jsonl_result("scholarly_works.jsonl")
events_result = entity_jsonl_result("public_health_events.jsonl")
works = {item.get("work_id"): item for item in works_result.payload if isinstance(item, dict)}
events = {item.get("event_id"): item for item in events_result.payload if isinstance(item, dict)}


def render_item(item_id: str) -> None:
    if item_id in events:
        event = events[item_id]
        location = event.get("location", {})
        counts = event.get("case_counts", {})
        source = event.get("primary_source", {})
        st.markdown(
            f'<div class="pdi-card"><div class="pdi-kicker">公共卫生事件 · {event.get("event_type", "unknown")}</div>'
            f'<h3>{event.get("summary") or item_id}</h3>'
            f'<div class="small-muted">地点：{location.get("country") or "未知"}　确诊：{counts.get("confirmed") if counts.get("confirmed") is not None else "未知"}　事件版本：{event.get("event_version", 1)}</div></div>',
            unsafe_allow_html=True,
        )
        if source.get("url"):
            st.link_button("查看原始来源", source["url"])
        return

    if item_id in works:
        work = works[item_id]
        title = work.get("title", {}).get("translated_zh") or work.get("title", {}).get("original") or item_id
        bibliography = work.get("bibliography", {})
        identifiers = work.get("identifiers", {})
        st.markdown(
            f'<div class="pdi-card"><div class="pdi-kicker">学术文献</div><h3>{title}</h3>'
            f'<div class="small-muted">{bibliography.get("journal") or "期刊未知"} · {bibliography.get("published_date") or "日期未知"} · DOI：{identifiers.get("doi") or "无"} · PMID：{identifiers.get("pmid") or "无"}</div></div>',
            unsafe_allow_html=True,
        )
        takeaway = (work.get("ai_analysis") or {}).get("one_sentence_takeaway")
        if takeaway:
            st.write(takeaway)
        return

    st.markdown(f"- `{item_id}`（实体明细暂未读取到）")


lead = issue.get("lead_story")
if lead:
    st.subheader("今日头条")
    render_item(str(lead.get("item_id")))
else:
    st.info("今日未发现符合头条条件的新记录。")

st.subheader("本期栏目")
for section in issue.get("sections", []):
    item_ids = section.get("item_ids", [])
    with st.expander(f"{section.get('title')}（{len(item_ids)}）", expanded=True):
        for item_id in item_ids:
            render_item(str(item_id))

observations = issue.get("daily_observations", [])
if observations:
    st.subheader("每日观察")
    for observation in observations:
        st.write(observation.get("text"))
        st.caption("支持对象：" + ", ".join(observation.get("supporting_item_ids", [])))

st.subheader("来源健康概览")
health = issue.get("source_health", [])
if health:
    status_counts: dict[str, int] = {}
    for item in health:
        status = str(item.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    st.write("　".join(f"**{key}：{value}**" for key, value in sorted(status_counts.items())))
else:
    st.info("本期没有来源健康记录。")

st.subheader("数据质量")
notes = issue.get("data_quality_notes", [])
if notes:
    for note in notes:
        st.markdown(f"- {note}")
else:
    st.write("本期没有额外数据质量提示。")

static_result = static_report_result()
left, right = st.columns(2)
left.download_button(
    "下载本期 DailyIssue JSON",
    data=json.dumps(issue, ensure_ascii=False, indent=2),
    file_name=f"pathogen_daily_issue_{issue.get('issue_date', 'latest')}.json",
    mime="application/json",
    use_container_width=True,
)
right.download_button(
    "下载静态报纸 HTML",
    data=str(static_result.payload or ""),
    file_name=f"pathogen_daily_issue_{issue.get('issue_date', 'latest')}.html",
    mime="text/html",
    use_container_width=True,
    disabled=not bool(static_result.payload),
)

st.caption("本简报仅用于信息跟踪和科研参考，不替代官方公共卫生通报、临床诊断或专业决策。")
