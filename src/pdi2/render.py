from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import html_escape, truncate


CSS = r"""
:root{--paper:#f5f0e6;--ink:#171412;--red:#7d1f1b;--line:#b8aa96;--muted:#6a6259;--button:#efe5d4;--ok:#476b46;--warn:#9a6418}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font-family:"Noto Serif SC","Source Han Serif SC","Songti SC",STSong,SimSun,serif;line-height:1.72}
a{color:var(--red)}sub,sup{line-height:0;font-size:.72em}.page{max-width:1280px;margin:auto;padding:24px 34px 70px}.mast{text-align:center;border-top:5px double var(--ink);border-bottom:2px solid var(--ink);padding:18px 0 12px}.mast h1{font-size:clamp(34px,6vw,68px);letter-spacing:.12em;margin:0}.mast p{margin:.2rem 0;color:var(--muted);letter-spacing:.12em}.date{text-align:center;padding:10px 0;border-bottom:1px solid var(--line);font-size:14px}.toolbar{display:flex;justify-content:flex-end;gap:8px;padding:10px 0;border-bottom:1px solid var(--line)}button{font:inherit}.global-language{border:1px solid var(--line);background:var(--button);padding:5px 10px;cursor:pointer}.stats{display:grid;grid-template-columns:repeat(4,1fr);border-bottom:2px solid var(--ink)}.stats div{text-align:center;padding:14px;border-right:1px solid var(--line)}.stats div:last-child{border-right:0}.stats strong{font-size:27px;display:block;color:var(--red)}.stats span{font-size:13px}section{border-top:1px solid var(--ink);margin-top:26px;padding-top:7px}section h2{font-size:22px;letter-spacing:.12em;margin:0 0 12px}.columns{columns:3 290px;column-gap:30px;column-rule:1px solid var(--line)}.story{position:relative;break-inside:avoid;border-bottom:1px solid var(--line);padding:0 30px 18px 0;margin:0 0 18px}.story h3{font-size:20px;line-height:1.38;margin:.25rem 0}.story p{margin:.45rem 0}.kicker{font-size:12px;color:var(--red);font-weight:700;letter-spacing:.05em}.meta,.muted,.audit-note{color:var(--muted);font-size:13px}.audit-note{font-size:11px;margin-top:6px}.language-toggle{position:absolute;top:0;right:0;z-index:2;display:inline-flex;align-items:center;justify-content:center;min-width:24px;height:20px;padding:0 3px;border:0;border-bottom:1px solid var(--line);background:transparent;color:var(--muted);font:600 10px/1 system-ui,sans-serif;cursor:pointer;text-transform:lowercase}.language-toggle:hover{color:var(--red);border-color:var(--red)}.summary{font-size:14px}.analysis-details{border-top:1px dotted var(--line);border-bottom:1px dotted var(--line);margin:10px 0;padding:7px 0;font-size:13px}.analysis-details summary{cursor:pointer;color:var(--red);font-weight:700}.analysis-grid{display:grid;grid-template-columns:76px 1fr;gap:4px 8px;margin-top:8px}.analysis-grid dt{font-weight:700;color:var(--red)}.analysis-grid dd{margin:0}.status{font-size:12px;padding:5px 7px;border-left:3px solid var(--line);background:rgba(255,255,255,.22)}.status.ok{border-color:var(--ok)}.status.warn{border-color:var(--warn)}.links{font-size:13px;margin-top:7px}footer{border-top:3px double var(--ink);margin-top:35px;padding-top:16px;font-size:12px;color:var(--muted)}[hidden]{display:none!important}@media(max-width:800px){.page{padding:15px 18px 45px}.stats{grid-template-columns:repeat(2,1fr)}.columns{columns:1}.toolbar{justify-content:center}}@media print{body{background:white}.page{max-width:none;padding:0}.toolbar,.language-toggle,.audit-note{display:none!important}.lang-en{display:none!important}.lang-zh{display:block!important}}
"""

JS = r"""
document.querySelectorAll('.language-toggle').forEach(function(button){button.addEventListener('click',function(){var card=button.closest('.bilingual-card');var zh=card.querySelector('.lang-zh');var en=card.querySelector('.lang-en');var toEnglish=en.hidden;zh.hidden=toEnglish;en.hidden=!toEnglish;button.textContent=toEnglish?'zh':'en';button.setAttribute('aria-expanded',String(toEnglish));button.title=toEnglish?'显示中文':'显示英文';});});
document.querySelectorAll('.global-language').forEach(function(button){button.addEventListener('click',function(){var language=button.dataset.language;document.querySelectorAll('.bilingual-card').forEach(function(card){var zh=card.querySelector('.lang-zh');var en=card.querySelector('.lang-en');var toggle=card.querySelector('.language-toggle');var showEnglish=language==='en';zh.hidden=showEnglish;en.hidden=!showEnglish;toggle.textContent=showEnglish?'zh':'en';toggle.title=showEnglish?'显示中文':'显示英文';});});});
"""


def _attempt_label(audit: dict[str, Any]) -> str:
    status = audit.get("status") or "unknown"
    provider = audit.get("provider") or "none"
    return f"{provider} · {status}"


def _paper_fields(kind: str) -> list[tuple[str, str]]:
    if kind == "review":
        return [("背景", "background"), ("主要方向", "main_directions"), ("研究现状", "current_state"), ("不足", "gaps"), ("后续研究", "future_research")]
    return [("背景", "background"), ("方法", "methods"), ("结果", "results"), ("贡献", "contribution"), ("局限", "limitations")]


def _paper_card(work: dict[str, Any]) -> str:
    kind = work.get("paper_type") or "research"
    title_zh = work.get("title_zh") or "中文标题翻译暂不可用"
    summary_zh = work.get("summary_zh") or "当前未形成中文总结；可点击右上角 en 查看英文原文。"
    english_analysis = (work.get("analysis") or {}).get("analysis") or {}
    english_summary = (work.get("analysis") or {}).get("summary_en") or " ".join(
        f"{label}: {english_analysis.get(field, 'not reported')}" for label, field in _paper_fields(kind)
    )
    zh_analysis = work.get("analysis_zh") or {}
    details = "".join(
        f"<dt>{html_escape(label)}</dt><dd>{html_escape(zh_analysis.get(field) or '未报告')}</dd>"
        for label, field in _paper_fields(kind)
    )
    meta_bits = [
        work.get("journal"),
        f"当前可报道日期 {work.get('availability_date') or '未知'}",
        f"作者：{', '.join((work.get('authors') or [])[:6])}" if work.get("authors") else None,
    ]
    bibliographic = "；".join(str(x) for x in [work.get("year"), work.get("volume"), work.get("issue"), work.get("pages")] if x)
    if bibliographic:
        meta_bits.append(bibliographic)
    links = []
    if work.get("doi"):
        links.append(f'<a href="https://doi.org/{html_escape(work["doi"])}">DOI</a>')
    pmid = (work.get("source_ids") or {}).get("pmid")
    if pmid:
        links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/{html_escape(pmid)}/">PMID</a>')
    if work.get("full_text_url"):
        links.append(f'<a href="{html_escape(work["full_text_url"])}">开放正文证据</a>')
    title_audit = (work.get("translation_audit") or {}).get("title") or {}
    return f"""
<article class="story paper bilingual-card" id="{html_escape(work.get('paper_id'))}">
  <div class="kicker">学术文献 · {'综述' if kind == 'review' else '研究'} · {html_escape(work.get('evidence_level') or 'E0')}</div>
  <div class="lang-panel lang-zh" data-lang="zh"><h3>{html_escape(title_zh)}</h3><p class="summary">{html_escape(summary_zh)}</p></div>
  <div class="lang-panel lang-en" data-lang="en" hidden><h3>{html_escape(work.get('title'))}</h3><p class="summary">{html_escape(english_summary)}</p></div>
  <button type="button" class="language-toggle" aria-expanded="false" title="显示英文">en</button>
  <div class="meta">{html_escape(' · '.join(str(x) for x in meta_bits if x))}</div>
  <p class="status {'ok' if work.get('evidence_level') in ('E1','E2') else 'warn'}">证据：{html_escape(work.get('evidence_level') or 'E0')}；内容解析：{html_escape(work.get('full_text_method') or ('abstract_api' if work.get('abstract') else 'metadata_only'))}。</p>
  <details class="analysis-details"><summary>查看五要素解读</summary><dl class="analysis-grid">{details}</dl></details>
  <div class="links">{' · '.join(links)}</div>
  <div class="audit-note">翻译：{html_escape(_attempt_label(title_audit))}；解读：{html_escape((work.get('analysis') or {}).get('status') or 'unknown')}</div>
</article>"""


def _news_card(article: dict[str, Any]) -> str:
    title_zh = article.get("title_zh") or "中文标题翻译暂不可用"
    summary_zh = article.get("summary_zh") or "未抓获可分析正文，当前仅保留标题与来源元数据。"
    english_analysis = (article.get("analysis") or {}).get("analysis") or {}
    fields = [("时间", "time"), ("地点", "location"), ("事件", "event"), ("影响", "impact"), ("状态", "status")]
    english_summary = (article.get("analysis") or {}).get("summary_en") or " ".join(
        f"{label}: {english_analysis.get(field, 'not reported')}" for label, field in fields
    )
    zh_analysis = article.get("analysis_zh") or {}
    details = "".join(
        f"<dt>{html_escape(label)}</dt><dd>{html_escape(zh_analysis.get(field) or '未报告')}</dd>" for label, field in fields
    )
    title_audit = (article.get("translation_audit") or {}).get("title") or {}
    status_class = "ok" if article.get("content_status") in ("full", "partial") else "warn"
    return f"""
<article class="story news bilingual-card" id="{html_escape(article.get('news_id'))}">
  <div class="kicker">新闻与公共卫生 · {html_escape(article.get('publisher') or article.get('source'))}</div>
  <div class="lang-panel lang-zh" data-lang="zh"><h3>{html_escape(title_zh)}</h3><p class="summary">{html_escape(summary_zh)}</p></div>
  <div class="lang-panel lang-en" data-lang="en" hidden><h3>{html_escape(article.get('title'))}</h3><p class="summary">{html_escape(english_summary)}</p></div>
  <button type="button" class="language-toggle" aria-expanded="false" title="显示英文">en</button>
  <div class="meta">{html_escape(article.get('published_date') or '日期未知')} · {html_escape(article.get('source'))}</div>
  <p class="status {status_class}">{'已抓获正文并完成证据化解读。' if article.get('content_status') in ('full','partial') else '未抓获可分析正文；不根据标题扩写新闻内容。'}</p>
  <details class="analysis-details"><summary>查看新闻五要素</summary><dl class="analysis-grid">{details}</dl></details>
  <div class="links"><a href="{html_escape(article.get('resolved_url') or article.get('url'))}">原始报道</a></div>
  <div class="audit-note">翻译：{html_escape(_attempt_label(title_audit))}；解读：{html_escape((article.get('analysis') or {}).get('status') or 'unknown')}</div>
</article>"""


def render_site(issue: dict[str, Any], output_dir: Path) -> None:
    site_dir = output_dir / "site"
    site_dir.mkdir(parents=True, exist_ok=True)
    papers = issue.get("papers") or []
    research = [p for p in papers if p.get("paper_type") == "research"]
    reviews = [p for p in papers if p.get("paper_type") == "review"]
    news = issue.get("news") or []
    sections = []
    if research:
        sections.append(f'<section><h2>研究型文献</h2><div class="columns">{"".join(_paper_card(p) for p in research)}</div></section>')
    if reviews:
        sections.append(f'<section><h2>综述与观点</h2><div class="columns">{"".join(_paper_card(p) for p in reviews)}</div></section>')
    if news:
        sections.append(f'<section><h2>新闻与公共卫生</h2><div class="columns">{"".join(_news_card(n) for n in news)}</div></section>')
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html_escape(issue['title_zh'])} · {html_escape(issue['issue_date'])}</title><style>{CSS}</style></head><body><main class="page">
<header class="mast"><h1>{html_escape(issue['title_zh'])}</h1><p>{html_escape(issue['title_en'])}</p></header>
<div class="date">{html_escape(issue['issue_date'])} · 覆盖窗口 {html_escape(issue['window_start'])}—{html_escape(issue['window_end'])}</div>
<div class="toolbar"><button class="global-language" data-language="en">全部显示英文</button><button class="global-language" data-language="zh">全部显示中文</button></div>
<div class="stats"><div><strong>{len(research)}</strong><span>研究文献</span></div><div><strong>{len(reviews)}</strong><span>综述文献</span></div><div><strong>{len(news)}</strong><span>新闻报道</span></div><div><strong>{issue.get('metrics',{}).get('translated',0)}</strong><span>中文翻译</span></div></div>
{''.join(sections)}
<footer>本页面由公开文献数据库、开放网页和结构化模型分析生成。无摘要或正文时仅保留书目信息，不根据标题编造结论。全文获取不绕过付费墙或访问控制。</footer>
</main><script>{JS}</script></body></html>"""
    (site_dir / "index.html").write_text(html, encoding="utf-8")
    feed_items = []
    for item in (papers[:10] + news[:10]):
        title = item.get("title_zh") or item.get("title")
        link = f"https://doi.org/{item.get('doi')}" if item.get("doi") else item.get("resolved_url") or item.get("url") or ""
        description = item.get("summary_zh") or ""
        feed_items.append(f"<item><title>{html_escape(title)}</title><link>{html_escape(link)}</link><description>{html_escape(description)}</description></item>")
    feed = f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{html_escape(issue["title_zh"])}</title><link>./</link><description>{html_escape(issue["title_en"])}</description>{"".join(feed_items)}</channel></rss>'
    (site_dir / "feed.xml").write_text(feed, encoding="utf-8")
