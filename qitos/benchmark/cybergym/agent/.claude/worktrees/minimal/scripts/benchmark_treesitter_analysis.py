#!/usr/bin/env python3
"""Run cold/warm analysis benchmarks without network access."""
from __future__ import annotations
import argparse, hashlib, json, tempfile, time
from pathlib import Path
from cybergym_agent.analysis.service import ANALYSIS_VERSION, AnalysisConfig, AnalysisService

DEFAULT_REPOS=["../cybergym_workspace/arvo_23764/repo-vul","../cybergym_workspace/arvo_17986/repo-vul","/private/tmp/defending-code-reference-harness"]
def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("repositories",nargs="*",default=DEFAULT_REPOS); p.add_argument("--output",default=""); p.add_argument("--timeout",type=float,default=8.0); args=p.parse_args(); reports=[]
    with tempfile.TemporaryDirectory(prefix="cybergym-analysis-bench-") as workspace:
        for raw in args.repositories:
            repo=Path(raw).resolve()
            if not repo.is_dir(): reports.append({"repository":str(repo),"status":"missing"}); continue
            key=hashlib.blake2s(str(repo).encode(),digest_size=6).hexdigest(); service=AnalysisService(repo,workspace_root=Path(workspace)/key,config=AnalysisConfig(analysis_timeout_seconds=max(1,int(args.timeout))))
            start=time.perf_counter(); cold=service.index_repository(force=True); cold["wall_ms"]=round((time.perf_counter()-start)*1000,2)
            # A fresh service measures durable SQLite reconstruction rather
            # than the trivial in-process fast path.  Partial indexes continue
            # filling their immutable graph during this pass.
            warm_service=AnalysisService(repo,workspace_root=Path(workspace)/key,config=AnalysisConfig(analysis_timeout_seconds=max(1,int(args.timeout))))
            start=time.perf_counter(); warm=warm_service.index_repository(); warm["wall_ms"]=round((time.perf_counter()-start)*1000,2)
            start=time.perf_counter(); navigation=warm_service.discover_sink_navigation_leads(limit=5); navigation_ms=round((time.perf_counter()-start)*1000,2)
            reports.append({"repository":str(repo),"cold":cold,"warm":warm,"navigation":{
                "wall_ms":navigation_ms,"lead_count":len(navigation.get("leads",[])),
                "roles":[item.get("role") for item in navigation.get("leads",[])],
                "reachable_leads":sum(bool(item.get("reachable_from_entry")) for item in navigation.get("leads",[])),
                "brief_tokens_estimate":warm_service._estimate_tokens(navigation.get("context_payload","")),
            },"status":"success"})
    result={"analysis_version":ANALYSIS_VERSION,"repositories":reports}; text=json.dumps(result,ensure_ascii=False,indent=2)
    if args.output: Path(args.output).write_text(text+"\n")
    print(text); return 0
if __name__=="__main__": raise SystemExit(main())
