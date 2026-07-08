"""Persistent analysis service used by automatic enrichment and query tools."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .indexer import index_file, index_file_isolated, resolve_calls, scan_files
from .models import (
    AnalysisPath, CallEdge, ConstraintIR,
    ExprIR, FunctionSummary, FunctionSymbol, RankedVulnerabilityPath,
    RiskSignal, SinkAnalysisBrief,
    SinkCandidateInput, SourceLocation, stable_value,
)
from .store import AnalysisStore, AnalysisConfig, ANALYSIS_VERSION, GRAMMAR_VERSION, _summary, _symbol
from .structured_bundle import StructuredBundleService
from .navigation_service import NavigationService, _description_value, _identifier_key, _expr_identifiers as _expr_identifiers_fn, _estimate_tokens as _estimate_tokens_fn
from .path_ranking import PathRankingService, _requirement_from_constraint as _requirement_from_constraint_fn, _target_resolution as _target_resolution_fn

_LOG = logging.getLogger(__name__)


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
        self._bundle = StructuredBundleService(self)
        self._navigation = NavigationService(self)
        self._ranking = PathRankingService(self, self._navigation)

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
        return _expr_identifiers_fn(expression)

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
        return _description_value(analysis, name)

    @staticmethod
    def _identifier_key(value: str) -> str:
        return _identifier_key(value)

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
        return self._ranking._resolve_target(candidate)

    @staticmethod
    def _target_resolution(
        candidate: SinkCandidateInput,
        target: FunctionSymbol,
        method: str,
        evidence: list[str],
    ) -> dict[str, Any]:
        return _target_resolution_fn(candidate, target, method, evidence)

    @staticmethod
    def _requirement_from_constraint(item: dict[str, Any], path_id: str, order: int) -> dict[str, Any]:
        return _requirement_from_constraint_fn(item, path_id, order)

    def _navigation_rows(self, *, description: str = "", focus_symbol_ids: set[str] | None = None, crash_type: str = "") -> list[dict[str, Any]]:
        return self._navigation._navigation_rows(description=description, focus_symbol_ids=focus_symbol_ids, crash_type=crash_type)

    @staticmethod
    def _diversify_navigation(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        return NavigationService._diversify_navigation(rows, limit)

    def discover_sink_navigation_leads(
        self, entrypoint: str | None = None, limit: int = 5,
        description: str = "", focus_symbol_ids: list[str] | None = None,
        crash_type: str = "",
    ) -> dict[str, Any]:
        return self._navigation.discover_sink_navigation_leads(
            entrypoint=entrypoint, limit=limit, description=description,
            focus_symbol_ids=focus_symbol_ids, crash_type=crash_type,
        )

    @staticmethod
    def _description_values(analysis: Any, field_name: str) -> list[str]:
        from .navigation_service import _description_values as _dv
        return _dv(analysis, field_name)

    def _selected_entry_ids(self, harness: dict[str, Any] | None) -> tuple[set[str], list[dict[str, Any]]]:
        return self._ranking._selected_entry_ids(harness)

    def _path_to_endpoint(self, entry_ids: set[str], endpoint_id: str, max_depth: int) -> tuple[list[str], list[CallEdge], str, list[dict[str, Any]]]:
        return self._ranking._path_to_endpoint(entry_ids, endpoint_id, max_depth)

    @staticmethod
    def _normalize_ranked_path_chain(
        entry_ids: set[str],
        endpoint_id: str,
        chain: list[str],
        status: str,
    ) -> tuple[list[str], str, list[dict[str, Any]], bool]:
        from .path_ranking import _normalize_ranked_path_chain as _nrpc
        return _nrpc(entry_ids, endpoint_id, chain, status)

    @staticmethod
    def _signal_kind(signal: RiskSignal | dict[str, Any] | None) -> str:
        from .path_ranking import _signal_kind as _sk
        return _sk(signal)

    @staticmethod
    def _event_role_for_signal(signal: RiskSignal | dict[str, Any] | None) -> str:
        from .path_ranking import _event_role_for_signal as _erfs
        return _erfs(signal)

    def _paired_endpoint_for_role(
        self,
        *,
        endpoint_id: str,
        needed_role: str,
        semantics_family: str,
        summaries: dict[str, FunctionSummary],
        by_id: dict[str, FunctionSymbol],
    ) -> dict[str, Any]:
        return self._ranking._paired_endpoint_for_role(
            endpoint_id=endpoint_id, needed_role=needed_role,
            semantics_family=semantics_family, summaries=summaries, by_id=by_id,
        )

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
        return self._ranking.discover_ranked_vulnerability_paths(
            description_analysis=description_analysis, verified_refs=verified_refs,
            harness=harness, crash_type=crash_type, fast_depth=fast_depth,
            deep_depth=deep_depth, top_k=top_k,
        )

    def reachable_functions_from_entry(
        self, entrypoint: str = "", limit: int = 20,
        crash_type: str = "", description: str = "",
    ) -> dict[str, Any]:
        return self._ranking.reachable_functions_from_entry(
            entrypoint=entrypoint, limit=limit, crash_type=crash_type, description=description,
        )

    def get_sink_search_brief(self, brief_id: str) -> dict[str, Any]:
        return self._navigation.get_sink_search_brief(brief_id)

    def mark_navigation_lead_reviewed(self, lead_id: str, outcome: str) -> dict[str, Any]:
        return self._navigation.mark_navigation_lead_reviewed(lead_id, outcome)

    def _expand_symbol_neighborhood(self, symbol_id: str, depth: int = 3, limit: int = 10) -> list[dict[str, Any]]:
        return self._navigation._expand_symbol_neighborhood(symbol_id, depth, limit)

    def expand_candidate_neighborhood(self, candidate_id: str, depth: int = 3, limit: int = 10) -> dict[str, Any]:
        return self._navigation.expand_candidate_neighborhood(candidate_id, depth, limit)

    def analyze_read_context(
        self,
        file: str,
        start_line: int,
        end_line: int,
        *,
        brief: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._navigation.analyze_read_context(
            file, start_line, end_line, brief=brief,
        )

    def analyze_sink_candidate(self, candidate: SinkCandidateInput | dict[str, Any], mode: str = "automatic", budget_profile: str = "default") -> dict[str, Any]:
        return self._ranking.analyze_sink_candidate(candidate, mode, budget_profile)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        return _estimate_tokens_fn(text)

    @staticmethod
    def render_brief(brief: SinkAnalysisBrief) -> str:
        from .ir_renderer import IRRenderer
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

    # ------------------------------------------------------------------
    # Structured analysis bundle (delegate to StructuredBundleService)
    # ------------------------------------------------------------------

    def discover_structured_analysis_bundle(
        self,
        *,
        ranked_paths: list[dict[str, Any]],
        description_analysis: dict[str, Any] | None = None,
        harness: dict[str, Any] | None = None,
        crash_type: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Build mechanism/objective/transcript/provenance summaries for active paths."""
        return self._bundle.discover_structured_analysis_bundle(
            ranked_paths=ranked_paths,
            description_analysis=description_analysis,
            harness=harness,
            crash_type=crash_type,
            top_k=top_k,
        )
