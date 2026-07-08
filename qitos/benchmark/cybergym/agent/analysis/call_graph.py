"""Call Graph — Bidirectional function-level call tracking for C/C++.

Absorbed from tree-sitter-analyzer's call_graph.py, trimmed to C/C++ only,
with C++ import extraction inlined and CachedCallGraph removed.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Optional

from .callee_resolution import CalleeResolver
from .function_extraction import walk_tree as _walk_tree

_LOG = logging.getLogger(__name__)

_EXCLUDE_DIRS = {
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "dist",
    "build",
    "htmlcov",
    ".cache",
    ".eggs",
    ".idea",
    ".vscode",
    ".claude",
}

_SUPPORTED_EXTS = {".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hh", ".hxx"}


# ---------------------------------------------------------------------------
# Language from extension (inlined from _language_from_ext)
# ---------------------------------------------------------------------------

def _language_from_ext(file_path: str) -> str | None:
    """Guess C/C++ language from file extension."""
    _, ext = os.path.splitext(file_path)
    ext = ext.lower()
    if ext in (".cpp", ".cc", ".cxx", ".hpp", ".hh", ".hxx"):
        return "cpp"
    if ext == ".c":
        return "c"
    if ext == ".h":
        return "c"  # ambiguous; default to c, caller may override
    return None


# ---------------------------------------------------------------------------
# C/C++ import extraction (inlined from import_extractors/_cpp.py)
# ---------------------------------------------------------------------------

def _node_text_simple(node: Any, source: str) -> str:
    """Extract text from a node using byte offsets."""
    if node is None:
        return ""
    text_attr = getattr(node, "text", None)
    if isinstance(text_attr, bytes):
        return text_attr.decode("utf-8", errors="replace")
    if isinstance(text_attr, str):
        return text_attr
    try:
        return source.encode("utf-8")[node.start_byte:node.end_byte].decode(
            "utf-8", errors="replace"
        )
    except (IndexError, TypeError, UnicodeDecodeError):
        return ""


def _walk_cpp_imports(
    node: Any, source: str, imports: list[dict[str, Any]]
) -> None:
    """Walk AST collecting #include directives for C/C++."""
    if getattr(node, "type", None) == "preproc_include":
        for child in node.children:
            ct = getattr(child, "type", None)
            if ct == "string_literal":
                raw = _node_text_simple(child, source).strip('"')
                if raw:
                    imports.append({
                        "module_name": raw,
                        "resolved_path": raw,
                        "names": [],
                        "is_relative": True,
                        "language": "cpp",
                    })
                return
            if ct == "system_lib_string":
                raw = _node_text_simple(child, source).strip("<>")
                if raw:
                    imports.append({
                        "module_name": raw,
                        "resolved_path": raw,
                        "names": [],
                        "is_relative": False,
                        "language": "cpp",
                    })
                return
    children = getattr(node, "children", None)
    if isinstance(children, (list, tuple)):
        for child in children:
            _walk_cpp_imports(child, source, imports)


# ---------------------------------------------------------------------------
# FunctionRef
# ---------------------------------------------------------------------------

class FunctionRef:
    """A qualified reference to a function/method in the project."""

    __slots__ = ("file_path", "name", "start_line", "end_line", "language", "receiver")

    def __init__(
        self,
        file_path: str,
        name: str,
        start_line: int,
        language: str,
        receiver: str | None = None,
        end_line: int | None = None,
    ) -> None:
        self.file_path = file_path
        self.name = name
        self.start_line = start_line
        self.end_line = end_line if end_line is not None else start_line
        self.language = language
        self.receiver = receiver

    def qualified_name(self) -> str:
        if self.receiver:
            return f"{self.file_path}:{self.receiver}.{self.name}"
        return f"{self.file_path}:{self.name}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FunctionRef):
            return NotImplemented
        return (
            self.file_path == other.file_path
            and self.name == other.name
            and self.start_line == other.start_line
        )

    def __hash__(self) -> int:
        return hash((self.file_path, self.name, self.start_line))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "file": self.file_path,
            "name": self.name,
            "line": self.start_line,
            "end_line": self.end_line,
            "language": self.language,
        }
        if self.receiver:
            d["receiver"] = self.receiver
        return d


# ---------------------------------------------------------------------------
# CallGraph
# ---------------------------------------------------------------------------

class CallGraph:
    """Project-level function call graph for C/C++.

    Nodes: FunctionRef objects representing function definitions.
    Edges: A -> B means function A calls function B.

    The call graph supports bidirectional queries:
    - callers_of(func_name): who calls this function?
    - callees_of(func_name): what does this function call?
    """

    def __init__(self, project_root: str) -> None:
        self.project_root = Path(project_root).resolve()
        self._functions: list[FunctionRef] = []
        self._func_by_name: dict[str, list[FunctionRef]] = defaultdict(list)
        self._func_by_qualified: dict[str, FunctionRef] = {}
        self._func_by_file: dict[str, list[FunctionRef]] = defaultdict(list)
        self._callees: dict[FunctionRef, list[FunctionRef]] = defaultdict(list)
        self._callers: dict[FunctionRef, list[FunctionRef]] = defaultdict(list)
        self._call_edges: list[tuple[FunctionRef, FunctionRef, int]] = []
        self._built = False
        self._imported_names: dict[str, dict[str, str]] = {}
        self._module_to_file: dict[str, str] = {}
        self._callee_resolver: CalleeResolver | None = None

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def build(self) -> None:
        """Scan the project and build the call graph."""
        if self._built:
            return

        all_files = self._iter_source_files()
        rel_to_abs: dict[str, str] = {}
        for f in all_files:
            try:
                rel = str(f.relative_to(self.project_root))
                rel_to_abs[rel] = str(f)
            except ValueError:
                continue

        from .parser import Parser

        parser = Parser()

        # Two-pass build: Pass 1 indexes definitions; Pass 2 resolves calls.
        per_file: list[
            tuple[str, str, str, dict[str, FunctionRef], list[dict[str, Any]]]
        ] = []

        for rel_path, abs_path in rel_to_abs.items():
            language = _language_from_ext(rel_path)
            if language is None:
                continue

            result = parser.parse_file(abs_path, language)
            if result is None:
                continue

            source_bytes = result.source
            source = source_bytes.decode("utf-8", errors="replace") if isinstance(source_bytes, bytes) else source_bytes
            root = result.root

            definitions, calls = _walk_tree(root, source, language, result._line_table)

            # Collect #include imports
            imports: list[dict[str, Any]] = []
            _walk_cpp_imports(root, source, imports)
            self._collect_import_map(rel_path, imports, rel_to_abs)

            file_funcs: dict[str, FunctionRef] = {}
            for defn in definitions:
                ref = FunctionRef(
                    file_path=rel_path,
                    name=defn["name"],
                    start_line=defn["start_line"],
                    language=language,
                    receiver=defn.get("class"),
                    end_line=defn.get("end_line", defn["start_line"]),
                )
                self._functions.append(ref)
                self._func_by_name[defn["name"]].append(ref)
                self._func_by_file[rel_path].append(ref)
                qname = ref.qualified_name()
                self._func_by_qualified[qname] = ref
                file_funcs[f"{defn['name']}:{defn['start_line']}"] = ref

            per_file.append((rel_path, abs_path, language, file_funcs, calls))

        self._callee_resolver = CalleeResolver(
            functions_by_name=self._func_by_name,
            functions_by_file=self._func_by_file,
            name_to_source=self._imported_names,
        )

        # Pass 2: resolve calls against the fully-populated index.
        for rel_path, _abs_path, _language, file_funcs, calls in per_file:
            for call in calls:
                caller_ref = self._find_enclosing_func(file_funcs, call["line"])
                if caller_ref is None:
                    continue
                callee_refs = self._resolve_callee(call, rel_path)
                for callee_ref in callee_refs:
                    self._callees[caller_ref].append(callee_ref)
                    self._callers[callee_ref].append(caller_ref)
                    self._call_edges.append((caller_ref, callee_ref, call["line"]))

        self._build_module_to_file_map(rel_to_abs)
        self._built = True

    # ------------------------------------------------------------------
    # File iteration
    # ------------------------------------------------------------------

    def _is_excluded(self, path: Path) -> bool:
        try:
            rel_parts = path.relative_to(self.project_root).parts
        except ValueError:
            rel_parts = path.parts
        return any(part in _EXCLUDE_DIRS or part.startswith(".") for part in rel_parts)

    def _iter_source_files(self) -> list[Path]:
        files: list[Path] = []
        for root, dirs, names in os.walk(self.project_root):
            dirs[:] = [
                name
                for name in dirs
                if name not in _EXCLUDE_DIRS and not name.startswith(".")
            ]
            for name in names:
                if name.startswith("."):
                    continue
                if Path(name).suffix.lower() in _SUPPORTED_EXTS:
                    files.append(Path(root) / name)
        return files

    # ------------------------------------------------------------------
    # Import handling
    # ------------------------------------------------------------------

    def _collect_import_map(
        self,
        rel_path: str,
        imports: list[dict[str, Any]],
        rel_to_abs: dict[str, str],
    ) -> None:
        name_to_source: dict[str, str] = {}
        for imp in imports:
            resolved = imp.get("resolved_path", "")
            is_relative = imp.get("is_relative", False)
            target_file = self._resolve_import_path(
                rel_path, resolved, is_relative, rel_to_abs
            )
            if target_file:
                # For C/C++, the included header name maps to the target file.
                # We don't extract individual symbol names from #include.
                names = imp.get("names", [])
                header_name = resolved.replace("/", "_").replace(".", "_").rstrip("_h")
                if not names:
                    names = [header_name]
                for name in names:
                    name_to_source[name] = target_file
        if name_to_source:
            self._imported_names[rel_path] = name_to_source

    def _resolve_import_path(
        self,
        source_rel: str,
        resolved_path: str,
        is_relative: bool,
        rel_to_abs: dict[str, str],
    ) -> str:
        if not resolved_path:
            return ""
        # For C/C++, try to find the header file directly.
        # Relative includes: resolve relative to the source file's directory.
        if is_relative:
            source_dir = str(Path(source_rel).parent)
            candidate = str(Path(source_dir) / resolved_path)
            if candidate in rel_to_abs:
                return candidate
            # Try with common extensions
            for ext in ("", ".h", ".hpp", ".hh", ".hxx"):
                check = candidate + ext
                if check in rel_to_abs:
                    return check
        else:
            # System / absolute include: search from project root
            if resolved_path in rel_to_abs:
                return resolved_path
            # Try as a project-relative path
            for ext in ("", ".h", ".hpp", ".hh", ".hxx"):
                check = resolved_path + ext
                if check in rel_to_abs:
                    return check
        return ""

    def _build_module_to_file_map(self, rel_to_abs: dict[str, str]) -> None:
        for rel_path in rel_to_abs:
            p = Path(rel_path)
            # For C/C++, the stem is the module identifier
            module_name = str(p.with_suffix("")).replace("/", ".").replace("\\", ".")
            self._module_to_file[module_name] = rel_path

    # ------------------------------------------------------------------
    # Enclosing function and callee resolution
    # ------------------------------------------------------------------

    def _find_enclosing_func(
        self,
        file_funcs: dict[str, FunctionRef],
        call_line: int,
    ) -> FunctionRef | None:
        """Find the function whose span contains the given line number."""
        best: FunctionRef | None = None
        for ref in file_funcs.values():
            if ref.start_line <= call_line <= ref.end_line:
                if best is None or (
                    (ref.end_line - ref.start_line) < (best.end_line - best.start_line)
                ):
                    best = ref
        return best

    def _resolve_callee(
        self,
        call: dict[str, Any],
        source_rel: str,
    ) -> list[FunctionRef]:
        name = call["name"]
        if self._callee_resolver is None:
            self._callee_resolver = CalleeResolver(
                functions_by_name=self._func_by_name,
                functions_by_file=self._func_by_file,
                name_to_source=self._imported_names,
            )
        return [
            ref
            for ref, _confidence in self._callee_resolver.resolve_items(
                name,
                source_rel,
            )
        ]

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def callers_of(
        self, func_name: str, file_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Find all functions that call the given function."""
        self.build()
        targets = self._resolve_targets(func_name, file_path)
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for target in targets:
            for caller in self._callers.get(target, []):
                key = caller.qualified_name()
                if key not in seen:
                    seen.add(key)
                    result.append(caller.to_dict())
        return result

    def callees_of(
        self, func_name: str, file_path: str | None = None
    ) -> list[dict[str, Any]]:
        """Find all functions called by the given function."""
        self.build()
        targets = self._resolve_targets(func_name, file_path)
        seen: set[str] = set()
        result: list[dict[str, Any]] = []
        for target in targets:
            for callee in self._callees.get(target, []):
                key = callee.qualified_name()
                if key not in seen:
                    seen.add(key)
                    result.append(callee.to_dict())
        return result

    def call_chain(
        self, func_name: str, file_path: str | None = None, depth: int = 5
    ) -> list[dict[str, Any]]:
        """Trace the full call chain from a function (callees, transitively)."""
        self.build()
        targets = self._resolve_targets(func_name, file_path)
        result: list[dict[str, Any]] = []
        visited: set[str] = set()
        queue: deque[tuple[FunctionRef, int]] = deque((t, 0) for t in targets)

        while queue:
            current, d = queue.popleft()
            if d >= depth:
                continue
            for callee in self._callees.get(current, []):
                key = f"{current.qualified_name()}->{callee.qualified_name()}"
                if key not in visited:
                    visited.add(key)
                    result.append({
                        "caller": current.to_dict(),
                        "callee": callee.to_dict(),
                        "depth": d + 1,
                    })
                    queue.append((callee, d + 1))
        return result

    def all_functions(self) -> list[dict[str, Any]]:
        """Return all discovered functions as dicts."""
        self.build()
        return [f.to_dict() for f in self._functions]

    def call_edges(self) -> list[tuple[FunctionRef, FunctionRef, int]]:
        """Return all call edges as (caller, callee, line) tuples."""
        self.build()
        return self._call_edges

    def all_call_edges(self) -> list[tuple[FunctionRef, FunctionRef, int]]:
        """Return a copy of all call edges."""
        return list(self.call_edges())

    def function_refs(self) -> list[FunctionRef]:
        """Return all discovered functions as FunctionRef objects."""
        self.build()
        return self._functions

    def callee_refs_of(self, func: FunctionRef) -> list[FunctionRef]:
        """Return callees of *func* as FunctionRef objects."""
        self.build()
        return list(self._callees.get(func, []))

    def caller_refs_of(self, func: FunctionRef) -> list[FunctionRef]:
        """Return callers of *func* as FunctionRef objects."""
        self.build()
        return list(self._callers.get(func, []))

    def callers_map(self) -> dict[FunctionRef, list[FunctionRef]]:
        """Return a shallow copy of the caller adjacency map."""
        self.build()
        return dict(self._callers)

    def callees_map(self) -> dict[FunctionRef, list[FunctionRef]]:
        """Return a shallow copy of the callee adjacency map."""
        self.build()
        return dict(self._callees)

    def functions_by_file(self) -> dict[str, list[FunctionRef]]:
        """Return a shallow copy of the file → FunctionRef list mapping."""
        self.build()
        return dict(self._func_by_file)

    def functions_in_file(self, file_path: str) -> list[dict[str, Any]]:
        """Return all functions defined in the given file."""
        self.build()
        return [f.to_dict() for f in self._func_by_file.get(file_path, [])]

    def function_refs_in_file(self, file_path: str) -> list[FunctionRef]:
        """Return FunctionRef objects for functions in *file_path*."""
        self.build()
        return list(self._func_by_file.get(file_path, []))

    def resolve_targets(
        self, func_name: str, file_path: str | None = None
    ) -> list[FunctionRef]:
        """Resolve a function name (and optional file) to FunctionRef(s)."""
        self.build()
        return self._resolve_targets(func_name, file_path)

    def file_impact(self, file_path: str) -> dict[str, Any]:
        """Analyze call-graph impact of changes to a file."""
        self.build()
        funcs = self._func_by_file.get(file_path, [])
        upstream: list[dict[str, Any]] = []
        downstream: list[dict[str, Any]] = []
        seen_up: set[str] = set()
        seen_down: set[str] = set()
        for func in funcs:
            for caller in self._callers.get(func, []):
                key = caller.qualified_name()
                if key not in seen_up:
                    seen_up.add(key)
                    upstream.append(caller.to_dict())
            for callee in self._callees.get(func, []):
                key = callee.qualified_name()
                if key not in seen_down:
                    seen_down.add(key)
                    downstream.append(callee.to_dict())
        return {
            "file": file_path,
            "function_count": len(funcs),
            "upstream_count": len(upstream),
            "downstream_count": len(downstream),
            "upstream": upstream,
            "downstream": downstream,
        }

    def summary(self) -> dict[str, Any]:
        """Return call graph summary statistics."""
        self.build()
        return {
            "function_count": len(self._functions),
            "call_edge_count": len(self._call_edges),
            "file_count": len({f.file_path for f in self._functions}),
        }

    @property
    def is_built(self) -> bool:
        return bool(self._built)

    # ------------------------------------------------------------------
    # Internal resolution
    # ------------------------------------------------------------------

    def _resolve_targets(
        self, func_name: str, file_path: str | None = None
    ) -> list[FunctionRef]:
        """Resolve a function name (and optional file) to FunctionRef(s)."""
        if file_path:
            qname = f"{file_path}:{func_name}"
            ref = self._func_by_qualified.get(qname)
            if ref:
                return [ref]

        if "." in func_name:
            receiver, _, suffix = func_name.rpartition(".")
            if receiver and suffix:
                bare_candidates = self._func_by_name.get(suffix, [])
                receiver_matches = [
                    c for c in bare_candidates if c.receiver == receiver
                ]
                if receiver_matches:
                    if file_path:
                        same = [c for c in receiver_matches if c.file_path == file_path]
                        return same if same else receiver_matches
                    return receiver_matches

        candidates = self._func_by_name.get(func_name, [])
        if file_path:
            same = [c for c in candidates if c.file_path == file_path]
            return same if same else candidates
        return candidates
