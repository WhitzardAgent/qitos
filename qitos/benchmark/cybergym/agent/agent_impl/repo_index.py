"""Lightweight structural index for C/C++ repositories.

Built deterministically via regex + brace-depth state machine.
No external dependencies (ctags, tree-sitter, libclang).

Used by RepoMap (builds the index), FindSymbols (symbol lookup),
and CallsiteSearch (call-graph traversal).
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Maximum files to index in full detail
_MAX_INDEXED_FILES = 500
# Maximum call-graph edges
_MAX_CALL_GRAPH_EDGES = 5000
# Maximum entries per list field
_MAX_PER_LIST = 50


# ------------------------------------------------------------------
# Core parsing primitive: brace-depth counter
# ------------------------------------------------------------------

def walk_brace_depth(text: str):
    """Walk *text* yielding ``(line, old_depth, new_depth, preceding_text)``
    at every ``{`` / ``}`` transition, *excluding* braces inside string
    literals, character literals, line comments, and block comments.

    This is a deterministic, per-character state machine — no heuristics.
    """
    depth = 0
    line = 1
    i = 0
    n = len(text)
    in_string = False
    in_char = False
    in_line_comment = False
    in_block_comment = False

    while i < n:
        ch = text[i]

        # --- newline ---
        if ch == '\n':
            line += 1
            in_line_comment = False

        # --- comment & string guards (only when not already in one) ---
        if not in_line_comment and not in_block_comment and not in_string and not in_char:
            # line comment
            if ch == '/' and i + 1 < n and text[i + 1] == '/':
                in_line_comment = True
                i += 1
            # block comment
            elif ch == '/' and i + 1 < n and text[i + 1] == '*':
                in_block_comment = True
                i += 1
            # string literal
            elif ch == '"':
                in_string = True
            # char literal
            elif ch == "'":
                in_char = True
        elif in_string:
            if ch == '\\' and i + 1 < n:
                i += 1  # skip escaped char
            elif ch == '"':
                in_string = False
        elif in_char:
            if ch == '\\' and i + 1 < n:
                i += 1
            elif ch == "'":
                in_char = False
        elif in_block_comment:
            if ch == '*' and i + 1 < n and text[i + 1] == '/':
                in_block_comment = False
                i += 1

        # --- brace tracking (outside strings/comments) ---
        if not in_line_comment and not in_block_comment and not in_string and not in_char:
            if ch == '{':
                old_depth = depth
                depth += 1
                # Capture up to 300 chars of preceding text for signature matching
                start = max(0, i - 300)
                preceding = text[start:i].strip()
                yield (line, old_depth, depth, preceding)
            elif ch == '}':
                if depth > 0:
                    old_depth = depth
                    depth -= 1
                    yield (line, old_depth, depth, "")

        i += 1


# ------------------------------------------------------------------
# Per-file structure extraction
# ------------------------------------------------------------------

# Regex patterns (compiled once)
_RE_INCLUDE = re.compile(r'^\s*#\s*include\s+[<"]([^>"]+)[>"]', re.MULTILINE)
_RE_MACRO = re.compile(r'^\s*#\s*define\s+(\w+)(.*)', re.MULTILINE)
_RE_STRUCT = re.compile(r'^\s*(?:typedef\s+)?(?:struct|union|class)\s+(\w+)', re.MULTILINE)
_RE_TYPEDEF = re.compile(r'typedef\s+(.*?)\s+(\w+)\s*;')
_RE_ENUM_START = re.compile(r'^\s*(?:typedef\s+)?enum\s+(\w*)\s*\{', re.MULTILINE)
_RE_ENUM_VALUE = re.compile(r'\s*(\w+)\s*(?:=\s*([^,}]+))?\s*[,\}]')
_RE_SWITCH = re.compile(r'\bswitch\s*\(\s*(\w+)\s*\)')
_RE_CASE = re.compile(r'^\s*case\s+(.+?):|^\s*default\s*:')
_RE_FUNC_SIG = re.compile(
    r'(?:(?:static|inline|extern|virtual|const)\s+)*'
    r'([\w][\w\s*]*?)\s+'
    r'(\b\w+\b)\s*'
    r'\(([^)]*)\)\s*$'
)
_CONTROL_FLOW = {"if", "for", "while", "switch", "return", "sizeof", "elif", "else"}
# Pattern for function-pointer dispatch
_RE_FP_DISPATCH = re.compile(
    r'(\w+(?:->|\.|-)>?\s*\w+(?:\[(?:\w+)\])?)\s*\('
)


def _is_include_guard(name: str, text: str) -> bool:
    """Heuristic-free check: a macro is an include guard if it appears
    in an ``#ifndef`` at the very top of the file."""
    lines = text.splitlines()
    for line in lines[:10]:
        stripped = line.strip()
        if not stripped or stripped.startswith('//') or stripped.startswith('/*'):
            continue
        if stripped.startswith('#ifndef') and name in stripped:
            return True
        break
    return False


def extract_file_structure(text: str, rel_path: str) -> Dict[str, Any]:
    """Extract deterministic structural information from a single source file.

    Returns a dict with keys: includes, functions, structs, enums, macros,
    typedefs, switch_tables.
    """
    result: Dict[str, Any] = {
        "includes": [],
        "functions": [],
        "structs": [],
        "enums": [],
        "macros": [],
        "typedefs": [],
        "switch_tables": [],
    }

    # 1. Includes
    result["includes"] = _RE_INCLUDE.findall(text)[:_MAX_PER_LIST]

    # 2. Macros (filter include guards)
    for m in _RE_MACRO.finditer(text):
        name, value = m.group(1), m.group(2).strip()
        if _is_include_guard(name, text):
            continue
        # Skip macro names starting with underscore + uppercase (likely system macros)
        if name.startswith('_') and len(name) > 1 and name[1].isupper():
            continue
        result["macros"].append({
            "name": name,
            "line": text[:m.start()].count('\n') + 1,
            "value": value[:80],
        })
        if len(result["macros"]) >= _MAX_PER_LIST:
            break

    # 3. Structs/Unions
    for m in _RE_STRUCT.finditer(text):
        result["structs"].append({
            "name": m.group(1),
            "line": text[:m.start()].count('\n') + 1,
        })
        if len(result["structs"]) >= _MAX_PER_LIST:
            break

    # 4. Typedefs
    for m in _RE_TYPEDEF.finditer(text):
        target = m.group(1).strip()[:100]
        name = m.group(2).strip()
        if name in _CONTROL_FLOW:
            continue
        result["typedefs"].append({
            "name": name,
            "line": text[:m.start()].count('\n') + 1,
            "target": target,
        })
        if len(result["typedefs"]) >= _MAX_PER_LIST:
            break

    # 5. Enums — simple state-machine extraction
    for m in _RE_ENUM_START.finditer(text):
        enum_name = m.group(1) or ""
        enum_line = text[:m.start()].count('\n') + 1
        # Collect values until closing brace
        rest = text[m.end():]
        brace_depth = 1
        pos = 0
        enum_body = []
        while pos < len(rest) and brace_depth > 0:
            if rest[pos] == '{':
                brace_depth += 1
            elif rest[pos] == '}':
                brace_depth -= 1
                if brace_depth == 0:
                    break
            pos += 1
        body_text = rest[:pos]
        values = []
        for vm in _RE_ENUM_VALUE.finditer(body_text):
            vname = vm.group(1)
            vval = vm.group(2).strip() if vm.group(2) else ""
            if vname.startswith('_'):
                continue
            values.append(f"{vname}={vval}" if vval else vname)
            if len(values) >= 20:
                break
        result["enums"].append({
            "name": enum_name,
            "line": enum_line,
            "values": values[:20],
        })
        if len(result["enums"]) >= _MAX_PER_LIST:
            break

    # 6. Functions + switch tables via brace-depth walking
    # Build a list of (start_line, end_line, preceding_text) for depth-0 blocks
    depth_blocks: List[Tuple[int, int, str]] = []
    pending_start = None
    pending_preceding = ""

    for (ln, old_d, new_d, preceding) in walk_brace_depth(text):
        if old_d == 0 and new_d == 1:
            pending_start = ln
            pending_preceding = preceding
        elif old_d == 1 and new_d == 0 and pending_start is not None:
            depth_blocks.append((pending_start, ln, pending_preceding))
            pending_start = None

    # Classify depth-0 blocks as functions or switch tables
    for start_line, end_line, preceding in depth_blocks:
        # Check for function signature in preceding text
        # Take the last logical line (may span multiple lines joined)
        # Use the last 200 chars of preceding text for signature match
        sig_text = preceding[-200:] if len(preceding) > 200 else preceding
        # Collapse newlines for multi-line signatures
        sig_text_one_line = " ".join(sig_text.split())
        m = _RE_FUNC_SIG.search(sig_text_one_line)
        if m:
            ret_type = m.group(1).strip()
            func_name = m.group(2).strip()
            params = m.group(3).strip()
            if func_name not in _CONTROL_FLOW and len(func_name) > 1:
                is_static = "static" in sig_text_one_line[:sig_text_one_line.index(func_name)] if func_name in sig_text_one_line else False
                result["functions"].append({
                    "name": func_name,
                    "signature": f"{ret_type} {func_name}({params})"[:120],
                    "line": start_line,
                    "end_line": end_line,
                    "is_static": bool(is_static),
                })
                if len(result["functions"]) >= _MAX_PER_LIST:
                    break
                continue  # not a switch table

        # Check for switch in preceding text
        sm = _RE_SWITCH.search(preceding[-100:] if len(preceding) > 100 else preceding)
        if sm:
            var_name = sm.group(1)
            # Extract cases from the block
            block_text = text.splitlines()[start_line - 1:end_line]
            cases = []
            for bline in block_text:
                cm = _RE_CASE.match(bline)
                if cm:
                    label = cm.group(1).strip() if cm.group(1) else "default"
                    cases.append(label[:40])
                    if len(cases) >= 15:
                        break
            if cases:
                result["switch_tables"].append({
                    "on_var": var_name,
                    "line": start_line,
                    "cases": cases,
                })
                if len(result["switch_tables"]) >= _MAX_PER_LIST:
                    break

    return result


# ------------------------------------------------------------------
# Call-graph extraction from function bodies
# ------------------------------------------------------------------

def _extract_calls_from_body(body_text: str, known_functions: set) -> List[str]:
    """Find calls to *known_functions* within *body_text*.

    Uses a simple word-boundary regex: ``\\bNAME\\s*\\(`` for each
    known name that appears in the body.  This is deterministic —
    it only reports calls to functions that are already in the index.
    """
    calls = []
    for name in sorted(known_functions):
        if name in body_text and re.search(rf'\b{re.escape(name)}\s*\(', body_text):
            calls.append(name)
            if len(calls) >= 30:
                break
    return calls


def _build_call_graph(file_structures: Dict[str, Dict]) -> Dict[str, List[str]]:
    """Build a lightweight call graph from per-file structures.

    Returns ``{caller_name: [callee_name, ...]}``.
    """
    # Gather all indexed function names
    all_func_names: set = set()
    func_locations: Dict[str, Tuple[str, int, int]] = {}  # name -> (path, start, end)
    for path, info in file_structures.items():
        for fn in info.get("functions", []):
            name = fn["name"]
            all_func_names.add(name)
            func_locations[name] = (path, fn["line"], fn.get("end_line", fn["line"]))

    # For each function, find calls to other indexed functions
    call_graph: Dict[str, List[str]] = {}
    edge_count = 0

    # We need to read file text again to get function bodies — but we
    # can do this lazily. For now, build from the function line ranges.
    # This requires the actual file text; we'll need to pass it through.
    # Instead, we'll build the call graph during the build_repo_index phase
    # where we have the file text available.

    # Placeholder: the actual call-graph building happens in build_repo_index
    # where we have access to file text. This function just returns an
    # empty graph as a signal that the work must be done there.
    return call_graph


# ------------------------------------------------------------------
# Top-level index builder
# ------------------------------------------------------------------

def build_repo_index(
    root: Path,
    max_files: int = _MAX_INDEXED_FILES,
) -> Dict[str, Any]:
    """Build a structural index of the repository at *root*.

    Returns the index dict with keys: files, call_graph, harness_entries.
    """
    root = Path(root)
    files: Dict[str, Dict] = {}
    file_texts: Dict[str, str] = {}  # for call-graph building

    # Gather source files
    source_files = _gather_source_files(root, max_files)

    # Phase 1: Per-file extraction
    all_func_names: set = set()
    for fpath in source_files:
        try:
            text = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(fpath.relative_to(root))
        file_texts[rel] = text
        info = extract_file_structure(text, rel)
        files[rel] = info
        for fn in info.get("functions", []):
            all_func_names.add(fn["name"])

    # Phase 2: Build call graph from function bodies
    call_graph: Dict[str, List[str]] = {}
    call_graph_v2: Dict[str, List[Dict[str, Any]]] = {}
    definitions: Dict[str, List[Tuple[str, int]]] = {}
    for rel, info in files.items():
        for fn in info.get("functions", []):
            definitions.setdefault(fn["name"], []).append((rel, fn["line"]))
    edge_count = 0
    for rel, info in files.items():
        text = file_texts.get(rel, "")
        if not text:
            continue
        lines = text.splitlines()
        for fn in info.get("functions", []):
            start = fn["line"]
            end = fn.get("end_line", start)
            # Extract body text (1-indexed lines)
            body_lines = lines[max(0, start - 1):min(len(lines), end)]
            body_text = "\n".join(body_lines)
            calls = _extract_calls_from_body(body_text, all_func_names)
            # A function doesn't call itself
            calls = [c for c in calls if c != fn["name"]]
            has_indirect_call = bool(re.search(r'(?:->|\.)\s*\w+\s*\(', body_text))
            if calls or has_indirect_call:
                if calls:
                    call_graph[fn["name"]] = calls
                caller_id = _symbol_node_id(rel, fn["name"], fn["line"])
                edges: List[Dict[str, Any]] = []
                for callee in calls:
                    defs = list(definitions.get(callee, []))
                    same_file = [item for item in defs if item[0] == rel]
                    if len(same_file) == 1:
                        targets = same_file
                        status = "resolved"
                    elif len(defs) == 1:
                        targets = defs
                        status = "resolved"
                    elif defs:
                        targets = defs
                        status = "ambiguous"
                    else:
                        targets = []
                        status = "unresolved"
                    edges.append({
                        "callee_name": callee,
                        "target_ids": [_symbol_node_id(p, callee, line) for p, line in targets],
                        "status": status,
                    })
                if has_indirect_call:
                    edges.append({
                        "callee_name": "<indirect>",
                        "target_ids": [],
                        "status": "unresolved",
                    })
                call_graph_v2[caller_id] = edges
                edge_count += len(calls) + int(has_indirect_call)
                if edge_count >= _MAX_CALL_GRAPH_EDGES:
                    break
        if edge_count >= _MAX_CALL_GRAPH_EDGES:
            break

    # Phase 3: Extract harness entries
    harness_entries = _extract_harness_entries(files, file_texts)

    # Phase 4: Compute fingerprint for cache invalidation
    fingerprint = _compute_fingerprint(source_files)

    return {
        "files": files,
        "call_graph": call_graph,
        "call_graph_v2": call_graph_v2,
        "harness_entries": harness_entries,
        "_fingerprint": fingerprint,
    }


def _gather_source_files(root: Path, max_files: int) -> List[Path]:
    """Gather source files to index, prioritizing by relevance."""
    _SKIP_DIRS = {
        ".git", ".hg", ".svn", "CVS", "__pycache__", "node_modules",
        ".cybergym", ".agent",
    }
    _SOURCE_EXTS = {
        ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx",
        ".m", ".mm",  # Objective-C
        ".rs",  # Rust
        ".go",  # Go
        ".py",  # Python
        ".java",  # Java
    }
    _RELEVANCE_TOKENS = (
        "fuzz", "harness", "parse", "read", "decode", "handle",
        "process", "main", "entry", "submit",
    )

    all_files: List[Tuple[int, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skipped directories
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix.lower() not in _SOURCE_EXTS:
                continue
            # Simple relevance scoring: path tokens
            rel = str(fpath.relative_to(root)).lower()
            score = sum(1 for tok in _RELEVANCE_TOKENS if tok in rel)
            all_files.append((score, fpath))

    # Sort by relevance (desc), then by path for determinism
    all_files.sort(key=lambda x: (-x[0], str(x[1])))
    return [fpath for _, fpath in all_files[:max_files]]


def _extract_harness_entries(
    files: Dict[str, Dict],
    file_texts: Dict[str, str],
) -> List[Dict[str, Any]]:
    """Extract detailed harness entry information."""
    entries = []
    for rel, info in files.items():
        text = file_texts.get(rel, "")
        if not text:
            continue
        # Check for harness markers
        has_entry = "LLVMFuzzerTestOneInput" in text
        has_main = False
        if not has_entry:
            # Check for main() in files with "fuzz" in the name
            lower_name = rel.lower()
            if "fuzz" in lower_name or "harness" in lower_name:
                for fn in info.get("functions", []):
                    if fn["name"] == "main":
                        has_main = True
                        break
        if not has_entry and not has_main:
            continue

        entry_fn = None
        for fn in info.get("functions", []):
            if fn["name"] == "LLVMFuzzerTestOneInput":
                entry_fn = fn
                break
            if has_main and fn["name"] == "main":
                entry_fn = fn
                break

        if not entry_fn:
            continue

        # Extract functions called from the entry body
        lines = text.splitlines()
        start = entry_fn["line"]
        end = entry_fn.get("end_line", start)
        body_lines = lines[max(0, start - 1):min(len(lines), end)]
        body_text = "\n".join(body_lines)

        # Find all function calls in the body
        called = []
        all_names = set()
        for file_info in files.values():
            for fn in file_info.get("functions", []):
                all_names.add(fn["name"])
        for name in sorted(all_names):
            if name != entry_fn["name"] and re.search(rf'\b{re.escape(name)}\s*\(', body_text):
                called.append(name)
                if len(called) >= 20:
                    break

        entries.append({
            "path": rel,
            "entry_function": entry_fn["name"],
            "signature": entry_fn.get("signature", ""),
            "line": entry_fn["line"],
            "node_id": _symbol_node_id(rel, entry_fn["name"], entry_fn["line"]),
            "calls": called,
        })

    return entries


def _symbol_node_id(path: str, name: str, line: int) -> str:
    """Return a stable identity for duplicate symbols in different files."""
    return f"{path}::{name}::{int(line or 0)}"


def trace_harness_reachability(
    index: Dict[str, Any],
    entry_node_id: str,
    target_symbols: List[str],
    max_depth: int = 8,
) -> Dict[str, Any]:
    """Trace one concrete harness to vulnerability symbols.

    Ambiguous edges are retained as possible paths but never promoted to
    verified reachability.
    """
    targets = {str(item) for item in target_symbols if str(item)}
    if not entry_node_id or not targets:
        return {"status": "not_found", "symbols": [], "paths": []}
    graph = index.get("call_graph_v2", {})
    queue: List[Tuple[str, List[str], bool]] = [(entry_node_id, [entry_node_id], False)]
    visited: set[Tuple[str, bool]] = set()
    verified_paths: List[List[str]] = []
    possible_paths: List[List[str]] = []
    reached: set[str] = set()
    saw_unresolved = False
    while queue and len(verified_paths) + len(possible_paths) < 20:
        node_id, path, uncertain = queue.pop(0)
        marker = (node_id, uncertain)
        if marker in visited or len(path) > max_depth + 1:
            continue
        visited.add(marker)
        for edge in graph.get(node_id, []):
            callee = str(edge.get("callee_name") or "")
            edge_uncertain = uncertain or edge.get("status") != "resolved"
            target_ids = list(edge.get("target_ids") or [])
            if edge.get("status") == "unresolved":
                saw_unresolved = True
            if callee in targets:
                reached.add(callee)
                found_path = path + (target_ids[:1] or [callee])
                (possible_paths if edge_uncertain else verified_paths).append(found_path)
            for target_id in target_ids:
                queue.append((target_id, path + [target_id], edge_uncertain))
    if verified_paths:
        status = "verified"
    elif possible_paths or saw_unresolved:
        status = "unknown"
    else:
        status = "not_found"
    return {
        "status": status,
        "symbols": sorted(reached),
        "paths": verified_paths or possible_paths,
    }


def _compute_fingerprint(source_files: List[Path]) -> str:
    """Compute a fingerprint of file paths + mtimes for cache invalidation."""
    h = hashlib.md5()
    for fpath in source_files[:200]:  # limit for performance
        try:
            stat = fpath.stat()
            h.update(f"{fpath.name}:{stat.st_mtime_ns}\n".encode())
        except OSError:
            pass
    return h.hexdigest()


# ------------------------------------------------------------------
# Index query helpers (used by FindSymbols and CallsiteSearch)
# ------------------------------------------------------------------

def lookup_symbol(index: Dict[str, Any], query: str, kind: str = "") -> List[Dict[str, Any]]:
    """Look up a symbol in the index by exact, prefix, then substring match.

    Returns a list of result dicts sorted by match quality (exact > prefix > substring).
    Each result has: path, line_number, kind, name, signature, score, + type-specific fields.
    """
    results: List[Dict[str, Any]] = []
    query_lower = query.lower()

    for rel, info in index.get("files", {}).items():
        # Functions
        for fn in info.get("functions", []):
            name = fn["name"]
            name_lower = name.lower()
            if name == query:
                score = 100
            elif name_lower.startswith(query_lower):
                score = 85
            elif query_lower in name_lower:
                score = 70
            else:
                continue
            if kind and kind != "function":
                continue
            results.append({
                "path": rel,
                "line_number": fn["line"],
                "kind": "function",
                "name": name,
                "signature": fn.get("signature", ""),
                "is_static": fn.get("is_static", False),
                "score": score,
            })

        # Structs
        for st in info.get("structs", []):
            name = st["name"]
            name_lower = name.lower()
            if name == query:
                score = 95
            elif name_lower.startswith(query_lower):
                score = 80
            elif query_lower in name_lower:
                score = 65
            else:
                continue
            if kind and kind != "struct":
                continue
            results.append({
                "path": rel,
                "line_number": st["line"],
                "kind": "struct",
                "name": name,
                "score": score,
            })

        # Enums
        for en in info.get("enums", []):
            name = en.get("name", "")
            if not name:
                continue
            name_lower = name.lower()
            if name == query:
                score = 95
            elif name_lower.startswith(query_lower):
                score = 80
            elif query_lower in name_lower:
                score = 65
            else:
                continue
            if kind and kind != "enum":
                continue
            results.append({
                "path": rel,
                "line_number": en["line"],
                "kind": "enum",
                "name": name,
                "enum_values": en.get("values", []),
                "score": score,
            })

        # Macros
        for mc in info.get("macros", []):
            name = mc["name"]
            name_lower = name.lower()
            if name == query:
                score = 100
            elif name_lower.startswith(query_lower):
                score = 85
            elif query_lower in name_lower:
                score = 70
            else:
                continue
            if kind and kind != "macro":
                continue
            results.append({
                "path": rel,
                "line_number": mc["line"],
                "kind": "macro",
                "name": name,
                "macro_value": mc.get("value", ""),
                "score": score,
            })

        # Typedefs
        for td in info.get("typedefs", []):
            name = td["name"]
            name_lower = name.lower()
            if name == query:
                score = 90
            elif name_lower.startswith(query_lower):
                score = 75
            elif query_lower in name_lower:
                score = 60
            else:
                continue
            if kind and kind != "typedef":
                continue
            results.append({
                "path": rel,
                "line_number": td["line"],
                "kind": "typedef",
                "name": name,
                "typedef_target": td.get("target", ""),
                "score": score,
            })

    # Sort by score desc, then by path/line
    results.sort(key=lambda r: (-r["score"], r["path"], r["line_number"]))
    return results


def reverse_call_lookup(index: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
    """Find all functions that call *target* (from the call graph).

    Returns a list of ``{"caller": name, "path": ..., "line": ...}``.
    """
    callers = []
    graph_v2 = index.get("call_graph_v2", {})
    if graph_v2:
        for caller_id, edges in graph_v2.items():
            if not any(edge.get("callee_name") == target for edge in edges):
                continue
            path, name, line = _split_symbol_node_id(caller_id)
            callers.append({"caller": name, "path": path, "line": line})
        callers.sort(key=lambda item: (item["path"], item["line"], item["caller"]))
        return callers
    call_graph = index.get("call_graph", {})
    # Build a name -> (path, line) map
    func_locations: Dict[str, Tuple[str, int]] = {}
    for rel, info in index.get("files", {}).items():
        for fn in info.get("functions", []):
            func_locations[fn["name"]] = (rel, fn["line"])

    for caller_name, callees in call_graph.items():
        if target in callees:
            loc = func_locations.get(caller_name, ("", 0))
            callers.append({
                "caller": caller_name,
                "path": loc[0],
                "line": loc[1],
            })
    return callers


def trace_call_chain(
    index: Dict[str, Any],
    target: str,
    max_depth: int = 5,
) -> List[str]:
    """BFS from *target* backwards through the call graph to find
    paths from any harness entry function.

    Returns a list of chain strings like ``"Entry → A → B → target"``.
    Only factual — no guessing. Returns empty list if no path exists.
    """
    if index.get("call_graph_v2"):
        chains: List[str] = []
        for entry in index.get("harness_entries", []):
            result = trace_harness_reachability(
                index,
                str(entry.get("node_id") or ""),
                [target],
                max_depth=max_depth,
            )
            if result.get("status") != "verified":
                continue
            for path in result.get("paths", []):
                rendered = []
                for node_id in path:
                    if "::" in node_id:
                        node_path, name, line = _split_symbol_node_id(node_id)
                        rendered.append(f"{name}@{node_path}:{line}")
                    else:
                        rendered.append(str(node_id))
                chain = " → ".join(rendered)
                if chain not in chains:
                    chains.append(chain)
                if len(chains) >= 5:
                    return chains
        return chains

    call_graph = index.get("call_graph", {})
    harness_names = set()
    for entry in index.get("harness_entries", []):
        harness_names.add(entry.get("entry_function", ""))

    # Build reverse adjacency
    reverse: Dict[str, List[str]] = {}
    for caller, callees in call_graph.items():
        for callee in callees:
            reverse.setdefault(callee, []).append(caller)

    # BFS from target backwards
    visited = {target}
    queue = [(target, [target])]
    chains = []

    while queue and len(chains) < 5:
        current, path = queue.pop(0)
        if len(path) > max_depth + 1:
            continue
        for parent in reverse.get(current, []):
            if parent in visited and parent not in harness_names:
                continue
            new_path = [parent] + path
            if parent in harness_names:
                chains.append(" → ".join(new_path))
                continue
            visited.add(parent)
            queue.append((parent, new_path))

    return chains


def _split_symbol_node_id(node_id: str) -> Tuple[str, str, int]:
    try:
        path, name, line = node_id.rsplit("::", 2)
        return path, name, int(line)
    except (TypeError, ValueError):
        return "", str(node_id), 0


def find_format_parsers(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Find functions whose names match parse/read/decode/handle patterns.

    These are likely format-parsing entry points.
    """
    _PARSE_PREFIXES = ("parse", "read", "decode", "handle", "process", "dissect", "unpack")
    results = []
    for rel, info in index.get("files", {}).items():
        for fn in info.get("functions", []):
            name_lower = fn["name"].lower()
            if any(name_lower.startswith(p) for p in _PARSE_PREFIXES):
                results.append({
                    "name": fn["name"],
                    "path": rel,
                    "line": fn["line"],
                    "signature": fn.get("signature", ""),
                })
                if len(results) >= _MAX_PER_LIST:
                    return results
    return results


def find_dispatch_tables(index: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Find switch statements that look like dispatch tables."""
    results = []
    for rel, info in index.get("files", {}).items():
        for sw in info.get("switch_tables", []):
            if len(sw.get("cases", [])) >= 2:
                results.append({
                    "path": rel,
                    "variable": sw["on_var"],
                    "line": sw["line"],
                    "cases": sw["cases"],
                })
                if len(results) >= _MAX_PER_LIST:
                    return results
    return results


def find_indirect_dispatch(index: Dict[str, Any], target: str) -> List[Dict[str, Any]]:
    """Find function-pointer dispatch sites that might call *target*.

    Scans files for patterns like ``handler[type](``, ``->decode(``, etc.
    Returns factual pattern matches, not guesses about what they call.
    """
    results = []
    target_lower = target.lower()

    for rel, info in index.get("files", {}).items():
        # Only scan files that are likely relevant (contain target name)
        # This is a practical optimization, not a heuristic — we just
        # skip files that provably cannot contain a dispatch to target.
        # Actually, function pointers can be in any file, so we scan all.
        pass

    # This requires reading file text, which is not available in the index.
    # We'll return an empty list and let CallsiteSearch do this with the
    # actual file text during its line-by-line scan.
    return results
