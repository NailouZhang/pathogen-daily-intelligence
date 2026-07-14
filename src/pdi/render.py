from __future__ import annotations

import html
from typing import Any


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _work_card(work: dict[str, Any]) -> str:
    title = work.get("title", {}).get("translated_zh") or work.get("title", {}).get("original") or "Untitled"
    original = work.get("title", {}).get("original") or ""
    bib = work.get("bibliography", {})
    ids = work.get("identifiers", {})
    abstract = work.get("abstract", {}).get("original")
    ai = work.get("ai_analysis") or {}
    takeaway = ai.get("one_sentence_takeaway") or (abstract[:420] + "…" if abstract and len(abstract) > 420 else abstract)
    links: list[str] = []
    if ids.get("doi"):
        links.append(f'<a href="https://doi.org/{_e(ids["doi"])}">DOI</a>')
    if ids.get("pmid"):
        links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{_e(ids["pmid"])}">PMID</a>')
    source_links = [x.get("url") for x in work.get("source_records", []) if x.get("url")]
    if not links and source_links:
        links.append(f'<a href="{_e(source_links[0])}">原始记录</a>')
    meta = " · ".join(
        x
        for x in [
            bib.get("journal"),
            bib.get("published_date"),
            ", ".join(work.get("authors", [])[:4]),
        ]
        if x
    )
    original_line = f'<div class="original">{_e(original)}</div>' if original and original != title else ""
    summary = f'<p>{_e(takeaway)}</p>' if takeaway else '<p class="muted">摘要暂不可用；未根据标题扩写研究结论。</p>'
    return f"""
    <article class="story paper">
      <div class="kicker">学术文献 · {_e(work.get('filter_result', {}).get('decision', 'archive'))}</div>
      <h3>{_e(title)}</h3>
      {original_line}
      <div class="meta">{_e(meta)}</div>
      {summary}
      <div class="links">{' · '.join(links)}</div>
    </article>
    """


def _event_card(event: dict[str, Any]) -> str:
    loc = event.get("location", {}).get("country") or "地点待核验"
    counts = event.get("case_counts", {})
    count_parts = []
    for label, key in [("确诊", "confirmed"), ("可能", "probable"), ("疑似", "suspected"), ("死亡", "deaths")]:
        if counts.get(key) is not None:
            count_parts.append(f"{label} {counts[key]}")
    count_line = "；".join(count_parts) if count_parts else "病例数字未明确报告"
    badge = "事件更新" if event.get("event_version", 1) > 1 else "公共卫生事件"
    primary = event.get("primary_source", {})
    link = f'<a href="{_e(primary.get("url"))}">{_e(primary.get("name") or "原始来源")}</a>' if primary.get("url") else _e(primary.get("name") or "来源待核验")
    return f"""
    <article class="story event">
      <div class="kicker">{_e(badge)} · {_e(event.get('official_status'))}</div>
      <h3>{_e(event.get('summary'))}</h3>
      <div class="meta">{_e(loc)} · {_e(event.get('event_type'))} · {_e(count_line)}</div>
      <p>该事件由 {len(event.get('source_articles', []))} 篇文章聚合；事件版本 v{_e(event.get('event_version'))}。</p>
      <div class="links">{link}</div>
    </article>
    """


def build_report_html(issue: dict[str, Any], works: list[dict[str, Any]], events: list[dict[str, Any]], profile: dict[str, Any]) -> str:
    work_map = {x["work_id"]: x for x in works}
    event_map = {x["event_id"]: x for x in events}
    sections_html: list[str] = []
    for section in issue.get("sections", []):
        cards = []
        for item_id in section.get("item_ids", []):
            if item_id in event_map:
                cards.append(_event_card(event_map[item_id]))
            elif item_id in work_map:
                cards.append(_work_card(work_map[item_id]))
        if cards:
            sections_html.append(f'<section><h2>{_e(section.get("title"))}</h2><div class="columns">{"".join(cards)}</div></section>')
    if not sections_html:
        sections_html.append('<section><h2>今日简讯</h2><p>今日未发现符合入选条件的新记录。来源运行状态与失败信息仍保留在本期审计中。</p></section>')

    stats = issue.get("statistics", {})
    stat_cells = "".join(
        f'<div><strong>{_e(value)}</strong><span>{_e(label)}</span></div>'
        for label, value in [
            ("入选文献", stats.get("scholarly_selected", 0)),
            ("公共卫生事件", stats.get("public_health_events", 0)),
            ("官方事件", stats.get("official_events", 0)),
            ("涉及国家", stats.get("countries", 0)),
        ]
    )
    health_rows = "".join(
        f'<tr><td>{_e(h.get("source_id"))}</td><td>{_e(h.get("status"))}</td><td>{_e(h.get("record_count", 0))}</td><td>{_e("; ".join(h.get("errors", [])[:2]))}</td></tr>'
        for h in issue.get("source_health", [])
    )
    notes = "".join(f'<li>{_e(x)}</li>' for x in issue.get("data_quality_notes", [])) or '<li>未记录额外数据质量问题。</li>'
    title = profile.get("editorial_preferences", {}).get("website_title", "病原每日情报")
    subtitle = profile.get("editorial_preferences", {}).get("website_subtitle", "Pathogen Daily Intelligence")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · {_e(issue.get('issue_date'))}</title>
<style>
:root{{--paper:#f5f0e6;--ink:#171412;--red:#7d1f1b;--line:#b8aa96;--muted:#6a6259}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Serif SC","Source Han Serif SC","Songti SC",STSong,SimSun,serif;line-height:1.72}}
a{{color:var(--red)}} .page{{max-width:1280px;margin:auto;padding:24px 34px 70px}} .mast{{text-align:center;border-top:5px double var(--ink);border-bottom:2px solid var(--ink);padding:18px 0 12px}}
.mast h1{{font-size:clamp(34px,6vw,68px);letter-spacing:.12em;margin:0}} .mast p{{margin:.2rem 0;color:var(--muted);letter-spacing:.12em}} .date{{font-size:14px;border-bottom:1px solid var(--line);padding:10px 0;text-align:center}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:2px solid var(--ink)}} .stats div{{text-align:center;padding:14px;border-right:1px solid var(--line)}} .stats div:last-child{{border-right:0}} .stats strong{{font-size:27px;display:block;color:var(--red)}} .stats span{{font-size:13px}}
section{{border-top:1px solid var(--ink);margin-top:26px;padding-top:7px}} section h2{{font-size:22px;letter-spacing:.12em;margin:0 0 12px}} .columns{{columns:3 290px;column-gap:30px;column-rule:1px solid var(--line)}} .story{{break-inside:avoid;border-bottom:1px solid var(--line);padding:0 0 18px;margin:0 0 18px}} .story h3{{font-size:20px;line-height:1.35;margin:.25rem 0}} .kicker{{font-size:12px;color:var(--red);font-weight:700;letter-spacing:.08em}} .meta,.original,.muted{{color:var(--muted);font-size:13px}} .links{{font-size:13px}} table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{text-align:left;border-bottom:1px solid var(--line);padding:7px}} footer{{border-top:3px double var(--ink);margin-top:35px;padding-top:16px;font-size:12px;color:var(--muted)}}
@media(max-width:800px){{.page{{padding:15px 18px 45px}}.stats{{grid-template-columns:repeat(2,1fr)}}.columns{{columns:1}}}}
@media print{{body{{background:white}}.page{{max-width:none;padding:0}}a{{color:black;text-decoration:none}}}}
</style></head><body><main class="page">
<header class="mast"><h1>{_e(title)}</h1><p>{_e(subtitle)}</p></header>
<div class="date">{_e(issue.get('issue_date'))} · 覆盖窗口 {_e(issue.get('coverage_window',{}).get('start'))}—{_e(issue.get('coverage_window',{}).get('end'))} · {_e(issue.get('coverage_window',{}).get('timezone'))}</div>
<div class="stats">{stat_cells}</div>
{''.join(sections_html)}
<section><h2>来源运行状态</h2><table><thead><tr><th>来源</th><th>状态</th><th>记录</th><th>错误摘要</th></tr></thead><tbody>{health_rows}</tbody></table></section>
<section><h2>数据质量说明</h2><ul>{notes}</ul></section>
<footer>本简报由自动化系统汇总公开文献与公共卫生信息，仅用于信息跟踪和科研参考，不替代官方公共卫生通报、临床诊断或专业决策。重要事件请以原始官方来源为准。</footer>
</main></body></html>"""


def build_email_html(issue: dict[str, Any], works: list[dict[str, Any]], events: list[dict[str, Any]], profile: dict[str, Any]) -> str:
    title = profile.get("editorial_preferences", {}).get("website_title", "病原每日情报")
    selected_events = [e for e in events if e.get("display_decision") in {"headline", "brief"}][:5]
    selected_works = [w for w in works if w.get("filter_result", {}).get("decision") in {"headline", "brief"}][:8]
    rows = []
    for event in selected_events:
        rows.append(f'<tr><td style="padding:14px 0;border-bottom:1px solid #c9bca9"><strong>{_e(event.get("summary"))}</strong><br><span style="color:#6a6259;font-size:13px">{_e(event.get("location",{}).get("country"))} · {_e(event.get("official_status"))}</span></td></tr>')
    for work in selected_works:
        rows.append(f'<tr><td style="padding:14px 0;border-bottom:1px solid #c9bca9"><strong>{_e(work.get("title",{}).get("translated_zh") or work.get("title",{}).get("original"))}</strong><br><span style="color:#6a6259;font-size:13px">{_e(work.get("bibliography",{}).get("journal"))} · {_e(work.get("bibliography",{}).get("published_date"))}</span></td></tr>')
    if not rows:
        rows.append('<tr><td style="padding:14px 0">今日未发现符合入选条件的新记录。</td></tr>')
    return f"""<!doctype html><html><body style="margin:0;background:#f5f0e6;color:#171412;font-family:Georgia,'Songti SC',serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center"><table role="presentation" width="680" style="max-width:680px;width:100%;background:#f5f0e6;padding:24px" cellspacing="0" cellpadding="0">
<tr><td style="border-top:4px double #171412;border-bottom:2px solid #171412;text-align:center;padding:18px 8px"><h1 style="margin:0;font-size:36px">{_e(title)}</h1><div>{_e(issue.get('issue_date'))}</div></td></tr>
<tr><td style="padding:18px 0"><strong>30 秒摘要：</strong> 本期入选 {_e(issue.get('statistics',{}).get('scholarly_selected',0))} 篇文献、{_e(issue.get('statistics',{}).get('public_health_events',0))} 个公共卫生事件。</td></tr>
{''.join(rows)}
<tr><td style="padding-top:22px;color:#6a6259;font-size:12px">本简报为自动化科研与公共卫生信息跟踪结果。重要事件请以原始官方来源为准。</td></tr>
</table></td></tr></table></body></html>"""


def build_rss(issue: dict[str, Any], profile: dict[str, Any]) -> str:
    title = _e(profile.get("editorial_preferences", {}).get("website_title", "病原每日情报"))
    description = _e(f"{issue.get('issue_date')}：文献 {issue.get('statistics',{}).get('scholarly_selected',0)}，事件 {issue.get('statistics',{}).get('public_health_events',0)}")
    item_link = f"archive/{issue.get('issue_date','').replace('-', '/')}/index.html"
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{title}</title><link>./</link><description>{title}</description><language>zh-CN</language>
<item><guid>{_e(issue.get('issue_id'))}</guid><title>{title} {_e(issue.get('issue_date'))}</title><link>{_e(item_link)}</link><description>{description}</description><pubDate>{_e(issue.get('generated_at'))}</pubDate></item>
</channel></rss>'''
