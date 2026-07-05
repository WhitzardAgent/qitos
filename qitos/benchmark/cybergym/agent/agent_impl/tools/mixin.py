"""ToolMixin -- file-reading, searching, and shell tool methods for CyberGymAgent.

Extracted from agent.py to keep the tool surface in its own module while
preserving the same runtime behaviour via MRO.
"""

from __future__ import annotations

import fnmatch
import hashlib
import math
import os
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from qitos.core.tool import ToolPermission, tool

from ...tool_names import (
    BASH,
    CALLSITE_SEARCH,
    CORPUS_INSPECT,
    FILE_INFO,
    FIND_SYMBOLS,
    GLOB,
    GREP,
    HEX_VIEW,
    READ,
    REPO_MAP,
    STRUCT_PROBE,
    WRITE,
)
from ...context import CyberGymContextHistory
from ..core.constants import DEFAULT_READ_LINE_LIMIT, DEFAULT_READ_MAX_CHARS, POC_OUTPUT_DIR, POC_PLACEHOLDER_CHARS
from ..core.utils import (
    add_line_numbers_to_read_result as _add_line_numbers_to_read_result,
    sanitize_model_text as _sanitize_model_text,
    sanitize_tool_payload as _sanitize_tool_payload,
)

if TYPE_CHECKING:
    from ...state import CyberGymState


# ---------------------------------------------------------------------------
# Tool name constants (match the class-level attributes on CyberGymAgent)
# ---------------------------------------------------------------------------
READ_TOOL = READ
GREP_TOOL = GREP
GLOB_TOOL = GLOB
FIND_SYMBOLS_TOOL = FIND_SYMBOLS
CALLSITE_SEARCH_TOOL = CALLSITE_SEARCH
REPO_MAP_TOOL = REPO_MAP
FILE_INFO_TOOL = FILE_INFO
HEX_VIEW_TOOL = HEX_VIEW
STRUCT_PROBE_TOOL = STRUCT_PROBE
CORPUS_INSPECT_TOOL = CORPUS_INSPECT
WRITE_TOOL = WRITE
BASH_TOOL = BASH


class ToolMixin:
    """Mixin providing all @tool-decorated methods and their helper functions.

    Cross-mixin calls (``self._validate_tool_access``, ``self._display_path``,
    etc.) resolve via MRO on the final composite class.
    """

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _render_output(self, tool_name: str, payload: Any, runtime_context: Optional[Dict[str, Any]] = None) -> Any:
        """Store structured payload for reduce(); return rendered text for LLM.

        When ``TOOL_RENDERING_ENABLED`` is False (or payload is not a dict),
        returns the payload unchanged so the engine falls back to JSON
        serialization as before.

        When rendering is enabled, stores the original structured dict in
        the buffer (for ``_process_action_result``) and returns the rendered
        text string.  The string goes directly into the LLM context and the
        TUI — both see the same human-readable output.
        """
        from .render import render_tool_output, TOOL_RENDERING_ENABLED

        if not TOOL_RENDERING_ENABLED or not isinstance(payload, dict):
            return payload

        # Store original payload for _process_action_result
        action_id = None
        if isinstance(runtime_context, dict):
            action_id = runtime_context.get("action_id")
        if action_id:
            self._structured_output_buffer[action_id] = payload
        else:
            self._last_structured_output = (tool_name, payload)

        return render_tool_output(tool_name, payload)

    # ------------------------------------------------------------------
    # READ
    # ------------------------------------------------------------------

    @tool(
        name=READ_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def READ(
        self,
        path: str = "",
        offset: Optional[int] = None,
        limit: Optional[int] = None,
        line: Optional[int] = None,
        radius: Optional[int] = None,
        match_id: str = "",
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Read one file by path, or jump to a search hit by match_id.

        Use match_id from GREP/FindSymbols/CallsiteSearch results to jump
        directly to a hit with surrounding context — more precise than
        path+offset when following up on a search result.

        Combo: GREP/FindSymbols/CallsiteSearch → READ(match_id=...)

        :param path: Path relative to the workspace root.
        :param offset: Optional zero-based line offset for targeted reads on long files.
        :param limit: Optional number of lines to read when offset is used. Without
            offset/limit, READ returns a bounded first chunk to keep context small.
        :param line: Optional absolute line number to center the read around.
        :param radius: Optional line radius when line or match_id is used.
        :param match_id: Optional stable match id returned by GREP/FindSymbols/CallsiteSearch.
        :param blocking_question: Internal guard parameter — do not use.
        :param runtime_context: Runtime state provided by the engine.
        """
        if self._coding_tools is None:
            return self._render_output(self.READ_TOOL, {"status": "error", "message": "READ tool backend is unavailable", "path": path}, runtime_context)
        target = self._read_target_from_match_id(match_id, runtime_context)
        if target:
            path = str(target.get("path") or path or "")
            line = int(target.get("line_number") or line or 0) or None
        if not str(path or "").strip():
            return self._render_output(self.READ_TOOL, {"status": "error", "message": "path or match_id is required", "path": path, "match_id": match_id}, runtime_context)
        read_guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.READ_TOOL,
            action_verb="read more files",
        )
        if read_guard:
            return self._render_output(self.READ_TOOL, {
                "status": "error",
                "message": read_guard,
                "error_category": "candidate_required_guard",
                "path": path,
            }, runtime_context)
        if line is not None:
            center = max(1, int(line or 1))
            span = max(1, min(int(radius or 40), 500))
            start = max(0, center - span - 1)
            size = max(1, min(span * 2 + 1, 1000))
            result = _sanitize_tool_payload(self._coding_tools.read_file_range(
                path=path,
                offset=start,
                limit=size,
                runtime_context=runtime_context,
            ))
            if isinstance(result, dict):
                if match_id:
                    result["match_id"] = str(match_id)
                result["requested_line"] = center
                result["requested_radius"] = span
            payload = _add_line_numbers_to_read_result(result)
            self._annotate_read_payload(payload, self._state_from_runtime(runtime_context))
            return self._render_output(self.READ_TOOL, payload, runtime_context)
        if offset is not None or limit is not None:
            start = max(0, int(offset or 0))
            size = max(1, int(limit or 200))
            result = _sanitize_tool_payload(self._coding_tools.read_file_range(
                path=path,
                offset=start,
                limit=size,
                runtime_context=runtime_context,
            ))
            if isinstance(result, dict) and match_id:
                result["match_id"] = str(match_id)
            payload = _add_line_numbers_to_read_result(result)
            self._annotate_read_payload(payload, self._state_from_runtime(runtime_context))
            return self._render_output(self.READ_TOOL, payload, runtime_context)
        result = self._coding_tools.file_read_v2(
            path=path,
            offset=0,
            limit=DEFAULT_READ_LINE_LIMIT,
            max_chars=DEFAULT_READ_MAX_CHARS,
            runtime_context=runtime_context,
        )
        if result.get("status") == "success":
            result["default_bounded_read"] = True
            result["max_chars"] = DEFAULT_READ_MAX_CHARS
            if result.get("has_more") or result.get("truncated"):
                result["message"] = (
                    "READ(path) returned a bounded first chunk. Use "
                    "READ(path, offset=..., limit=...) for the specific next region."
                )
        payload = _add_line_numbers_to_read_result(_sanitize_tool_payload(result))
        self._annotate_read_payload(payload, self._state_from_runtime(runtime_context))
        return self._render_output(self.READ_TOOL, payload, runtime_context)

    # ------------------------------------------------------------------
    # GREP
    # ------------------------------------------------------------------

    @tool(
        name=GREP_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def GREP(
        self,
        pattern: str,
        path: str = ".",
        glob: str = "",
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        fixed: bool = False,
        case_sensitive: bool = True,
        # default is content mode so the LLM sees match previews directly
        output_mode: str = "content",
        type: str = "",
        context: int = 0,
        head_limit: int = 250,
        offset: int = 0,
        multiline: bool = False,
        max_matches: int = 0,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Search file contents. Each match includes a match_id for instant READ jumps.

        Combo: GREP → READ(match_id=...) to follow up on any hit without
        manually specifying path+offset. Also detects harness signals and
        path constraints from match content.

        :param pattern: Regex pattern to search for. Set fixed=true for literal text.
        :param path: File or directory to search, relative to the workspace root.
        :param glob: Optional include glob such as "*.c" or "src/**/*.h".
        :param include: Additional include globs.
        :param exclude: Exclude globs, for example ["build/**", "*.o"].
        :param fixed: Treat pattern as literal text.
        :param case_sensitive: Whether matching is case-sensitive.
        :param output_mode: Output mode: 'content' (default, shows matching lines), 'files_with_matches' (just file paths), or 'count'.
        :param type: Optional ripgrep file type filter such as "py", "c", "rust".
        :param context: Number of context lines before/after each match in content mode.
        :param head_limit: Limit result lines/entries after search. Set 0 for unlimited.
        :param offset: Skip the first N result lines/entries before applying head_limit.
        :param multiline: Enable ripgrep multiline mode.
        :param max_matches: Backward-compatible alias for head_limit.
        :param blocking_question: Internal guard parameter — do not use.
        """
        search_guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.GREP_TOOL,
            action_verb="run source searches",
        )
        if search_guard:
            return self._render_output(self.GREP_TOOL, {
                "status": "error",
                "message": search_guard,
                "error_category": "candidate_required_guard",
                "pattern": pattern,
                "path": path,
            }, runtime_context)
        if not str(pattern or "").strip():
            return self._render_output(self.GREP_TOOL, {"status": "error", "message": "pattern is required", "pattern": pattern}, runtime_context)
        root = self._resolve_workspace_search_path(path)
        if root is None:
            return self._render_output(self.GREP_TOOL, {
                "status": "error",
                "message": "path must stay inside the workspace",
                "path": path,
            }, runtime_context)
        include_globs = self._coerce_globs(glob, include)
        exclude_globs = self._coerce_globs("", exclude)
        mode = str(output_mode or "files_with_matches").strip()
        if mode not in {"files_with_matches", "content", "count"}:
            return self._render_output(self.GREP_TOOL, {
                "status": "error",
                "message": "output_mode must be one of: files_with_matches, content, count",
                "output_mode": output_mode,
            }, runtime_context)
        limit = int(max_matches or head_limit or 0)
        if limit < 0:
            limit = 250
        if limit:
            limit = min(limit, 1000)
        start_offset = max(0, int(offset or 0))
        # P32: raised context cap from 5 to 10 — multi-line conditionals,
        # switch statements, and function signatures often span 6+ lines.
        ctx = max(0, min(int(context or 0), 10))
        result = self._grep_with_rg(
            pattern=str(pattern),
            root=root,
            include_globs=include_globs,
            exclude_globs=exclude_globs,
            fixed=bool(fixed),
            case_sensitive=bool(case_sensitive),
            output_mode=mode,
            type_filter=str(type or "").strip(),
            context=ctx,
            head_limit=limit,
            offset=start_offset,
            multiline=bool(multiline),
        )
        if result is None:
            result = self._grep_with_python(
                pattern=str(pattern),
                root=root,
                include_globs=include_globs,
                exclude_globs=exclude_globs,
                fixed=bool(fixed),
                case_sensitive=bool(case_sensitive),
                output_mode=mode,
                type_filter=str(type or "").strip(),
                context=ctx,
                head_limit=limit,
                offset=start_offset,
                multiline=bool(multiline),
            )
        # ── GREP graph enrichment: file-level function summary
        state = self._state_from_runtime(runtime_context)
        svc = self._get_analysis_service(state) if state else None
        if svc and svc.index_status in {"GRAPH_READY", "PARTIAL_INDEX"}:
            matches = result.get("matches") or result.get("results") or []
            file_funcs: dict[str, list] = {}
            for sym in svc.symbols:
                file_funcs.setdefault(sym.file, []).append(sym)
            annotated = 0
            for item in matches[:20]:
                if annotated >= 5:
                    break
                fpath = str(item.get("path", "") or "").removeprefix("repo-vul/")
                funcs = file_funcs.get(fpath)
                if funcs and not item.get("graph_summary"):
                    reachable = sum(1 for f in funcs if f.symbol_id in svc.entry_paths)
                    item["graph_summary"] = f"{len(funcs)} funcs, {reachable} reachable"
                    annotated += 1
        self._annotate_grep_payload(result, state)
        self._remember_evidence_matches(runtime_context, result)
        return self._render_output(self.GREP_TOOL, _sanitize_tool_payload(result), runtime_context)

    # ------------------------------------------------------------------
    # GLOB
    # ------------------------------------------------------------------

    @tool(
        name=GLOB_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def GLOB(
        self,
        pattern: str,
        path: str = ".",
        max_results: int = 200,
        include_dirs: bool = False,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Find files or directories by path pattern inside the workspace.

        Combo: GLOB → READ to inspect matching files. Use GLOB to narrow
        down files before calling GREP or FindSymbols on specific paths.

        :param pattern: Glob pattern such as "*.c", "**/*fuzz*", or "repo-vul/**/*.h".
        :param path: Directory under the workspace to search.
        :param max_results: Maximum results to return.
        :param include_dirs: Include matching directories as well as files.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.GLOB_TOOL,
            action_verb="find files",
        )
        if guard:
            return self._render_output(self.GLOB_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "pattern": pattern,
                "path": path,
            }, runtime_context)
        raw_pattern = str(pattern or "").strip()
        if not raw_pattern:
            return self._render_output(self.GLOB_TOOL, {"status": "error", "message": "pattern is required", "pattern": pattern}, runtime_context)
        root = self._resolve_workspace_search_path(path)
        if root is None:
            return self._render_output(self.GLOB_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        if not root.exists():
            return self._render_output(self.GLOB_TOOL, {"status": "error", "message": "path does not exist", "path": self._display_search_path(root)}, runtime_context)
        limit = max(1, min(int(max_results or 200), 1000))
        matches: List[Dict[str, Any]] = []
        if root.is_file():
            candidates = [root]
        else:
            candidates = []
            for current, dirs, filenames in os.walk(root):
                dirs[:] = [name for name in dirs if not self._skip_evidence_dir(name)]
                current_path = Path(current)
                if include_dirs:
                    candidates.extend(current_path / name for name in dirs)
                candidates.extend(current_path / name for name in filenames)
                if len(candidates) >= limit * 20:
                    break
        for item in candidates:
            rel = self._display_search_path(item)
            if not (
                fnmatch.fnmatch(rel, raw_pattern)
                or fnmatch.fnmatch(item.name, raw_pattern)
                or item.match(raw_pattern)
            ):
                continue
            is_dir = item.is_dir()
            if is_dir and not include_dirs:
                continue
            entry: Dict[str, Any] = {
                "path": rel + ("/" if is_dir and not rel.endswith("/") else ""),
                "kind": "dir" if is_dir else "file",
            }
            if not is_dir:
                try:
                    entry["size"] = item.stat().st_size
                except OSError:
                    pass
            matches.append(entry)
        state = self._state_from_runtime(runtime_context)
        self._annotate_glob_entries(matches, state)
        limited = matches[:limit]
        return self._render_output(self.GLOB_TOOL, _sanitize_tool_payload(
            {
                "status": "success",
                "pattern": raw_pattern,
                "path": self._display_search_path(root),
                "result_count": len(limited),
                "truncated": len(matches) > len(limited),
                "matches": limited,
                "paths": [str(item.get("path") or "") for item in limited],
            }
        ), runtime_context)

    # ------------------------------------------------------------------
    # FindSymbols
    # ------------------------------------------------------------------

    @tool(
        name=FIND_SYMBOLS_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def FindSymbols(
        self,
        query: str,
        kind: str = "",
        path: str = "repo-vul",
        max_results: int = 50,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Find definitions of functions, macros, structs, enums, and constants.

        Returns the definition location, kind, and for functions the full
        signature — often enough to understand the API without a separate READ.

        Combo: FindSymbols → READ(match_id=...) when you need the function
        body, not just its signature. Use kind="function" to filter definitions
        only, then trace callers with CallsiteSearch.

        :param query: Symbol or substring to locate.
        :param kind: Optional kind filter: function, macro, struct, enum, constant, typedef, reference, or file.
        :param path: File or directory under the workspace.
        :param max_results: Maximum number of results to return.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.FIND_SYMBOLS_TOOL,
            action_verb=f"call {self.FIND_SYMBOLS_TOOL} before submitting",
        )
        if guard:
            return self._render_output(self.FIND_SYMBOLS_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "query": query,
                "path": path,
            }, runtime_context)
        root = self._resolve_workspace_search_path(path)
        if root is None:
            return self._render_output(self.FIND_SYMBOLS_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        needle = str(query or "").strip()
        if not needle:
            return self._render_output(self.FIND_SYMBOLS_TOOL, {"status": "error", "message": "query is required", "query": query}, runtime_context)
        kind_filter = str(kind or "").strip().lower()
        limit = max(1, min(int(max_results or 50), 200))
        results: List[Dict[str, Any]] = []

        # --- Try index-based lookup first ---
        idx = self._get_repo_index(runtime_context)
        if idx:
            from ..repo.index import lookup_symbol
            index_results = lookup_symbol(idx, needle, kind=kind_filter)
            if index_results:
                results.extend(index_results)

        # --- Fallback: line-by-line scan if index had no matches ---
        if not results:
            for file_path in self._iter_evidence_files(root, max_files=20_000):
                rel = self._display_search_path(file_path)
                if needle not in rel:
                    try:
                        text = file_path.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    lines = text.splitlines()
                else:
                    lines = []
                    results.append(
                        {
                            "path": rel,
                            "line_number": 0,
                            "kind": "file",
                            "preview": rel,
                            "score": 60,
                        }
                    )
                    if len(results) >= limit * 4:
                        break
                    continue
                for index, line in enumerate(lines, start=1):
                    if needle not in line:
                        continue
                    hit_kind, score = self._classify_symbol_line(needle, line)
                    if kind_filter and hit_kind != kind_filter:
                        continue
                    result_entry = {
                        "path": rel,
                        "line_number": index,
                        "kind": hit_kind,
                        "preview": line.strip(),
                        "score": score,
                    }
                    # Extract function signature for function results
                    if hit_kind == "function":
                        sig = self._extract_function_signature(line.strip())
                        if sig:
                            result_entry["signature"] = sig
                    results.append(result_entry)
                    if len(results) >= limit * 4:
                        break
                if len(results) >= limit * 4:
                    break
        results.sort(key=lambda item: (-int(item.get("score", 0)), str(item.get("path", "")), int(item.get("line_number", 0))))
        limited = results[:limit]
        limited = self._attach_read_refs_to_hits(limited)

        # ── Reachability tagging: mark top-5 functions as [REACHABLE]/[UNREACHABLE]
        state = self._state_from_runtime(runtime_context)
        svc = self._get_analysis_service(state) if state else None
        if svc is not None and getattr(svc, "index_status", "") in {"GRAPH_READY", "PARTIAL_INDEX"}:
            checked = 0
            for item in limited:
                if item.get("kind") != "function" or checked >= 5:
                    break
                name = str(item.get("name", "") or "")
                if name:
                    reachable = svc.is_reachable_from_entry(name)
                    if reachable is True:
                        item["reachable"] = True
                    elif reachable is False:
                        item["reachable"] = False
                    checked += 1

        # Build summary counts for quick overview
        summary = {
            "functions": sum(1 for r in limited if r.get("kind") == "function"),
            "structs": sum(1 for r in limited if r.get("kind") == "struct"),
            "enums": sum(1 for r in limited if r.get("kind") == "enum"),
            "macros": sum(1 for r in limited if r.get("kind") == "macro"),
            "typedefs": sum(1 for r in limited if r.get("kind") == "typedef"),
            "other": sum(1 for r in limited if r.get("kind") not in (
                "function", "struct", "enum", "macro", "typedef")),
            "top_names": [r.get("name", "") for r in limited[:5] if r.get("name")],
        }

        payload = {
            "status": "success",
            "query": needle,
            "kind": kind_filter,
            "path": self._display_search_path(root),
            "result_count": len(limited),
            "truncated": len(results) > len(limited),
            "results": [
                {key: value for key, value in item.items()
                 if key != "score"}
                for item in limited
            ],
            "summary": summary,
        }
        self._remember_evidence_matches(runtime_context, {"matches": payload["results"]})
        return self._render_output(self.FIND_SYMBOLS_TOOL, _sanitize_tool_payload(
            {
                **payload,
            }
        ), runtime_context)

    # ------------------------------------------------------------------
    # CallsiteSearch
    # ------------------------------------------------------------------

    @tool(
        name=CALLSITE_SEARCH_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def CallsiteSearch(
        self,
        symbol: str,
        path: str = "repo-vul",
        max_results: int = 80,
        include_definition: bool = True,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Find definitions and callsites for one function or macro symbol.

        Separates where a function is DEFINED from where it is CALLED.
        The callsites reveal the data-flow path into the function — critical
        for understanding how your PoC input reaches the vulnerable code.

        Combo: CallsiteSearch → READ(match_id=...) on both a definition and
        a callsite in parallel to trace the full input→crash chain.
        Use after FindSymbols to go from "where is this defined?" to
        "how does data reach this function?".
        For multi-hop caller chains across files, prefer find_callers
        instead — it uses the structural index and can trace deeper
        call hierarchies with confidence scores.

        :param symbol: Symbol name to trace.
        :param path: File or directory under the workspace.
        :param max_results: Maximum callsite results.
        :param include_definition: Include definition-like hits.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.CALLSITE_SEARCH_TOOL,
            action_verb=f"call {self.CALLSITE_SEARCH_TOOL} before submitting",
        )
        if guard:
            return self._render_output(self.CALLSITE_SEARCH_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "symbol": symbol,
                "path": path,
            }, runtime_context)
        root = self._resolve_workspace_search_path(path)
        if root is None:
            return self._render_output(self.CALLSITE_SEARCH_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        name = str(symbol or "").strip()
        if not name:
            return self._render_output(self.CALLSITE_SEARCH_TOOL, {"status": "error", "message": "symbol is required", "symbol": symbol}, runtime_context)
        limit = max(1, min(int(max_results or 80), 200))
        definitions: List[Dict[str, Any]] = []
        callsites: List[Dict[str, Any]] = []
        call_re = re.compile(rf"\b{re.escape(name)}\s*\(")
        for file_path in self._iter_evidence_files(root, max_files=20_000):
            try:
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            rel = self._display_search_path(file_path)
            for index, line in enumerate(lines, start=1):
                if name not in line or not call_re.search(line):
                    continue
                preview = line.strip()
                item = {
                    "path": rel,
                    "line_number": index,
                    "preview": preview,
                }
                if ToolMixin._is_definition_like_line(name, line):
                    if include_definition:
                        definitions.append(item)
                else:
                    callsites.append(item)
                if len(callsites) >= limit * 2:
                    break
            if len(callsites) >= limit * 2:
                break
        definitions = self._attach_read_refs_to_hits(definitions[: min(20, limit)])
        callsites = self._attach_read_refs_to_hits(callsites[:limit])
        suggestions = self._read_suggestions_for_hits(definitions + callsites)

        # --- New: index-based enhancements ---
        reverse_calls: List[Dict[str, Any]] = []
        call_chain_hint: List[str] = []
        indirect_callsites: List[Dict[str, Any]] = []

        idx = self._get_repo_index(runtime_context)
        if idx:
            from ..repo.index import reverse_call_lookup, trace_call_chain
            reverse_calls = reverse_call_lookup(idx, name)
            call_chain_hint = trace_call_chain(idx, name)

        # Detect function-pointer dispatch sites
        fp_re = re.compile(
            r'(\w+(?:\[.+?\])?(?:->|\.|-)>?\s*\w+(?:\[(?:\w+)\])?)\s*\('
        )
        for file_path in self._iter_evidence_files(root, max_files=5_000):
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if name not in text:
                continue
            rel = self._display_search_path(file_path)
            lines = text.splitlines()
            for idx_line, line in enumerate(lines, start=1):
                # Look for indirect dispatch patterns containing the target name nearby
                for m in fp_re.finditer(line):
                    dispatch_var = m.group(1)
                    # Check if the target name appears in surrounding context
                    context_start = max(0, idx_line - 3)
                    context_end = min(len(lines), idx_line + 3)
                    context = "\n".join(lines[context_start:context_end])
                    if name in context:
                        indirect_callsites.append({
                            "path": rel,
                            "line": idx_line,
                            "dispatch_var": dispatch_var,
                            "context": line.strip()[:120],
                        })
                        if len(indirect_callsites) >= 10:
                            break
                if len(indirect_callsites) >= 10:
                    break
            if len(indirect_callsites) >= 10:
                break

        payload = {
                "status": "success",
                "symbol": name,
                "path": self._display_search_path(root),
                "definition_count": len(definitions),
                "callsite_count": len(callsites),
                "definitions": definitions,
                "callsites": callsites,
                "next_read_suggestions": suggestions,
                "truncated": len(callsites) >= limit,
                # New fields
                "reverse_calls": reverse_calls[:10],
                "call_chain_hint": call_chain_hint[:5],
                "indirect_callsites": indirect_callsites[:5],
        }
        self._remember_evidence_matches(
            runtime_context,
            {"matches": [*definitions, *callsites]},
        )
        return self._render_output(self.CALLSITE_SEARCH_TOOL, _sanitize_tool_payload(payload), runtime_context)

    # ------------------------------------------------------------------
    # RepoMap
    # ------------------------------------------------------------------

    @tool(
        name=REPO_MAP_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def RepoMap(
        self,
        path: str = "repo-vul",
        max_entries: int = 120,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Summarize repository layout, harness files, corpus directories, and build files.

        Combo: RepoMap → GREP/FindSymbols on discovered harness or source
        paths. RepoMap gives you the map; use the other tools to drill into
        specific files. Start every task with RepoMap to orient quickly.

        :param path: Repository directory under the workspace.
        :param max_entries: Maximum entries per major section.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.REPO_MAP_TOOL,
            action_verb=f"call {self.REPO_MAP_TOOL} before submitting",
        )
        if guard:
            return self._render_output(self.REPO_MAP_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "path": path,
            }, runtime_context)
        root = self._resolve_workspace_search_path(path)
        if root is None:
            return self._render_output(self.REPO_MAP_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        if not root.exists():
            return self._render_output(self.REPO_MAP_TOOL, {"status": "error", "message": "path does not exist", "path": self._display_search_path(root)}, runtime_context)
        limit = max(10, min(int(max_entries or 120), 500))
        top_level = self._top_level_entries(root, limit=limit)
        source_roots = self._detect_source_roots(root, limit=limit)
        build_files: List[str] = []
        harness_files: List[str] = []
        for file_path in self._iter_evidence_files(root, max_files=30_000):
            rel = self._display_search_path(file_path)
            if self._is_build_file(file_path):
                build_files.append(rel)
            if self._is_likely_harness_file(file_path):
                harness_files.append(rel)
            if len(build_files) >= limit and len(harness_files) >= limit:
                break
        corpus_dirs = self._find_corpus_dirs(root, max_dirs=min(limit, 50), max_files_per_dir=5)

        # --- Build / reuse structural index ---
        idx = self._ensure_repo_index(root, runtime_context)
        harness_detail = []
        entry_point_signatures = {}
        format_parsers = []
        dispatch_tables = []
        include_chains = {}
        harness_resolution = {}
        harness_candidates = []

        if idx:
            from ..repo.index import find_format_parsers, find_dispatch_tables
            harness_detail = idx.get("harness_entries", [])
            for entry in harness_detail:
                sig = entry.get("signature", "")
                name = entry.get("entry_function", "")
                if name and sig:
                    entry_point_signatures[name] = sig
            format_parsers = find_format_parsers(idx)
            dispatch_tables = find_dispatch_tables(idx)
            # Include chains for harness files (depth 1)
            for entry in harness_detail[:5]:
                hpath = entry.get("path", "")
                finfo = idx.get("files", {}).get(hpath, {})
                incs = finfo.get("includes", [])
                if incs:
                    include_chains[hpath] = incs[:10]
            current_state = self._state_from_runtime(runtime_context)
            if current_state is not None:
                resolution = getattr(current_state, "harness_resolution", None)
                harness_resolution = {
                    "status": getattr(resolution, "status", "unresolved"),
                    "selected_candidate_id": getattr(resolution, "selected_candidate_id", ""),
                    "selected_binary": getattr(resolution, "selected_binary", ""),
                    "reasons": list(getattr(resolution, "reasons", []) or []),
                    "conflicts": list(getattr(resolution, "conflicts", []) or []),
                    "next_action": getattr(resolution, "next_action", ""),
                }
                harness_candidates = [
                    {
                        "candidate_id": item.candidate_id,
                        "binary_names": list(item.binary_names),
                        "source_path": item.source_path,
                        "entry_function": item.entry_function,
                        "line": item.line,
                        "reachable_symbols": list(item.reachable_symbols),
                        "status": item.status,
                    }
                    for item in list(getattr(current_state, "harness_candidates", []) or [])[:20]
                ]

        return self._render_output(self.REPO_MAP_TOOL, _sanitize_tool_payload(
            {
                "status": "success",
                "path": self._display_search_path(root),
                "top_level": top_level,
                "source_roots": source_roots,
                "harness_files": sorted(dict.fromkeys(harness_files))[:limit],
                "build_files": sorted(dict.fromkeys(build_files))[:limit],
                "corpus_dirs": corpus_dirs,
                "truncated": len(top_level) >= limit,
                # New fields from structural index
                "harness_detail": harness_detail[:5],
                "harness_candidates": harness_candidates,
                "harness_resolution": harness_resolution,
                "entry_point_signatures": entry_point_signatures,
                "format_parsers": format_parsers[:15],
                "dispatch_tables": dispatch_tables[:10],
                "include_chains": include_chains,
            }
        ), runtime_context)

    # ------------------------------------------------------------------
    # FileInfo
    # ------------------------------------------------------------------

    @tool(
        name=FILE_INFO_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def FileInfo(
        self,
        path: str,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Inspect one file's size, type, magic bytes, printable ratio, and entropy.

        Combo: FileInfo → HexView/StructProbe. Use FileInfo first to decide
        whether a file is text, binary, or compressed, then use HexView or
        StructProbe to inspect the relevant region.

        :param path: File path under the workspace.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.FILE_INFO_TOOL,
            action_verb=f"call {self.FILE_INFO_TOOL} before submitting",
        )
        if guard:
            return self._render_output(self.FILE_INFO_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "path": path,
            }, runtime_context)
        file_path = self._resolve_workspace_file_path(path)
        if file_path is None:
            return self._render_output(self.FILE_INFO_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        if not file_path.exists() or not file_path.is_file():
            return self._render_output(self.FILE_INFO_TOOL, {"status": "error", "message": "path must be an existing file", "path": self._display_search_path(file_path)}, runtime_context)
        return self._render_output(self.FILE_INFO_TOOL, _sanitize_tool_payload(self._file_info_payload(file_path)), runtime_context)

    # ------------------------------------------------------------------
    # HexView
    # ------------------------------------------------------------------

    @tool(
        name=HEX_VIEW_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def HexView(
        self,
        path: str,
        offset: int = 0,
        length: int = 256,
        width: int = 16,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Return a bounded hex and ASCII view of one file region.

        Combo: HexView → BASH. Use HexView to find the exact offset to
        mutate in a binary, then use BASH with Python or toolbox to write
        the patched candidate. Also use after StructProbe to verify raw bytes.

        :param path: File path under the workspace.
        :param offset: Byte offset.
        :param length: Number of bytes to read, capped to 4096.
        :param width: Bytes per row, from 8 to 32.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.HEX_VIEW_TOOL,
            action_verb=f"call {self.HEX_VIEW_TOOL} before submitting",
        )
        if guard:
            return self._render_output(self.HEX_VIEW_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "path": path,
            }, runtime_context)
        file_path = self._resolve_workspace_file_path(path)
        if file_path is None:
            return self._render_output(self.HEX_VIEW_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        if not file_path.exists() or not file_path.is_file():
            return self._render_output(self.HEX_VIEW_TOOL, {"status": "error", "message": "path must be an existing file", "path": self._display_search_path(file_path)}, runtime_context)
        file_size = file_path.stat().st_size
        start = max(0, int(offset or 0))
        size = max(1, min(int(length or 256), 4096))
        row_width = max(8, min(int(width or 16), 32))
        with file_path.open("rb") as fh:
            fh.seek(start)
            data = fh.read(size)
        content = self._format_hex_view(data, base_offset=start, width=row_width)
        return self._render_output(self.HEX_VIEW_TOOL, _sanitize_tool_payload(
            {
                "status": "success",
                "path": self._display_search_path(file_path),
                "offset": start,
                "length": len(data),
                "requested_length": size,
                "file_size": file_size,
                "width": row_width,
                "content": content,
                "has_more_before": start > 0,
                "has_more_after": start + len(data) < file_size,
                "truncated": len(data) < min(size, max(0, file_size - start)),
            }
        ), runtime_context)

    # ------------------------------------------------------------------
    # StructProbe
    # ------------------------------------------------------------------

    @tool(
        name=STRUCT_PROBE_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def StructProbe(
        self,
        path: str,
        offset: int = 0,
        formats: Optional[List[str]] = None,
        endian: str = "little",
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Decode small binary fields from one file at a byte offset.

        Combo: StructProbe → BASH. Use StructProbe to decode header fields
        (magic, size, offsets) of a seed file or candidate, then use BASH
        to construct a candidate with the correct field values. Pair with
        HexView to cross-check decoded values against raw bytes.

        :param path: File path under the workspace.
        :param offset: Byte offset to start decoding.
        :param formats: Sequential field formats — each entry is one of: u8, i8, u16, i16, u32, i32, u64, i64, bytes:N, cstring:N. At least one format required.
        :param endian: little or big.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.STRUCT_PROBE_TOOL,
            action_verb=f"call {self.STRUCT_PROBE_TOOL} before submitting",
        )
        if guard:
            return self._render_output(self.STRUCT_PROBE_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "path": path,
            }, runtime_context)
        file_path = self._resolve_workspace_file_path(path)
        if file_path is None:
            return self._render_output(self.STRUCT_PROBE_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        if not file_path.exists() or not file_path.is_file():
            return self._render_output(self.STRUCT_PROBE_TOOL, {"status": "error", "message": "path must be an existing file", "path": self._display_search_path(file_path)}, runtime_context)
        normalized_formats = [str(item or "").strip() for item in list(formats or []) if str(item or "").strip()]
        if not normalized_formats:
            return self._render_output(self.STRUCT_PROBE_TOOL, {"status": "error", "message": "formats must contain at least one field", "path": self._display_search_path(file_path)}, runtime_context)
        byte_order = str(endian or "little").strip().lower()
        if byte_order not in {"little", "big"}:
            return self._render_output(self.STRUCT_PROBE_TOOL, {"status": "error", "message": "endian must be little or big", "endian": endian}, runtime_context)
        data = file_path.read_bytes()
        cursor = max(0, int(offset or 0))
        fields: List[Dict[str, Any]] = []
        for raw_format in normalized_formats[:64]:
            parsed = self._decode_struct_field(data, cursor, raw_format, byte_order)
            if parsed.get("status") == "error":
                parsed["path"] = self._display_search_path(file_path)
                return self._render_output(self.STRUCT_PROBE_TOOL, parsed, runtime_context)
            fields.append(parsed)
            cursor += int(parsed.get("size", 0) or 0)
        return self._render_output(self.STRUCT_PROBE_TOOL, _sanitize_tool_payload(
            {
                "status": "success",
                "path": self._display_search_path(file_path),
                "offset": max(0, int(offset or 0)),
                "end_offset": cursor,
                "endian": byte_order,
                "fields": fields,
            }
        ), runtime_context)

    # ------------------------------------------------------------------
    # CorpusInspect
    # ------------------------------------------------------------------

    @tool(
        name=CORPUS_INSPECT_TOOL,
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        concurrency_safe=True,
    )
    def CorpusInspect(
        self,
        path: str = "repo-vul",
        max_dirs: int = 8,
        max_files_per_dir: int = 8,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Find likely seed/corpus directories and summarize small example files.

        Combo: CorpusInspect → HexView/StructProbe → BASH. Use CorpusInspect
        to locate seed inputs, then inspect their structure with HexView or
        StructProbe, then use BASH to write mutated variants as candidates.

        :param path: Repository directory under the workspace.
        :param max_dirs: Maximum corpus directories.
        :param max_files_per_dir: Maximum example files per directory.
        :param blocking_question: Internal guard parameter — do not use.
        """
        guard = self._validate_tool_access(
            runtime_context=runtime_context,
            tool_label=self.CORPUS_INSPECT_TOOL,
            action_verb=f"call {self.CORPUS_INSPECT_TOOL} before submitting",
        )
        if guard:
            return self._render_output(self.CORPUS_INSPECT_TOOL, {
                "status": "error",
                "message": guard,
                "error_category": "candidate_required_guard",
                "path": path,
            }, runtime_context)
        root = self._resolve_workspace_search_path(path)
        if root is None:
            return self._render_output(self.CORPUS_INSPECT_TOOL, {"status": "error", "message": "path must stay inside the workspace", "path": path}, runtime_context)
        if not root.exists():
            return self._render_output(self.CORPUS_INSPECT_TOOL, {"status": "error", "message": "path does not exist", "path": self._display_search_path(root)}, runtime_context)
        dirs = self._find_corpus_dirs(
            root,
            max_dirs=max(1, min(int(max_dirs or 8), 50)),
            max_files_per_dir=max(1, min(int(max_files_per_dir or 8), 50)),
        )
        return self._render_output(self.CORPUS_INSPECT_TOOL, _sanitize_tool_payload(
            {
                "status": "success",
                "path": self._display_search_path(root),
                "dir_count": len(dirs),
                "corpus_dirs": dirs,
            }
        ), runtime_context)

    # ------------------------------------------------------------------
    # WRITE
    # ------------------------------------------------------------------

    @tool(
        name=WRITE_TOOL,
        permissions=ToolPermission(filesystem_write=True),
    )
    def WRITE(
        self,
        path: str,
        content: str,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Write one file. Use this to create or overwrite a candidate or helper file.

        Combo: READ/HexView/StructProbe → WRITE. Use search/inspection tools
        first to understand what the candidate should contain, then WRITE it.
        For binary payloads, prefer BASH with Python instead.

        :param path: Path relative to the workspace root.
        :param content: Full file contents to write.
        :param runtime_context: Runtime state provided by the engine.
        """
        write_guard = self._validate_candidate_ready_non_submit(
            runtime_context=runtime_context,
            tool_name=self.WRITE_TOOL,
            path=path,
        )
        if write_guard:
            return self._render_output(self.WRITE_TOOL, {
                "status": "error",
                "message": write_guard,
                "error_category": "candidate_submit_ready_guard",
                "path": path,
            }, runtime_context)
        if self._coding_tools is None:
            return self._render_output(self.WRITE_TOOL, {"status": "error", "message": "WRITE tool backend is unavailable", "path": path}, runtime_context)
        result = self._coding_tools.write_file(
            path=path,
            content=content,
            runtime_context=runtime_context,
        )
        return self._render_output(self.WRITE_TOOL, _sanitize_tool_payload(result), runtime_context)

    # ------------------------------------------------------------------
    # BASH
    # ------------------------------------------------------------------

    @tool(
        name=BASH_TOOL,
        permissions=ToolPermission(command=True),
    )
    def BASH(
        self,
        command: str,
        blocking_question: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Execute one shell command in the workspace.

        Use this for python/cp/mkdir/xxd-style command execution, not for raw file reading or content search.

        Combo: HexView/StructProbe → BASH → submit_poc. After inspecting a
        seed file's structure, use BASH with Python to write a mutated candidate,
        then submit it. Also use BASH with toolbox to generate minimal carriers
        and patch specific offsets.

        :param command: Shell command to execute.
        :param blocking_question: Internal guard parameter — do not use.
        :param runtime_context: Runtime state provided by the engine.
        """
        if self._coding_tools is None:
            return self._render_output(self.BASH_TOOL, {"status": "error", "message": "BASH tool backend is unavailable", "command": command}, runtime_context)
        command_guard = self._validate_bash_command(
            runtime_context=runtime_context,
            command=command,
            blocking_question=blocking_question,
        )
        if command_guard:
            return self._render_output(self.BASH_TOOL, {
                "status": "error",
                "message": command_guard,
                "error_category": "candidate_required_guard",
                "command": command,
            }, runtime_context)
        run_bash = getattr(self._coding_tools, "_run_bash_command", None)
        if callable(run_bash):
            return self._render_output(self.BASH_TOOL, _sanitize_tool_payload(
                run_bash(
                    command=command,
                    allow_needs_review=True,
                    runtime_context=runtime_context,
                )
            ), runtime_context)
        return self._render_output(self.BASH_TOOL, _sanitize_tool_payload(self._coding_tools.run_command(
            command=command,
            runtime_context=runtime_context,
        )), runtime_context)

    # ==================================================================
    # Helper methods
    # ==================================================================

    @staticmethod
    def _read_target_from_match_id(
        match_id: str,
        runtime_context: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        key = str(match_id or "").strip()
        if not key or not isinstance(runtime_context, dict):
            return None
        state = runtime_context.get("state")
        metadata = getattr(state, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        matches = metadata.get("evidence_matches")
        if not isinstance(matches, dict):
            return None
        target = matches.get(key)
        return target if isinstance(target, dict) else None

    @staticmethod
    def _match_id_for(path: str, line_number: int, preview: str) -> str:
        raw = f"{path}:{int(line_number or 0)}:{preview}"
        digest = hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:12]
        return f"m_{digest}"

    def _enrich_search_matches(
        self,
        matches: List[Dict[str, Any]],
        *,
        pattern: str,
    ) -> List[Dict[str, Any]]:
        """Enrich raw grep matches with preview text and read-follow links.

        No heuristic classification or constraint extraction — GREP is a pure
        search tool.  Constraint maintenance is the LLM's responsibility via
        ``record_gate`` / ``record_chain_node``.
        """
        enriched: List[Dict[str, Any]] = []
        for item in matches:
            path = str(item.get("path") or "").strip()
            try:
                line_number = int(item.get("line_number") or 0)
            except Exception:
                line_number = 0
            line = str(item.get("line") or "")
            preview = " ".join(line.split())
            entry = dict(item)
            entry["preview"] = preview
            if path and line_number > 0:
                match_id = self._match_id_for(path, line_number, preview)
                entry["match_id"] = match_id
                entry["next_read"] = {
                    "tool": self.READ_TOOL,
                    "match_id": match_id,
                    "radius": 40,
                }
            enriched.append(entry)
        return enriched

    @staticmethod
    def _annotate_glob_entries(entries: List[Dict[str, Any]], state: Any) -> None:
        """Attach short static leads and reorder matches without dropping any."""
        if state is None or not entries:
            entries.sort(key=lambda entry: (entry.get("kind") != "dir", str(entry.get("path") or "")))
            return
        from ..static.tool_hints import AnnotatedHit, annotate_file_path, hint_to_dict, rank_annotated_hits

        by_identity: Dict[int, Dict[str, Any]] = {}
        annotated_hits: List[AnnotatedHit] = []
        for entry in entries:
            hints = annotate_file_path(state, str(entry.get("path") or ""))
            hit = AnnotatedHit(
                path=str(entry.get("path") or ""),
                text=str(entry.get("kind") or ""),
                hints=hints,
            )
            by_identity[id(hit)] = entry
            annotated_hits.append(hit)
        ranked = rank_annotated_hits(annotated_hits)
        ordered: List[Dict[str, Any]] = []
        for hit in ranked:
            entry = by_identity[id(hit)]
            if hit.hints:
                entry["static_hints"] = [hint_to_dict(item) for item in hit.hints[:2]]
                entry["static_score"] = hit.score
            ordered.append(entry)
        entries[:] = ordered

    @staticmethod
    def _annotate_grep_payload(result: Any, state: Any) -> None:
        """Rerank returned GREP hits using state-backed static leads."""
        if state is None or not isinstance(result, dict) or result.get("status") != "success":
            return
        from ..static.tool_hints import AnnotatedHit, annotate_file_path, annotate_text_hit, hint_to_dict, rank_annotated_hits

        matches = result.get("matches")
        if isinstance(matches, list) and matches:
            by_identity: Dict[int, Dict[str, Any]] = {}
            annotated_hits: List[AnnotatedHit] = []
            for item in matches:
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "")
                line = int(item.get("line_number") or 0)
                text = str(item.get("preview") or item.get("line") or "")
                hints = annotate_text_hit(state, path, line, text)
                hit = AnnotatedHit(path=path, line=line, text=text, hints=hints)
                by_identity[id(hit)] = item
                annotated_hits.append(hit)
            ranked = rank_annotated_hits(annotated_hits)
            ordered: List[Dict[str, Any]] = []
            for hit in ranked:
                item = by_identity[id(hit)]
                if hit.hints:
                    item["static_hints"] = [hint_to_dict(hint) for hint in hit.hints[:2]]
                    item["static_score"] = hit.score
                ordered.append(item)
            result["matches"] = ordered
            return

        filenames = list(result.get("filenames") or [])
        if not filenames:
            return
        file_hits = [
            AnnotatedHit(path=str(path), hints=annotate_file_path(state, str(path)))
            for path in filenames
        ]
        ranked_files = rank_annotated_hits(file_hits)
        result["filenames"] = [hit.path for hit in ranked_files]
        result["file_matches"] = [
            {
                "path": hit.path,
                "static_score": hit.score,
                "static_hints": [hint_to_dict(hint) for hint in hit.hints[:2]],
            }
            for hit in ranked_files
        ]

    @staticmethod
    def _annotate_read_payload(payload: Any, state: Any) -> None:
        """Add a bounded Static context block to a successful READ payload."""
        if state is None or not isinstance(payload, dict) or payload.get("status") != "success":
            return
        from ..static.tool_hints import annotate_read_region, hint_to_dict

        path = str(payload.get("path") or "")
        if not path:
            return
        offset = int(payload.get("offset") or 0)
        content = str(payload.get("content") or "")
        line_count = len(content.splitlines())
        if content.startswith("// Lines "):
            line_count = max(0, line_count - 1)
        start = offset + 1
        end = max(start, offset + max(1, line_count))
        hints = annotate_read_region(state, path, start, end, content)
        if hints:
            payload["static_context"] = [hint_to_dict(item) for item in hints[:5]]

    @staticmethod
    def _remember_evidence_matches(
        runtime_context: Optional[Dict[str, Any]],
        result: Any,
    ) -> None:
        if not isinstance(runtime_context, dict) or not isinstance(result, dict):
            return
        state = runtime_context.get("state")
        metadata = getattr(state, "metadata", None)
        if not isinstance(metadata, dict):
            return
        matches = result.get("matches")
        if not isinstance(matches, list):
            return
        store = metadata.setdefault("evidence_matches", {})
        if not isinstance(store, dict):
            store = {}
            metadata["evidence_matches"] = store
        for item in matches:
            if not isinstance(item, dict):
                continue
            match_id = str(item.get("match_id") or "").strip()
            path = str(item.get("path") or "").strip()
            if not match_id or not path:
                continue
            store[match_id] = {
                "path": path,
                "line_number": int(item.get("line_number") or 0),
                "preview": str(item.get("preview") or item.get("line") or ""),
                "kind": str(item.get("kind") or ""),
            }
        if len(store) > 200:
            trimmed = list(store.items())[-200:]
            metadata["evidence_matches"] = dict(trimmed)

    @staticmethod
    def _capture_glob_metrics(state: CyberGymState, output: Any) -> None:
        if not isinstance(output, dict):
            return
        metrics = state.metadata.setdefault("aci_metrics", {})
        if isinstance(metrics, dict):
            metrics["glob_success_count"] = int(metrics.get("glob_success_count", 0) or 0) + (
                1 if output.get("status") == "success" else 0
            )

    @staticmethod
    def _track_match_read_follow(state: CyberGymState, output: Any) -> None:
        if not isinstance(output, dict):
            return
        match_id = str(output.get("match_id") or "").strip()
        if not match_id:
            return
        metrics = state.metadata.setdefault("aci_metrics", {})
        if isinstance(metrics, dict):
            metrics["grep_read_follow_count"] = int(metrics.get("grep_read_follow_count", 0) or 0) + 1
            followed = metrics.setdefault("followed_match_ids", [])
            if isinstance(followed, list) and match_id not in followed:
                followed.append(match_id)

    def _resolve_workspace_search_path(self, raw_path: str) -> Optional[Path]:
        workspace = Path(self.workspace_root).resolve()
        path = Path(str(raw_path or "."))
        if not path.is_absolute():
            path = workspace / path
        try:
            resolved = path.resolve(strict=False)
            resolved.relative_to(workspace)
        except Exception:
            # Symlinks inside workspace may resolve outside (e.g. level1_tasks/
            # symlinks to original repo-vul).  If the *unresolved* path is
            # inside the workspace, allow it — the symlink is intentional.
            try:
                unresolved = Path(self.workspace_root) / (raw_path or ".")
                unresolved.relative_to(Path(self.workspace_root))
                # Unresolved path is inside workspace; return the resolved
                # path so tools can actually read the files.
                return resolved
            except Exception:
                return None
        return resolved

    def _resolve_workspace_file_path(self, raw_path: str) -> Optional[Path]:
        path = self._resolve_workspace_search_path(raw_path)
        if path is None:
            return None
        return path

    @staticmethod
    def _skip_evidence_dir(name: str) -> bool:
        return str(name or "") in {
            ".git",
            ".svn",
            ".hg",
            ".bzr",
            ".jj",
            ".sl",
            ".agent",
            "__pycache__",
            "node_modules",
            ".mypy_cache",
            ".pytest_cache",
            "build",
            "cmake-build-debug",
            "cmake-build-release",
            "dist",
            "target",
        }

    @staticmethod
    def _likely_text_source_path(path: Path) -> bool:
        suffix = path.suffix.lower()
        if suffix in {
            ".c",
            ".h",
            ".cc",
            ".cpp",
            ".cxx",
            ".hh",
            ".hpp",
            ".hxx",
            ".rs",
            ".go",
            ".java",
            ".py",
            ".js",
            ".ts",
            ".php",
            ".rb",
            ".pl",
            ".pm",
            ".sh",
            ".s",
            ".asm",
            ".inc",
            ".m",
            ".mm",
            ".swift",
            ".proto",
            ".fbs",
            ".thrift",
            ".txt",
            ".md",
            ".cmake",
        }:
            return True
        return path.name in {
            "Makefile",
            "CMakeLists.txt",
            "configure.ac",
            "configure.in",
            "meson.build",
            "build.sh",
        }

    def _iter_evidence_files(self, root: Path, *, max_files: int = 20_000) -> List[Path]:
        if root.is_file():
            return [root] if self._likely_text_source_path(root) else []
        files: List[Path] = []
        for current, dirs, filenames in os.walk(root):
            dirs[:] = [name for name in dirs if not self._skip_evidence_dir(name)]
            for filename in filenames:
                path = Path(current) / filename
                if not self._likely_text_source_path(path):
                    continue
                files.append(path)
                if len(files) >= max_files:
                    return files
        return files

    @staticmethod
    def _classify_symbol_line(query: str, line: str) -> tuple[str, int]:
        text = str(line or "").strip()
        symbol = re.escape(str(query or ""))
        if re.search(rf"^\s*#\s*define\s+{symbol}\b", text):
            return "macro", 100
        if re.search(rf"\b(struct|class|union)\s+{symbol}\b", text):
            return "struct", 95
        if re.search(rf"\benum\s+{symbol}\b", text):
            return "enum", 95
        if ToolMixin._is_definition_like_line(str(query or ""), text):
            return "function", 90
        if re.search(rf"\b{symbol}\b\s*=", text):
            return "constant", 70
        return "reference", 40

    @staticmethod
    def _is_definition_like_line(symbol: str, line: str) -> bool:
        text = str(line or "").strip()
        if not text or text.startswith(("//", "/*", "*")):
            return False
        name = re.escape(str(symbol or ""))
        if re.search(rf"^\s*#\s*define\s+{name}\b", text):
            return True
        if text.endswith(";"):
            return False
        if re.search(rf"\b(if|for|while|switch|return)\s*\([^)]*\b{name}\s*\(", text):
            return False
        return bool(re.search(rf"\b{name}\s*\([^;]*\)\s*(?:\{{|$)", text))

    @staticmethod
    def _extract_function_signature(line: str) -> str:
        """Extract a C/C++ function signature from a source line.

        Returns the signature string (return_type name(params)) or "".
        """
        text = str(line or "").strip()
        if not text or text.startswith(("//", "/*", "*", "#")):
            return ""
        # Match: [static] [inline] return_type name(params) [{]
        m = re.match(
            r"^\s*(?:(?:static|inline|extern|virtual|const)\s+)*"
            r"([\w][\w\s*]*?)\s+"        # return type (greedy but backtracks)
            r"(\b\w+\b)\s*"              # function name
            r"\(([^)]*)\)",              # params
            text,
        )
        if not m:
            return ""
        ret = m.group(1).strip()
        name = m.group(2).strip()
        params = m.group(3).strip()
        # Reject control-flow false positives
        if name in ("if", "for", "while", "switch", "return", "sizeof", "elif"):
            return ""
        return f"{ret} {name}({params})"

    @staticmethod
    def _read_suggestions_for_hits(hits: List[Dict[str, Any]]) -> List[str]:
        suggestions: List[str] = []
        seen: set[tuple[str, int]] = set()
        for item in hits:
            path = str(item.get("path") or "")
            line_number = int(item.get("line_number") or 0)
            if not path or line_number <= 0:
                continue
            offset = max(0, line_number - 8)
            key = (path, offset)
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(f"READ({path!r}, offset={offset}, limit=40)")
            if len(suggestions) >= 6:
                break
        return suggestions

    def _attach_read_refs_to_hits(self, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enriched: List[Dict[str, Any]] = []
        for item in hits:
            entry = dict(item)
            path = str(entry.get("path") or "").strip()
            try:
                line_number = int(entry.get("line_number") or 0)
            except Exception:
                line_number = 0
            preview = str(entry.get("preview") or entry.get("line") or "")
            if path and line_number > 0:
                match_id = self._match_id_for(path, line_number, preview)
                entry["match_id"] = match_id
                entry["next_read"] = {
                    "tool": self.READ_TOOL,
                    "match_id": match_id,
                    "radius": 40,
                }
            enriched.append(entry)
        return enriched

    def _top_level_entries(self, root: Path, *, limit: int) -> List[str]:
        if root.is_file():
            return [self._display_search_path(root)]
        entries: List[str] = []
        try:
            for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                suffix = "/" if item.is_dir() else ""
                entries.append(f"{self._display_search_path(item)}{suffix}")
                if len(entries) >= limit:
                    break
        except OSError:
            pass
        return entries

    def _detect_source_roots(self, root: Path, *, limit: int) -> List[Dict[str, Any]]:
        candidates: Dict[str, int] = {}
        for file_path in self._iter_evidence_files(root, max_files=30_000):
            rel_parent = self._display_search_path(file_path.parent)
            parts = Path(rel_parent).parts
            if len(parts) >= 2:
                key = str(Path(*parts[: min(3, len(parts))]))
            else:
                key = rel_parent
            candidates[key] = candidates.get(key, 0) + 1
        ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))
        return [
            {"path": path, "source_file_count": count}
            for path, count in ranked[:limit]
        ]

    @staticmethod
    def _is_build_file(path: Path) -> bool:
        name = path.name
        return name in {
            "CMakeLists.txt",
            "Makefile",
            "configure",
            "configure.ac",
            "configure.in",
            "meson.build",
            "BUILD",
            "BUILD.bazel",
            "WORKSPACE",
            "Cargo.toml",
            "go.mod",
            "pom.xml",
            "build.gradle",
            "build.sh",
        }

    def _is_likely_harness_file(self, path: Path) -> bool:
        lower_name = path.name.lower()
        if "fuzz" in lower_name or "harness" in lower_name:
            return True
        if not self._likely_text_source_path(path):
            return False
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:12000]
        except OSError:
            return False
        return any(
            marker in text
            for marker in (
                "LLVMFuzzerTestOneInput",
                "FuzzedDataProvider",
                "libfuzzer",
                "fuzz_",
                "FUZZ_TARGET",
            )
        )

    @staticmethod
    def _looks_like_corpus_dir(path: Path) -> bool:
        name = path.name.lower()
        if name in {"corpus", "seeds", "seed", "testcases", "inputs"}:
            return True
        return "corpus" in name or "seed" in name or "fuzz_" in name

    def _ensure_repo_index(
        self,
        root: Path,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build or return the cached structural repo index.

        The index is cached in ``state.metadata["repo_index_v2"]`` and
        invalidated when file mtimes change.  Returns ``None`` on failure.
        """
        state = self._state_from_runtime(runtime_context)
        if state is None:
            return None

        cached = state.metadata.get("repo_index_v2")
        if isinstance(cached, dict):
            # Check freshness
            from ..repo.index import _compute_fingerprint, _gather_source_files
            current_fp = _compute_fingerprint(_gather_source_files(root, 200))
            if cached.get("_fingerprint") == current_fp:
                return cached

        # Build fresh index
        try:
            from ..repo.index import build_repo_index
            idx = build_repo_index(root)
            state.metadata["repo_index_v2"] = idx
            repo_root_text = str(getattr(state, "repo_dir", "") or "")
            repo_root = Path(repo_root_text) if repo_root_text else None
            if repo_root is not None and repo_root.exists() and root.resolve() == repo_root.resolve():
                target_records = self._discover_fuzzer_targets(str(repo_root))
                state.metadata["fuzzer_targets"] = target_records
                state.harness_candidates = self._build_harness_candidates(
                    idx, target_records, list(state.submit_harness_targets or []),
                )
                self._resolve_harness_candidates(state, idx)
                state.likely_fuzz_targets = sorted({
                    name
                    for candidate in state.harness_candidates
                    for name in candidate.binary_names
                }, key=str.lower)[:12]
                state.input_format = self._build_input_format_model(state)
            return idx
        except Exception:
            return None

    def _get_repo_index(
        self,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the cached index if available (no build)."""
        state = self._state_from_runtime(runtime_context)
        if state is None:
            return None
        cached = state.metadata.get("repo_index_v2")
        return cached if isinstance(cached, dict) else None

    def _get_analysis_service(self, state: Any):
        """Return the cached AnalysisService for state's repo_dir, or None."""
        from ...analysis.service import AnalysisService
        repo = str(getattr(state, "repo_dir", "") or "")
        if not repo or not Path(repo).is_dir():
            return None
        services = getattr(self, "_static_analysis_services", None)
        if services is None:
            return None
        key = str(Path(repo).resolve())
        return services.get(key)

    @staticmethod
    def _state_from_runtime(runtime_context: Optional[Dict[str, Any]] = None):
        """Extract state from runtime context if available."""
        if not runtime_context:
            return None
        # The state is typically passed via the runtime_context by the QitOS engine
        return runtime_context.get("state")

    def _find_corpus_dirs(
        self,
        root: Path,
        *,
        max_dirs: int,
        max_files_per_dir: int,
    ) -> List[Dict[str, Any]]:
        found: List[Dict[str, Any]] = []
        if root.is_file():
            return found
        for current, dirs, filenames in os.walk(root):
            dirs[:] = [name for name in dirs if not self._skip_evidence_dir(name)]
            current_path = Path(current)
            if not self._looks_like_corpus_dir(current_path):
                continue
            files = [
                current_path / name
                for name in filenames
                if (current_path / name).is_file()
            ]
            files.sort(key=lambda p: (p.stat().st_size if p.exists() else 0, p.name))
            examples = [
                self._small_file_summary(path)
                for path in files[:max_files_per_dir]
            ]
            found.append(
                {
                    "path": self._display_search_path(current_path),
                    "file_count": len(files),
                    "files": examples,
                }
            )
            if len(found) >= max_dirs:
                break
        found.sort(key=lambda item: str(item.get("path", "")))
        return found

    def _small_file_summary(self, path: Path) -> Dict[str, Any]:
        try:
            size = path.stat().st_size
            first = path.read_bytes()[:16]
        except OSError:
            size = 0
            first = b""
        return {
            "path": self._display_search_path(path),
            "size": size,
            "magic_hex": first.hex(),
            "magic_guess": self._guess_magic(first),
            "suggested_hex_view": f"HexView({self._display_search_path(path)!r}, offset=0, length=128)",
        }

    def _file_info_payload(self, file_path: Path) -> Dict[str, Any]:
        size = file_path.stat().st_size
        sample = file_path.read_bytes()[:4096]
        first = sample[:16]
        file_type = ""
        try:
            completed = subprocess.run(
                ["file", "-b", str(file_path)],
                cwd=self.workspace_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if completed.returncode == 0:
                file_type = completed.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            file_type = ""
        return {
            "status": "success",
            "path": self._display_search_path(file_path),
            "size": size,
            "file_type": file_type,
            "magic_hex": first.hex(),
            "magic_ascii": "".join(chr(b) if 32 <= b < 127 else "." for b in first),
            "magic_guess": self._guess_magic(first),
            "printable_ratio": self._printable_ratio(sample),
            "entropy": self._byte_entropy(sample),
            "suggested_hex_view": f"HexView({self._display_search_path(file_path)!r}, offset=0, length=256)",
        }

    @staticmethod
    def _guess_magic(data: bytes) -> str:
        checks = (
            (b"\x7fELF", "ELF"),
            (b"%PDF", "PDF"),
            (b"\xff\xd8\xff", "JPEG"),
            (b"\x89PNG\r\n\x1a\n", "PNG"),
            (b"GIF87a", "GIF"),
            (b"GIF89a", "GIF"),
            (b"II*\x00", "TIFF little-endian"),
            (b"MM\x00*", "TIFF big-endian"),
            (b"PK\x03\x04", "ZIP"),
            (b"\x1f\x8b", "gzip"),
            (b"BZh", "bzip2"),
            (b"\xfd7zXZ\x00", "xz"),
            (b"CRAM", "CRAM"),
            (b"PAR1", "Parquet"),
            (b"\xd4\xc3\xb2\xa1", "pcap little-endian"),
            (b"\xa1\xb2\xc3\xd4", "pcap big-endian"),
            (b"MZ", "PE/MZ"),
        )
        for prefix, name in checks:
            if data.startswith(prefix):
                return name
        if not data:
            return "empty"
        return "unknown"

    @staticmethod
    def _printable_ratio(data: bytes) -> float:
        if not data:
            return 0.0
        printable = sum(1 for b in data if b in (9, 10, 13) or 32 <= b < 127)
        return round(printable / len(data), 3)

    @staticmethod
    def _byte_entropy(data: bytes) -> float:
        if not data:
            return 0.0
        counts = [0] * 256
        for byte in data:
            counts[byte] += 1
        total = len(data)
        entropy = 0.0
        for count in counts:
            if count:
                p = count / total
                entropy -= p * math.log2(p)
        return round(entropy, 3)

    @staticmethod
    def _format_hex_view(data: bytes, *, base_offset: int, width: int) -> str:
        lines: List[str] = []
        for row_start in range(0, len(data), width):
            row = data[row_start:row_start + width]
            left = " ".join(f"{byte:02x}" for byte in row)
            if width == 16 and len(row) > 8:
                left = " ".join(
                    [
                        " ".join(f"{byte:02x}" for byte in row[:8]),
                        " ".join(f"{byte:02x}" for byte in row[8:]),
                    ]
                )
            padded = left.ljust(width * 3 + (1 if width == 16 else 0) - 1)
            ascii_text = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in row)
            lines.append(f"{base_offset + row_start:08x}  {padded}  |{ascii_text}|")
        return "\n".join(lines)

    @staticmethod
    def _decode_struct_field(
        data: bytes,
        offset: int,
        raw_format: str,
        endian: str,
    ) -> Dict[str, Any]:
        fmt = str(raw_format or "").strip().lower()
        integer_sizes = {
            "u8": (1, False),
            "i8": (1, True),
            "u16": (2, False),
            "i16": (2, True),
            "u32": (4, False),
            "i32": (4, True),
            "u64": (8, False),
            "i64": (8, True),
        }
        if fmt in integer_sizes:
            size, signed = integer_sizes[fmt]
            if offset + size > len(data):
                return {"status": "error", "message": "field extends past end of file", "offset": offset, "format": raw_format}
            raw = data[offset:offset + size]
            value = int.from_bytes(raw, endian, signed=signed)
            return {
                "format": fmt,
                "offset": offset,
                "size": size,
                "value": value,
                "hex": raw.hex(),
            }
        match = re.match(r"^(bytes|cstring):(\d+)$", fmt)
        if match:
            kind, size_text = match.groups()
            size = max(0, min(int(size_text), 4096))
            if offset + size > len(data):
                return {"status": "error", "message": "field extends past end of file", "offset": offset, "format": raw_format}
            raw = data[offset:offset + size]
            value_bytes = raw.split(b"\x00", 1)[0] if kind == "cstring" else raw
            return {
                "format": fmt,
                "offset": offset,
                "size": size,
                "hex": value_bytes.hex(),
                "text": value_bytes.decode("utf-8", errors="replace"),
            }
        return {"status": "error", "message": "unsupported field format", "format": raw_format, "offset": offset}

    @staticmethod
    def _coerce_globs(primary: str, values: Optional[List[str]]) -> List[str]:
        globs: List[str] = []
        if str(primary or "").strip():
            globs.extend(ToolMixin._split_grep_globs(str(primary).strip()))
        if isinstance(values, list):
            for item in values:
                if str(item).strip():
                    globs.extend(ToolMixin._split_grep_globs(str(item).strip()))
        return globs

    @staticmethod
    def _split_grep_globs(raw: str) -> List[str]:
        patterns: List[str] = []
        for item in str(raw or "").split():
            if "{" in item and "}" in item:
                patterns.append(item)
            else:
                patterns.extend(part for part in item.split(",") if part)
        return patterns

    @staticmethod
    def _apply_head_limit(items: List[Any], head_limit: int, offset: int) -> tuple[List[Any], Optional[int], Optional[int], bool]:
        start = max(0, int(offset or 0))
        if head_limit == 0:
            sliced = items[start:]
            return sliced, None, start if start else None, False
        limit = max(1, int(head_limit or 250))
        sliced = items[start:start + limit]
        truncated = len(items) - start > limit
        return sliced, limit if truncated else None, start if start else None, truncated

    def _display_search_path(self, path: Path) -> str:
        workspace = Path(self.workspace_root).resolve()
        candidate = path if path.is_absolute() else workspace / path
        try:
            return str(candidate.resolve(strict=False).relative_to(workspace))
        except Exception:
            return str(path)

    def _grep_with_rg(
        self,
        *,
        pattern: str,
        root: Path,
        include_globs: List[str],
        exclude_globs: List[str],
        fixed: bool,
        case_sensitive: bool,
        output_mode: str,
        type_filter: str,
        context: int,
        head_limit: int,
        offset: int,
        multiline: bool,
    ) -> Optional[Dict[str, Any]]:
        command = [
            "rg",
            "--hidden",
            "--color",
            "never",
        ]
        for vcs_dir in (".git", ".svn", ".hg", ".bzr", ".jj", ".sl"):
            command.extend(["--glob", f"!{vcs_dir}"])
        # Exclude non-source runtime artifacts that pollute search results
        for artifact in (
            "render_events.jsonl", "events.jsonl", "agent_trace.jsonl",
            "summary_*.csv", "run_*.log",
        ):
            command.extend(["--glob", f"!{artifact}"])
        if output_mode == "files_with_matches":
            command.append("-l")
        elif output_mode == "count":
            command.append("-c")
        else:
            command.extend(["--line-number", "--with-filename"])
        if fixed:
            command.append("--fixed-strings")
        if not case_sensitive:
            command.append("--ignore-case")
        if multiline:
            command.extend(["-U", "--multiline-dotall"])
        if output_mode == "content" and context:
            command.extend(["-C", str(context)])
        for item in include_globs:
            command.extend(["--glob", item])
        for item in exclude_globs:
            command.extend(["--glob", f"!{item}"])
        if type_filter:
            command.extend(["--type", type_filter])
        if pattern.startswith("-"):
            command.extend(["-e", pattern])
        else:
            command.append(pattern)
        command.append(str(root))
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=min(max(10, self.shell_timeout), 60),
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        if completed.returncode not in (0, 1):
            return {
                "status": "error",
                "message": completed.stderr.strip()[:1000] or "rg failed",
                "pattern": pattern,
                "path": self._display_search_path(root),
                "backend": "rg",
            }
        raw_lines = [line for line in completed.stdout.splitlines() if line]
        if output_mode == "files_with_matches":
            sorted_paths = sorted(
                (Path(line) for line in raw_lines),
                key=lambda item: (-self._safe_mtime_ms(item), self._display_search_path(item)),
            )
            limited, applied_limit, applied_offset, truncated = self._apply_head_limit(
                sorted_paths,
                head_limit,
                offset,
            )
            filenames = [self._display_search_path(path) for path in limited]
            return self._grep_payload(
                backend="rg",
                mode=output_mode,
                pattern=pattern,
                root=root,
                filenames=filenames,
                applied_limit=applied_limit,
                applied_offset=applied_offset,
                truncated=truncated,
            )
        if output_mode == "count":
            limited, applied_limit, applied_offset, truncated = self._apply_head_limit(
                raw_lines,
                head_limit,
                offset,
            )
            final_lines: List[str] = []
            total_matches = 0
            for line in limited:
                rel_line, count = self._relativize_count_line(line)
                final_lines.append(rel_line)
                total_matches += count
            return self._grep_payload(
                backend="rg",
                mode=output_mode,
                pattern=pattern,
                root=root,
                content="\n".join(final_lines),
                num_matches=total_matches,
                num_files=len(final_lines),
                applied_limit=applied_limit,
                applied_offset=applied_offset,
                truncated=truncated,
            )
        limited, applied_limit, applied_offset, truncated = self._apply_head_limit(
            raw_lines,
            head_limit,
            offset,
        )
        final_lines = [self._relativize_content_line(line) for line in limited]
        matches = [item for item in (self._parse_content_match_line(line) for line in final_lines) if item]
        filenames = sorted({str(item["path"]) for item in matches})
        return self._grep_payload(
            backend="rg",
            mode=output_mode,
            pattern=pattern,
            root=root,
            filenames=filenames,
            content="\n".join(final_lines),
            matches=matches,
            applied_limit=applied_limit,
            applied_offset=applied_offset,
            truncated=truncated,
        )

    def _grep_payload(
        self,
        *,
        backend: str,
        mode: str,
        pattern: str,
        root: Path,
        filenames: Optional[List[str]] = None,
        content: str = "",
        matches: Optional[List[Dict[str, Any]]] = None,
        num_matches: Optional[int] = None,
        num_files: Optional[int] = None,
        applied_limit: Optional[int] = None,
        applied_offset: Optional[int] = None,
        truncated: bool = False,
    ) -> Dict[str, Any]:
        files = list(filenames or [])
        structured_matches = self._enrich_search_matches(list(matches or []), pattern=pattern)
        file_count = num_files if num_files is not None else len(files)
        match_count = num_matches if num_matches is not None else len(structured_matches)
        payload: Dict[str, Any] = {
            "status": "success",
            "backend": backend,
            "mode": mode,
            "pattern": pattern,
            "path": self._display_search_path(root),
            "numFiles": file_count,
            "filenames": files,
            "truncated": bool(truncated),
            "file_count": file_count,
            "match_count": match_count,
            "matches": structured_matches,
        }
        if mode == "content":
            if content:
                cleaned_lines = []
                for raw_line in content.split("\n"):
                    if not raw_line.strip():
                        continue
                    m = re.match(r"^(.*?)([:\-])(\d+)([:\-])(.*)$", raw_line)
                    if m:
                        _path_text, sep1, lnum, sep2, text = m.groups()
                        if sep1 == ":" and sep2 == ":":
                            cleaned_lines.append(f"L{lnum}: {text.strip()}")
                        else:
                            cleaned_lines.append(f"  {text.strip()}")
                    else:
                        cleaned_lines.append(raw_line.strip())
                content = "\n".join(cleaned_lines)
            payload["content"] = content
            payload["numLines"] = len(content.splitlines()) if content else 0
            payload["numMatches"] = match_count
        elif mode == "count":
            payload["content"] = content
            payload["numMatches"] = match_count
        if applied_limit is not None:
            payload["appliedLimit"] = applied_limit
        if applied_offset is not None:
            payload["appliedOffset"] = applied_offset
        return payload

    @staticmethod
    def _safe_mtime_ms(path: Path) -> float:
        try:
            return path.stat().st_mtime * 1000.0
        except OSError:
            return 0.0

    def _relativize_count_line(self, line: str) -> tuple[str, int]:
        path_text, sep, count_text = str(line).rpartition(":")
        if not sep:
            return line, 0
        rel = self._display_search_path(Path(path_text))
        try:
            count = int(count_text)
        except ValueError:
            count = 0
        return f"{rel}:{count_text}", count

    def _relativize_content_line(self, line: str) -> str:
        match = re.match(r"^(.*?)([:\-])(\d+)([:\-])(.*)$", str(line))
        if not match:
            return line
        path_text, first_sep, line_number, second_sep, text = match.groups()
        return f"{self._display_search_path(Path(path_text))}{first_sep}{line_number}{second_sep}{text}"

    @staticmethod
    def _parse_content_match_line(line: str) -> Optional[Dict[str, Any]]:
        path_text, sep, rest = str(line).partition(":")
        if not sep:
            return None
        line_number_text, sep, text = rest.partition(":")
        if not sep:
            return None
        try:
            line_number = int(line_number_text)
        except ValueError:
            return None
        return {
            "path": path_text,
            "line_number": line_number,
            "line": text,
        }

    @staticmethod
    def _file_matches_rg_type(path: Path, type_filter: str) -> bool:
        suffix = path.suffix.lower().lstrip(".")
        mapping = {
            "c": {"c", "h"},
            "cpp": {"cc", "cpp", "cxx", "hpp", "hh", "hxx"},
            "py": {"py", "pyi"},
            "python": {"py", "pyi"},
            "js": {"js", "jsx", "mjs", "cjs"},
            "ts": {"ts", "tsx"},
            "go": {"go"},
            "rust": {"rs"},
            "rs": {"rs"},
            "java": {"java"},
            "json": {"json"},
            "md": {"md", "markdown"},
            "txt": {"txt"},
            "sh": {"sh", "bash", "zsh"},
        }
        allowed = mapping.get(str(type_filter or "").lower())
        return suffix in allowed if allowed else suffix == str(type_filter or "").lower().lstrip(".")

    @staticmethod
    def _rg_path_text(data: Dict[str, Any]) -> str:
        path_obj = data.get("path") if isinstance(data, dict) else {}
        if isinstance(path_obj, dict):
            text = str(path_obj.get("text") or "")
        else:
            text = ""
        return text

    def _grep_with_python(
        self,
        *,
        pattern: str,
        root: Path,
        include_globs: List[str],
        exclude_globs: List[str],
        fixed: bool,
        case_sensitive: bool,
        output_mode: str,
        type_filter: str,
        context: int,
        head_limit: int,
        offset: int,
        multiline: bool,
    ) -> Dict[str, Any]:
        flags = 0 if case_sensitive else re.IGNORECASE
        if multiline:
            flags |= re.DOTALL
        try:
            regex = re.compile(re.escape(pattern) if fixed else pattern, flags)
        except re.error as exc:
            return {
                "status": "error",
                "message": f"invalid regex: {exc}",
                "pattern": pattern,
                "path": self._display_search_path(root),
                "backend": "python",
            }
        files = [root] if root.is_file() else [path for path in root.rglob("*") if path.is_file()]
        content_lines: List[str] = []
        matches: List[Dict[str, Any]] = []
        matched_files: set[str] = set()
        count_lines: List[str] = []
        for file_path in files:
            rel = self._display_search_path(file_path)
            if any(part in {".git", ".svn", ".hg", ".bzr", ".jj", ".sl"} for part in file_path.parts):
                continue
            # Skip non-source runtime artifacts
            _artifact_names = {"render_events.jsonl", "events.jsonl", "agent_trace.jsonl"}
            if file_path.name in _artifact_names:
                continue
            if type_filter and not self._file_matches_rg_type(file_path, type_filter):
                continue
            if include_globs and not any(fnmatch.fnmatch(rel, item) or file_path.match(item) for item in include_globs):
                continue
            if exclude_globs and any(fnmatch.fnmatch(rel, item) or file_path.match(item) for item in exclude_globs):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lines = text.splitlines()
            file_match_count = 0
            for index, line in enumerate(lines, start=1):
                found = list(regex.finditer(line))
                if not found:
                    continue
                matched_files.add(rel)
                file_match_count += len(found)
                item: Dict[str, Any] = {
                    "path": rel,
                    "line_number": index,
                    "line": line,
                    "columns": [
                        {"start": match.start() + 1, "end": match.end() + 1}
                        for match in found[:8]
                    ],
                }
                if context:
                    start = max(1, index - context)
                    end = min(len(lines), index + context)
                    item["context"] = [
                        {
                            "line_number": line_no,
                            "line": lines[line_no - 1],
                        }
                        for line_no in range(start, end + 1)
                        if line_no != index
                    ]
                matches.append(item)
                content_lines.append(f"{rel}:{index}:{line}")
            if file_match_count:
                count_lines.append(f"{rel}:{file_match_count}")
        if output_mode == "files_with_matches":
            sorted_files = sorted(
                matched_files,
                key=lambda item: (-self._safe_mtime_ms(Path(self.workspace_root) / item), item),
            )
            limited, applied_limit, applied_offset, truncated = self._apply_head_limit(
                sorted_files,
                head_limit,
                offset,
            )
            return self._grep_payload(
                backend="python",
                mode=output_mode,
                pattern=pattern,
                root=root,
                filenames=limited,
                applied_limit=applied_limit,
                applied_offset=applied_offset,
                truncated=truncated,
            )
        if output_mode == "count":
            limited, applied_limit, applied_offset, truncated = self._apply_head_limit(
                count_lines,
                head_limit,
                offset,
            )
            total_matches = sum(int(line.rsplit(":", 1)[-1]) for line in limited if line.rsplit(":", 1)[-1].isdigit())
            return self._grep_payload(
                backend="python",
                mode=output_mode,
                pattern=pattern,
                root=root,
                content="\n".join(limited),
                num_matches=total_matches,
                num_files=len(limited),
                applied_limit=applied_limit,
                applied_offset=applied_offset,
                truncated=truncated,
            )
        limited_lines, applied_limit, applied_offset, truncated = self._apply_head_limit(
            content_lines,
            head_limit,
            offset,
        )
        limited_matches = [item for item in (self._parse_content_match_line(line) for line in limited_lines) if item]
        return self._grep_payload(
            backend="python",
            mode=output_mode,
            pattern=pattern,
            root=root,
            filenames=sorted({str(item["path"]) for item in limited_matches}),
            content="\n".join(limited_lines),
            matches=limited_matches,
            applied_limit=applied_limit,
            applied_offset=applied_offset,
            truncated=truncated,
        )
