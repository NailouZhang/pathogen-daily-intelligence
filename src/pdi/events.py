from __future__ import annotations

from datetime import date
from typing import Any

from rapidfuzz.fuzz import token_set_ratio

from .utils import normalize_title, stable_hash, utc_now_iso


def _day(value:str|None)->date|None:
    try:return date.fromisoformat((value or "")[:10])
    except ValueError:return None


def _similar(a:dict[str,Any],b:dict[str,Any])->float:
    ea=a.get("entities",{}); eb=b.get("entities",{})
    pathogen=1.0 if set(ea.get("pathogens") or []) & set(eb.get("pathogens") or []) else 0.0
    location=1.0 if ea.get("country") and ea.get("country")==eb.get("country") else (0.4 if not ea.get("country") or not eb.get("country") else 0.0)
    event=1.0 if ea.get("event_type")==eb.get("event_type") else 0.2
    da,db=_day(a.get("published_at")),_day(b.get("published_at")); date_score=0.5
    if da and db: date_score=max(0.0,1-abs((da-db).days)/14)
    title=token_set_ratio(normalize_title(a.get("title",{}).get("original")),normalize_title(b.get("title",{}).get("original")))/100
    return 0.28*pathogen+0.23*location+0.18*event+0.14*date_score+0.17*title


def cluster_events(articles:list[dict[str,Any]],previous_state:dict[str,Any]|None=None)->tuple[list[dict[str,Any]],dict[str,Any]]:
    previous_state=previous_state or {}; prior=previous_state.get("events") or []
    clusters=[]
    for article in articles:
        best=None;best_score=0.0
        for cluster in clusters:
            score=max(_similar(article,x) for x in cluster["articles"])
            if score>best_score:best,best_score=cluster,score
        if best is not None and best_score>=0.78:
            best["articles"].append(article);best["scores"].append(best_score)
        else:clusters.append({"articles":[article],"scores":[1.0]})
    events=[];new_state=[]
    for cluster in clusters:
        arts=cluster["articles"]; primary=sorted(arts,key=lambda a:({"A":0,"B":1,"C":2,"D":3,"unknown":4}.get(a.get("source",{}).get("reliability_tier"),4),a.get("published_at") or "9999"))[0]
        e=primary.get("entities",{}); sig="|".join([str((e.get("pathogens") or ["unknown"])[0]),str(e.get("country") or "unknown"),str(e.get("event_type") or "other"),normalize_title(primary.get("title",{}).get("original"))[:80]])
        event_id=None;version=1;history=[]
        for old in prior:
            old_stub={"entities":{"pathogens":old.get("pathogens") or [],"country":old.get("country"),"event_type":old.get("event_type")},"published_at":old.get("published_at"),"title":{"original":old.get("title") or ""}}
            if _similar(primary,old_stub)>=0.83:
                event_id=old.get("event_id");version=int(old.get("event_version",1));history=old.get("change_history") or []
                break
        if not event_id:event_id="event-"+stable_hash(sig)
        counts={k:max([a.get("entities",{}).get(k+"_cases") for a in arts if a.get("entities",{}).get(k+"_cases") is not None] or [None]) for k in ["confirmed","probable","suspected"]}
        counts["deaths"]=max([a.get("entities",{}).get("deaths") for a in arts if a.get("entities",{}).get("deaths") is not None] or [None])
        material=not any(old.get("event_id")==event_id for old in prior)
        old_match=next((old for old in prior if old.get("event_id")==event_id),None)
        if old_match and old_match.get("case_counts")!=counts:
            material=True;version+=1;history=history+[{"changed_at":utc_now_iso(),"type":"case_count_change","previous":old_match.get("case_counts"),"current":counts}]
        event={"schema_version":"1.0","event_id":event_id,"event_version":version,"event_type":e.get("event_type") or "other","pathogens":sorted({p for a in arts for p in (a.get("entities",{}).get("pathogens") or [])}),"diseases":[],"location":{"country":e.get("country"),"admin1":None,"admin2":None,"city":None,"latitude":None,"longitude":None},"timeline":{"event_date":None,"first_reported_at":min((a.get("published_at") for a in arts if a.get("published_at")),default=None),"first_seen_at":min((a.get("first_seen_at") for a in arts if a.get("first_seen_at")),default=utc_now_iso()),"last_updated_at":utc_now_iso()},"case_counts":{**counts,"as_of":max((a.get("published_at") for a in arts if a.get("published_at")),default=None)},"hosts":sorted({h for a in arts for h in (a.get("entities",{}).get("hosts") or [])}),"official_status":"official" if any(a.get("source",{}).get("reliability_tier")=="A" for a in arts) else "unconfirmed","primary_source":{"article_id":primary["article_id"],"name":primary.get("source",{}).get("name"),"url":primary.get("canonical_url")},"source_articles":[a["article_id"] for a in arts],"change_history":history,"cluster_quality":{"score":round(sum(cluster["scores"])/len(cluster["scores"]),3),"decision":"auto_merge" if len(arts)>1 else "single_article"},"summary":primary.get("title",{}).get("original"),"material_change":material}
        for a in arts:a["event_id"]=event_id
        events.append(event)
        new_state.append({"event_id":event_id,"event_version":version,"title":primary.get("title",{}).get("original"),"pathogens":event["pathogens"],"country":event["location"]["country"],"event_type":event["event_type"],"published_at":event["timeline"]["first_reported_at"],"case_counts":counts,"change_history":history})
    return events,{"events":new_state,"updated_at":utc_now_iso()}
