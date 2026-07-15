from __future__ import annotations

import re
from typing import Any

from .utils import normalize_space, sentence_split

COUNTRIES = {
    "china": "China", "中国": "China", "united states": "United States", "u.s.": "United States", "usa": "United States", "美国": "United States",
    "chile": "Chile", "智利": "Chile", "argentina": "Argentina", "阿根廷": "Argentina", "germany": "Germany", "德国": "Germany",
    "russia": "Russia", "俄罗斯": "Russia", "south korea": "South Korea", "韩国": "South Korea", "brazil": "Brazil", "巴西": "Brazil",
    "panama": "Panama", "巴拿马": "Panama", "saudi arabia": "Saudi Arabia", "沙特阿拉伯": "Saudi Arabia", "canada": "Canada", "加拿大": "Canada",
    "south africa": "South Africa", "南非": "South Africa", "democratic republic of the congo": "Democratic Republic of the Congo", "uganda": "Uganda",
    "paraguay": "Paraguay", "uruguay": "Uruguay", "new mexico": "United States", "netherlands": "Netherlands", "荷兰": "Netherlands",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "英国": "United Kingdom", "france": "France", "法国": "France",
}
HOST_TERMS = {
    "rodent": "rodent", "mouse": "mouse", "mice": "mouse", "rat": "rat", "shrew": "shrew", "bat": "bat",
    "啮齿动物": "rodent", "鼠": "rodent", "蝙蝠": "bat", "鼩鼱": "shrew",
}
PATHOGEN_TERM_TYPES = {
    "pathogen_name", "virus_entity_name", "taxonomy_family", "taxonomy_genus",
    "disease_name", "clinical_syndrome",
}


def _approved_pathogen_terms(lexicon: list[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(term.get("term"))
            for term in lexicon
            if term.get("status") == "accepted_for_search"
            and term.get("term_type") in PATHOGEN_TERM_TYPES
            and term.get("term")
        },
        key=len,
        reverse=True,
    )


def _sentences_with_target(text: str, terms: list[str]) -> list[str]:
    rows = sentence_split(text, "S")
    return [
        row["text"]
        for row in rows
        if any(term.casefold() in row["text"].casefold() for term in terms if len(term) >= 4)
    ]


def _case_counts(text: str) -> dict[str, int | None]:
    low = normalize_space(text).casefold()
    counts: dict[str, int | None] = {"confirmed": None, "probable": None, "suspected": None, "deaths": None}
    patterns = {
        "confirmed": [r"(\d[\d,]*)\s+(?:laboratory[- ]confirmed|confirmed)\s+cases?", r"确诊(?:病例)?\s*(\d[\d,]*)\s*例"],
        "probable": [r"(\d[\d,]*)\s+probable\s+cases?", r"可能(?:病例)?\s*(\d[\d,]*)\s*例"],
        "suspected": [r"(\d[\d,]*)\s+suspected\s+cases?", r"疑似(?:病例)?\s*(\d[\d,]*)\s*例"],
        "deaths": [r"(\d[\d,]*)\s+deaths?", r"死亡\s*(\d[\d,]*)\s*例"],
    }
    for key, candidates in patterns.items():
        for pattern in candidates:
            match = re.search(pattern, low, re.I)
            if match:
                counts[key] = int(match.group(1).replace(",", ""))
                break
    return counts


def extract_entities(text: str, lexicon: list[dict[str, Any]], *, contextual: bool = False) -> dict[str, Any]:
    full = normalize_space(text)
    low = full.casefold()
    terms = _approved_pathogen_terms(lexicon)
    pathogens = [term for term in terms if term.casefold() in low]

    # For news, case counts, country and event type are read only from sentences
    # that explicitly mention the monitored pathogen/disease.  This prevents an
    # Ebola article that merely recalls an old hantavirus event from donating its
    # Ebola case counts and geography to a hantavirus event.
    target_sentences = _sentences_with_target(full, terms) if contextual else []
    context = " ".join(target_sentences) if target_sentences else ("" if contextual else full)
    context_low = context.casefold()

    countries: list[str] = []
    for token, name in COUNTRIES.items():
        if token in context_low and name not in countries:
            countries.append(name)
    hosts: list[str] = []
    for token, name in HOST_TERMS.items():
        if token in context_low and name not in hosts:
            hosts.append(name)

    event_type = "other"
    if any(x in context_low for x in ["rodent surveillance", "reservoir surveillance", "啮齿动物监测", "宿主监测"]):
        event_type = "host_surveillance"
    elif any(x in context_low for x in ["confirmed case", "laboratory-confirmed case", "human case", "确诊病例", "病例报告", "感染者"]):
        event_type = "human_case"
    elif any(x in context_low for x in ["outbreak", "暴发", "疫情"]):
        event_type = "outbreak"
    elif any(x in context_low for x in ["seroprevalence", "surveillance", "监测"]):
        event_type = "surveillance"

    return {
        "pathogens": pathogens,
        "countries": countries,
        "hosts": hosts,
        "event_type": event_type,
        "case_counts": _case_counts(context) if context else {"confirmed": None, "probable": None, "suspected": None, "deaths": None},
        "context_sentences": target_sentences,
    }


def annotate_work(work: dict[str, Any], lexicon: list[dict[str, Any]]) -> dict[str, Any]:
    full_text = " ".join(section.get("text") or "" for section in (work.get("full_text") or {}).get("sections", []))
    text = " ".join([(work.get("title") or {}).get("original") or "", (work.get("abstract") or {}).get("original") or "", full_text])
    ent = extract_entities(text, lexicon)
    work["entities"].update({"pathogens": ent["pathogens"], "hosts": ent["hosts"], "countries": ent["countries"]})
    return work


def annotate_article(article: dict[str, Any], lexicon: list[dict[str, Any]]) -> dict[str, Any]:
    text = " ".join([
        (article.get("title") or {}).get("original") or "",
        (article.get("content") or {}).get("analysis_text") or (article.get("content") or {}).get("excerpt") or "",
    ])
    ent = extract_entities(text, lexicon, contextual=True)
    article["entities"].update(
        {
            "pathogens": ent["pathogens"],
            "hosts": ent["hosts"],
            "event_type": ent["event_type"],
            "country": ent["countries"][0] if ent["countries"] else None,
            "confirmed_cases": ent["case_counts"]["confirmed"],
            "probable_cases": ent["case_counts"]["probable"],
            "suspected_cases": ent["case_counts"]["suspected"],
            "deaths": ent["case_counts"]["deaths"],
        }
    )
    article["entity_evidence_context"] = ent["context_sentences"]
    return article
