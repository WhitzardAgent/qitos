"""SQLite-backed store for persistent analysis data."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import (
    CallCandidate, CallEdge, CallSite, ConstraintIR, DefinitionIR,
    ExprIR, FunctionSummary, FunctionSymbol, Parameter,
    RiskSignal, SourceLocation, stable_value,
)

ANALYSIS_VERSION = "4-navigation"
GRAMMAR_VERSION = "tree-sitter-c-cpp-v2"

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnalysisConfig:
    max_files: int = 50_000
    max_file_size_mb: int = 5
    max_call_depth: int = 8
    max_paths: int = 20
    max_candidates_per_call: int = 10
    max_constraints_per_path: int = 100
    analysis_timeout_seconds: int = 120
    automatic_top_paths: int = 3
    automatic_max_call_depth: int = 6
    automatic_max_constraints: int = 12
    automatic_max_dataflow_steps: int = 8
    automatic_token_budget: int = 1500
    automatic_timeout_seconds: int = 60
    minimum_candidate_confidence: float = .30
    excludes: tuple[str, ...] = ()

    def hash(self) -> str:
        return hashlib.sha256(json.dumps(stable_value(self), sort_keys=True).encode()).hexdigest()[:16]


def _loc(v: dict[str, Any] | None) -> SourceLocation | None:
    return SourceLocation(**v) if v else None


def _expr(v: dict[str, Any]) -> ExprIR:
    return ExprIR(v.get("kind", "unknown"), v.get("value"), tuple(_expr(x) for x in v.get("children", [])), v.get("source_text", ""), _loc(v.get("location")))


def _constraint(v: dict[str, Any]) -> ConstraintIR:
    return ConstraintIR(
        _expr(v["expression"]), v["source_text"], v["normalized_text"],
        bool(v["polarity"]), v["origin_function"],
        _loc(v["origin_location"]) or SourceLocation("", 0), v["reason"],
        float(v["confidence"]), v.get("role", "reachability"),
        v.get("gate_type", "path_gate"), v.get("safe_formula", ""),
        v.get("violation_formula", ""), v.get("input_mapping", ""),
    )


def _call(v: dict[str, Any]) -> CallSite:
    return CallSite(v["callsite_id"], v["caller_id"], v["callee_text"], _expr(v["receiver"]) if v.get("receiver") else None, [_expr(x) for x in v.get("arguments", [])], _loc(v["location"]) or SourceLocation("", 0), [_constraint(x) for x in v.get("local_guards", [])], [CallCandidate(**x) for x in v.get("candidates", [])], v.get("resolution_status", "unresolved"), v.get("receiver_type", ""))


def _summary(v: dict[str, Any]) -> FunctionSummary:
    return FunctionSummary(v["function_id"], list(v.get("parameters", [])), [_call(x) for x in v.get("calls", [])], [_expr(x) for x in v.get("returns", [])], [DefinitionIR(x["target"], _expr(x["expression"]), _loc(x["location"]) or SourceLocation("", 0), [_constraint(g) for g in x.get("guards", [])]) for x in v.get("local_definitions", [])], [DefinitionIR(x["target"], _expr(x["expression"]), _loc(x["location"]) or SourceLocation("", 0), [_constraint(g) for g in x.get("guards", [])]) for x in v.get("field_writes", [])], [_loc(x) for x in v.get("early_exits", []) if _loc(x)], list(v.get("unresolved_nodes", [])), [RiskSignal(x["signal_id"], x["kind"], x.get("expression", ""), _loc(x.get("location")) or SourceLocation("", 0), float(x.get("severity", 0)), list(x.get("parameter_dependencies", [])), x.get("reason", "")) for x in v.get("risk_signals", [])])


def _symbol(v: dict[str, Any]) -> FunctionSymbol:
    return FunctionSymbol(v["symbol_id"], v["name"], v["qualified_name"], v["file"], v.get("scope"), [Parameter(**x) for x in v.get("parameters", [])], bool(v.get("is_static")), v.get("language", "c"), _loc(v["body_location"]) or SourceLocation("", 0), v.get("source_text", ""))


class AnalysisStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS kv (kind TEXT, key TEXT, value TEXT, updated REAL, PRIMARY KEY(kind,key))")
        self.db.execute("CREATE TABLE IF NOT EXISTS metadata (graph_key TEXT, name TEXT, value TEXT, updated REAL, PRIMARY KEY(graph_key,name))")
        self.db.execute("CREATE TABLE IF NOT EXISTS files (graph_key TEXT, path TEXT, digest TEXT, status TEXT, unresolved TEXT, updated REAL, PRIMARY KEY(graph_key,path))")
        self.db.execute("CREATE TABLE IF NOT EXISTS symbols (graph_key TEXT, symbol_id TEXT, file TEXT, value TEXT, PRIMARY KEY(graph_key,symbol_id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS function_summaries (graph_key TEXT, function_id TEXT, file TEXT, value TEXT, PRIMARY KEY(graph_key,function_id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS callsites (graph_key TEXT, callsite_id TEXT, function_id TEXT, file TEXT, value TEXT, PRIMARY KEY(graph_key,callsite_id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS call_edges (graph_key TEXT, callsite_id TEXT, caller_id TEXT, callee_id TEXT, value TEXT, PRIMARY KEY(graph_key,callsite_id,callee_id))")
        self.db.execute("CREATE TABLE IF NOT EXISTS query_results (graph_key TEXT, query_key TEXT, value TEXT, updated REAL, PRIMARY KEY(graph_key,query_key))")
        self.db.execute("CREATE TABLE IF NOT EXISTS analysis_results (result_id TEXT PRIMARY KEY, value TEXT, updated REAL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS briefs (brief_id TEXT PRIMARY KEY, candidate_id TEXT, value TEXT, updated REAL)")
        self.db.commit()

    def get(self, kind: str, key: str) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE kind=? AND key=?", (kind, key)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, kind: str, key: str, value: Any) -> None:
        raw = json.dumps(stable_value(value), ensure_ascii=False, sort_keys=True)
        self.db.execute("INSERT OR REPLACE INTO kv(kind,key,value,updated) VALUES(?,?,?,?)", (kind, key, raw, time.time()))
        if kind == "analysis":
            self.db.execute(
                "INSERT OR REPLACE INTO analysis_results(result_id,value,updated) VALUES(?,?,?)",
                (key, raw, time.time()),
            )
        elif kind == "brief":
            candidate_id = str(value.get("candidate_id", "")) if isinstance(value, dict) else ""
            self.db.execute(
                "INSERT OR REPLACE INTO briefs(brief_id,candidate_id,value,updated) VALUES(?,?,?,?)",
                (key, candidate_id, raw, time.time()),
            )
        elif kind in {"path", "candidate_fingerprint"}:
            self.db.execute(
                "INSERT OR REPLACE INTO query_results(graph_key,query_key,value,updated) VALUES(?,?,?,?)",
                ("runtime", f"{kind}:{key}", raw, time.time()),
            )
        self.db.commit()

    def file(self, graph_key: str, path: str) -> dict[str, Any] | None:
        row = self.db.execute(
            "SELECT digest,status,unresolved FROM files WHERE graph_key=? AND path=?",
            (graph_key, path),
        ).fetchone()
        if not row:
            return None
        symbols = [json.loads(item[0]) for item in self.db.execute(
            "SELECT value FROM symbols WHERE graph_key=? AND file=? ORDER BY symbol_id",
            (graph_key, path),
        )]
        summaries = [json.loads(item[0]) for item in self.db.execute(
            "SELECT value FROM function_summaries WHERE graph_key=? AND file=? ORDER BY function_id",
            (graph_key, path),
        )]
        return {
            "digest": row[0], "status": row[1],
            "unresolved": json.loads(row[2] or "[]"),
            "symbols": symbols, "summaries": summaries,
        }

    def put_file(
        self,
        graph_key: str,
        path: str,
        digest: str,
        symbols: list[FunctionSymbol],
        summaries: list[FunctionSummary],
        unresolved: list[dict[str, Any]],
    ) -> None:
        status = "partial" if unresolved else "success"
        now = time.time()
        self.db.execute("DELETE FROM callsites WHERE graph_key=? AND file=?", (graph_key, path))
        self.db.execute("DELETE FROM function_summaries WHERE graph_key=? AND file=?", (graph_key, path))
        self.db.execute("DELETE FROM symbols WHERE graph_key=? AND file=?", (graph_key, path))
        self.db.execute(
            "INSERT OR REPLACE INTO files(graph_key,path,digest,status,unresolved,updated) VALUES(?,?,?,?,?,?)",
            (graph_key, path, digest, status, json.dumps(unresolved, ensure_ascii=False, sort_keys=True), now),
        )
        for symbol in symbols:
            self.db.execute(
                "INSERT OR REPLACE INTO symbols(graph_key,symbol_id,file,value) VALUES(?,?,?,?)",
                (graph_key, symbol.symbol_id, path, json.dumps(stable_value(symbol), ensure_ascii=False, sort_keys=True)),
            )
        for summary in summaries:
            self.db.execute(
                "INSERT OR REPLACE INTO function_summaries(graph_key,function_id,file,value) VALUES(?,?,?,?)",
                (graph_key, summary.function_id, path, json.dumps(stable_value(summary), ensure_ascii=False, sort_keys=True)),
            )
            for call in summary.calls:
                self.db.execute(
                    "INSERT OR REPLACE INTO callsites(graph_key,callsite_id,function_id,file,value) VALUES(?,?,?,?,?)",
                    (graph_key, call.callsite_id, summary.function_id, path, json.dumps(stable_value(call), ensure_ascii=False, sort_keys=True)),
                )
        self.db.commit()

    def put_edges(self, graph_key: str, edges: list[CallEdge]) -> None:
        self.db.execute("DELETE FROM call_edges WHERE graph_key=?", (graph_key,))
        for edge in edges:
            self.db.execute(
                "INSERT OR REPLACE INTO call_edges(graph_key,callsite_id,caller_id,callee_id,value) VALUES(?,?,?,?,?)",
                (graph_key, edge.callsite_id, edge.caller_id, edge.callee_id, json.dumps(stable_value(edge), ensure_ascii=False, sort_keys=True)),
            )
        self.db.commit()

    def put_metadata(self, graph_key: str, name: str, value: Any) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO metadata(graph_key,name,value,updated) VALUES(?,?,?,?)",
            (graph_key, name, json.dumps(stable_value(value), ensure_ascii=False, sort_keys=True), time.time()),
        )
        self.db.commit()
