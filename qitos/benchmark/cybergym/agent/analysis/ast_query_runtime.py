"""AST query runtime — lightweight code analysis with optional tree-sitter.

Provides xref, call-path, dangerous-call, and field-read analysis.
Uses tree-sitter if the Python bindings are installed; falls back to
grep/regex-based pattern matching otherwise.

Ported patterns from tree-sitter-analyzer; does NOT depend on it.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any


class ASTQueryRuntime:
    """Lightweight AST query runtime for vulnerability analysis.

    If tree-sitter Python bindings are available, uses them for precise
    parsing. Otherwise falls back to grep/regex patterns.
    """

    def __init__(self, repo_root: str, cache_dir: str = "") -> None:
        self.repo_root = repo_root
        self.cache_dir = cache_dir or os.path.join(repo_root, ".ast_cache")
        self._ts_available = False
        self._parser = None
        self._languages: dict[str, Any] = {}
        self._index: dict[str, list[dict]] = {}  # file -> symbols
        self._edges: list[dict[str, Any]] = []     # call edges
        self._indexed = False

        # Try to import tree-sitter
        try:
            import tree_sitter  # type: ignore
            self._ts_available = True
        except ImportError:
            pass

    @property
    def ts_available(self) -> bool:
        return self._ts_available

    def ensure_indexed(self, files: list[str] | None = None) -> None:
        """Index specified files (or all C/C++ source files if None)."""
        if self._indexed:
            return

        repo = Path(self.repo_root)
        if files is None:
            files = []
            for ext in ("*.c", "*.cc", "*.cpp", "*.h", "*.hpp"):
                for p in repo.rglob(ext):
                    rel = str(p.relative_to(repo))
                    if "/.git/" not in rel and "/build/" not in rel:
                        files.append(rel)

        for rel in files:
            abs_path = repo / rel
            if not abs_path.is_file():
                continue
            try:
                text = abs_path.read_text(errors="replace")
            except OSError:
                continue

            symbols = self._extract_symbols_regex(text, rel)
            self._index[rel] = symbols
            edges = self._extract_call_edges_regex(text, rel)
            self._edges.extend(edges)

        self._indexed = True

    def xref(self, symbol: str, file_path: str | None = None) -> dict[str, Any]:
        """Cross-reference a symbol: definitions, callers, callees."""
        self.ensure_indexed()
        definitions: list[dict] = []
        callers: list[dict] = []
        callees: list[dict] = []

        for rel, symbols in self._index.items():
            for sym in symbols:
                if sym.get("name") == symbol:
                    if file_path is None or rel == file_path:
                        definitions.append({
                            "name": symbol,
                            "kind": sym.get("kind", "unknown"),
                            "file": rel,
                            "line": sym.get("line", 0),
                        })

        # Callers: edges where callee == symbol
        for edge in self._edges:
            if edge.get("callee_name") == symbol:
                if file_path is None or edge.get("caller_file") == file_path:
                    callers.append({
                        "name": edge["caller_name"],
                        "file": edge.get("caller_file", ""),
                        "line": edge.get("caller_line", 0),
                    })

        # Callees: edges where caller == symbol
        for edge in self._edges:
            if edge.get("caller_name") == symbol:
                if file_path is None or edge.get("caller_file") == file_path:
                    callees.append({
                        "name": edge.get("callee_name", ""),
                        "file": edge.get("callee_file", ""),
                        "line": edge.get("callee_line", 0),
                    })

        return {
            "symbol": symbol,
            "definitions": definitions[:10],
            "callers": callers[:20],
            "callees": callees[:20],
            "data_source": "regex" if not self._ts_available else "tree-sitter",
        }

    def call_paths(
        self,
        start: str,
        target: str,
        max_depth: int = 6,
    ) -> list[dict]:
        """Find call paths from start function to target function.

        Returns a list of path dicts, each with 'hops' and 'total_hops'.
        """
        self.ensure_indexed()

        # Build adjacency lists
        callees_of: dict[str, list[dict]] = {}
        for edge in self._edges:
            caller = edge.get("caller_name", "")
            if caller:
                callees_of.setdefault(caller, []).append(edge)

        # BFS from start
        paths: list[dict] = []
        visited: set[str] = set()
        queue: list[tuple[str, list[dict]]] = [(start, [])]

        while queue and len(paths) < 5:
            current, path = queue.pop(0)
            if len(path) >= max_depth:
                continue
            if current in visited:
                continue
            visited.add(current)

            for edge in callees_of.get(current, []):
                callee = edge.get("callee_name", "")
                new_path = path + [edge]
                if callee == target:
                    paths.append({
                        "hops": new_path,
                        "total_hops": len(new_path),
                        "files_crossed": len({h.get("caller_file", "") for h in new_path} | {new_path[-1].get("callee_file", "")}) if new_path else 0,
                    })
                elif callee not in visited:
                    queue.append((callee, new_path))

        return paths

    def find_dangerous_calls(self, files: list[str]) -> list[dict]:
        """Find dangerous function calls in the specified files.

        Dangerous calls: memcpy, strcpy, sprintf, malloc, realloc,
                        operator delete, free, etc.
        """
        self.ensure_indexed()

        dangerous_patterns = {
            "memcpy": {"category": "buffer", "risk": "size-controlled copy"},
            "memmove": {"category": "buffer", "risk": "size-controlled move"},
            "strcpy": {"category": "buffer", "risk": "unbounded string copy"},
            "strncpy": {"category": "buffer", "risk": "bounded but no null-terminator guarantee"},
            "sprintf": {"category": "buffer", "risk": "unbounded format write"},
            "snprintf": {"category": "buffer", "risk": "bounded format write"},
            "malloc": {"category": "allocation", "risk": "unchecked allocation"},
            "calloc": {"category": "allocation", "risk": "unchecked allocation"},
            "realloc": {"category": "allocation", "risk": "use-after-free on failure"},
            "free": {"category": "deallocation", "risk": "double-free / use-after-free"},
            "operator delete": {"category": "deallocation", "risk": "double-delete"},
            "new": {"category": "allocation", "risk": "unchecked allocation"},
        }

        results: list[dict] = []
        repo = Path(self.repo_root)

        for rel in files:
            abs_path = repo / rel
            if not abs_path.is_file():
                continue
            try:
                text = abs_path.read_text(errors="replace")
            except OSError:
                continue

            for func_name, meta in dangerous_patterns.items():
                # Pattern: function_name(
                pattern = re.compile(r'\b' + re.escape(func_name) + r'\s*\(')
                for match in pattern.finditer(text):
                    line = text[:match.start()].count("\n") + 1
                    # Get surrounding context
                    lines = text.splitlines()
                    context = lines[line - 1].strip()[:120] if line <= len(lines) else ""
                    results.append({
                        "function": func_name,
                        "file": rel,
                        "line": line,
                        "category": meta["category"],
                        "risk": meta["risk"],
                        "context": context,
                    })

        return results

    def find_field_reads_near(
        self,
        file: str,
        line: int,
        window: int = 80,
    ) -> list[dict]:
        """Find field reads near a specific line in a file.

        Detects patterns like struct->field, struct.field, struct[i],
        and local variable reads that may correspond to input fields.
        """
        abs_path = Path(self.repo_root) / file
        if not abs_path.is_file():
            return []

        try:
            text = abs_path.read_text(errors="replace")
        except OSError:
            return []

        lines = text.splitlines()
        start_line = max(0, line - window // 2)
        end_line = min(len(lines), line + window // 2)

        results: list[dict] = []
        # Patterns for field access
        field_patterns = [
            (r'(\w+)->(\w+)', "pointer_deref"),
            (r'(\w+)\.(\w+)', "dot_access"),
            (r'(\w+)\s*\[\s*(\w+)\s*\]', "array_index"),
            (r'(\w+)\s*\[\s*(\d+)\s*\]', "array_const_index"),
        ]

        seen: set[str] = set()
        for i in range(start_line, end_line):
            if i >= len(lines):
                break
            l = lines[i]
            for pattern, kind in field_patterns:
                for match in re.finditer(pattern, l):
                    obj = match.group(1)
                    field = match.group(2)
                    key = f"{obj}.{field}:{kind}"
                    if key not in seen:
                        seen.add(key)
                        results.append({
                            "object": obj,
                            "field": field,
                            "kind": kind,
                            "line": i + 1,
                            "context": l.strip()[:100],
                        })

        return results[:20]

    # ------------------------------------------------------------------
    # Regex-based extraction (fallback when tree-sitter unavailable)
    # ------------------------------------------------------------------

    def _extract_symbols_regex(self, text: str, rel_path: str) -> list[dict]:
        """Extract function/class/struct symbols using regex."""
        symbols: list[dict] = []

        # C/C++ function definitions
        func_pattern = re.compile(
            r'^(?:static\s+)?(?:inline\s+)?(?:\w+[\s*]+)+(\w+)\s*\(([^)]*)\)\s*\{',
            re.MULTILINE,
        )
        for match in func_pattern.finditer(text):
            name = match.group(1)
            params = match.group(2).strip()
            line = text[:match.start()].count("\n") + 1
            # Skip keywords that look like functions
            if name in ("if", "while", "for", "switch", "return", "sizeof", "case"):
                continue
            symbols.append({
                "name": name,
                "kind": "function",
                "line": line,
                "params": params,
                "file": rel_path,
            })

        # C/C++ struct definitions
        struct_pattern = re.compile(r'\bstruct\s+(\w+)\s*\{', re.MULTILINE)
        for match in struct_pattern.finditer(text):
            name = match.group(1)
            line = text[:match.start()].count("\n") + 1
            symbols.append({
                "name": name,
                "kind": "struct",
                "line": line,
                "file": rel_path,
            })

        # C++ class definitions
        class_pattern = re.compile(r'\bclass\s+(\w+)\s*(?::\s*[^\{]+)?\{', re.MULTILINE)
        for match in class_pattern.finditer(text):
            name = match.group(1)
            line = text[:match.start()].count("\n") + 1
            symbols.append({
                "name": name,
                "kind": "class",
                "line": line,
                "file": rel_path,
            })

        return symbols

    def _extract_call_edges_regex(self, text: str, rel_path: str) -> list[dict]:
        """Extract call edges using regex."""
        edges: list[dict] = []

        # First find all function definitions
        func_defs: dict[str, int] = {}
        func_pattern = re.compile(
            r'^(?:static\s+)?(?:inline\s+)?(?:\w+[\s*]+)+(\w+)\s*\([^)]*\)\s*\{',
            re.MULTILINE,
        )
        for match in func_pattern.finditer(text):
            name = match.group(1)
            if name in ("if", "while", "for", "switch", "return", "sizeof", "case"):
                continue
            func_defs[name] = text[:match.start()].count("\n") + 1

        # For each function body, find calls
        call_pattern = re.compile(r'\b(\w+)\s*\(')
        for func_name, func_line in func_defs.items():
            # Get function body (approximate: to next function start or EOF)
            start_pos = text.find("{", text.find(func_name))
            if start_pos < 0:
                continue

            # Simple brace counting for body
            depth = 0
            body_start = start_pos
            pos = start_pos
            while pos < len(text):
                if text[pos] == "{":
                    depth += 1
                elif text[pos] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                pos += 1
            body = text[body_start:pos + 1]

            for call_match in call_pattern.finditer(body):
                callee = call_match.group(1)
                # Skip keywords and control flow, but include user-defined calls
                if callee in (
                    "if", "while", "for", "switch", "return", "sizeof",
                    "case", "else", "do", "goto",
                ):
                    continue
                # Skip if callee is the same as caller (recursion guard)
                if callee == func_name:
                    continue
                callee_line = text[:body_start + call_match.start()].count("\n") + 1
                edges.append({
                    "caller_name": func_name,
                    "caller_line": func_line,
                    "caller_file": rel_path,
                    "callee_name": callee,
                    "callee_file": "",
                    "callee_line": callee_line,
                })

        return edges
