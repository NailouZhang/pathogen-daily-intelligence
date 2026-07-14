from __future__ import annotations

from typing import Any

from .utils import normalize_space, utc_now_iso


def _text_item(item:dict[str,Any],kind:str)->str:
    if kind=="work":
        full_text=" ".join(section.get("text") or "" for section in item.get("full_text",{}).get("sections",[]))
        return " ".join([item.get("title",{}).get("original") or "",item.get("abstract",{}).get("original") or "",full_text])
    return " ".join([item.get("title",{}).get("original") or "",item.get("content",{}).get("analysis_text") or item.get("content",{}).get("excerpt") or ""])


def relevance(item:dict[str,Any],profile:dict[str,Any],kind:str)->tuple[str,list[str]]:
    text=normalize_space(_text_item(item,kind)).casefold(); reasons=[]
    matched=[x for x in profile.get("lexicon",[]) if x.get("status")=="accepted_for_search" and (x.get("term") or "").casefold() in text]
    for amb in profile.get("ambiguous_terms",[]):
        term=(amb.get("term") or "").casefold()
        if term and term in text and not any((x or "").casefold() in text for x in amb.get("must_cooccur_with",[])):
            return "ambiguous",["AMBIGUOUS_TERM_WITHOUT_REQUIRED_CONTEXT"]
    if not matched:return "irrelevant",["NO_APPROVED_PATHOGEN_TERM"]
    if any(x.get("term_type") in {"pathogen_name","virus_entity_name","taxonomy_family","taxonomy_genus"} for x in matched):
        reasons.append("APPROVED_PATHOGEN_TERM");return "strong",reasons
    return "combined",["APPROVED_DISEASE_OR_SYNDROME_TERM"]


def classify_work(work:dict[str,Any],profile:dict[str,Any],is_new:bool=True)->dict[str,Any]:
    rel,reasons=relevance(work,profile,"work")
    score=0.0
    if rel=="strong":score+=0.45
    elif rel=="combined":score+=0.3
    if work.get("abstract",{}).get("original"):score+=0.15
    if work.get("quality",{}).get("source_count",0)>=2:score+=0.15
    if is_new:score+=0.15
    topics=work.get("entities",{}).get("topics") or []
    if any(x in topics for x in ["outbreak","clinical","genomics","intervention"]):score+=0.1
    decision="archive"
    missing_date=not work.get("bibliography",{}).get("published_date")
    if missing_date:reasons.append("MISSING_PUBLISHED_DATE")
    if rel in {"irrelevant","ambiguous"}:decision="review" if rel=="ambiguous" else "archive"
    elif missing_date:decision="review"
    elif score>=0.72:decision="headline"
    elif score>=0.42:decision="brief"
    work["filter_result"]={"decision":decision,"reason_codes":reasons,"score":round(score,3),"rule_version":"1.0","processed_at":utc_now_iso()}
    return work


def classify_article(article:dict[str,Any],profile:dict[str,Any],event:dict[str,Any]|None=None)->dict[str,Any]:
    rel,reasons=relevance(article,profile,"article")
    tier=article.get("source",{}).get("reliability_tier","unknown")
    score={"A":0.35,"B":0.25,"C":0.12,"D":0.05,"unknown":0.0}.get(tier,0.0)
    if rel=="strong":score+=0.3
    elif rel=="combined":score+=0.2
    if event and event.get("material_change"):score+=0.2;reasons.append("MATERIAL_EVENT_CHANGE")
    if event and event.get("official_status")=="official":score+=0.1;reasons.append("OFFICIAL_SOURCE_PRESENT")
    if article.get("content",{}).get("analysis_text"):score+=0.1;reasons.append("FULLER_CONTENT_EXTRACTED")
    elif article.get("content",{}).get("excerpt"):score+=0.05
    decision="archive"
    missing_date=not article.get("published_at")
    if missing_date:reasons.append("MISSING_PUBLISHED_DATE")
    if rel=="ambiguous":decision="review"
    elif rel=="irrelevant":decision="archive"
    elif missing_date:decision="review"
    elif score>=0.75:decision="headline"
    elif score>=0.42:decision="brief"
    article["classification"]={"decision":decision,"reason_codes":reasons,"score":round(score,3),"rule_version":"1.0","processed_at":utc_now_iso(),"relevance":rel}
    return article
