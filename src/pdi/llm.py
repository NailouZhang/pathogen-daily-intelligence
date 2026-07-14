from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .http import HttpClient
from .utils import content_hash, utc_now_iso


@dataclass
class LLMRun:
    provider: str
    model: str | None
    status: str
    output: dict[str, Any] | None
    error: str | None
    retry_count: int
    fallback_used: bool
    input_hash: str
    generated_at: str

    def audit(self) -> dict[str, Any]:
        data=asdict(self); data.pop("output",None); return data


class ModelRouter:
    def __init__(self, root: Path, policy: dict[str, Any], client: HttpClient | None = None) -> None:
        self.root=root; self.policy=policy; self.client=client or HttpClient(timeout=60)
        self.providers=[policy.get("primary_provider","gemini")]+[x for x in policy.get("fallback_providers",[]) if x!=policy.get("primary_provider")]
        if "deterministic" not in self.providers:self.providers.append("deterministic")

    def _prompt(self,name:str)->str:
        return (self.root/"prompts"/f"{name}.txt").read_text(encoding="utf-8")

    def _json_from_text(self,text:str)->dict[str,Any]:
        text=text.strip()
        if text.startswith("```"):
            text=text.strip("`"); text=text.removeprefix("json").strip()
        start=text.find("{");end=text.rfind("}")
        if start>=0 and end>start:text=text[start:end+1]
        data=json.loads(text)
        if not isinstance(data,dict):raise ValueError("Model output must be a JSON object")
        return data

    def _gemini_models(self,key:str)->list[str]:
        data,audit=self.client.get_json("https://generativelanguage.googleapis.com/v1beta/models",params={"key":key})
        if not data:return []
        out=[]
        for m in data.get("models",[]):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):out.append(m.get("name","").removeprefix("models/"))
        return [x for x in out if x]

    def _github_models(self,token:str)->list[str]:
        data,audit=self.client.get_json("https://models.github.ai/catalog/models",headers={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2026-03-10"})
        return [x.get("id") for x in (data or []) if x.get("id")]

    def _groq_models(self,key:str)->list[str]:
        data,audit=self.client.get_json("https://api.groq.com/openai/v1/models",headers={"Authorization":f"Bearer {key}"})
        return [x.get("id") for x in (data or {}).get("data",[]) if x.get("id")]

    @staticmethod
    def _choose(models:list[str],preferred:str|None,keywords:list[str])->str|None:
        if preferred and preferred in models:return preferred
        for kw in keywords:
            hit=next((m for m in models if kw in m.casefold()),None)
            if hit:return hit
        return models[0] if models else preferred

    def _call_gemini(self,key:str,model:str,prompt:str,payload:dict[str,Any])->dict[str,Any]:
        url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body={"contents":[{"role":"user","parts":[{"text":prompt+"\n\nINPUT JSON:\n"+json.dumps(payload,ensure_ascii=False)}]}],"generationConfig":{"temperature":0.0,"responseMimeType":"application/json"}}
        data,audit=self.client.post_json(url,params={"key":key},json=body,max_attempts=1)
        if not data:raise RuntimeError(audit.error or f"Gemini HTTP {audit.status_code}")
        text=data.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","")
        return self._json_from_text(text)

    def _call_openai_compatible(self,url:str,key:str,model:str,prompt:str,payload:dict[str,Any],headers:dict[str,str]|None=None)->dict[str,Any]:
        hdr={"Authorization":f"Bearer {key}","Content-Type":"application/json"};hdr.update(headers or {})
        body={"model":model,"messages":[{"role":"system","content":prompt},{"role":"user","content":json.dumps(payload,ensure_ascii=False)}],"temperature":0,"response_format":{"type":"json_object"}}
        data,audit=self.client.post_json(url,headers=hdr,json=body,max_attempts=1)
        if not data:raise RuntimeError(audit.error or f"Model HTTP {audit.status_code}")
        text=data.get("choices",[{}])[0].get("message",{}).get("content","")
        return self._json_from_text(text)

    def run(self,task_name:str,payload:dict[str,Any])->LLMRun:
        prompt=self._prompt(task_name); ih=content_hash(payload); fallback=False
        max_retries=int(self.policy.get("max_retries_per_provider",2))
        for provider in self.providers:
            if provider=="deterministic":
                return LLMRun(provider,None,"fallback",None,"No usable LLM provider; deterministic output retained.",0,fallback,ih,utc_now_iso())
            try:
                if provider=="gemini":
                    key=os.getenv("GEMINI_API_KEY","") or os.getenv("GOOGLE_AI_STUDIO_API_KEY","")
                    if not key:raise RuntimeError("Missing GEMINI_API_KEY")
                    models=self._gemini_models(key); model=self._choose(models,os.getenv("GEMINI_MODEL"),["flash","pro"])
                    if not model:raise RuntimeError("No Gemini generateContent model available")
                    call=lambda:self._call_gemini(key,model,prompt,payload)
                elif provider=="github_models":
                    key=os.getenv("GITHUB_MODELS_TOKEN","") or os.getenv("GITHUB_TOKEN","")
                    if not key:raise RuntimeError("Missing GitHub Models token")
                    models=self._github_models(key); model=self._choose(models,os.getenv("GITHUB_MODELS_MODEL"),["gpt-4.1-mini","gpt-4.1","phi","llama"])
                    if not model:raise RuntimeError("No GitHub model available")
                    call=lambda:self._call_openai_compatible("https://models.github.ai/inference/chat/completions",key,model,prompt,payload,{"Accept":"application/vnd.github+json","X-GitHub-Api-Version":"2026-03-10"})
                elif provider=="groq":
                    key=os.getenv("GROQ_API_KEY","")
                    if not key:raise RuntimeError("Missing GROQ_API_KEY")
                    models=self._groq_models(key);model=self._choose(models,os.getenv("GROQ_MODEL"),["gpt-oss-20b","llama","qwen"])
                    if not model:raise RuntimeError("No Groq model available")
                    call=lambda:self._call_openai_compatible("https://api.groq.com/openai/v1/chat/completions",key,model,prompt,payload)
                else:
                    fallback=True;continue
                last=None
                for attempt in range(max_retries+1):
                    try:
                        output=call();return LLMRun(provider,model,"success",output,None,attempt,fallback,ih,utc_now_iso())
                    except Exception as exc:
                        last=exc
                        if attempt<max_retries:time.sleep(min(8,2**attempt))
                fallback=True
            except Exception:
                fallback=True
                continue
        return LLMRun("deterministic",None,"fallback",None,"All LLM providers failed.",0,True,ih,utc_now_iso())
