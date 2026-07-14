from __future__ import annotations

import html
from typing import Any

from .markup import safe_scientific_html, strip_scientific_markup


def _e(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _plain_summary(value: Any, limit: int = 520) -> str | None:
    text = strip_scientific_markup(value)
    if not text:
        return None
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")


def _language_panels(
    card_id: str,
    zh_title: str | None,
    zh_summary: str | None,
    en_title: str | None,
    en_summary: str | None,
) -> str:
    zh_title_html = safe_scientific_html(zh_title or "中文标题暂不可用")
    if zh_summary:
        zh_summary_html = f"<p>{safe_scientific_html(zh_summary)}</p>"
    else:
        zh_summary_html = '<p class="muted">中文摘要暂不可用；系统不会根据标题编造内容。可点击“显示英文”查看原文。</p>'
    en_title_html = safe_scientific_html(en_title or "English title unavailable")
    if en_summary:
        en_summary_html = f"<p>{safe_scientific_html(en_summary)}</p>"
    else:
        en_summary_html = '<p class="muted">Original abstract or excerpt is unavailable.</p>'
    return f"""
      <div class="lang-panel lang-zh" data-lang="zh">
        <h3>{zh_title_html}</h3>
        {zh_summary_html}
      </div>
      <div class="lang-panel lang-en" data-lang="en" hidden>
        <h3>{en_title_html}</h3>
        {en_summary_html}
      </div>
      <button type="button" class="language-toggle" data-card-id="{_e(card_id)}" aria-expanded="false">显示英文</button>
    """


def _work_card(work: dict[str, Any]) -> str:
    card_id = f"work-{work.get('work_id', '')}"
    title = work.get("title", {})
    abstract = work.get("abstract", {})
    display = work.get("display_summary") or {}
    zh_title = title.get("translated_zh")
    en_title = title.get("original")
    zh_summary = display.get("zh") or abstract.get("translated_zh")
    en_summary = display.get("en") or _plain_summary(abstract.get("original"))
    bib = work.get("bibliography", {})
    ids = work.get("identifiers", {})
    links: list[str] = []
    if ids.get("doi"):
        links.append(f'<a href="https://doi.org/{_e(ids["doi"])}">DOI</a>')
    if ids.get("pmid"):
        links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{_e(ids["pmid"])}">PMID</a>')
    source_links = [record.get("url") for record in work.get("source_records", []) if record.get("url")]
    if not links and source_links:
        links.append(f'<a href="{_e(source_links[0])}">原始记录</a>')
    meta = " · ".join(
        value
        for value in [
            bib.get("journal"),
            bib.get("published_date"),
            ", ".join(work.get("authors", [])[:4]),
        ]
        if value
    )
    audit = work.get("translation_audit") or {}
    translation_note = (
        '<span class="translation-status unavailable">中文翻译未通过校验</span>'
        if not zh_title
        else '<span class="translation-status">中文默认</span>'
    )
    return f"""
    <article class="story paper bilingual-card" id="{_e(card_id)}">
      <div class="kicker">学术文献 · {_e(work.get('filter_result', {}).get('decision', 'archive'))} · {translation_note}</div>
      {_language_panels(card_id, zh_title, zh_summary, en_title, en_summary)}
      <div class="meta">{_e(meta)}</div>
      <div class="links">{' · '.join(links)}</div>
      <div class="audit-note">翻译：{_e(audit.get('provider') or '不可用')} · {_e(audit.get('validation_status') or 'unknown')}</div>
    </article>
    """


def _event_card(event: dict[str, Any]) -> str:
    card_id = f"event-{event.get('event_id', '')}"
    loc = event.get("location", {}).get("country") or "地点待核验"
    counts = event.get("case_counts", {})
    count_parts = []
    for label, key in [("确诊", "confirmed"), ("可能", "probable"), ("疑似", "suspected"), ("死亡", "deaths")]:
        if counts.get(key) is not None:
            count_parts.append(f"{label} {counts[key]}")
    count_line = "；".join(count_parts) if count_parts else "病例数字未明确报告"
    badge = "事件更新" if event.get("event_version", 1) > 1 else "公共卫生事件"
    primary = event.get("primary_source", {})
    link = (
        f'<a href="{_e(primary.get("url"))}">{_e(primary.get("name") or "原始来源")}</a>'
        if primary.get("url")
        else _e(primary.get("name") or "来源待核验")
    )
    display = event.get("display_summary") or {}
    zh_title = event.get("summary_zh")
    en_title = event.get("summary_original") or event.get("summary")
    zh_summary = display.get("zh")
    en_summary = display.get("en")
    audit = event.get("translation_audit") or {}
    return f"""
    <article class="story event bilingual-card" id="{_e(card_id)}">
      <div class="kicker">{_e(badge)} · {_e(event.get('official_status'))}</div>
      {_language_panels(card_id, zh_title, zh_summary, en_title, en_summary)}
      <div class="meta">{_e(loc)} · {_e(event.get('event_type'))} · {_e(count_line)}</div>
      <p class="event-note">该事件由 {len(event.get('source_articles', []))} 篇文章聚合；事件版本 v{_e(event.get('event_version'))}。</p>
      <div class="links">{link}</div>
      <div class="audit-note">翻译：{_e(audit.get('provider') or '不可用')} · {_e(audit.get('validation_status') or 'unknown')}</div>
    </article>
    """


def build_report_html(
    issue: dict[str, Any],
    works: list[dict[str, Any]],
    events: list[dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    work_map = {work["work_id"]: work for work in works}
    event_map = {event["event_id"]: event for event in events}
    sections_html: list[str] = []
    for section in issue.get("sections", []):
        cards: list[str] = []
        for item_id in section.get("item_ids", []):
            if item_id in event_map:
                cards.append(_event_card(event_map[item_id]))
            elif item_id in work_map:
                cards.append(_work_card(work_map[item_id]))
        if cards:
            sections_html.append(
                f'<section><h2>{_e(section.get("title"))}</h2><div class="columns">{"".join(cards)}</div></section>'
            )
    if not sections_html:
        sections_html.append(
            '<section><h2>今日简讯</h2><p>今日未发现符合入选条件的新记录。来源运行状态与失败信息仍保留在本期审计中。</p></section>'
        )

    stats = issue.get("statistics", {})
    stat_cells = "".join(
        f'<div><strong>{_e(value)}</strong><span>{_e(label)}</span></div>'
        for label, value in [
            ("入选文献", stats.get("scholarly_selected", 0)),
            ("公共卫生事件", stats.get("public_health_events", 0)),
            ("官方事件", stats.get("official_events", 0)),
            ("中文翻译", stats.get("translated_works", 0) + stats.get("translated_events", 0)),
        ]
    )
    health_rows = "".join(
        f'<tr><td>{_e(health.get("source_id"))}</td><td>{_e(health.get("status"))}</td><td>{_e(health.get("record_count", 0))}</td><td>{_e("; ".join(health.get("errors", [])[:2]))}</td></tr>'
        for health in issue.get("source_health", [])
    )
    notes = "".join(f"<li>{_e(note)}</li>" for note in issue.get("data_quality_notes", [])) or "<li>未记录额外数据质量问题。</li>"
    title = profile.get("editorial_preferences", {}).get("website_title", "病原每日情报")
    subtitle = profile.get("editorial_preferences", {}).get("website_subtitle", "Pathogen Daily Intelligence")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · {_e(issue.get('issue_date'))}</title>
<style>
:root{{--paper:#f5f0e6;--ink:#171412;--red:#7d1f1b;--line:#b8aa96;--muted:#6a6259;--button:#efe5d4}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Serif SC","Source Han Serif SC","Songti SC",STSong,SimSun,serif;line-height:1.72}}
a{{color:var(--red)}} sub,sup{{line-height:0;font-size:.72em}} .page{{max-width:1280px;margin:auto;padding:24px 34px 70px}} .mast{{text-align:center;border-top:5px double var(--ink);border-bottom:2px solid var(--ink);padding:18px 0 12px}}
.mast h1{{font-size:clamp(34px,6vw,68px);letter-spacing:.12em;margin:0}} .mast p{{margin:.2rem 0;color:var(--muted);letter-spacing:.12em}} .date{{font-size:14px;border-bottom:1px solid var(--line);padding:10px 0;text-align:center}}
.toolbar{{display:flex;justify-content:flex-end;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)}} button{{font:inherit}} .global-language,.language-toggle{{border:1px solid var(--line);background:var(--button);color:var(--ink);padding:5px 10px;cursor:pointer;border-radius:0}} .global-language:hover,.language-toggle:hover{{border-color:var(--red);color:var(--red)}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:2px solid var(--ink)}} .stats div{{text-align:center;padding:14px;border-right:1px solid var(--line)}} .stats div:last-child{{border-right:0}} .stats strong{{font-size:27px;display:block;color:var(--red)}} .stats span{{font-size:13px}}
section{{border-top:1px solid var(--ink);margin-top:26px;padding-top:7px}} section h2{{font-size:22px;letter-spacing:.12em;margin:0 0 12px}} .columns{{columns:3 290px;column-gap:30px;column-rule:1px solid var(--line)}} .story{{break-inside:avoid;border-bottom:1px solid var(--line);padding:0 0 18px;margin:0 0 18px}} .story h3{{font-size:20px;line-height:1.38;margin:.25rem 0}} .story p{{margin:.45rem 0}} .kicker{{font-size:12px;color:var(--red);font-weight:700;letter-spacing:.06em}} .meta,.muted,.audit-note{{color:var(--muted);font-size:13px}} .audit-note{{margin-top:6px;font-size:11px}} .links{{font-size:13px;margin-top:7px}} .language-toggle{{margin:5px 0 8px;font-size:12px}} .translation-status{{color:var(--red)}} .translation-status.unavailable{{color:var(--muted)}} .event-note{{font-size:13px}} table{{width:100%;border-collapse:collapse;font-size:13px}} th,td{{text-align:left;border-bottom:1px solid var(--line);padding:7px}} footer{{border-top:3px double var(--ink);margin-top:35px;padding-top:16px;font-size:12px;color:var(--muted)}}
[hidden]{{display:none!important}}
@media(max-width:800px){{.page{{padding:15px 18px 45px}}.stats{{grid-template-columns:repeat(2,1fr)}}.columns{{columns:1}}.toolbar{{justify-content:center}}}}
@media print{{body{{background:white}}.page{{max-width:none;padding:0}}a{{color:black;text-decoration:none}}.toolbar,.language-toggle,.audit-note{{display:none!important}}.lang-en{{display:none!important}}.lang-zh{{display:block!important}}}}
</style></head><body><main class="page">
<header class="mast"><h1>{_e(title)}</h1><p>{_e(subtitle)}</p></header>
<div class="date">{_e(issue.get('issue_date'))} · 覆盖窗口 {_e(issue.get('coverage_window',{}).get('start'))}—{_e(issue.get('coverage_window',{}).get('end'))} · {_e(issue.get('coverage_window',{}).get('timezone'))}</div>
<div class="toolbar"><button type="button" class="global-language" data-language="en">全部显示英文</button><button type="button" class="global-language" data-language="zh">全部显示中文</button></div>
<div class="stats">{stat_cells}</div>
{''.join(sections_html)}
<section><h2>来源运行状态</h2><table><thead><tr><th>来源</th><th>状态</th><th>记录</th><th>错误摘要</th></tr></thead><tbody>{health_rows}</tbody></table></section>
<section><h2>数据质量说明</h2><ul>{notes}</ul></section>
<footer>本简报由自动化系统汇总公开文献与公共卫生信息，仅用于信息跟踪和科研参考，不替代官方公共卫生通报、临床诊断或专业决策。重要事件请以原始官方来源为准。</footer>
</main>
<script>
(function(){{
  function setCardLanguage(card, language){{
    const zh = card.querySelector('.lang-zh');
    const en = card.querySelector('.lang-en');
    const button = card.querySelector('.language-toggle');
    if(!zh || !en || !button) return;
    const showEnglish = language === 'en';
    zh.hidden = showEnglish;
    en.hidden = !showEnglish;
    button.textContent = showEnglish ? '显示中文' : '显示英文';
    button.setAttribute('aria-expanded', String(showEnglish));
  }}
  document.querySelectorAll('.language-toggle').forEach(function(button){{
    button.addEventListener('click', function(){{
      const card = button.closest('.bilingual-card');
      if(!card) return;
      const en = card.querySelector('.lang-en');
      setCardLanguage(card, en && en.hidden ? 'en' : 'zh');
    }});
  }});
  document.querySelectorAll('.global-language').forEach(function(button){{
    button.addEventListener('click', function(){{
      const language = button.getAttribute('data-language') || 'zh';
      document.querySelectorAll('.bilingual-card').forEach(function(card){{setCardLanguage(card, language);}});
    }});
  }});
}})();
</script></body></html>"""


def build_email_html(
    issue: dict[str, Any],
    works: list[dict[str, Any]],
    events: list[dict[str, Any]],
    profile: dict[str, Any],
) -> str:
    title = profile.get("editorial_preferences", {}).get("website_title", "病原每日情报")
    selected_events = [event for event in events if event.get("display_decision") in {"headline", "brief"}][:5]
    selected_works = [work for work in works if work.get("filter_result", {}).get("decision") in {"headline", "brief"}][:8]
    rows: list[str] = []
    for event in selected_events:
        zh_title = event.get("summary_zh") or "中文标题暂不可用"
        zh_summary = (event.get("display_summary") or {}).get("zh")
        en_title = event.get("summary_original") or event.get("summary")
        rows.append(
            '<tr><td style="padding:14px 0;border-bottom:1px solid #c9bca9">'
            f'<strong>{safe_scientific_html(zh_title)}</strong>'
            + (f'<br><span>{safe_scientific_html(zh_summary)}</span>' if zh_summary else '<br><span style="color:#6a6259">中文摘要暂不可用</span>')
            + f'<br><span style="color:#6a6259;font-size:12px">English: {safe_scientific_html(en_title)}</span>'
            + f'<br><span style="color:#6a6259;font-size:13px">{_e(event.get("location",{}).get("country"))} · {_e(event.get("official_status"))}</span></td></tr>'
        )
    for work in selected_works:
        zh_title = work.get("title", {}).get("translated_zh") or "中文标题暂不可用"
        zh_summary = (work.get("display_summary") or {}).get("zh")
        en_title = work.get("title", {}).get("original")
        rows.append(
            '<tr><td style="padding:14px 0;border-bottom:1px solid #c9bca9">'
            f'<strong>{safe_scientific_html(zh_title)}</strong>'
            + (f'<br><span>{safe_scientific_html(zh_summary)}</span>' if zh_summary else '<br><span style="color:#6a6259">摘要暂不可用</span>')
            + f'<br><span style="color:#6a6259;font-size:12px">English: {safe_scientific_html(en_title)}</span>'
            + f'<br><span style="color:#6a6259;font-size:13px">{_e(work.get("bibliography",{}).get("journal"))} · {_e(work.get("bibliography",{}).get("published_date"))}</span></td></tr>'
        )
    if not rows:
        rows.append('<tr><td style="padding:14px 0">今日未发现符合入选条件的新记录。</td></tr>')
    return f"""<!doctype html><html><body style="margin:0;background:#f5f0e6;color:#171412;font-family:Georgia,'Songti SC',serif">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center"><table role="presentation" width="680" style="max-width:680px;width:100%;background:#f5f0e6;padding:24px" cellspacing="0" cellpadding="0">
<tr><td style="border-top:4px double #171412;border-bottom:2px solid #171412;text-align:center;padding:18px 8px"><h1 style="margin:0;font-size:36px">{_e(title)}</h1><div>{_e(issue.get('issue_date'))}</div></td></tr>
<tr><td style="padding:18px 0"><strong>30 秒摘要：</strong> 本期入选 {_e(issue.get('statistics',{}).get('scholarly_selected',0))} 篇文献、{_e(issue.get('statistics',{}).get('public_health_events',0))} 个公共卫生事件。邮件默认显示中文，英文原题以小号文字保留。</td></tr>
{''.join(rows)}
<tr><td style="padding-top:22px;color:#6a6259;font-size:12px">本简报为自动化科研与公共卫生信息跟踪结果。重要事件请以原始官方来源为准。</td></tr>
</table></td></tr></table></body></html>"""


def build_rss(issue: dict[str, Any], profile: dict[str, Any]) -> str:
    title = _e(profile.get("editorial_preferences", {}).get("website_title", "病原每日情报"))
    description = _e(
        f"{issue.get('issue_date')}：文献 {issue.get('statistics',{}).get('scholarly_selected',0)}，事件 {issue.get('statistics',{}).get('public_health_events',0)}"
    )
    item_link = f"archive/{issue.get('issue_date','').replace('-', '/')}/index.html"
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{title}</title><link>./</link><description>{title}</description><language>zh-CN</language>
<item><guid>{_e(issue.get('issue_id'))}</guid><title>{title} {_e(issue.get('issue_date'))}</title><link>{_e(item_link)}</link><description>{description}</description><pubDate>{_e(issue.get('generated_at'))}</pubDate></item>
</channel></rss>'''
