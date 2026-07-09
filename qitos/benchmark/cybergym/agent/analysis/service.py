"""Persistent analysis service used by automatic enrichment and query tools."""

from __future__ import annotations

import hashlib
import html
import json
import logging
import math
import os
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .indexer import index_file, index_file_isolated, resolve_calls, scan_files
from .models import (
    AnalysisPath, CallCandidate, CallEdge, CallSite, ConstraintIR, DefinitionIR,
    ExprIR, FunctionSummary, FunctionSymbol, Parameter, RankedVulnerabilityPath,
    RiskSignal, SinkAnalysisBrief,
    SinkCandidateInput, SourceLocation, stable_value,
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


class AnalysisService:
    def __init__(self, repository: str | Path, *, workspace_root: str | Path | None = None, config: AnalysisConfig | None = None) -> None:
        self.root = Path(repository).resolve()
        self.config = config or AnalysisConfig()
        workspace = Path(workspace_root).resolve() if workspace_root else self.root.parent
        self.store = AnalysisStore(workspace / ".cybergym" / "analysis" / "index.sqlite3")
        self.symbols: list[FunctionSymbol] = []
        self.summaries: list[FunctionSummary] = []
        self.edges: list[CallEdge] = []
        self.unresolved: list[dict[str, Any]] = []
        self.file_hashes: dict[str, str] = {}
        self.file_status: dict[str, str] = {}
        self.graph_key = hashlib.sha256(
            f"{self.root}:{GRAMMAR_VERSION}:{ANALYSIS_VERSION}:{self.config.hash()}".encode()
        ).hexdigest()
        self.graph_id = ""
        self.index_status = "NO_INDEX"
        self.index_report: dict[str, Any] = {}
        self.entrypoints: list[str] = []
        self.sccs: list[list[str]] = []
        self.input_control: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.entry_paths: dict[str, list[str]] = {}
        self._call_graph: Any | None = None
        self._path_finder: Any | None = None

    def _refresh_graph_metadata(self) -> None:
        by_caller: dict[str, list[str]] = {}
        for edge in self.edges:
            by_caller.setdefault(edge.caller_id, []).append(edge.callee_id)
        index = 0
        stack: list[str] = []
        on_stack: set[str] = set()
        indexes: dict[str, int] = {}
        lowlinks: dict[str, int] = {}
        components: list[list[str]] = []

        def visit(node: str) -> None:
            nonlocal index
            indexes[node] = lowlinks[node] = index
            index += 1
            stack.append(node); on_stack.add(node)
            for target in by_caller.get(node, []):
                if target not in indexes:
                    visit(target); lowlinks[node] = min(lowlinks[node], lowlinks[target])
                elif target in on_stack:
                    lowlinks[node] = min(lowlinks[node], indexes[target])
            if lowlinks[node] == indexes[node]:
                component: list[str] = []
                while stack:
                    item = stack.pop(); on_stack.discard(item); component.append(item)
                    if item == node: break
                components.append(sorted(component))

        for symbol in self.symbols:
            if symbol.symbol_id not in indexes:
                visit(symbol.symbol_id)
        self.sccs = components
        self.entrypoints = [
            item.symbol_id for item in self.symbols
            if item.name in {"LLVMFuzzerTestOneInput", "LLVMFuzzerTestOneInputEx", "main"}
            or item.name.startswith("fuzz_")
        ]
        self._compute_input_control()

    @staticmethod
    def _expr_identifiers(expression: ExprIR) -> set[str]:
        values = {str(expression.value)} if expression.kind == "identifier" and expression.value else set()
        for child in expression.children:
            values.update(AnalysisService._expr_identifiers(child))
        return values

    def _compute_input_control(self) -> None:
        """Propagate harness-derived values through bindings with a bounded fixpoint."""
        summaries = {item.function_id: item for item in self.summaries}
        symbols = {item.symbol_id: item for item in self.symbols}
        controlled: dict[str, dict[str, list[dict[str, Any]]]] = {}
        paths: dict[str, list[str]] = {}
        for entry_id in self.entrypoints:
            symbol = symbols.get(entry_id)
            if symbol is None:
                continue
            controlled[entry_id] = {
                item.name: [{"entrypoint": symbol.qualified_name, "parameter": item.name}]
                for item in symbol.parameters
            }
            paths[entry_id] = [entry_id]

        def expression_is_controlled(expr: ExprIR, summary: FunctionSummary | None,
                                     known: set[str], depth: int = 0) -> tuple[bool, list[str]]:
            identifiers = self._expr_identifiers(expr)
            direct = sorted(identifiers & known)
            if direct:
                return True, direct
            if summary is None or depth >= self.config.automatic_max_dataflow_steps:
                return False, []
            for definition in reversed(summary.local_definitions + summary.field_writes):
                leaf = definition.target.rsplit("->", 1)[-1].rsplit(".", 1)[-1]
                if definition.target in identifiers or leaf in identifiers:
                    yes, origins = expression_is_controlled(definition.expression, summary, known, depth + 1)
                    if yes:
                        return True, origins
            return False, []

        for _round in range(max(2, self.config.automatic_max_call_depth + 1)):
            changed = False
            for edge in sorted(self.edges, key=lambda item: -item.confidence):
                caller_values = controlled.get(edge.caller_id)
                if not caller_values:
                    continue
                caller_summary = summaries.get(edge.caller_id)
                target_values = controlled.setdefault(edge.callee_id, {})
                for formal, actual in edge.bindings.items():
                    yes, origins = expression_is_controlled(actual, caller_summary, set(caller_values))
                    if yes and formal not in target_values:
                        target_values[formal] = [{
                            "caller_id": edge.caller_id, "callsite_id": edge.callsite_id,
                            "actual": actual.render(), "from_parameters": origins,
                            "confidence": edge.confidence,
                        }]
                        changed = True
                if edge.callee_id not in paths and edge.caller_id in paths:
                    paths[edge.callee_id] = [*paths[edge.caller_id], edge.callee_id]
                    changed = True
            if not changed:
                break
        self.input_control = controlled
        self.entry_paths = paths
        # Build name→symbol_id lookup for fast reachability checks
        self._name_to_ids: dict[str, set[str]] = {}
        for sym in self.symbols:
            for key in (sym.name, sym.qualified_name):
                self._name_to_ids.setdefault(key, set()).add(sym.symbol_id)

    def is_reachable_from_entry(self, function_name: str) -> bool | None:
        """Check if *function_name* is reachable from any fuzz harness entry.

        Returns True/False if the graph has been built, or None if no index
        is available (so the caller should skip tagging).
        """
        if self.index_status not in {"GRAPH_READY", "PARTIAL_INDEX"} or not self.entry_paths:
            return None
        ids = self._name_to_ids.get(function_name)
        if not ids:
            return False
        return any(sid in self.entry_paths for sid in ids)

    def index_repository(self, *, force: bool = False, timeout_seconds: float | None = None, priority_files: list[str] | None = None) -> dict[str, Any]:
        started = time.perf_counter()
        requested_timeout = timeout_seconds if timeout_seconds is not None else self.config.analysis_timeout_seconds
        if self.index_status in {"GRAPH_READY", "PARTIAL_INDEX"} and not force:
            return {**self.index_report, "cache_hits": len(self.file_hashes), "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}
        deadline = started + max(.1, requested_timeout * .80)
        paths = scan_files(self.root, excludes=set(self.config.excludes), max_files=self.config.max_files, max_file_size=self.config.max_file_size_mb * 1024 * 1024)
        priorities = {str(Path(x).as_posix()).removeprefix("repo-vul/") for x in (priority_files or []) if x}
        def priority(path: Path) -> tuple[int, str]:
            rel = path.relative_to(self.root).as_posix()
            name = path.name.lower()
            if any(rel == item or rel.endswith("/" + item) or item.endswith("/" + rel) for item in priorities): rank = 0
            elif any(token in name for token in ("fuzz", "harness", "test_one_input")): rank = 1
            elif name == "compile_commands.json": rank = 2
            else: rank = 3
            return rank, rel
        paths.sort(key=priority)
        current: dict[str, str] = {}
        for path in paths:
            try: current[path.relative_to(self.root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError: continue
        cache_hits = 0
        indexed_files: set[str] = set()
        symbols_by_file: dict[str, list[FunctionSymbol]] = {}
        summaries_by_file: dict[str, list[FunctionSummary]] = {}
        self.unresolved = []
        pending: list[Path] = []
        for path in paths:
            rel = path.relative_to(self.root).as_posix()
            cached = None if force else self.store.file(self.graph_key, rel)
            if cached and cached.get("digest") == current.get(rel):
                cache_hits += 1
                indexed_files.add(rel)
                symbols_by_file[rel] = [_symbol(x) for x in cached.get("symbols", [])]
                summaries_by_file[rel] = [_summary(x) for x in cached.get("summaries", [])]
                self.unresolved.extend(cached.get("unresolved", []))
                self.file_status[rel] = str(cached.get("status") or "success")
            else:
                pending.append(path)
        workers = max(1, min(4, os.cpu_count() or 1))
        timed_out = False
        while pending and time.perf_counter() < deadline:
            batch, pending = pending[:workers], pending[workers:]
            remaining = max(.1, deadline - time.perf_counter())
            per_file_timeout = max(5.0, min(30.0, remaining))
            with ThreadPoolExecutor(max_workers=len(batch), thread_name_prefix="cybergym-index") as pool:
                future_paths = {
                    pool.submit(index_file, self.root, path): path
                    for path in batch
                }
                for future in as_completed(future_paths):
                    path = future_paths[future]
                    rel = path.relative_to(self.root).as_posix()
                    try:
                        digest, syms, sums, unresolved = future.result()
                    except Exception as exc:
                        digest, syms, sums = current.get(rel, ""), [], []
                        unresolved = [{"file": rel, "reason": "index_exception", "detail": str(exc)[:200]}]
                    symbols_by_file[rel], summaries_by_file[rel] = syms, sums
                    indexed_files.add(rel)
                    self.unresolved.extend(unresolved)
                    self.file_status[rel] = "partial" if unresolved else "success"
                    self.store.put_file(self.graph_key, rel, digest, syms, sums, unresolved)
        self.symbols = [x for rel in sorted(symbols_by_file) for x in symbols_by_file[rel]]
        self.summaries = [x for rel in sorted(summaries_by_file) for x in summaries_by_file[rel]]
        self.edges = resolve_calls(self.symbols, self.summaries)
        self._refresh_graph_metadata()

        # Spend the reserved tail budget on the harness-forward frontier.  A
        # partial graph should follow unresolved calls from reachable harness
        # code instead of continuing in lexical file order.
        overall_deadline = started + max(.1, requested_timeout)
        if pending and self.entrypoints and time.perf_counter() < overall_deadline:
            reachable_ids = set(self.entry_paths)
            frontier_names = {
                call.callee_text.rsplit("::", 1)[-1].rsplit("->", 1)[-1].rsplit(".", 1)[-1]
                for summary in self.summaries if summary.function_id in reachable_ids
                for call in summary.calls if call.resolution_status == "unresolved"
            }
            frontier_names = {name for name in frontier_names if re.fullmatch(r"[A-Za-z_]\w*", name)}
            frontier_paths: list[Path] = []
            if frontier_names:
                pattern = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in sorted(frontier_names, key=len, reverse=True)[:200]) + r")\s*\(")
                for path in pending[:1000]:
                    if time.perf_counter() >= overall_deadline or len(frontier_paths) >= workers:
                        break
                    try:
                        if pattern.search(path.read_text(errors="ignore")):
                            frontier_paths.append(path)
                    except OSError:
                        continue
            if frontier_paths and time.perf_counter() < overall_deadline:
                remaining = max(.2, overall_deadline - time.perf_counter())
                with ThreadPoolExecutor(max_workers=len(frontier_paths), thread_name_prefix="cybergym-frontier") as pool:
                    futures = {
                        pool.submit(index_file, self.root, path): path
                        for path in frontier_paths
                    }
                    for future in as_completed(futures):
                        path = futures[future]; rel = path.relative_to(self.root).as_posix()
                        try:
                            digest, syms, sums, unresolved = future.result()
                        except Exception as exc:
                            digest, syms, sums = current.get(rel, ""), [], []
                            unresolved = [{"file": rel, "reason": "frontier_index_exception", "detail": str(exc)[:200]}]
                        symbols_by_file[rel], summaries_by_file[rel] = syms, sums
                        indexed_files.add(rel); self.unresolved.extend(unresolved)
                        self.file_status[rel] = "partial" if unresolved else "success"
                        self.store.put_file(self.graph_key, rel, digest, syms, sums, unresolved)
                pending = [path for path in pending if path not in set(frontier_paths)]
                self.symbols = [x for rel in sorted(symbols_by_file) for x in symbols_by_file[rel]]
                self.summaries = [x for rel in sorted(summaries_by_file) for x in summaries_by_file[rel]]
                self.edges = resolve_calls(self.symbols, self.summaries)
                self._refresh_graph_metadata()

        timed_out = bool(pending)
        indexed_hashes = {rel: digest for rel, digest in current.items() if rel in indexed_files}
        if timed_out:
            self.unresolved.append({"reason": "analysis_timeout", "analyzed_files": len(indexed_files), "total_files": len(paths)})
        self.store.put_edges(self.graph_key, self.edges)
        self.file_hashes = indexed_hashes
        fingerprint = hashlib.sha256(json.dumps({
            "files": current, "grammar": GRAMMAR_VERSION,
            "analysis": ANALYSIS_VERSION, "config": self.config.hash(),
        }, sort_keys=True).encode()).hexdigest()
        self.graph_id = "graph_" + fingerprint[:16]
        partial_files = sum(status != "success" for status in self.file_status.values())
        complete = len(indexed_files) == len(paths) and partial_files == 0
        self.index_status = "GRAPH_READY" if complete else "PARTIAL_INDEX"
        self.index_report = {
            "graph_id": self.graph_id,
            "index_status": self.index_status,
            "files_total": len(paths),
            "files_indexed": len(indexed_files),
            "functions": len(self.symbols),
            "callsites": sum(len(x.calls) for x in self.summaries),
            "unresolved_callsites": sum(c.resolution_status == "unresolved" for s in self.summaries for c in s.calls),
            "partial_files": partial_files,
            "entrypoints": len(self.entrypoints),
            "sccs": len(self.sccs),
            "cache_hits": cache_hits,
            "status": "success" if complete else "partial",
            "reason": "analysis_timeout" if timed_out else "partial_files" if partial_files else "",
            "elapsed_ms": round((time.perf_counter()-started)*1000, 2),
        }
        self.store.put_metadata(self.graph_key, "snapshot", self.index_report)

        # Build CallGraph + CallPathFinder for supplementary bidirectional queries
        self._build_call_graph()

        return dict(self.index_report)

    def ensure_file_indexed(self, file: str, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
        """Fill a file missing from a partial immutable graph."""
        rel = self._resolve_file_name(file)
        if rel in self.file_hashes and self.file_status.get(rel) == "success":
            return {"status": "ready", "file": rel, "cache_hit": True}
        path = (self.root / rel).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return {"status": "error", "reason": "outside_repository", "file": rel}
        if not path.is_file():
            return {"status": "not_found", "file": rel}
        digest, syms, sums, unresolved = index_file(self.root, path)
        self.store.put_file(self.graph_key, rel, digest, syms, sums, unresolved)
        old_file_ids = {s.symbol_id for s in self.symbols if s.file == rel}
        self.symbols = [s for s in self.symbols if s.file != rel] + syms
        replaced_ids = {s.function_id for s in sums}
        self.summaries = [s for s in self.summaries if s.function_id not in old_file_ids | replaced_ids] + sums
        self.file_hashes[rel] = digest
        self.file_status[rel] = "partial" if unresolved else "success"
        self.edges = resolve_calls(self.symbols, self.summaries)
        self._refresh_graph_metadata()
        self.store.put_edges(self.graph_key, self.edges)
        self.index_status = "PARTIAL_INDEX" if unresolved or self.index_status == "PARTIAL_INDEX" else "GRAPH_READY"
        return {"status": "partial" if unresolved else "success", "file": rel, "functions": len(syms), "unresolved": unresolved}

    def _ensure(self, timeout_seconds: float | None = None) -> None:
        if not self.symbols: self.index_repository(timeout_seconds=timeout_seconds)

    def _build_call_graph(self) -> None:
        """Build a CallGraph from the TSA modules for supplementary queries.

        Instead of letting CallGraph.build() re-parse the entire repo (double
        parsing), we build a lightweight wrapper that reuses our already-indexed
        symbols, summaries, and edges.  CallPathFinder can still operate on it.
        """
        try:
            from .call_graph import CallGraph, FunctionRef
            from .call_path import CallPathFinder
            # Build a CallGraph populated from our existing index
            cg = CallGraph.__new__(CallGraph)
            cg.project_root = self.root
            cg._functions = []
            cg._func_by_name = {}
            cg._func_by_qualified = {}
            cg._func_by_file = {}
            cg._callees = {}
            cg._callers = {}
            cg._call_edges = []
            cg._built = True
            cg._imported_names = {}
            cg._module_to_file = {}
            cg._callee_resolver = None

            from collections import defaultdict
            cg._func_by_name = defaultdict(list)
            cg._func_by_qualified = {}
            cg._func_by_file = defaultdict(list)
            cg._callees = defaultdict(list)
            cg._callers = defaultdict(list)

            ref_map: dict[str, FunctionRef] = {}
            for sym in self.symbols:
                ref = FunctionRef(
                    file_path=sym.file,
                    name=sym.name,
                    start_line=sym.body_location.start_line,
                    language=sym.language,
                    receiver=sym.scope,
                    end_line=sym.body_location.end_line,
                )
                ref_map[sym.symbol_id] = ref
                cg._functions.append(ref)
                cg._func_by_name[sym.name].append(ref)
                cg._func_by_file[sym.file].append(ref)

            for edge in self.edges:
                caller_ref = ref_map.get(edge.caller_id)
                callee_ref = ref_map.get(edge.callee_id)
                if caller_ref and callee_ref:
                    cg._callees[caller_ref].append(callee_ref)
                    cg._callers[callee_ref].append(caller_ref)
                    cg._call_edges.append((caller_ref, callee_ref, 0))

            self._call_graph = cg
            self._path_finder = CallPathFinder(str(self.root), graph=cg)
            _LOG.debug("CallGraph built from existing index: %d functions, %d edges",
                       len(cg._functions), len(cg._call_edges))
        except Exception as exc:
            _LOG.warning("CallGraph build failed (non-fatal, edge-list fallback active): %s", exc)
            self._call_graph = None
            self._path_finder = None

    def _symbols_matching(self, query: str) -> list[FunctionSymbol]:
        self._ensure(); q = query.strip()
        return [s for s in self.symbols if s.symbol_id == q or s.qualified_name == q or s.name == q or s.symbol_id.endswith(q)]

    @staticmethod
    def _description_value(analysis: Any, name: str) -> list[str]:
        value = analysis.get(name, []) if isinstance(analysis, dict) else getattr(analysis, name, [])
        return [str(item).strip() for item in (value or []) if str(item).strip()]

    @staticmethod
    def _identifier_key(value: str) -> str:
        return "".join(re.findall(r"[a-z0-9]+", str(value or "").casefold()))

    def verify_description_references(
        self, analysis: Any, limit_per_hint: int = 5,
    ) -> dict[str, Any]:
        """Resolve description priors to bounded source-backed references.

        This method deliberately does not create or promote sink candidates.
        A hit means only that a description-derived string exists in indexed
        source; reachability and vulnerability semantics are separate evidence.
        """
        self._ensure(self.config.automatic_timeout_seconds)
        per_hint = max(1, min(int(limit_per_hint or 5), 10))
        function_queries = self._description_value(analysis, "suspect_functions")
        hint_queries = self._description_value(analysis, "search_hints")
        file_queries = self._description_value(analysis, "suspect_files")
        all_queries = list(dict.fromkeys(function_queries + hint_queries + file_queries))[:48]
        indexed_files = sorted(set(self.file_hashes) | {item.file for item in self.symbols})

        refs: list[dict[str, Any]] = []
        unresolved: list[str] = []
        seen: set[tuple[str, str, str, int]] = set()
        truncated = False

        def add_ref(
            query: str, *, symbol: FunctionSymbol | None = None, file: str = "",
            line: int = 0, match_kind: str, confidence: float, evidence: str,
        ) -> bool:
            nonlocal truncated
            target_file = symbol.file if symbol is not None else file
            symbol_id = symbol.symbol_id if symbol is not None else ""
            key = (query.casefold(), symbol_id, target_file, int(line or 0))
            if key in seen:
                return False
            query_count = sum(item[0] == query.casefold() for item in seen)
            if query_count >= per_hint:
                truncated = True
                return False
            seen.add(key)
            material = f"{self.graph_id}|{query}|{symbol_id}|{target_file}|{line}|{match_kind}"
            refs.append({
                "query": query,
                "ref_id": "ref_" + hashlib.blake2s(material.encode(), digest_size=7).hexdigest(),
                "symbol_id": symbol_id,
                "symbol": symbol.qualified_name if symbol is not None else "",
                "file": target_file,
                "line": int(line or (symbol.body_location.start_line if symbol is not None else 0)),
                "match_kind": match_kind,
                "confidence": round(float(confidence), 3),
                "evidence": evidence[:180],
                "status": "verified",
            })
            return True

        # Function-like priors: exact qualified/name, then casefold, then a
        # conservative normalized-identifier equality (never substring).
        for query in list(dict.fromkeys(function_queries + hint_queries)):
            before = len(refs)
            exact = [s for s in self.symbols if query in {s.name, s.qualified_name, s.symbol_id}]
            for symbol in exact:
                add_ref(query, symbol=symbol, line=symbol.body_location.start_line,
                        match_kind="exact_symbol", confidence=.96,
                        evidence="exact indexed symbol match")
            if len(refs) == before:
                folded = query.casefold()
                matches = [s for s in self.symbols if folded in {s.name.casefold(), s.qualified_name.casefold()}]
                for symbol in matches:
                    add_ref(query, symbol=symbol, line=symbol.body_location.start_line,
                            match_kind="casefold_symbol", confidence=.90,
                            evidence="case-insensitive indexed symbol match")
            if len(refs) == before:
                normalized = self._identifier_key(query)
                if len(normalized) >= 3:
                    matches = [s for s in self.symbols if normalized in {
                        self._identifier_key(s.name), self._identifier_key(s.qualified_name)
                    }]
                    for symbol in matches:
                        add_ref(query, symbol=symbol, line=symbol.body_location.start_line,
                                match_kind="normalized_symbol", confidence=.84,
                                evidence="token-normalized indexed symbol match")

        # File priors: exact relative path, basename, or path suffix.
        for query in file_queries:
            before = len(refs)
            normalized = query.replace("\\", "/").lstrip("./").casefold()
            for rel in indexed_files:
                rel_folded = rel.casefold()
                if rel_folded == normalized or Path(rel).name.casefold() == Path(normalized).name.casefold() or rel_folded.endswith("/" + normalized):
                    add_ref(query, file=rel, line=1, match_kind="file", confidence=.92,
                            evidence="indexed file path match")
            if len(refs) == before:
                unresolved.append(query)

        # Literal fallback searches only indexed source files and only after a
        # symbol/file miss. re.escape makes model-provided metacharacters data.
        resolved_queries = {item["query"].casefold() for item in refs}
        literal_queries = [q for q in all_queries if q.casefold() not in resolved_queries]
        source_suffixes = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm"}
        for query in literal_queries:
            if len(query) < 2:
                unresolved.append(query)
                continue
            pattern = re.compile(re.escape(query), re.IGNORECASE)
            matched = False
            for rel in indexed_files:
                if Path(rel).suffix.lower() not in source_suffixes:
                    continue
                path = self.root / rel
                try:
                    if not path.is_file() or path.stat().st_size > self.config.max_file_size_mb * 1024 * 1024:
                        continue
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for match in pattern.finditer(text):
                    line = text.count("\n", 0, match.start()) + 1
                    matched |= add_ref(query, file=rel, line=line, match_kind="literal_text",
                                       confidence=.68, evidence="bounded literal text match in indexed source")
                    if sum(item[0] == query.casefold() for item in seen) >= per_hint:
                        break
                if sum(item[0] == query.casefold() for item in seen) >= per_hint:
                    break
            if not matched:
                unresolved.append(query)

        unresolved = list(dict.fromkeys(item for item in unresolved if item.casefold() not in {
            ref["query"].casefold() for ref in refs
        }))[:24]
        partial = self.index_status != "GRAPH_READY"
        gaps = []
        if partial and unresolved:
            gaps.append("index is partial; unresolved hints are unknown, not evidence of absence")
        return {
            "status": "partial" if partial else "success",
            "refs": refs[:24],
            "unresolved_hints": unresolved,
            "truncated": truncated or len(refs) > 24,
            "gaps": gaps,
            "graph_id": self.graph_id,
        }

    def summarize_function(self, symbol_id: str) -> dict[str, Any]:
        matches = self._symbols_matching(symbol_id)
        if not matches: return {"status": "not_found", "symbol": symbol_id}
        ids = {x.symbol_id for x in matches}
        return {"status": "success", "symbols": stable_value(matches), "summaries": stable_value([s for s in self.summaries if s.function_id in ids])}

    def find_callers(self, symbol: str, max_depth: int = 5, top_k: int = 10) -> dict[str, Any]:
        targets = {s.symbol_id for s in self._symbols_matching(symbol)}
        reverse: dict[str, list[CallEdge]] = {}
        for edge in self.edges: reverse.setdefault(edge.callee_id, []).append(edge)
        rows, queue, seen = [], [(t, 0) for t in targets], set(targets)
        while queue and len(rows) < top_k:
            current, depth = queue.pop(0)
            if depth >= max_depth: continue
            for edge in sorted(reverse.get(current, []), key=lambda e: -e.confidence):
                rows.append({"depth": depth + 1, "edge": stable_value(edge)})
                if edge.caller_id not in seen:
                    seen.add(edge.caller_id); queue.append((edge.caller_id, depth + 1))
                if len(rows) >= top_k: break
        return {"status": "success" if targets else "not_found", "callers": rows, "truncated": bool(queue)}

    def find_paths_to_target(self, target: str, entrypoint_patterns: list[str] | None = None, max_depth: int = 8, top_k: int = 10) -> dict[str, Any]:
        target_ids = {s.symbol_id for s in self._symbols_matching(target)}
        reverse: dict[str, list[CallEdge]] = {}
        for edge in self.edges: reverse.setdefault(edge.callee_id, []).append(edge)
        patterns = entrypoint_patterns or ["LLVMFuzzerTestOneInput", "main", "fuzz_", "parse_", "handle_"]
        paths: list[AnalysisPath] = []
        def visit(current: str, rev_edges: list[CallEdge], visited: set[str]) -> None:
            if len(paths) >= max(top_k * 4, top_k) or len(rev_edges) >= max_depth: return
            incoming = reverse.get(current, [])
            symbol = next((s for s in self.symbols if s.symbol_id == current), None)
            entry = bool(symbol and any(symbol.name == p or (p.endswith("_") and symbol.name.startswith(p)) for p in patterns))
            if rev_edges and (entry or not incoming):
                edges = list(reversed(rev_edges)); score = math.prod(e.confidence for e in edges) * (.97 ** len(edges))
                constraints: list[ConstraintIR] = []
                environment: dict[str, ExprIR] = {}
                for edge in edges:
                    for constraint in edge.guards:
                        expression = constraint.expression.substitute(environment)
                        constraints.append(ConstraintIR(
                            expression, constraint.source_text, expression.render(),
                            constraint.polarity, constraint.origin_function,
                            constraint.origin_location, constraint.reason,
                            constraint.confidence, constraint.role,
                            constraint.gate_type, constraint.safe_formula,
                            constraint.violation_formula, constraint.input_mapping,
                        ))
                    environment = {name: expression.substitute(environment) for name, expression in edge.bindings.items()}
                raw = "|".join(e.callsite_id for e in edges)
                rendered = {item.normalized_text.strip() for item in constraints if item.normalized_text.strip()}
                contradictions = sorted({
                    f"{expr} contradicts !{expr}"
                    for expr in rendered
                    if not expr.startswith("!") and f"!{expr}" in rendered
                })
                paths.append(AnalysisPath(
                    "path_" + hashlib.blake2s(raw.encode(), digest_size=6).hexdigest(),
                    [edges[0].caller_id, *[e.callee_id for e in edges]], edges,
                    constraints, score * (.5 if contradictions else 1.0),
                    bool(not entry or contradictions), contradictions,
                ))
            for edge in sorted(incoming, key=lambda e: -e.confidence):
                if edge.caller_id in visited: continue
                visit(edge.caller_id, rev_edges + [edge], visited | {edge.caller_id})
        for target_id in target_ids: visit(target_id, [], {target_id})
        paths.sort(key=lambda p: (-p.score, len(p.edges), p.path_id))
        selected = paths[:top_k]
        for path in selected: self.store.put("path", path.path_id, path)
        return {"status": "success" if selected else "not_found", "paths": stable_value(selected), "truncated": len(paths) > len(selected), "target_ids": sorted(target_ids)}

    def extract_constraints(self, function: str, target_line: int, max_paths: int = 8) -> dict[str, Any]:
        matches = self._symbols_matching(function); ids = {x.symbol_id for x in matches}
        calls = [c for s in self.summaries if s.function_id in ids for c in s.calls if not target_line or c.location.start_line == target_line]
        return {"status": "success" if calls else "not_found", "callsites": stable_value(calls[:max_paths]), "truncated": len(calls) > max_paths}

    def trace_value(self, function: str, line: int, expression: str, direction: str = "backward", max_steps: int | None = None) -> dict[str, Any]:
        if direction != "backward": return {"status": "unsupported", "reason": "backward_only"}
        ids = {x.symbol_id for x in self._symbols_matching(function)}; summary = next((s for s in self.summaries if s.function_id in ids), None)
        if summary is None: return {"status": "not_found"}
        defs = [d for d in summary.local_definitions + summary.field_writes if d.location.start_line <= line]
        steps, current, limit, seen = [], expression, max_steps or self.config.automatic_max_dataflow_steps, set()
        while len(steps) < limit and current not in seen:
            seen.add(current); choices = [d for d in defs if d.target == current]
            if not choices: break
            latest = max(choices, key=lambda d: d.location.start_line)
            steps.append({"target": current, "expression": latest.expression.render(), "location": stable_value(latest.location)})
            current = str(latest.expression.value) if latest.expression.kind == "identifier" else latest.expression.render()
        status = "resolved" if current in summary.parameters or not current else "partially_resolved" if steps else "unresolved"
        return {"status": status, "expression": expression, "steps": steps, "origin": current, "truncated": len(steps) >= limit}

    def get_path_details(self, path_id: str, **options: Any) -> dict[str, Any]:
        value = self.store.get("path", path_id)
        return {"status": "success", "path": value, "options": options} if value else {"status": "not_found", "path_id": path_id}

    def explain_path(self, path_id: str, format: str = "compact_text") -> dict[str, Any]:
        value = self.store.get("path", path_id)
        if not value: return {"status": "not_found", "path_id": path_id}
        names = {s.symbol_id: s.name for s in self.symbols}
        lines: list[str] = []
        for edge in value.get("edges", []):
            lines.append(names.get(edge["caller_id"], edge["caller_id"]))
            for guard in edge.get("guards", []): lines.append(f"  [{guard['normalized_text']}]")
            bindings = ", ".join(f"{k}:={_expr(v).render()}" for k, v in edge.get("bindings", {}).items())
            lines.append(f"  -> {names.get(edge['callee_id'], edge['callee_id'])}({bindings})")
        return {"status": "success", "format": format, "text": "\n".join(lines)}

    def resolve_callsite_candidates(self, callsite_id: str, max_candidates: int = 20, **_: Any) -> dict[str, Any]:
        call = next((c for s in self.summaries for c in s.calls if c.callsite_id == callsite_id), None)
        return {"status": call.resolution_status, "candidates": stable_value(call.candidates[:max_candidates])} if call else {"status": "not_found"}

    def get_analysis_result(self, full_result_id: str, section: str = "", offset: int = 0, limit: int = 20) -> dict[str, Any]:
        value = self.store.get("analysis", full_result_id)
        if value is None: return {"status": "not_found", "full_result_id": full_result_id}
        selected = value.get(section, value) if section else value
        if isinstance(selected, list):
            return {"status": "success", "section": section, "items": selected[offset:offset+limit], "offset": offset, "limit": limit, "has_more": offset + limit < len(selected)}
        return {"status": "success", "section": section, "value": selected}

    @staticmethod
    def _normalise_file(file: str) -> str:
        return str(Path(file or "").as_posix()).removeprefix("repo-vul/").lstrip("./")

    def _resolve_file_name(self, file: str) -> str:
        rel = self._normalise_file(file)
        if not rel or rel in self.file_hashes or (self.root / rel).is_file():
            return rel
        known = set(self.file_hashes)
        if not known:
            known = {
                path.relative_to(self.root).as_posix()
                for path in scan_files(
                    self.root, excludes=set(self.config.excludes),
                    max_files=self.config.max_files,
                    max_file_size=self.config.max_file_size_mb * 1024 * 1024,
                )
            }
        matches = sorted(item for item in known if item.endswith("/" + rel) or Path(item).name == Path(rel).name)
        return matches[0] if len(matches) == 1 else rel

    def _resolve_target(self, candidate: SinkCandidateInput) -> tuple[FunctionSymbol | None, dict[str, Any]]:
        """Resolve function/call-expression candidates without inventing uniqueness."""
        file = self._resolve_file_name(candidate.file)
        symbols = list(self.symbols)
        by_id = {item.symbol_id: item for item in symbols}
        evidence: list[str] = []

        if file and candidate.line:
            located = [
                item for item in symbols
                if item.file == file
                and item.body_location.start_line <= candidate.line <= item.body_location.end_line
            ]
            if len(located) == 1:
                target = located[0]
                evidence.append("file+line resolved enclosing function")
                return target, self._target_resolution(candidate, target, "enclosing_function", evidence)

        query = str(candidate.function or "").strip()
        if query:
            matches = [
                item for item in symbols
                if item.symbol_id == query or item.qualified_name == query or item.name == query
            ]
            file_matches = [item for item in matches if not file or item.file == file]
            selected = file_matches or matches
            if len(selected) == 1:
                evidence.append("exact function symbol")
                return selected[0], self._target_resolution(candidate, selected[0], "function_symbol", evidence)

        expressions = [str(x or "").strip() for x in (candidate.callee, candidate.expression, candidate.function) if str(x or "").strip()]
        call_matches: list[tuple[FunctionSymbol, CallSite]] = []
        for summary in self.summaries:
            caller = by_id.get(summary.function_id)
            if caller is None or (file and caller.file != file):
                continue
            for call in summary.calls:
                if candidate.line and abs(call.location.start_line - candidate.line) > 2:
                    continue
                leaf = call.callee_text.rsplit("::", 1)[-1].rsplit("->", 1)[-1].rsplit(".", 1)[-1]
                if any(expr == call.callee_text or expr == leaf or expr in call.callee_text for expr in expressions):
                    call_matches.append((caller, call))
        callers = {caller.symbol_id: caller for caller, _call in call_matches}
        if len(callers) == 1:
            target = next(iter(callers.values()))
            call = call_matches[0][1]
            evidence.append(f"call expression {call.callee_text} resolved to enclosing function")
            value = self._target_resolution(candidate, target, "callsite_enclosing_function", evidence)
            value["sink_expression"] = call.callee_text
            value["sink_location"] = stable_value(call.location)
            value["callsite_id"] = call.callsite_id
            return target, value

        candidates = sorted({item.symbol_id for item in (file_matches if query and 'file_matches' in locals() else [])})
        if not candidates:
            candidates = sorted(callers)[:8]
        return None, {
            "status": "unresolved", "requested": stable_value(candidate),
            "reason": "target_not_uniquely_located", "candidate_symbol_ids": candidates,
            "evidence": evidence,
        }

    @staticmethod
    def _target_resolution(
        candidate: SinkCandidateInput,
        target: FunctionSymbol,
        method: str,
        evidence: list[str],
    ) -> dict[str, Any]:
        return {
            "status": "confirmed" if method in {"enclosing_function", "function_symbol"} else "inferred",
            "method": method,
            "requested": {
                "function": candidate.function, "callee": candidate.callee,
                "expression": candidate.expression, "file": candidate.file, "line": candidate.line,
            },
            "symbol_id": target.symbol_id,
            "function": target.qualified_name,
            "file": target.file,
            "location": stable_value(target.body_location),
            "evidence": evidence,
        }

    @staticmethod
    def _requirement_from_constraint(item: dict[str, Any], path_id: str, order: int) -> dict[str, Any]:
        expression = str(item.get("normalized_text") or item.get("expression") or "").strip()
        origin = item.get("origin_location") or item.get("origin") or {}
        material = json.dumps([path_id, order, expression, origin], sort_keys=True, default=str)
        return {
            "requirement_id": "req_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest(),
            "path_id": path_id,
            "order": order,
            "role": item.get("role", "reachability"),
            "gate_type": item.get("gate_type", "path_gate"),
            "expression": expression,
            "safe_formula": item.get("safe_formula", ""),
            "violation_formula": item.get("violation_formula", ""),
            "input_mapping": item.get("input_mapping", ""),
            "status": item.get("status", "inferred"),
            "confidence": float(item.get("confidence", item.get("confidence_score", .5)) or .5),
            "origin": origin,
            "reason": item.get("reason") or item.get("description") or "source control dependence",
        }

    def _navigation_rows(self, *, description: str = "", focus_symbol_ids: set[str] | None = None, crash_type: str = "") -> list[dict[str, Any]]:
        by_id = {item.symbol_id: item for item in self.symbols}
        summaries = {item.function_id: item for item in self.summaries}
        incoming: dict[str, list[CallEdge]] = {}
        outgoing: dict[str, list[CallEdge]] = {}
        for edge in self.edges:
            incoming.setdefault(edge.callee_id, []).append(edge)
            outgoing.setdefault(edge.caller_id, []).append(edge)
        description_tokens = {
            token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", description)
            if token.lower() not in {"the", "and", "with", "from", "that", "this", "function"}
        }
        utility_tokens = ("mem", "byte", "copy", "insert", "append", "read", "write", "free", "release", "destroy", "convert", "decode")
        rows: list[dict[str, Any]] = []
        for symbol in self.symbols:
            summary = summaries.get(symbol.symbol_id)
            if summary is None:
                continue
            signals = sorted(summary.risk_signals, key=lambda item: -item.severity)
            controlled = self.input_control.get(symbol.symbol_id, {})
            reachable = symbol.symbol_id in self.entry_paths
            if not signals and not reachable and symbol.symbol_id not in (focus_symbol_ids or set()):
                continue
            direct_dependencies = {
                name for signal in signals for name in signal.parameter_dependencies
            } & set(controlled)
            input_score = .30 if direct_dependencies else .22 if controlled else 0.0
            risk_score = .25 * (signals[0].severity if signals else 0.0)
            reach_score = .15 if reachable else 0.0
            direct_score = .10 if signals and signals[0].kind not in {"utility_call", "loop_progress", "input_arithmetic"} else .04 if signals else 0.0
            utility = any(token in symbol.name.lower() for token in utility_tokens) or any(
                item.kind in {"memory_copy", "io", "lifecycle", "allocation", "utility_call"} for item in signals
            )
            utility_score = .10 if utility else 0.0
            focus_score = .05 if symbol.symbol_id in (focus_symbol_ids or set()) else 0.0
            name_tokens = set(re.findall(r"[a-z0-9]+", symbol.qualified_name.lower().replace("_", " ")))
            description_score = .05 if description_tokens & name_tokens else 0.0
            penalty = 0.0
            # Depth-aware scoring: shallow functions (depth<=1) are dispatchers,
            # deep-but-reachable functions (depth>=4) are likely actual crash sites.
            call_depth = len(self.entry_paths.get(symbol.symbol_id, [])) if reachable else 0
            depth_score = 0.0
            if reachable and self.entrypoints:
                if call_depth <= 1:
                    penalty += .12  # Too shallow — dispatcher, not crash site
                elif call_depth >= 4:
                    depth_score = .08  # Deep + reachable = likely crash function
                elif call_depth >= 3:
                    depth_score = .04  # Moderately deep
            if not reachable and self.entrypoints:
                penalty += .15
            if summary.unresolved_nodes:
                penalty += min(.08, .02 * len(summary.unresolved_nodes))
            # Stdlib functions without vulnerability patterns are very unlikely sinks
            from .vuln_patterns import classify_call
            leaf_name = symbol.name.rsplit("::", 1)[-1]
            call_class = classify_call(leaf_name)
            if call_class == "stdlib":
                penalty += .50  # Safe libc — very unlikely to be the sink
            elif call_class == "vuln_stdlib":
                risk_score += .10  # Boost unsafe libc calls (strcpy, memcpy, etc.)
                utility_score += .05
                # Add vuln-specific risk signal if not already present
                from .vuln_patterns import get_vuln_pattern
                vp = get_vuln_pattern(leaf_name)
                if vp is not None and not any(s.kind == "unsafe_api" for s in signals):
                    from .models import RiskSignal, SourceLocation
                    vuln_signal = RiskSignal(
                        signal_id=f"vuln_{leaf_name}",
                        kind="unsafe_api",
                        severity=float({"critical": .95, "high": .85, "medium": .65, "low": .40}.get(vp.severity, .50)),
                        expression=leaf_name,
                        location=SourceLocation(symbol.file, symbol.body_location.start_line, 0,
                                                symbol.body_location.end_line, 0),
                        reason=f"unsafe {vp.category}: {vp.description} — prefer {vp.safe_alternative}",
                        parameter_dependencies=sorted(controlled),
                    )
                    signals.append(vuln_signal)
            # Entry-point functions are never the actual crash sink
            from .vuln_patterns import is_entry_point_function
            if is_entry_point_function(leaf_name):
                penalty += 0.30
            # Crash-type-aware keyword boosts
            crash_type_boost = 0.0
            if crash_type:
                from .vuln_patterns import CRASH_TYPE_SINK_HINTS
                hints = CRASH_TYPE_SINK_HINTS.get(crash_type)
                if hints:
                    name_lower = symbol.name.lower()
                    for kw, boost in hints["keywords"].items():
                        if kw in name_lower:
                            crash_type_boost += boost
                    cat_boosts = hints.get("vuln_categories", {})
                    for sig in signals:
                        if sig.kind in cat_boosts:
                            crash_type_boost += cat_boosts[sig.kind]
            score = max(0.0, min(1.0, input_score + risk_score + reach_score + direct_score + utility_score + focus_score + description_score + depth_score + crash_type_boost - penalty))
            if score < .10:
                continue
            if signals and signals[0].kind == "lifecycle":
                role = "lifecycle"
            elif signals and direct_score >= .10:
                role = "direct_operation"
            elif utility:
                role = "utility"
            elif len(outgoing.get(symbol.symbol_id, [])) >= 3:
                role = "dispatch"
            else:
                role = "wrapper"
            path_ids = self.entry_paths.get(symbol.symbol_id, [])
            chain = [by_id[item].name for item in path_ids if item in by_id]
            top_signals = [{
                "signal_id": item.signal_id, "kind": item.kind,
                "expression": item.expression, "severity": item.severity,
                "location": stable_value(item.location), "reason": item.reason,
                "input_dependencies": item.parameter_dependencies,
            } for item in signals[:3]]
            material = f"{self.graph_id}|{symbol.symbol_id}"
            rows.append({
                "lead_id": "lead_" + hashlib.blake2s(material.encode(), digest_size=7).hexdigest(),
                "symbol_id": symbol.symbol_id, "function": symbol.qualified_name,
                "file": symbol.file, "line": symbol.body_location.start_line,
                "end_line": symbol.body_location.end_line, "score": round(score, 4),
                "role": role, "reachable_from_entry": reachable,
                "input_controlled_parameters": sorted(controlled),
                "input_path": chain, "risk_signals": top_signals,
                "why_inspect": (
                    top_signals[0]["reason"] if top_signals
                    else "reachable from the fuzz entry and useful for following input flow"
                ),
                "next_read": {
                    "path": symbol.file, "offset": max(0, symbol.body_location.start_line - 1),
                    "limit": min(240, max(1, symbol.body_location.end_line - symbol.body_location.start_line + 1)),
                },
                "evidence": {
                    "input_control": round(input_score, 3), "risk": round(risk_score, 3),
                    "reachability": round(reach_score, 3), "directness": round(direct_score, 3),
                    "utility": round(utility_score, 3), "read_focus": round(focus_score, 3),
                    "description_prior": round(description_score, 3), "depth_prior": round(depth_score, 3),
                    "crash_type_prior": round(crash_type_boost, 3),
                    "penalty": round(penalty, 3), "call_depth": call_depth,
                },
                "incoming": [item.caller_id for item in incoming.get(symbol.symbol_id, [])[:4]],
                "outgoing": [item.callee_id for item in outgoing.get(symbol.symbol_id, [])[:4]],
            })
        return sorted(rows, key=lambda item: (-item["score"], -len(item["risk_signals"]), item["file"], item["line"]))

    @staticmethod
    def _diversify_navigation(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        remaining = list(rows)
        while remaining and len(selected) < limit:
            def adjusted(row: dict[str, Any]) -> float:
                penalty = 0.0
                for prior in selected:
                    adjacent = row["symbol_id"] in prior.get("incoming", []) + prior.get("outgoing", [])
                    if adjacent:
                        penalty = max(penalty, .14)
                    elif row["file"] == prior["file"] and row["role"] == prior["role"]:
                        penalty = max(penalty, .08)
                return float(row["score"]) - penalty
            best = max(remaining, key=adjusted)
            selected.append(best)
            remaining.remove(best)
        for row in selected:
            row.pop("incoming", None); row.pop("outgoing", None)
        return selected

    def discover_sink_navigation_leads(
        self, entrypoint: str | None = None, limit: int = 5,
        description: str = "", focus_symbol_ids: list[str] | None = None,
        crash_type: str = "",
    ) -> dict[str, Any]:
        """Return source-backed places to inspect; never claims a true sink."""
        self._ensure(self.config.automatic_timeout_seconds)
        limit = max(1, min(int(limit or 5), 20))
        rows = self._navigation_rows(description=description, focus_symbol_ids=set(focus_symbol_ids or []), crash_type=crash_type)
        if entrypoint:
            matched = {item.symbol_id for item in self._symbols_matching(entrypoint)}
            rows = [row for row in rows if not matched or any(item in matched for item in self.entry_paths.get(row["symbol_id"], [])[:1])]
        leads = self._diversify_navigation(rows, limit)
        mentioned = [item for item in self.symbols if item.name.lower() in description.lower()]
        lead_ids = {item["symbol_id"] for item in leads}
        warnings = []
        for symbol in mentioned[:5]:
            if symbol.symbol_id not in lead_ids:
                warnings.append({
                    "kind": "description_anchor_stale", "function": symbol.qualified_name,
                    "reason": "description match did not outrank input reachability and direct operation evidence",
                })
        material = json.dumps({"graph": self.graph_id, "entrypoint": entrypoint, "description": description, "leads": [x["lead_id"] for x in leads]}, sort_keys=True)
        brief_id = "search_" + hashlib.blake2s(material.encode(), digest_size=8).hexdigest()
        entry_names = [next((s.name for s in self.symbols if s.symbol_id == item), item) for item in self.entrypoints]
        lines = [
            f"Static navigation brief: {brief_id}",
            "Inspect these navigation leads and explicitly confirm one with record_sink_candidate.",
        ]
        if entry_names:
            lines.append("entries=" + ", ".join(entry_names[:3]))
        for index, lead in enumerate(leads, 1):
            signal = lead["risk_signals"][0] if lead["risk_signals"] else {}
            path = " -> ".join(lead["input_path"][-5:]) if lead["input_path"] else "unverified"
            lines.append(f'{index}. {lead["function"]} @{lead["file"]}:{lead["line"]} role={lead["role"]} score={lead["score"]:.2f}')
            lines.append(f'   input_path={path}; signal={signal.get("kind", "none")}: {signal.get("expression", "")[:160]}')
            lines.append(f'   next_read=READ(path="{lead["file"]}", offset={lead["next_read"]["offset"]}, limit={lead["next_read"]["limit"]})')
        for warning in warnings[:2]:
            lines.append(f'warning={warning["kind"]}: {warning["function"]} — {warning["reason"]}')
        payload = "\n".join(lines)
        while self._estimate_tokens(payload) > self.config.automatic_token_budget and len(leads) > 1:
            leads.pop(); lines = lines[:2 + len(leads) * 3]; payload = "\n".join(lines)
        result = {
            "status": "success" if leads else "partial", "brief_id": brief_id,
            "graph_id": self.graph_id, "entrypoints": entry_names, "leads": leads,
            "warnings": warnings, "context_payload": payload,
            "truncated": len(rows) > len(leads),
        }
        self.store.put("sink_search_brief", brief_id, result)
        return result

    @staticmethod
    def _description_values(analysis: Any, field_name: str) -> list[str]:
        if analysis is None:
            return []
        value = analysis.get(field_name, []) if isinstance(analysis, dict) else getattr(analysis, field_name, [])
        return [str(item).strip() for item in (value or []) if str(item).strip()]

    def _selected_entry_ids(self, harness: dict[str, Any] | None) -> tuple[set[str], list[dict[str, Any]]]:
        gaps: list[dict[str, Any]] = []
        selected_path = str((harness or {}).get("source_path") or "")
        selected_entry = str((harness or {}).get("entry_function") or "")
        selected_line = int((harness or {}).get("line") or 0)
        candidates = list(self.entrypoints)
        if selected_entry:
            matches = [
                item.symbol_id for item in self.symbols
                if item.name == selected_entry
                and (not selected_path or item.file == selected_path)
                and (not selected_line or abs(item.body_location.start_line - selected_line) <= 3)
            ]
            if len(matches) == 1:
                return {matches[0]}, gaps
            if matches:
                gaps.append({
                    "id": "ambiguous_selected_harness_entry",
                    "reason": f"{len(matches)} symbols match selected harness",
                    "candidate_symbol_ids": matches[:8],
                })
                return set(matches), gaps
            gaps.append({
                "id": "selected_harness_entry_not_resolved",
                "reason": f"could not resolve {selected_entry} @{selected_path}:{selected_line}",
            })
        if not candidates:
            gaps.append({"id": "entrypoint_required", "reason": "no indexed fuzz entrypoint found"})
        return set(candidates), gaps

    def _path_to_endpoint(self, entry_ids: set[str], endpoint_id: str, max_depth: int) -> tuple[list[str], list[CallEdge], str, list[dict[str, Any]]]:
        by_caller: dict[str, list[CallEdge]] = {}
        for edge in self.edges:
            by_caller.setdefault(edge.caller_id, []).append(edge)
        parent: dict[str, tuple[str, CallEdge]] = {}
        queue = [(eid, 0) for eid in entry_ids]
        seen = set(entry_ids)
        while queue:
            current, depth = queue.pop(0)
            if current == endpoint_id:
                chain = [current]
                edges: list[CallEdge] = []
                while current in parent:
                    prev, edge = parent[current]
                    edges.append(edge)
                    current = prev
                    chain.append(current)
                return list(reversed(chain)), list(reversed(edges)), "resolved", []
            if depth >= max_depth:
                continue
            for edge in sorted(by_caller.get(current, []), key=lambda e: -e.confidence):
                if edge.callee_id in seen:
                    continue
                seen.add(edge.callee_id)
                parent[edge.callee_id] = (current, edge)
                queue.append((edge.callee_id, depth + 1))
        if endpoint_id in self.entry_paths:
            chain = list(self.entry_paths.get(endpoint_id) or [])
            if chain and chain[0] in entry_ids:
                return chain, [], "partial", [{
                    "id": "path_edges_not_reconstructed",
                    "reason": "entry_paths had symbols but callsite edges were incomplete",
                }]
        return [endpoint_id], [], "partial", [{
            "id": "entry_to_endpoint_path_unresolved",
            "reason": f"no path within depth {max_depth}; unresolved/indirect calls may exist",
        }]

    def discover_ranked_vulnerability_paths(
        self,
        *,
        description_analysis: dict[str, Any] | None = None,
        verified_refs: list[dict[str, Any]] | None = None,
        harness: dict[str, Any] | None = None,
        crash_type: str = "",
        fast_depth: int = 8,
        deep_depth: int = 24,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Return diversified, source-backed candidate vulnerability paths."""
        self._ensure(self.config.automatic_timeout_seconds)
        top_k = max(1, min(int(top_k or 5), 10))
        from .vulnerability_knowledge import (
            classify_endpoint_role,
            endpoint_semantics,
            score_risk_signal,
        )
        from .vuln_patterns import is_entry_point_function

        semantics = endpoint_semantics(crash_type)
        entry_ids, entry_gaps = self._selected_entry_ids(harness)
        verified_refs = list(verified_refs or [])
        ref_symbol_ids = {
            str(item.get("symbol_id") or "") for item in verified_refs
            if str(item.get("symbol_id") or "")
        }
        ref_files = {
            str(item.get("file") or "") for item in verified_refs
            if str(item.get("file") or "")
        }
        ref_by_symbol: dict[str, list[str]] = {}
        for item in verified_refs:
            sid = str(item.get("symbol_id") or "")
            if sid:
                ref_by_symbol.setdefault(sid, []).append(str(item.get("ref_id") or ""))
        suspect_funcs = {value.casefold() for value in self._description_values(description_analysis, "suspect_functions")}
        suspect_files = {value.casefold() for value in self._description_values(description_analysis, "suspect_files")}
        harness_first_hops = {
            str(item).casefold()
            for item in ((harness or {}).get("first_hops") or [])
            if str(item).strip()
        }

        rows = self._navigation_rows(
            description=" ".join(
                self._description_values(description_analysis, "suspect_functions")
                + self._description_values(description_analysis, "search_hints")
            ),
            focus_symbol_ids=ref_symbol_ids,
            crash_type=crash_type,
        )[:50]
        by_id = {item.symbol_id: item for item in self.symbols}
        summaries = {item.function_id: item for item in self.summaries}
        pool: list[RankedVulnerabilityPath] = []
        endpoint_seen: set[str] = set()

        for row in rows:
            endpoint_id = str(row.get("symbol_id") or "")
            symbol = by_id.get(endpoint_id)
            if symbol is None or is_entry_point_function(symbol.name):
                continue
            summary = summaries.get(endpoint_id)
            risk_signals = list(summary.risk_signals if summary else [])
            if not risk_signals:
                fake_signal = RiskSignal(
                    signal_id=f"path_anchor_{hashlib.blake2s(endpoint_id.encode(), digest_size=5).hexdigest()}",
                    kind="path_anchor",
                    expression=symbol.name,
                    location=symbol.body_location,
                    severity=.25,
                    reason="reachable or description-focused path anchor",
                )
                risk_signals = [fake_signal]
            best_signal = max(risk_signals, key=lambda sig: score_risk_signal(sig, semantics))
            risk_semantics = 0.30 * score_risk_signal(best_signal, semantics)

            chain, path_edges, status, gaps = self._path_to_endpoint(entry_ids, endpoint_id, fast_depth)
            if status != "resolved" and entry_ids:
                deep_chain, deep_edges, deep_status, deep_gaps = self._path_to_endpoint(entry_ids, endpoint_id, deep_depth)
                if deep_status == "resolved":
                    chain, path_edges, status, gaps = deep_chain, deep_edges, deep_status, []
                else:
                    gaps.extend(deep_gaps)
            reachability = 0.25 if status == "resolved" and chain and chain[0] in entry_ids else 0.10 if chain else 0.0
            unresolved_edges = sum(1 for edge in path_edges if edge.confidence < .50)
            input_control = 0.20 if self.input_control.get(endpoint_id) else 0.0
            if risk_signals and any(set(sig.parameter_dependencies) & set(self.input_control.get(endpoint_id, {})) for sig in risk_signals):
                input_control = 0.20
            elif self.input_control.get(endpoint_id):
                input_control = 0.12
            desc_match = 0.0
            if endpoint_id in ref_symbol_ids or symbol.file in ref_files:
                desc_match = 0.15
            elif symbol.name.casefold() in suspect_funcs or symbol.file.casefold() in suspect_files:
                desc_match = 0.04
            harness_alignment = 0.0
            if harness_first_hops:
                chain_names = {by_id[sid].name.casefold() for sid in chain if sid in by_id}
                if chain_names & harness_first_hops:
                    harness_alignment = 0.10
            elif chain and chain[0] in entry_ids:
                harness_alignment = 0.04
            penalty = 0.0
            if unresolved_edges:
                penalty += min(.12, .04 * unresolved_edges)
            if status != "resolved":
                penalty += .10
            if len(chain) <= 2 and risk_semantics < .20:
                penalty += .06
            if not risk_signals or best_signal.kind == "path_anchor":
                penalty += .05
            total = max(0.0, min(1.0, reachability + risk_semantics + input_control + desc_match + harness_alignment - penalty))
            role = classify_endpoint_role(best_signal, semantics)
            if endpoint_id in endpoint_seen and len(pool) >= 50:
                continue
            endpoint_seen.add(endpoint_id)
            callsite_material = [edge.callsite_id for edge in path_edges]
            path_material = json.dumps([self.graph_id, chain, callsite_material, best_signal.signal_id], sort_keys=True)
            path_id = "vpath_" + hashlib.blake2s(path_material.encode(), digest_size=8).hexdigest()
            chain_items = []
            for sid in chain:
                sym = by_id.get(sid)
                if sym:
                    chain_items.append({
                        "symbol_id": sid, "function": sym.qualified_name,
                        "file": sym.file, "line": sym.body_location.start_line,
                    })
            channels = ["entry_forward"]
            if endpoint_id in ref_symbol_ids or symbol.file in ref_files:
                channels.append("verified_description")
            if risk_semantics > .15:
                channels.append("risk_backward")
            if harness_alignment:
                channels.append("harness_first_hop")
            pool.append(RankedVulnerabilityPath(
                path_id=path_id,
                symbol_ids=chain,
                endpoint_symbol_id=endpoint_id,
                endpoint_signal_id=best_signal.signal_id,
                endpoint_role=role,
                candidate_family=semantics.family,
                score=round(total, 4),
                score_breakdown={
                    "reach": round(reachability, 3),
                    "risk": round(risk_semantics, 3),
                    "input": round(input_control, 3),
                    "desc": round(desc_match, 3),
                    "harness": round(harness_alignment, 3),
                    "penalty": round(penalty, 3),
                },
                resolution_status=status,
                description_ref_ids=ref_by_symbol.get(endpoint_id, []),
                graph_distance_hint=max(0, len(chain) - 1) if chain else None,
                gaps=gaps,
                generation_channels=channels,
                chain=chain_items,
                endpoint={
                    "symbol_id": endpoint_id,
                    "function": symbol.qualified_name,
                    "file": symbol.file,
                    "line": symbol.body_location.start_line,
                    "signal": {
                        "signal_id": best_signal.signal_id,
                        "kind": best_signal.kind,
                        "expression": best_signal.expression,
                        "severity": best_signal.severity,
                        "reason": best_signal.reason,
                    },
                },
                next_read={
                    "path": symbol.file,
                    "offset": max(0, symbol.body_location.start_line - 20),
                    "limit": min(180, max(80, symbol.body_location.end_line - symbol.body_location.start_line + 40)),
                },
            ))

        pool.sort(key=lambda item: (-item.score, item.endpoint["file"], item.endpoint["line"]))
        selected: list[RankedVulnerabilityPath] = []
        file_counts: dict[str, int] = {}
        role_counts: dict[str, int] = {}
        for item in pool:
            file_name = str(item.endpoint.get("file", ""))
            if file_counts.get(file_name, 0) >= 2:
                continue
            if role_counts.get(item.endpoint_role, 0) >= 3:
                continue
            selected.append(item)
            file_counts[file_name] = file_counts.get(file_name, 0) + 1
            role_counts[item.endpoint_role] = role_counts.get(item.endpoint_role, 0) + 1
            if len(selected) >= top_k:
                break
        if len(selected) < top_k:
            for item in pool:
                if item not in selected:
                    selected.append(item)
                    if len(selected) >= top_k:
                        break
        result = {
            "status": "success" if selected else "partial",
            "paths": [stable_value(item) for item in selected],
            "endpoint_count": len(pool),
            "truncated": len(pool) > len(selected),
            "gaps": entry_gaps,
            "graph_id": self.graph_id,
            "crash_type": crash_type,
            "candidate_pool_size": min(len(pool), 50),
        }
        fingerprint = hashlib.sha256(json.dumps({
            "graph": self.graph_id,
            "paths": [item.path_id for item in selected],
            "pool": len(pool),
        }, sort_keys=True).encode()).hexdigest()
        self.store.put("ranked_vulnerability_paths", fingerprint, result)
        return result

    def reachable_functions_from_entry(
        self, entrypoint: str = "", limit: int = 20,
        crash_type: str = "", description: str = "",
    ) -> dict[str, Any]:
        """Collect all functions reachable from a fuzz driver entry, filtered
        and ranked by vulnerability relevance.

        Implements the expert method's Layer 2+3: starting from the fuzz driver,
        enumerate reachable functions via BFS, then score by crash-type keywords,
        dangerous operations, description match, and call depth.
        """
        self._ensure(self.config.automatic_timeout_seconds)
        limit = max(1, min(int(limit or 20), 50))

        # 1. Find entry symbol(s)
        entry_ids: set[str] = set()
        if entrypoint:
            entry_ids = {item.symbol_id for item in self._symbols_matching(entrypoint)}
        if not entry_ids:
            entry_ids = set(self.entrypoints)

        if not entry_ids:
            return {"status": "error", "reason": "no entrypoint found"}

        # 2. BFS through call graph, depth up to 8
        adjacency: dict[str, set[str]] = {}
        for edge in self.edges:
            adjacency.setdefault(edge.caller_id, set()).add(edge.callee_id)

        reachable: dict[str, int] = {}  # symbol_id -> depth
        queue = [(eid, 0) for eid in entry_ids]
        for eid, d in queue:
            if eid in reachable or d > 8:
                continue
            reachable[eid] = d
            for callee in adjacency.get(eid, set()):
                if callee not in reachable:
                    queue.append((callee, d + 1))

        # 3. Score each reachable function
        from .vuln_patterns import CRASH_TYPE_SINK_HINTS, is_entry_point_function
        hints = CRASH_TYPE_SINK_HINTS.get(crash_type, {})
        crash_keywords = hints.get("keywords", {})
        crash_categories = hints.get("vuln_categories", {})
        desc_tokens = {
            token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", description)
            if token.lower() not in {"the", "and", "with", "from", "that", "this", "function"}
        }

        by_id = {item.symbol_id: item for item in self.symbols}
        summaries = {item.function_id: item for item in self.summaries}

        candidates: list[dict[str, Any]] = []
        for sid, depth in reachable.items():
            symbol = by_id.get(sid)
            if symbol is None:
                continue
            if is_entry_point_function(symbol.name):
                continue

            summary = summaries.get(sid)
            signals = sorted(summary.risk_signals, key=lambda item: -item.severity) if summary else []

            score = 0.0
            # Crash-type keyword match
            name_lower = symbol.name.lower()
            for kw, boost in crash_keywords.items():
                if kw in name_lower:
                    score += boost
            # Crash-type category match
            for sig in signals:
                if sig.kind in crash_categories:
                    score += crash_categories[sig.kind]
            # Dangerous operation signal
            if signals:
                score += 0.10 * min(signals[0].severity, 1.0)
            # Description token match
            name_tokens = set(re.findall(r"[a-z0-9]+", symbol.qualified_name.lower().replace("_", " ")))
            if desc_tokens & name_tokens:
                score += 0.08
            # Depth bonus: deeper = more likely crash site
            if depth >= 4:
                score += 0.08
            elif depth >= 3:
                score += 0.04
            # Entry depth penalty
            if depth <= 1:
                score -= 0.10

            if score < 0.05:
                continue

            candidates.append({
                "symbol_id": sid,
                "function": symbol.qualified_name,
                "file": symbol.file,
                "line": symbol.body_location.start_line,
                "score": round(score, 4),
                "depth": depth,
                "risk_signals": [{"kind": s.kind, "expression": s.expression, "severity": s.severity} for s in signals[:2]],
                "why": signals[0].reason if signals else f"reachable at depth {depth}",
            })

        # 4. Sort by score, diversify across files
        candidates.sort(key=lambda c: -c["score"])
        # Deduplicate by file: max 3 per file
        file_count: dict[str, int] = {}
        diversified: list[dict[str, Any]] = []
        for c in candidates:
            fc = file_count.get(c["file"], 0)
            if fc < 3:
                diversified.append(c)
                file_count[c["file"]] = fc + 1
            if len(diversified) >= limit:
                break

        return {
            "status": "success",
            "entry_functions": [by_id[eid].name for eid in entry_ids if eid in by_id],
            "total_reachable": len(reachable),
            "candidates": diversified,
            "crash_type": crash_type,
        }

    def get_sink_search_brief(self, brief_id: str) -> dict[str, Any]:
        return self.store.get("sink_search_brief", brief_id) or {"status": "not_found", "brief_id": brief_id}

    def mark_navigation_lead_reviewed(self, lead_id: str, outcome: str) -> dict[str, Any]:
        if outcome not in {"confirmed", "rejected", "deferred"}:
            return {"status": "error", "reason": "outcome must be confirmed, rejected, or deferred"}
        value = {"status": "success", "lead_id": lead_id, "outcome": outcome, "updated": time.time()}
        self.store.put("navigation_review", lead_id, value)
        return value

    def _expand_symbol_neighborhood(self, symbol_id: str, depth: int = 3, limit: int = 10) -> list[dict[str, Any]]:
        rows = {item["symbol_id"]: item for item in self._navigation_rows()}
        adjacency: dict[str, set[str]] = {}
        for edge in self.edges:
            adjacency.setdefault(edge.caller_id, set()).add(edge.callee_id)
            adjacency.setdefault(edge.callee_id, set()).add(edge.caller_id)
        distances = {symbol_id: 0}; queue = [symbol_id]
        while queue:
            current = queue.pop(0)
            if distances[current] >= max(1, min(depth, 6)):
                continue
            for target in adjacency.get(current, set()):
                if target not in distances:
                    distances[target] = distances[current] + 1; queue.append(target)
        current = rows.get(symbol_id, {})
        alternatives = []
        for target_id, distance in distances.items():
            if target_id == symbol_id or target_id not in rows:
                continue
            row = dict(rows[target_id]); relative = "alternate"
            if row.get("role") == "direct_operation" and current.get("role") in {"wrapper", "dispatch"}:
                relative = "possibly_too_shallow"
            elif current.get("role") == "utility" and row.get("role") in {"wrapper", "dispatch"}:
                relative = "possibly_too_deep"
            elif row.get("role") == "direct_operation":
                relative = "direct_hazard_site"
            row.update({"distance": distance, "diagnosis": relative})
            alternatives.append(row)
        return sorted(alternatives, key=lambda item: (-item["score"], item["distance"]))[:max(1, min(limit, 20))]

    def expand_candidate_neighborhood(self, candidate_id: str, depth: int = 3, limit: int = 10) -> dict[str, Any]:
        latest = self.store.get("brief_latest", candidate_id) or {}
        result = latest.get("result", {}) if isinstance(latest, dict) else {}
        symbol_id = str((result.get("brief") or {}).get("target_resolution", {}).get("symbol_id") or "")
        if not symbol_id:
            matches = self._symbols_matching(candidate_id)
            symbol_id = matches[0].symbol_id if len(matches) == 1 else ""
        if not symbol_id:
            return {"status": "not_found", "candidate_id": candidate_id, "alternatives": []}
        return {"status": "success", "candidate_id": candidate_id, "symbol_id": symbol_id, "alternatives": self._expand_symbol_neighborhood(symbol_id, depth, limit)}

    def analyze_read_context(
        self,
        file: str,
        start_line: int,
        end_line: int,
        *,
        brief: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Map a model-selected READ range to the immutable full-file graph."""
        self._ensure(self.config.automatic_timeout_seconds)
        rel = self._resolve_file_name(file)
        if rel not in self.file_hashes:
            fill = self.ensure_file_indexed(rel, timeout_seconds=2.0)
            if fill.get("status") in {"error", "not_found"}:
                return {"status": "no_change", "reason": fill.get("reason", "file_not_indexed")}
        focus = [
            symbol for symbol in self.symbols
            if symbol.file == rel
            and symbol.body_location.start_line <= max(start_line, end_line)
            and symbol.body_location.end_line >= min(start_line, end_line)
        ]
        if not focus:
            return {"status": "no_change", "reason": "read_range_has_no_function"}
        focus_ids = {item.symbol_id for item in focus}
        by_id = {item.symbol_id: item for item in self.symbols}
        caller_names: list[str] = []
        callee_names: list[str] = []
        guards: list[str] = []
        focus_signals: list[dict[str, Any]] = []
        for edge in self.edges:
            if edge.callee_id in focus_ids and edge.caller_id in by_id:
                caller_names.append(by_id[edge.caller_id].name)
            if edge.caller_id in focus_ids and edge.callee_id in by_id:
                callee_names.append(by_id[edge.callee_id].name)
        for summary in self.summaries:
            if summary.function_id not in focus_ids:
                continue
            focus_signals.extend({
                "signal_id": item.signal_id, "kind": item.kind,
                "expression": item.expression, "severity": item.severity,
                "location": stable_value(item.location), "reason": item.reason,
            } for item in summary.risk_signals[:3])
            for call in summary.calls:
                guards.extend(item.normalized_text for item in call.local_guards if item.confidence >= .5)
        paths = (brief or {}).get("candidate_paths", [])
        selected_path = next((path for path in paths if any(
            detail.get("function") in {item.name for item in focus}
            and detail.get("file") == rel
            for detail in path.get("chain_details", [])
        )), None)
        # Classify callees by risk (common crash-site patterns)
        _RISKY_PATTERNS = re.compile(
            r"memcpy|memmove|memset|realloc|calloc|malloc|free|"
            r"bebytes2|lebytes2|"
            r"decode|parse|read|write|copy|convert|compress|decompress|"
            r"alloc|destroy|release|insert|remove|append|push|pop|"
            r"strcpy|strcat|strncpy|sprintf|vsprintf|scanf|sscanf|gets|fgets",
            re.IGNORECASE,
        )
        # Count sub-callees per callee for depth annotation (leaf = 0 = crash site)
        callee_sub_count: dict[str, int] = {}
        for cr_name in dict.fromkeys(callee_names):
            callee_sym = next((s for s in self.symbols if s.name == cr_name), None)
            if callee_sym:
                callee_sub_count[cr_name] = sum(
                    1 for e in self.edges if e.caller_id == callee_sym.symbol_id
                )
            else:
                callee_sub_count[cr_name] = -1
        callee_risks = [
            {
                "name": n,
                "risk": "risky" if _RISKY_PATTERNS.search(n) else "normal",
                "sub_callees": callee_sub_count.get(n, -1),
            }
            for n in dict.fromkeys(callee_names)
        ][:6]
        base = {
            "focus": [{
                "symbol_id": item.symbol_id, "function": item.qualified_name,
                "file": item.file, "start_line": item.body_location.start_line,
                "end_line": item.body_location.end_line,
            } for item in focus[:3]],
            "direct_callers": list(dict.fromkeys(caller_names))[:3],
            "relevant_callees": [c["name"] for c in callee_risks],  # backward compat
            "callee_risks": callee_risks,
            "notable_guards": list(dict.fromkeys(guards))[:3],
            "risk_signals": sorted(focus_signals, key=lambda item: -item["severity"])[:5],
            "input_controlled_parameters": {
                by_id[sid].qualified_name: sorted(self.input_control.get(sid, {}))
                for sid in focus_ids if sid in by_id and self.input_control.get(sid)
            },
        }
        navigation = self.discover_sink_navigation_leads(
            limit=5, focus_symbol_ids=list(focus_ids),
        )
        related_leads = [
            item for item in navigation.get("leads", [])
            if item["symbol_id"] in focus_ids
            or item["symbol_id"] in {edge.callee_id for edge in self.edges if edge.caller_id in focus_ids}
            or item["symbol_id"] in {edge.caller_id for edge in self.edges if edge.callee_id in focus_ids}
        ][:3]
        base["navigation_leads"] = related_leads
        base["focus_role"] = related_leads[0].get("role", "structural") if related_leads else "structural"
        if selected_path:
            payload = {
                "status": "success", "kind": "static_analysis_delta",
                "path_id": selected_path.get("path_id", ""), **base,
                "added": [f"[reachability] {item}" for item in base["notable_guards"]],
                "revised": [], "invalidated": [],
            }
        else:
            entry_names = {"LLVMFuzzerTestOneInput", "LLVMFuzzerTestOneInputEx", "main"}
            reachable = any(item.symbol_id in self.entry_paths for item in focus)
            payload = {"status": "success", "kind": "code_index_context", "reachable_from_entry": reachable, **base}
        fingerprint_material = json.dumps({"graph": self.graph_id, "payload": payload}, sort_keys=True)
        payload["fingerprint"] = hashlib.sha256(fingerprint_material.encode()).hexdigest()
        return payload

    def analyze_sink_candidate(self, candidate: SinkCandidateInput | dict[str, Any], mode: str = "automatic", budget_profile: str = "default") -> dict[str, Any]:
        candidate = candidate if isinstance(candidate, SinkCandidateInput) else SinkCandidateInput(**candidate)
        if not candidate.reason: return {"status": "error", "reason": "candidate.reason is required"}
        if not self.symbols:
            self.index_repository(timeout_seconds=self.config.automatic_timeout_seconds if mode == "automatic" else self.config.analysis_timeout_seconds, priority_files=[candidate.file])
        if candidate.file and self._resolve_file_name(candidate.file) not in self.file_hashes:
            self.ensure_file_indexed(candidate.file, timeout_seconds=2.0)
        fingerprint = hashlib.sha256(json.dumps({"candidate": stable_value(candidate), "graph_id": self.graph_id, "files": self.file_hashes, "grammar": GRAMMAR_VERSION, "analysis": ANALYSIS_VERSION, "config": self.config.hash(), "mode": mode}, sort_keys=True).encode()).hexdigest()
        cached = self.store.get("candidate_fingerprint", fingerprint)
        if cached: return {**cached, "cache_hit": True}
        target, target_resolution = self._resolve_target(candidate)
        path_result = self.find_paths_to_target(target.symbol_id if target else candidate.function or candidate.callee or "", max_depth=self.config.automatic_max_call_depth if mode == "automatic" else self.config.max_call_depth, top_k=self.config.automatic_top_paths if mode == "automatic" else self.config.max_paths)
        paths = path_result.get("paths", [])
        target_summary = next((s for s in self.summaries if target and s.function_id == target.symbol_id), None)
        sink_names = [str(value or "") for value in (candidate.callee, candidate.expression, candidate.function) if value]
        sink_calls = [c for c in (target_summary.calls if target_summary else []) if (not sink_names or any(name in c.callee_text for name in sink_names)) and (not candidate.line or abs(c.location.start_line-candidate.line) <= 2)]
        provenance = []
        for call in sink_calls[:1]:
            for index, arg in enumerate(call.arguments):
                trace = self.trace_value(target.symbol_id, call.location.start_line, arg.render()) if target else {"status": "unresolved"}
                provenance.append({"sink_argument": f"{call.callee_text}.arg{index}", "expression": arg.render(), "status": trace.get("status"), "trace": trace.get("steps", []), "origin": trace.get("origin", "")})
        if not provenance and paths:
            for name, value in paths[0].get("edges", [])[-1].get("bindings", {}).items() if paths[0].get("edges") else []:
                provenance.append({"sink_argument": name, "expression": _expr(value).render(), "status": "partially_resolved", "trace": []})
        from .input_mapping import derive_input_mapping
        input_mappings = []
        for item in provenance[:4]:
            mapping = derive_input_mapping(
                item,
                harness=candidate.metadata.get("harness") if isinstance(candidate.metadata, dict) else None,
                sink_argument=str(item.get("sink_argument") or ""),
                sink_expression=str(item.get("expression") or ""),
                constraint="",
            )
            input_mappings.append(stable_value(mapping))
        requirements: list[dict[str, Any]] = []
        for path in paths:
            for order, item in enumerate(path.get("constraints", []), start=1):
                requirement = self._requirement_from_constraint(item, path.get("path_id", ""), order)
                if requirement["expression"] and requirement["expression"] not in {x["expression"] for x in requirements}:
                    requirements.append(requirement)
        trigger_conditions: list[dict[str, Any]] = []
        sink_result: dict[str, Any] = {"status": "not_run", "candidates": []}
        if target:
            from .sink_detector import analyze_sink_file_isolated
            sink_result = analyze_sink_file_isolated(
                self.root, target.file, sink_function=target.qualified_name,
                line=candidate.line, description=" ".join(filter(None, [candidate.category, candidate.reason])),
                timeout=2.0,
            )
            base_order = len(requirements) + 1
            for offset, item in enumerate(sink_result.get("candidates", [])):
                role = item.get("role", "trigger")
                if role not in {"trigger", "hazard", "reachability", "binding", "structure", "dispatch", "carrier", "avoid"}:
                    role = "trigger"
                sink_origin = dict(item.get("source_span") or {})
                sink_origin.setdefault("file", target.file)
                source_item = {
                    "expression": item.get("required_formula") or item.get("normalized_formula") or item.get("required_condition"),
                    "origin": sink_origin,
                    "reason": item.get("description") or item.get("origin"),
                    "role": role,
                    "gate_type": item.get("gate_type", "value_gate"),
                    "safe_formula": item.get("safe_formula", ""),
                    "violation_formula": item.get("violation_formula", ""),
                    "confidence": item.get("confidence_score", .5),
                    "status": "inferred",
                }
                req = self._requirement_from_constraint(source_item, paths[0]["path_id"] if paths else "sink_local", base_order + offset)
                if req["expression"] and req["expression"] not in {x["expression"] for x in requirements}:
                    requirements.append(req)
                if role in {"trigger", "hazard"}:
                    trigger_conditions.append(req)
        gaps: list[dict[str, Any]] = []
        if target is None:
            gaps.append({
                "id": "target_resolution_required", "reason": target_resolution.get("reason"),
                "candidate_symbol_ids": target_resolution.get("candidate_symbol_ids", []),
                "next_query": {"tool": "resolve_callsite_candidates", "arguments": {"callsite_id": target_resolution.get("callsite_id", "")}},
            })
        elif not paths:
            gaps.append({"id": "caller_path_required", "reason": "no_verified_entry_to_target_path", "next_query": {"tool": "find_callers", "arguments": {"symbol": target.symbol_id}}})
        if target and sink_result.get("status") == "partial" and sink_result.get("reason"):
            gaps.append({"id": "sink_semantics_partial", "reason": sink_result.get("reason"), "next_query": {"tool": "summarize_function", "arguments": {"symbol_id": target.symbol_id}}})
        version_key = candidate.candidate_id
        previous = self.store.get("brief_latest", version_key) or {}; version = int(previous.get("version", 0)) + 1
        full_id = f"analysis_{candidate.candidate_id}_v{version}"; brief_id = f"brief_{candidate.candidate_id}_v{version}"
        alternatives = self._expand_symbol_neighborhood(target.symbol_id, depth=3, limit=10) if target else []
        if candidate.metadata.get("description_derived") and target:
            current_row = next((item for item in self._navigation_rows(description=candidate.reason) if item["symbol_id"] == target.symbol_id), None)
            if current_row and current_row.get("evidence", {}).get("description_prior", 0) and current_row.get("score", 0) < .4:
                alternatives.insert(0, {
                    "symbol_id": target.symbol_id, "function": target.qualified_name,
                    "file": target.file, "line": target.body_location.start_line,
                    "diagnosis": "description_anchor", "score": current_row["score"],
                    "why_inspect": "description match is stronger than code-path evidence; validate before selecting",
                })
        mapped_requirements = []
        for mapping in input_mappings:
            if mapping.get("status") in {"confirmed", "inferred"}:
                req = {
                    "requirement_id": "req_" + mapping["mapping_id"],
                    "path_id": paths[0]["path_id"] if paths else "sink_local",
                    "order": len(requirements) + len(mapped_requirements) + 1,
                    "role": "dataflow",
                    "gate_type": "value_gate",
                    "expression": f"{mapping.get('sink_argument')} <- {mapping.get('source_parameter')}[{mapping.get('offset_expression') or '?'}]",
                    "safe_formula": "",
                    "violation_formula": "",
                    "input_mapping": mapping["mapping_id"],
                    "status": mapping.get("status", "inferred"),
                    "confidence": mapping.get("confidence", .5),
                    "origin": stable_value(mapping.get("evidence", [{}])[0]) if mapping.get("evidence") else {},
                    "reason": "sink argument has source-backed input byte mapping",
                }
                mapped_requirements.append(req)
        requirements.extend(mapped_requirements)
        full = {"candidate": stable_value(candidate), "target": stable_value(target) if target else {}, "target_resolution": target_resolution, "paths": paths, "requirements": requirements, "trigger_conditions": trigger_conditions, "sink_dataflow": provenance, "input_mappings": input_mappings, "gaps": gaps, "alternatives": alternatives}
        self.store.put("analysis", full_id, full)
        def _path_brief(p):
            chain_details = []
            for sid in p["symbol_ids"]:
                sym = next((s for s in self.symbols if s.symbol_id == sid), None)
                if sym:
                    chain_details.append({"function": sym.name, "file": sym.file, "line": sym.body_location.start_line})
                else:
                    chain_details.append({"function": sid, "file": "", "line": 0})
            return {"path_id": p["path_id"], "chain": [d["function"] for d in chain_details], "chain_details": chain_details, "confidence": p["score"]}
        path_briefs = [_path_brief(p) for p in paths[:self.config.automatic_top_paths]]
        suggested = ([{"tool": "get_path_details", "arguments": {"path_id": paths[0]["path_id"]}}] if paths else []) + ([{"tool": "trace_value", "arguments": {"function": target.symbol_id, "line": candidate.line, "expression": provenance[0]["expression"]}}] if target and provenance else [])
        if alternatives:
            suggested.append({"tool": "expand_candidate_neighborhood", "arguments": {"candidate_id": candidate.candidate_id, "depth": 3, "limit": 10}})
        brief_mappings = [
            item for item in input_mappings
            if item.get("status") in {"confirmed", "inferred"}
        ][:4] + [
            item for item in input_mappings
            if item.get("status") == "unresolved" and item.get("gaps")
        ][:2]
        brief = SinkAnalysisBrief(
            brief_id, candidate.candidate_id,
            "success" if target and (paths or trigger_conditions) and not gaps else "partial",
            {"symbol_id": target.symbol_id if target else "", "expression": candidate.expression or candidate.callee or candidate.function, "location": f"{candidate.file}:{candidate.line}"},
            path_briefs, requirements[:self.config.automatic_max_constraints], provenance,
            gaps[:3], suggested, {"call_chain": max((p["score"] for p in paths), default=0.0), "constraints": .8 if requirements else 0.0, "dataflow": .7 if provenance else 0.0},
            {"paths_truncated": path_result.get("truncated", False), "token_budget": self.config.automatic_token_budget}, full_id,
            target_resolution=target_resolution,
            requirements=requirements[:self.config.automatic_max_constraints],
            trigger_conditions=trigger_conditions[:6], gaps=gaps[:3],
            alternatives=alternatives[:5],
            input_mappings=brief_mappings,
        )
        brief.context_payload = self.render_brief(brief)
        while self._estimate_tokens(brief.context_payload) > self.config.automatic_token_budget and (brief.requirements or brief.gaps or brief.alternatives or len(brief.candidate_paths) > 1):
            if brief.gaps: brief.gaps.pop(); brief.unresolved = list(brief.gaps)
            elif brief.alternatives: brief.alternatives.pop()
            elif brief.requirements: brief.requirements.pop(); brief.key_constraints = list(brief.requirements)
            else: brief.candidate_paths.pop()
            brief.truncation["context_budget_reached"] = True; brief.context_payload = self.render_brief(brief)
        result = {"brief_id": brief.brief_id, "candidate_id": candidate.candidate_id, "status": brief.status, "context_payload": brief.context_payload, "full_result_id": full_id, "brief": stable_value(brief), "cache_hit": False}
        self.store.put("brief", brief.brief_id, result)
        self.store.put("brief_latest", version_key, {"version": version, "result": result}); self.store.put("candidate_fingerprint", fingerprint, result)
        return result

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return max(1, sum(1 if ord(c) > 127 else .25 for c in text))

    @staticmethod
    def render_brief(brief: SinkAnalysisBrief) -> str:
        from ..agent_impl.ir_renderer import IRRenderer
        # Build a plain dict from the brief for the renderer
        brief_data = {
            "target_resolution": brief.target_resolution if brief.target_resolution else None,
            "target": brief.target if not brief.target_resolution else None,
            "candidate_paths": list(brief.candidate_paths or []),
            "requirements": list(brief.requirements or []),
            "trigger_conditions": list(brief.trigger_conditions or []),
            "argument_provenance": list(brief.argument_provenance or []),
            "gaps": list(brief.gaps or []),
            "alternatives": list(brief.alternatives or []),
            "suggested_queries": list(brief.suggested_queries or []),
        }
        return IRRenderer.render_brief_xml(
            brief_data,
            brief_id=brief.brief_id,
            candidate_id=brief.candidate_id,
            status=brief.status,
        )
