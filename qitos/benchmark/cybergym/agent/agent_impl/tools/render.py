"""Per-tool renderers that convert structured dicts to LLM-friendly text.

Each tool has its own renderer that extracts high-value signals and formats
them as human-readable text instead of raw JSON.  The QitOS engine has a
fast-path in ``_serialize_for_tool_message`` that passes strings through
unchanged, so returning a rendered string means the LLM gets intuitive text
instead of JSON.

Feature flag: ``CYBERGYM_TOOL_RENDERING=0`` disables all rendering.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

TOOL_RENDERING_ENABLED: bool = os.environ.get(
    "CYBERGYM_TOOL_RENDERING", "1"
).strip().lower() not in {"0", "false", "no", "off"}

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SANITIZER_RE = re.compile(
    r"(AddressSanitizer|MemorySanitizer|UndefinedBehaviorSanitizer)"
    r"|heap-buffer-overflow|stack-buffer-overflow|use-after-free"
    r"|heap-use-after-free|stack-use-after-scope|double-free"
    r"|out-of-bounds|undefined behavior",
    re.IGNORECASE,
)

_CRASH_TYPE_RE = re.compile(
    r"==\d+==ERROR:\s*(AddressSanitizer|MemorySanitizer|UndefinedBehaviorSanitizer)"
    r"[:\s]+(.+?)(?:\s+on\s+|\s+in\s+|\n)",
    re.IGNORECASE,
)

_CRASH_LOCATION_RE = re.compile(
    r"(?:READ|WRITE|ERROR|SUMMARY).*?\b(\S+\.\w+):(\d+)",
    re.IGNORECASE,
)


def _format_size(n: int) -> str:
    """Format byte count as human-readable string."""
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _header(tool: str, *parts: str) -> str:
    """Build a header line like ``[READ] src/parser.c lines 1-80``."""
    inner = "  ".join(p for p in parts if p)
    return f"[{tool}] {inner}"


def _call_header(tool: str, **params: Any) -> str:
    """Build a header like ``[READ("path", offset=0, limit=80)]``.

    Includes call parameters so that when multiple tools are called in
    parallel, the LLM can unambiguously correlate each result to its
    originating action.  Skips empty/zero/default params.
    """
    parts = []
    for k, v in params.items():
        if v is None or v == "" or v is False:
            continue
        # offset=0 is a valid, meaningful value for positional tools (READ, HexView)
        # so we never skip it.  Only skip numeric 0 for counters/limits that default to 0.
        if v == 0 and k not in ("offset",):
            continue
        if isinstance(v, str):
            display = v
            parts.append(f'{k}={display}')
        elif isinstance(v, bool):
            parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}={v}")
    param_str = ", ".join(parts)
    return f"[{tool}({param_str})]"


def _entropy_label(entropy: float) -> str:
    """Return a human interpretation of byte entropy."""
    if entropy < 3.0:
        return "likely text or structured data"
    if entropy < 5.5:
        return "likely structured binary"
    if entropy < 7.0:
        return "likely compressed or mixed"
    return "likely compressed/encrypted"


def _crash_summary(stderr: str) -> str:
    """Extract a one-line crash summary from sanitizer output."""
    if not stderr:
        return ""
    # Try to find crash type
    m = _CRASH_TYPE_RE.search(stderr)
    if m:
        sanitizer = m.group(1)
        crash_type = m.group(2).strip()
        # Try to find location
        loc_m = _CRASH_LOCATION_RE.search(stderr)
        location = ""
        if loc_m:
            location = f" at {loc_m.group(1)}:{loc_m.group(2)}"
        return f"{sanitizer}: {crash_type}{location}"
    # Fallback: first non-empty line that looks like a crash
    for line in stderr.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(kw in line.lower() for kw in ("error:", "signal", "segfault", "crash")):
            return line
    return ""


def _static_hint_text(hint: Dict[str, Any]) -> str:
    """Render one static-aware lead as one compact, non-authoritative line."""
    role = str(hint.get("role") or "unknown")
    confidence = float(hint.get("confidence") or 0.0)
    path_id = str(hint.get("path_id") or "")
    candidate_id = str(hint.get("candidate_id") or "")
    family = str(hint.get("family") or "")
    reasons = [str(item) for item in list(hint.get("reasons") or []) if str(item).strip()]
    next_actions = [str(item) for item in list(hint.get("next_actions") or []) if str(item).strip()]
    tags = [f"role={role}", f"score={confidence:.2f}"]
    if path_id:
        tags.append(f"path={path_id}")
    if candidate_id:
        tags.append(f"candidate={candidate_id}")
    if family:
        tags.append(f"family={family}")
    detail = reasons[0] if reasons else "state-backed navigation lead"
    if next_actions:
        detail += f"; next: {next_actions[0]}"
    return f"[static lead {' '.join(tags)}] {detail}"





# ---------------------------------------------------------------------------
# Error renderer (shared)
# ---------------------------------------------------------------------------

def render_error(payload: Dict[str, Any], tool_name: str = "") -> str:
    """Render an error result for any tool."""
    message = str(payload.get("message") or payload.get("error") or "unknown error")
    category = str(payload.get("error_category") or "")
    if category and category not in message:
        message = f"{category} -- {message}"
    label = tool_name if tool_name else "ERROR"
    # Find the identifying parameter for correlation
    params = {}
    for k in ("path", "pattern", "symbol", "query", "command", "poc_path"):
        v = payload.get(k)
        if v:
            params[k] = v
            break
    return _call_header(label, **params) + "\n  ERROR: " + message


# ---------------------------------------------------------------------------
# Per-tool renderers
# ---------------------------------------------------------------------------

def render_READ(payload: Dict[str, Any]) -> str:
    """Render READ tool output.

    High-value: path, line range, content (already cat -n formatted), continuation hint.
    """
    path = str(payload.get("path") or "")
    content = str(payload.get("content") or "")
    offset = int(payload.get("offset") or 0)
    limit = int(payload.get("limit") or 0)
    total_lines = int(payload.get("total_lines") or 0)
    has_more = payload.get("has_more") or payload.get("truncated")
    match_id = str(payload.get("match_id") or "")
    radius = payload.get("radius")
    message = str(payload.get("message") or "")

    # Count lines in content
    lines = content.splitlines()
    # Skip the header line added by add_line_numbers_to_read_result
    start_line = offset + 1
    end_line = offset + len(lines)
    if lines and lines[0].startswith("//"):
        end_line = offset + len(lines) - 1  # header line doesn't count

    # Build header with call parameters
    if match_id:
        hdr_params: Dict[str, Any] = {"match_id": match_id}
        if radius is not None:
            hdr_params["radius"] = int(radius)
        result_parts = [_call_header("read", **hdr_params)]
    else:
        hdr_params = {}
        if path:
            hdr_params["path"] = path
        if offset or limit:
            hdr_params["offset"] = offset
        if limit:
            hdr_params["limit"] = limit
        result_parts = [_call_header("read", **hdr_params)]

    # Line range summary after header
    range_str = f"lines {start_line}-{end_line}"
    if total_lines > 0:
        range_str += f" of {total_lines}"
    result_parts.append(f"  {range_str}")

    static_context = list(payload.get("static_context") or [])
    if static_context:
        result_parts.append("  Static context (navigation leads; verify in source):")
        for hint in static_context[:5]:
            if isinstance(hint, dict):
                result_parts.append(f"  - {_static_hint_text(hint)}")

    # Content (already formatted with line numbers by add_line_numbers_to_read_result)
    if content:
        result_parts.append(content)

    # Footer: continuation hint
    if has_more and total_lines > 0:
        remaining = total_lines - end_line
        if remaining > 0:
            result_parts.append(
                f"--- {remaining} more lines below. "
                f'Use READ("{path}", offset={end_line}) ---'
            )
    elif has_more:
        result_parts.append(
            f'--- More content below. Use READ("{path}", offset={end_line}) ---'
        )

    if message and "offset=" not in message and "bounded" not in message.lower():
        # Only include non-redundant messages
        pass  # Skip the default bounded read message

    return "\n".join(result_parts)


def render_GREP(payload: Dict[str, Any]) -> str:
    """Render GREP tool output.

    High-value: pattern, match count, file:line previews with kind tags,
    harness_signal / constraint summaries.
    """
    pattern = str(payload.get("pattern") or "")
    match_count = int(payload.get("match_count") or 0)
    file_count = int(payload.get("file_count") or payload.get("numFiles") or 0)
    matches = payload.get("matches") or []
    truncated = payload.get("truncated")
    applied_offset = int(payload.get("appliedOffset") or 0)
    grep_path = str(payload.get("path") or "")
    glob_filter = str(payload.get("glob") or "")
    output_mode = str(payload.get("output_mode") or "")

    # Header with call parameters
    hdr_params: Dict[str, Any] = {"pattern": pattern}
    if grep_path:
        hdr_params["path"] = grep_path
    if glob_filter:
        hdr_params["glob"] = glob_filter
    if output_mode and output_mode != "files_with_matches":
        hdr_params["output_mode"] = output_mode
    result_parts = [_call_header("grep", **hdr_params)]

    # Result summary after header
    file_str = f" in {file_count} file{'s' if file_count != 1 else ''}" if file_count else ""
    result_parts.append(f"  {match_count} match{('es' if match_count != 1 else '')}{file_str}")

    # Matches — show file:line with preview, group by file for graph_summary
    last_file = ""
    for item in matches:
        path = str(item.get("path") or "")
        line_number = int(item.get("line_number") or 0)
        preview = str(item.get("preview") or item.get("line") or "")

        # File-level graph summary annotation
        if path != last_file:
            graph_summary = str(item.get("graph_summary") or "")
            if graph_summary:
                result_parts.append(f"  {path} [{graph_summary}]")
            last_file = path

        loc = f"{path}:{line_number}" if line_number else path
        result_parts.append(f"  {loc}  | {preview}")
        for hint in list(item.get("static_hints") or [])[:1]:
            if isinstance(hint, dict):
                result_parts.append(f"    {_static_hint_text(hint)}")

    if not matches:
        for item in list(payload.get("file_matches") or []):
            path = str(item.get("path") or "")
            result_parts.append(f"  {path}")
            for hint in list(item.get("static_hints") or [])[:1]:
                if isinstance(hint, dict):
                    result_parts.append(f"    {_static_hint_text(hint)}")

    return "\n".join(result_parts)


def render_GLOB(payload: Dict[str, Any]) -> str:
    """Render GLOB tool output.

    High-value: pattern, result count, file paths with sizes, directory entries.
    """
    pattern = str(payload.get("pattern") or "")
    result_count = int(payload.get("result_count") or 0)
    matches = payload.get("matches") or []
    truncated = payload.get("truncated")
    glob_path = str(payload.get("path") or "")

    # Header with call parameters
    hdr_params: Dict[str, Any] = {"pattern": pattern}
    if glob_path:
        hdr_params["path"] = glob_path
    result_parts = [_call_header("glob", **hdr_params)]
    result_parts.append(f"  {result_count} result{'s' if result_count != 1 else ''}")

    # Determine max path width for alignment
    max_path_len = 0
    for item in matches:
        p = str(item.get("path") or "")
        if len(p) > max_path_len:
            max_path_len = min(len(p), 60)

    for item in matches:
        path = str(item.get("path") or "")
        kind = str(item.get("kind") or "file")
        size = item.get("size")

        if kind == "dir":
            result_parts.append(f"  {path:<{max_path_len}}  (dir)")
        elif size is not None:
            result_parts.append(f"  {path:<{max_path_len}}  {_format_size(int(size))}")
        else:
            result_parts.append(f"  {path}")
        for hint in list(item.get("static_hints") or [])[:1]:
            if isinstance(hint, dict):
                result_parts.append(f"    {_static_hint_text(hint)}")

    return "\n".join(result_parts)


def _kind_tag(kind: str) -> str:
    """Return a short tag for the symbol kind."""
    tag_map = {
        "function": "FUNC",
        "macro": "MACRO",
        "struct": "STRUCT",
        "enum": "ENUM",
        "constant": "CONST",
        "definition": "DEF",
        "reference": "REF",
        "call": "CALL",
        "file": "FILE",
        "variable": "VAR",
        "typedef": "TYPE",
    }
    return tag_map.get(str(kind or "").lower(), str(kind or "?")[:4].upper())


def render_FindSymbols(payload: Dict[str, Any]) -> str:
    """Render FindSymbols tool output.

    High-value signals for PoC generation:
    - kind (function/macro/struct/enum) tells the agent what *type* of symbol
      it found -- critical for deciding whether to READ the definition.
    - signature for function defs -- often enough to understand the API without
      a separate READ call.
    - match_id -- one-click jump to source context via READ(match_id=...).
    - Grouped by file so the agent sees concentration hotspots.
    - Score-ordered (already sorted by the tool), so top hits are the best
      definitions, not noise references.
    """
    query = str(payload.get("query") or "")
    kind_filter = str(payload.get("kind") or "")
    result_count = int(payload.get("result_count") or 0)
    results = payload.get("results") or []
    truncated = payload.get("truncated")

    filter_str = f" kind={kind_filter}" if kind_filter else ""
    # Header with call parameters
    hdr_params: Dict[str, Any] = {"query": query}
    if kind_filter:
        hdr_params["kind"] = kind_filter
    fs_path = str(payload.get("path") or "")
    if fs_path:
        hdr_params["path"] = fs_path
    result_parts = [_call_header("find_symbols", **hdr_params)]
    result_parts.append(f"  {result_count} hit{'s' if result_count != 1 else ''}{filter_str}")

    # Summary line from index
    summary = payload.get("summary")
    if isinstance(summary, dict):
        parts = []
        for key in ("functions", "structs", "enums", "macros", "typedefs"):
            count = summary.get(key, 0)
            if count:
                parts.append(f"{count} {key}")
        if parts:
            top_names = summary.get("top_names") or []
            name_str = f"  top: {', '.join(n for n in top_names[:5] if n)}" if top_names else ""
            result_parts.append(f"  Summary: {', '.join(parts)}{name_str}")

    # Separate definitions from references for clarity
    definitions = []
    references = []
    for item in results:
        kind = str(item.get("kind") or "")
        if kind in ("function", "macro", "struct", "enum", "constant", "definition", "file"):
            definitions.append(item)
        else:
            references.append(item)

    # -- Definitions section --
    if definitions:
        result_parts.append("  Definitions:")
        # Group by file
        by_file: Dict[str, list] = {}
        for item in definitions:
            fpath = str(item.get("path") or "")
            by_file.setdefault(fpath, []).append(item)

        for fpath, items in by_file.items():
            for item in items:
                kind = str(item.get("kind") or "")
                tag = _kind_tag(kind)
                line_number = int(item.get("line_number") or 0)
                loc = f"{fpath}:{line_number}" if line_number else fpath
                match_id = str(item.get("match_id") or "")
                signature = str(item.get("signature") or "")
                preview = str(item.get("preview") or "")
                name = str(item.get("name") or "")

                # For functions: show signature (most actionable signal)
                if kind == "function" and signature:
                    display = signature
                elif kind == "function" and preview:
                    display = preview
                elif name and not preview:
                    display = name
                else:
                    display = preview

                # Append type-specific info
                enum_values = item.get("enum_values") or []
                if enum_values:
                    display += f"  [{', '.join(str(v) for v in enum_values[:8])}]"
                macro_value = str(item.get("macro_value") or "")
                if macro_value and kind == "macro":
                    display += f"  = {macro_value}"
                typedef_target = str(item.get("typedef_target") or "")
                if typedef_target and kind == "typedef":
                    display += f"  -> {typedef_target}"

                # match_id enables instant READ jump
                id_str = f" [id={match_id}]" if match_id else ""
                # Reachability tag from graph analysis
                reach_tag = ""
                if item.get("reachable") is True:
                    reach_tag = " [REACHABLE]"
                elif item.get("reachable") is False:
                    reach_tag = " [UNREACHABLE]"
                result_parts.append(f"  {tag}  {loc}{id_str}{reach_tag}  | {display}")

    # -- References section --
    if references:
        result_parts.append("  References:")
        for item in references:
            kind = str(item.get("kind") or "")
            tag = _kind_tag(kind)
            path = str(item.get("path") or "")
            line_number = int(item.get("line_number") or 0)
            loc = f"{path}:{line_number}" if line_number else path
            match_id = str(item.get("match_id") or "")
            preview = str(item.get("preview") or "")
            id_str = f" [id={match_id}]" if match_id else ""
            result_parts.append(f"  {tag}  {loc}{id_str}  | {preview}")

    if len(results) > 50:
        result_parts.append(f"  Note: {len(results)} total results. Use FindSymbols with max_results=N for more.")

    return "\n".join(result_parts)


def render_CallsiteSearch(payload: Dict[str, Any]) -> str:
    """Render CallsiteSearch tool output.

    High-value signals for PoC generation:
    - Definition vs callsite separation: the agent needs to know WHERE a
      function is defined (to understand its behavior) vs WHERE it is called
      (to trace data flow into the target).
    - match_id on each hit: one-click READ jump to surrounding context.
    - Call chain direction: callsites show who feeds data INTO the symbol,
      which is the path the PoC must traverse.
    - next_read_suggestions: the tool already computes the most useful
      READ offsets -- surface them as clickable references.
    """
    symbol = str(payload.get("symbol") or "")
    def_count = int(payload.get("definition_count") or 0)
    call_count = int(payload.get("callsite_count") or 0)
    definitions = payload.get("definitions") or []
    callsites = payload.get("callsites") or []
    suggestions = payload.get("next_read_suggestions") or []
    truncated = payload.get("truncated")

    # Header with call parameters
    hdr_params: Dict[str, Any] = {"symbol": symbol}
    cs_path = str(payload.get("path") or "")
    if cs_path:
        hdr_params["path"] = cs_path
    result_parts = [_call_header("callsite_search", **hdr_params)]
    result_parts.append(f"  {def_count} def{'s' if def_count != 1 else ''}, {call_count} callsite{'s' if call_count != 1 else ''}")

    # -- Definitions: where the symbol is implemented --
    if definitions:
        result_parts.append("  Defs:")
        for item in definitions:
            path = str(item.get("path") or "")
            line_number = int(item.get("line_number") or 0)
            loc = f"{path}:{line_number}" if line_number else path
            match_id = str(item.get("match_id") or "")
            preview = str(item.get("preview") or "")
            id_str = f" [id={match_id}]" if match_id else ""
            result_parts.append(f"  DEF   {loc}{id_str}  | {preview}")
    elif def_count == 0:
        result_parts.append("  Defs: (none found -- symbol may be in a library or header)")

    # -- Callsites: where the symbol is invoked (data flow entry) --
    if callsites:
        result_parts.append("  Calls:")
        # Group by file to show call concentration
        by_file: Dict[str, list] = {}
        for item in callsites:
            fpath = str(item.get("path") or "")
            by_file.setdefault(fpath, []).append(item)

        for fpath, items in by_file.items():
            for item in items:
                line_number = int(item.get("line_number") or 0)
                loc = f"{fpath}:{line_number}" if line_number else fpath
                match_id = str(item.get("match_id") or "")
                preview = str(item.get("preview") or "")
                id_str = f" [id={match_id}]" if match_id else ""
                result_parts.append(f"  CALL  {loc}{id_str}  | {preview}")
    elif call_count == 0:
        result_parts.append("  Calls: (no callers found)")

    if len(callsites) > 30:
        result_parts.append(f"  Note: {len(callsites)} total callsites. Use CallsiteSearch with max_results=N for more.")

    # -- Suggested next reads: pre-computed best offsets to jump to --
    if suggestions:
        sugg_parts = []
        for s in suggestions[:6]:
            s_str = str(s)
            # Suggestions are strings like "READ('path', offset=N, limit=40)"
            # Extract path:offset for a compact display
            m = re.match(r"READ\(['\"](.+?)['\"],\s*offset=(\d+)", s_str)
            if m:
                sugg_parts.append(f"{m.group(1)}:{m.group(2)}")
            else:
                sugg_parts.append(s_str)
        result_parts.append("-> Next reads: " + ", ".join(sugg_parts))

    # --- New: reverse calls from call graph ---
    reverse_calls = payload.get("reverse_calls") or []
    if reverse_calls:
        result_parts.append("  Callers (from call graph):")
        for rc in reverse_calls[:8]:
            caller = rc.get("caller", "")
            cpath = rc.get("path", "")
            cline = rc.get("line", 0)
            loc = f"{cpath}:{cline}" if cline else cpath
            result_parts.append(f"    {caller} @ {loc}")

    # --- New: call chain hints ---
    call_chain_hint = payload.get("call_chain_hint") or []
    if call_chain_hint:
        result_parts.append("  Chain:")
        for chain in call_chain_hint:
            result_parts.append(f"    {chain}")

    # --- New: indirect dispatch sites ---
    indirect = payload.get("indirect_callsites") or []
    if indirect:
        result_parts.append("  Indirect dispatch:")
        for ic in indirect:
            dvar = ic.get("dispatch_var", "")
            iloc = f"{ic.get('path', '')}:{ic.get('line', 0)}"
            ctx = ic.get("context", "")
            result_parts.append(f"    {dvar} @ {iloc}  | {ctx}")

    return "\n".join(result_parts)


def render_RepoMap(payload: Dict[str, Any]) -> str:
    """Render RepoMap tool output.

    High-value: harness files (most critical for PoC), corpus dirs,
    source roots, top-level layout. Build files de-emphasized.
    """
    path = str(payload.get("path") or "")
    top_level = payload.get("top_level") or []
    source_roots = payload.get("source_roots") or []
    harness_files = payload.get("harness_files") or []
    build_files = payload.get("build_files") or []
    corpus_dirs = payload.get("corpus_dirs") or []

    result_parts = [_call_header("repo_map", path=path) if path else _call_header("repo_map")]

    # Layout
    if top_level:
        result_parts.append("  Layout:  " + "  ".join(str(t) for t in top_level[:12]))

    # Source roots
    if source_roots:
        result_parts.append("  Source:  " + "  ".join(str(s) for s in source_roots[:8]))

    # Harness (high priority)
    if harness_files:
        result_parts.append(
            "  * Harness: " + ", ".join(str(h) for h in harness_files[:8])
        )

    # Corpus (high priority)
    if corpus_dirs:
        corpus_strs = []
        for d in corpus_dirs[:8]:
            if isinstance(d, dict):
                dpath = str(d.get("path") or "")
                fcount = d.get("file_count", "")
                corpus_strs.append(f"{dpath} ({fcount} files)" if fcount else dpath)
            else:
                corpus_strs.append(str(d))
        result_parts.append("  * Corpus:  " + ", ".join(corpus_strs))

    # Build (low priority)
    if build_files:
        result_parts.append("  Build:   " + ", ".join(str(b) for b in build_files[:6]))

    # --- New: Structural index fields ---
    harness_detail = payload.get("harness_detail") or []
    entry_point_signatures = payload.get("entry_point_signatures") or {}
    format_parsers = payload.get("format_parsers") or []
    dispatch_tables = payload.get("dispatch_tables") or []
    include_chains = payload.get("include_chains") or {}

    # Harness detail: entry function signatures and calls
    if harness_detail:
        result_parts.append("  * Entry Points:")
        for hd in harness_detail[:5]:
            fn = hd.get("entry_function", "")
            sig = hd.get("signature", "")
            calls = hd.get("calls", [])
            line = f"    {fn}"
            if sig:
                line += f"  {sig}"
            if calls:
                line += f"  → calls: {', '.join(calls[:8])}"
            result_parts.append(line)

    # Entry point signatures summary
    if entry_point_signatures:
        sig_lines = []
        for name, sig in list(entry_point_signatures.items())[:5]:
            sig_lines.append(f"    {sig}")
        if sig_lines:
            result_parts.append("  * Signatures:")
            result_parts.extend(sig_lines)

    # Format parsers
    if format_parsers:
        result_parts.append("  * Parsers:")
        for fp in format_parsers[:10]:
            name = fp.get("name", "")
            sig = fp.get("signature", "")
            fpath = fp.get("path", "")
            line_num = fp.get("line", 0)
            loc = f"{fpath}:{line_num}" if line_num else fpath
            display = sig if sig else name
            result_parts.append(f"    {name}  {loc}  | {display}")

    # Dispatch tables
    if dispatch_tables:
        result_parts.append("  * Dispatch:")
        for dt in dispatch_tables[:5]:
            var = dt.get("variable", "")
            fpath = dt.get("path", "")
            line_num = dt.get("line", 0)
            cases = dt.get("cases", [])
            loc = f"{fpath}:{line_num}" if line_num else fpath
            case_str = ", ".join(cases[:6])
            result_parts.append(f"    switch({var}) @ {loc}  [{case_str}]")

    # Include chains for harness files
    if include_chains:
        for hpath, incs in list(include_chains.items())[:3]:
            result_parts.append(f"    Includes({hpath}): {', '.join(incs[:6])}")

    return "\n".join(result_parts)


def render_FileInfo(payload: Dict[str, Any]) -> str:
    """Render FileInfo tool output.

    High-value: file size, type, magic bytes, printable ratio, entropy
    with interpretation.
    """
    path = str(payload.get("path") or "")
    size = payload.get("size")
    mime_type = str(payload.get("mime_type") or payload.get("file_type") or "")
    magic = str(payload.get("magic") or "")
    printable = payload.get("printable_ratio")
    entropy = payload.get("entropy")

    result_parts = [_call_header("file_info", path=path)]

    # Size + type
    parts = []
    if size is not None:
        parts.append(_format_size(int(size)))
    if mime_type:
        parts.append(mime_type)
    if magic:
        parts.append(f"Magic: {magic}")
    if parts:
        result_parts.append("  " + " | ".join(parts))

    # Printable + entropy
    info_parts = []
    if printable is not None:
        pct = float(printable) * 100 if float(printable) <= 1.0 else float(printable)
        info_parts.append(f"Printable: {pct:.0f}%")
    if entropy is not None:
        ent_val = float(entropy)
        label = _entropy_label(ent_val)
        info_parts.append(f"Entropy: {ent_val:.1f} bits/byte ({label})")
    if info_parts:
        result_parts.append("  " + " | ".join(info_parts))

    return "\n".join(result_parts)


def render_HexView(payload: Dict[str, Any]) -> str:
    """Render HexView tool output.

    High-value: hex dump content (already formatted), offset context,
    navigation hints.
    """
    path = str(payload.get("path") or "")
    offset = int(payload.get("offset") or 0)
    length = int(payload.get("length") or 0)
    file_size = int(payload.get("file_size") or 0)
    content = str(payload.get("content") or "")
    has_more_before = payload.get("has_more_before")
    has_more_after = payload.get("has_more_after")

    size_info = f"{_format_size(length)}"
    if file_size > 0:
        size_info += f" of {_format_size(file_size)}"

    result_parts = [_call_header("hex_view", path=path, offset=offset, length=length)]
    result_parts.append(f"  {size_info}")

    if content:
        result_parts.append(content)

    # Navigation hints
    nav_parts = []
    if has_more_before and offset > 0:
        nav_parts.append(f"HexView(\"{path}\", offset={max(0, offset - length)})")
    if has_more_after and file_size > 0:
        nav_parts.append(f"HexView(\"{path}\", offset={offset + length})")
    if nav_parts:
        result_parts.append("--- More: " + " or ".join(nav_parts) + " ---")

    return "\n".join(result_parts)


def render_StructProbe(payload: Dict[str, Any]) -> str:
    """Render StructProbe tool output.

    High-value: decoded field values, raw hex for verification, field names.
    """
    path = str(payload.get("path") or "")
    offset = int(payload.get("offset") or 0)
    endian = str(payload.get("endian") or "little")
    fields = payload.get("fields") or []

    result_parts = [_call_header("struct_probe", path=path, offset=offset, endian=endian)]

    for field in fields:
        name = str(field.get("name") or "field")
        raw_hex = str(field.get("raw_hex") or "")
        decoded = str(field.get("decoded") or "")
        fmt = str(field.get("format") or "")
        status = str(field.get("status") or "")

        if status == "error":
            msg = str(field.get("message") or "decode error")
            result_parts.append(f"  {name}: {msg}")
            continue

        parts = [f"  {name}:"]
        if raw_hex:
            parts.append(f"{raw_hex}")
        if decoded:
            parts.append(f"-> {decoded}")
        if fmt:
            parts.append(f"({fmt})")
        result_parts.append(" ".join(parts))

    if len(fields) > 32:
        result_parts.append(f"  Note: {len(fields)} total fields.")

    return "\n".join(result_parts)


def render_CorpusInspect(payload: Dict[str, Any]) -> str:
    """Render CorpusInspect tool output.

    High-value: corpus directory paths, example file previews and sizes.
    """
    path = str(payload.get("path") or "")
    corpus_dirs = payload.get("corpus_dirs") or []

    result_parts = [_call_header("corpus_inspect", path=path) if path else _call_header("corpus_inspect")]
    result_parts.append(f"  {len(corpus_dirs)} corpus dir{'s' if len(corpus_dirs) != 1 else ''}")

    for d in corpus_dirs[:10]:
        if isinstance(d, dict):
            dpath = str(d.get("path") or "")
            fcount = d.get("file_count", 0)
            examples = d.get("examples") or []
            result_parts.append(f"  {dpath}/ ({fcount} files)")
            for ex in examples[:6]:
                if isinstance(ex, dict):
                    ex_path = str(ex.get("path") or "")
                    ex_size = ex.get("size")
                    ex_preview = str(ex.get("preview") or "")
                    size_str = f"({_format_size(int(ex_size))})" if ex_size is not None else ""
                    result_parts.append(f"    {ex_path}  {size_str} | {ex_preview}")
                else:
                    result_parts.append(f"    {ex}")
        else:
            result_parts.append(f"  {d}")

    return "\n".join(result_parts)


def render_WRITE(payload: Dict[str, Any]) -> str:
    """Render WRITE tool output.

    High-value: path written, bytes written.
    """
    path = str(payload.get("path") or "")
    size = payload.get("size")
    message = str(payload.get("message") or "")

    if size is not None:
        return _call_header("write", path=path) + f"\n  {_format_size(int(size))} written"
    if message:
        return _call_header("write", path=path) + f"\n  {message}"
    return _call_header("write", path=path) + "\n  done"


def render_BASH(payload: Dict[str, Any]) -> str:
    """Render BASH tool output.

    High-value: command, exit code, stdout, stderr (especially sanitizer
    traces). Stderr shown last for emphasis.
    """
    command = str(payload.get("command") or "")
    returncode = payload.get("returncode")
    stdout = str(payload.get("stdout") or "")
    stderr = str(payload.get("stderr") or "")
    message = str(payload.get("message") or "")

    result_parts = [_call_header("bash", command=command)]

    # Exit code
    if returncode is not None:
        rc = int(returncode)
        rc_label = "exit_code=0" if rc == 0 else f"exit_code={rc} (non-zero)"
        result_parts.append(f"  {rc_label}")

    # Error message (for blocked/timeout cases)
    if message and not stdout and not stderr:
        result_parts.append(f"  {message}")

    # Stdout
    if stdout:
        result_parts.append(stdout)

    # Stderr — shown last for emphasis (especially ASAN traces)
    if stderr:
        is_sanitizer = bool(_SANITIZER_RE.search(stderr))
        label = "SANITIZER TRACE:" if is_sanitizer else "STDERR:"
        result_parts.append(f"  {label}")
        result_parts.append(stderr)

    return "\n".join(result_parts)


def render_submit_poc(payload: Dict[str, Any]) -> str:
    """Render submit_poc tool output.

    High-value: verification status (the single most important signal),
    vul_exit_code, crash trace from stderr/vul_stderr.

    Note: the engine's _model_visible_tool_output may have already renamed
    fields (vul_exit_code -> exit_code, raw_output -> output, vul_stderr ->
    stderr, vul_stdout -> stdout).  We handle both naming conventions.
    """
    status = str(payload.get("status") or "")
    # Accept both original and engine-renamed field names
    vul_exit_code = payload.get("vul_exit_code") if payload.get("vul_exit_code") is not None else payload.get("exit_code")
    accepted = payload.get("accepted")
    verification_status = str(payload.get("verification_status") or "")
    poc_path = str(payload.get("poc_path") or "")
    raw_output = str(payload.get("raw_output") or payload.get("output") or "")
    vul_stderr = str(payload.get("vul_stderr") or payload.get("stderr") or "")
    vul_stdout = str(payload.get("vul_stdout") or payload.get("stdout") or "")
    error = str(payload.get("error") or "")

    # Error case
    if status == "error":
        return _call_header("submit_poc", poc_path=poc_path) + f"\n  ERROR: {error or 'submission failed'}"

    result_parts = [_call_header("submit_poc", poc_path=poc_path)]

    # Determine verification outcome
    if accepted is True or verification_status == "accepted":
        result_parts.append("  * ACCEPTED -- vulnerability confirmed!")
    elif verification_status == "vul_only_triggered":
        result_parts.append("  ! VUL TRIGGERED -- the PoC crashed the vulnerable target")
    elif verification_status == "no_trigger" or (vul_exit_code is not None and vul_exit_code == 0):
        result_parts.append("  x NO CRASH -- vulnerable binary exited normally (reachability unknown)")
    elif verification_status == "rejected":
        result_parts.append("  x REJECTED -- crash triggered but not the target vulnerability")
    elif vul_exit_code is not None and vul_exit_code != 0:
        result_parts.append("  ! VUL TRIGGERED -- vulnerable binary crashed")

    # Exit code
    if vul_exit_code is not None:
        vul_code = int(vul_exit_code)
        crash_word = "crash triggered" if vul_code != 0 else "no crash"
        result_parts.append(f"  vul_exit_code={vul_code} ({crash_word})")

    # Crash summary from stderr
    if vul_stderr:
        crash_summ = _crash_summary(vul_stderr)
        if crash_summ:
            result_parts.append(f"  {crash_summ}")

    # Server output
    if raw_output:
        if raw_output.strip():
            result_parts.append(f"  Server output: {raw_output.strip()}")

    # Stdout
    if vul_stdout and vul_stdout.strip():
        result_parts.append(f"  stdout: {vul_stdout.strip()}")

    return "\n".join(result_parts)


def render_record_chain_node(payload: Dict[str, Any]) -> str:
    """Render record_chain_node tool output."""
    parts = [_call_header("record_chain_node")]
    role = str(payload.get("role") or "")
    status = str(payload.get("status") or "")
    func = str(payload.get("function") or "")
    loc = str(payload.get("location") or "")
    parts.append(f"  {role} [{status}] {func} @ {loc}")
    return "\n".join(parts)


def render_record_gate(payload: Dict[str, Any]) -> str:
    """Render record_gate tool output."""
    parts = [_call_header("record_gate")]
    gt = str(payload.get("gate_type") or "")
    status = str(payload.get("status") or "")
    desc = str(payload.get("description") or "")
    parts.append(f"  [{gt}] {desc} ({status})")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

_RENDERERS = {
    "read": render_READ,
    "READ": render_READ,
    "grep": render_GREP,
    "GREP": render_GREP,
    "glob": render_GLOB,
    "GLOB": render_GLOB,
    "find_symbols": render_FindSymbols,
    "FindSymbols": render_FindSymbols,
    "callsite_search": render_CallsiteSearch,
    "CallsiteSearch": render_CallsiteSearch,
    "repo_map": render_RepoMap,
    "RepoMap": render_RepoMap,
    "file_info": render_FileInfo,
    "FileInfo": render_FileInfo,
    "hex_view": render_HexView,
    "HexView": render_HexView,
    "struct_probe": render_StructProbe,
    "StructProbe": render_StructProbe,
    "corpus_inspect": render_CorpusInspect,
    "CorpusInspect": render_CorpusInspect,
    "write": render_WRITE,
    "WRITE": render_WRITE,
    "bash": render_BASH,
    "BASH": render_BASH,
    "submit_poc": render_submit_poc,
    "record_chain_node": render_record_chain_node,
    "record_gate": render_record_gate,
}


def render_tool_output(tool_name: str, payload: Any) -> Any:
    """Dispatch to the appropriate per-tool renderer.

    Returns the **rendered text string** when a renderer matches, or the
    original payload when it doesn't.  The string return ensures that:

    1. The engine's ``_serialize_for_tool_message`` fast-path passes
       strings through unchanged, so the LLM sees human-readable text
       instead of raw JSON.
    2. The TUI's ``ContentFirstRenderer`` displays the text directly.
    3. The original structured dict is preserved in the
       ``_structured_output_buffer`` so ``_process_action_result`` can
       still access clean structured fields for state updates.

    When rendering is disabled or no renderer matches, the original
    payload is returned unchanged.
    """
    if not isinstance(payload, dict):
        return payload

    # Error results get special handling
    if payload.get("status") == "error":
        rendered = render_error(payload, tool_name)
        if rendered:
            return rendered
        return payload

    renderer = _RENDERERS.get(tool_name)
    if renderer:
        try:
            rendered = renderer(payload)
            if rendered:
                return rendered
        except Exception:
            pass

    return payload
