from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def validate_schema(data:dict[str,Any],schema_path:Path)->list[str]:
    schema=json.loads(schema_path.read_text(encoding="utf-8"))
    return [f"{'.'.join(str(x) for x in err.path)}: {err.message}" for err in Draft202012Validator(schema).iter_errors(data)]


def validate_ai_output(output:dict[str,Any]|None,evidence:list[dict[str,str]],approved_terms:list[str],support_ids:set[str]|None=None)->dict[str,Any]:
    if output is None:return {"valid":False,"errors":["NO_OUTPUT"],"unsupported_claim_count":0}
    evidence_map={x.get("id"):x.get("text","") for x in evidence if x.get("id")}
    evidence_text=" ".join(evidence_map.values()).casefold();errors=[];unsupported=0
    def walk(value:Any,path:str=""):
        nonlocal unsupported
        if isinstance(value,dict):
            ids=value.get("evidence_ids")
            if ids is not None:
                missing=[x for x in ids if x not in evidence_map]
                if missing:errors.append(f"{path}.evidence_ids missing: {missing}")
            for k,v in value.items():walk(v,f"{path}.{k}" if path else k)
        elif isinstance(value,list):
            for i,v in enumerate(value):walk(v,f"{path}[{i}]")
        elif isinstance(value,(int,float)) and not isinstance(value,bool):
            token=str(value)
            if token not in evidence_text:unsupported+=1;errors.append(f"Unsupported numeric value at {path}: {token}")
    walk(output)
    if support_ids is not None:
        for sid in output.get("supporting_item_ids",[]) or []:
            if sid not in support_ids:errors.append(f"Unknown supporting item id: {sid}")
    return {"valid":not errors,"errors":errors[:30],"unsupported_claim_count":unsupported}
