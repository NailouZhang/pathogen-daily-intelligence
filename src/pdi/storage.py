from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .utils import read_json, write_json


def load_state(state_dir:Path)->dict[str,Any]:
    return read_json(state_dir/"seen_items.json",{}) or {}


def save_outputs(output_dir:Path,issue:dict[str,Any],works:list[dict[str,Any]],articles:list[dict[str,Any]],events:list[dict[str,Any]],state:dict[str,Any],html_text:str,email_html:str,rss_text:str)->dict[str,str]:
    data=output_dir/"data";site=output_dir/"site"
    issue_date=issue["issue_date"];parts=issue_date.split("-")
    archive_rel=Path("archive")/parts[0]/parts[1]/parts[2]
    write_json(data/"latest.json",issue)
    write_json(data/archive_rel/"issue.json",issue)
    write_json(data/"state"/"seen_items.json",state)
    (data/"entities").mkdir(parents=True,exist_ok=True)
    for name,rows in [("scholarly_works.jsonl",works),("news_articles.jsonl",articles),("public_health_events.jsonl",events)]:
        (data/"entities"/name).write_text("\n".join(json.dumps(x,ensure_ascii=False,default=str) for x in rows)+("\n" if rows else ""),encoding="utf-8")
    history=read_json(data/"history_index.json",[]) or []
    history=[x for x in history if x.get("issue_date")!=issue_date]
    history.insert(0,{"issue_date":issue_date,"issue_id":issue["issue_id"],"path":f"archive/{parts[0]}/{parts[1]}/{parts[2]}/issue.json","statistics":issue.get("statistics",{})})
    write_json(data/"history_index.json",history[:730])
    site.mkdir(parents=True,exist_ok=True);(site/"index.html").write_text(html_text,encoding="utf-8");write_json(site/"latest.json",issue);(site/"feed.xml").write_text(rss_text,encoding="utf-8")
    (site/archive_rel).mkdir(parents=True,exist_ok=True);(site/archive_rel/"index.html").write_text(html_text,encoding="utf-8")
    (output_dir/"email").mkdir(parents=True,exist_ok=True);(output_dir/"email"/"latest.html").write_text(email_html,encoding="utf-8")
    items=[]
    for w in works:items.append({"item_type":"scholarly_work","item_id":w["work_id"],"title":w.get("title",{}).get("original"),"decision":w.get("filter_result",{}).get("decision"),"source_count":w.get("quality",{}).get("source_count")})
    for e in events:items.append({"item_type":"public_health_event","item_id":e["event_id"],"title":e.get("summary"),"decision":e.get("display_decision"),"source_count":len(e.get("source_articles",[]))})
    with (data/"latest_items.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=["item_type","item_id","title","decision","source_count"]);writer.writeheader();writer.writerows(items)
    manifest={"latest_json":"data/latest.json","site_index":"site/index.html","email_html":"email/latest.html","issue_archive":(Path("data")/archive_rel/"issue.json").as_posix()}
    write_json(output_dir/"output_manifest.json",manifest);return manifest
