from __future__ import annotations

import re
from typing import Any

from .utils import normalize_space

COUNTRIES={
"china":"China","中国":"China","united states":"United States","u.s.":"United States","usa":"United States","美国":"United States","chile":"Chile","智利":"Chile","argentina":"Argentina","阿根廷":"Argentina","germany":"Germany","德国":"Germany","russia":"Russia","俄罗斯":"Russia","south korea":"South Korea","韩国":"South Korea","brazil":"Brazil","巴西":"Brazil","panama":"Panama","巴拿马":"Panama"
}
HOST_TERMS={"rodent":"rodent","mouse":"mouse","mice":"mouse","rat":"rat","shrew":"shrew","bat":"bat","啮齿动物":"rodent","鼠":"rodent","蝙蝠":"bat","鼩鼱":"shrew"}


def extract_entities(text: str, lexicon: list[dict[str,Any]]) -> dict[str,Any]:
    low=normalize_space(text).casefold()
    pathogens=[]
    for term in lexicon:
        value=(term.get("term") or "").strip()
        if value and value.casefold() in low and value not in pathogens:
            pathogens.append(value)
    countries=[]
    for token,name in COUNTRIES.items():
        if token in low and name not in countries: countries.append(name)
    hosts=[]
    for token,name in HOST_TERMS.items():
        if token in low and name not in hosts: hosts.append(name)
    event_type="other"
    # Host/environment surveillance is a hard semantic boundary from human-case events.
    if any(x in low for x in ["rodent surveillance","reservoir surveillance","啮齿动物监测","宿主监测"]):
        event_type="host_surveillance"
    elif any(x in low for x in ["outbreak","暴发","疫情"]):
        event_type="outbreak"
    elif any(x in low for x in ["confirmed case","laboratory-confirmed case","human case","确诊病例","病例报告","感染者"]):
        event_type="human_case"
    elif any(x in low for x in ["seroprevalence","surveillance","监测"]):
        event_type="surveillance"
    counts={"confirmed":None,"probable":None,"suspected":None,"deaths":None}
    patterns={
      "confirmed":[r"(\d[\d,]*)\s+(?:laboratory[- ]confirmed|confirmed)\s+cases?",r"确诊(?:病例)?\s*(\d[\d,]*)\s*例"],
      "probable":[r"(\d[\d,]*)\s+probable\s+cases?",r"可能(?:病例)?\s*(\d[\d,]*)\s*例"],
      "suspected":[r"(\d[\d,]*)\s+suspected\s+cases?",r"疑似(?:病例)?\s*(\d[\d,]*)\s*例"],
      "deaths":[r"(\d[\d,]*)\s+deaths?",r"死亡\s*(\d[\d,]*)\s*例"]}
    for key,plist in patterns.items():
        for pat in plist:
            m=re.search(pat,low,re.I)
            if m:
                counts[key]=int(m.group(1).replace(",",""));break
    return {"pathogens":pathogens,"countries":countries,"hosts":hosts,"event_type":event_type,"case_counts":counts}


def annotate_work(work: dict[str,Any],lexicon:list[dict[str,Any]])->dict[str,Any]:
    full_text=" ".join(section.get("text") or "" for section in work.get("full_text",{}).get("sections",[]))
    text=" ".join([work.get("title",{}).get("original") or "",work.get("abstract",{}).get("original") or "",full_text])
    ent=extract_entities(text,lexicon)
    work["entities"].update({"pathogens":ent["pathogens"],"hosts":ent["hosts"],"countries":ent["countries"]})
    return work


def annotate_article(article:dict[str,Any],lexicon:list[dict[str,Any]])->dict[str,Any]:
    text=" ".join([article.get("title",{}).get("original") or "",article.get("content",{}).get("analysis_text") or article.get("content",{}).get("excerpt") or ""])
    ent=extract_entities(text,lexicon)
    article["entities"].update({"pathogens":ent["pathogens"],"hosts":ent["hosts"],"event_type":ent["event_type"],"country":ent["countries"][0] if ent["countries"] else None,"confirmed_cases":ent["case_counts"]["confirmed"],"probable_cases":ent["case_counts"]["probable"],"suspected_cases":ent["case_counts"]["suspected"],"deaths":ent["case_counts"]["deaths"]})
    return article
