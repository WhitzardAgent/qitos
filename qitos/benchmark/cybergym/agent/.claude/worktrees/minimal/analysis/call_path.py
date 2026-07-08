"""Call Path Finder — BFS-based execution path discovery for C/C++.

Absorbed from tree-sitter-analyzer's call_path.py, keeping only the
in-memory BFS backend (SQL backend removed — depends on ast_cache which
we are not absorbing).

Supports forward, backward, and bidirectional BFS with file-aware path
signatures for deduplication.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .call_graph import CallGraph, FunctionRef

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Direction helpers — forward (callee) direction
# ---------------------------------------------------------------------------


def _graph_fwd_state(callee: dict[str, Any]) -> tuple[str, str | None]:
    return (callee.get("name", ""), callee.get("file", "") or None)


def _graph_fwd_hop(
    current_name: str, current_file: str | None, callee: dict[str, Any]
) -> dict[str, Any]:
    return {
        "caller": current_name,
        "caller_file": current_file or "",
        "callee": callee.get("name", ""),
        "callee_file": callee.get("file", ""),
        "line": callee.get("line", 0),
    }


# ---------------------------------------------------------------------------
# Direction helpers — backward (caller) direction
# ---------------------------------------------------------------------------


def _graph_bwd_state(caller: dict[str, Any]) -> tuple[str, str | None]:
    return (caller.get("name", ""), caller.get("file", "") or None)


def _graph_bwd_hop(
    current_name: str, current_file: str | None, caller: dict[str, Any]
) -> dict[str, Any]:
    return {
        "caller": caller.get("name", ""),
        "caller_file": caller.get("file", ""),
        "callee": current_name,
        "callee_file": current_file or "",
        "line": caller.get("line", 0),
    }


# ---------------------------------------------------------------------------
# Target matching
# ---------------------------------------------------------------------------


def _target_match(
    name: str,
    file_: str | None,
    target: tuple[str, str | None],
) -> bool:
    """Return True when (name, file_) matches the target (name, file) pair."""
    target_name, target_file = target
    if name != target_name:
        return False
    if target_file and file_ and file_ != target_file:
        return False
    return True


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CallChain:
    """A single call chain from source to target."""

    hops: list[dict[str, Any]] = field(default_factory=list)
    total_hops: int = 0
    files_crossed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hops": self.hops,
            "total_hops": self.total_hops,
            "files_crossed": self.files_crossed,
        }


@dataclass
class CallPathResult:
    """Result of a call path search."""

    source: str
    target: str
    paths: list[CallChain] = field(default_factory=list)
    data_source: str = "unknown"
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "path_count": len(self.paths),
            "truncated": self.truncated,
            "data_source": self.data_source,
            "paths": [p.to_dict() for p in self.paths],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _files_in_chain(hops: list[dict[str, Any]]) -> int:
    seen: set[str] = set()
    for hop in hops:
        f = hop.get("caller_file", "") or hop.get("callee_file", "")
        if f:
            seen.add(f)
    return len(seen)


def _make_chain(path: list[dict[str, Any]]) -> CallChain:
    return CallChain(
        hops=path, total_hops=len(path), files_crossed=_files_in_chain(path)
    )


def _path_signature(
    path: list[dict[str, Any]],
) -> tuple[tuple[str, str, str, str], ...]:
    """Stable signature for a hop list, used to dedup equivalent paths."""
    return tuple(
        (
            hop.get("caller", ""),
            hop.get("caller_file", ""),
            hop.get("callee", ""),
            hop.get("callee_file", ""),
        )
        for hop in path
    )


def _lookup_in_visited(
    state: tuple[str, str | None],
    visited: dict[tuple[str, str | None], list[dict[str, Any]]],
) -> tuple[bool, list[dict[str, Any]]]:
    """Lookup *state* in *visited*, with name-only wildcard support."""
    if state in visited:
        return True, visited[state]
    if state[1] is not None:
        name_only = (state[0], None)
        if name_only in visited:
            return True, visited[name_only]
    return False, []


# ---------------------------------------------------------------------------
# BFS engine (in-memory)
# ---------------------------------------------------------------------------


def _bfs_graph_core(
    get_neighbors: Callable,
    start_name: str,
    start_file: str | None,
    target_key: tuple[str, str | None],
    max_depth: int,
    max_paths: int,
    paths: list[Any],
    state_fn: Callable,
    hop_fn: Callable,
    prepend: bool,
) -> None:
    """BFS over an in-memory CallGraph in one direction."""
    queue: deque[tuple[str, str | None, list[dict[str, Any]]]] = deque(
        [(start_name, start_file, [])]
    )
    visited: set[tuple[str, str | None]] = {(start_name, start_file)}
    while queue and len(paths) < max_paths:
        current_name, current_file, path = queue.popleft()
        if len(path) >= max_depth:
            continue
        for neighbor in get_neighbors(current_name, current_file):
            hop = hop_fn(current_name, current_file, neighbor)
            new_path = [hop] + path if prepend else path + [hop]
            state = state_fn(neighbor)
            if _target_match(state[0], state[1], target_key):
                paths.append(_make_chain(new_path))
            elif state not in visited:
                visited.add(state)
                queue.append((*state, new_path))


# ---------------------------------------------------------------------------
# CallPathFinder
# ---------------------------------------------------------------------------


class CallPathFinder:
    """Find execution paths between two functions via BFS on a CallGraph.

    Parameters
    ----------
    project_root : str
        Root directory of the project to analyse.
    graph : CallGraph | None
        Optional pre-built CallGraph.  When *None* the finder will
        build one on first query.
    """

    def __init__(
        self, project_root: str, graph: CallGraph | None = None
    ) -> None:
        self._project_root = project_root
        self._graph = graph
        self._data_source = "unknown"

    def _ensure_graph(self) -> CallGraph:
        if self._graph is None:
            self._graph = CallGraph(self._project_root)
            self._graph.build()
            self._data_source = "parse"
        return self._graph

    def find_path(
        self,
        source_function: str,
        target_function: str,
        source_file: str | None = None,
        target_file: str | None = None,
        max_depth: int = 10,
        max_paths: int = 5,
        direction: str = "forward",
    ) -> CallPathResult:
        """Find call paths from *source_function* to *target_function*.

        Parameters
        ----------
        source_function : str
            Name of the starting function.
        target_function : str
            Name of the destination function.
        source_file, target_file : str | None
            Optional files to disambiguate.
        max_depth : int
            Maximum BFS depth (default 10).
        max_paths : int
            Maximum number of paths to return (default 5).
        direction : str
            ``"forward"`` (follow callees from source),
            ``"backward"`` (follow callers from target),
            or ``"bidirectional"`` (BFS from both ends).
        """
        graph = self._ensure_graph()

        paths: list[CallChain] = []

        if direction in ("forward", "bidirectional"):
            _bfs_graph_core(
                graph.callees_of,
                source_function,
                source_file,
                (target_function, target_file),
                max_depth,
                max_paths,
                paths,
                _graph_fwd_state,
                _graph_fwd_hop,
                prepend=False,
            )

        if direction == "backward":
            _bfs_graph_core(
                graph.callers_of,
                target_function,
                target_file,
                (source_function, source_file),
                max_depth,
                max_paths,
                paths,
                _graph_bwd_state,
                _graph_bwd_hop,
                prepend=True,
            )
        elif direction == "bidirectional" and len(paths) < max_paths:
            backward_paths: list[CallChain] = []
            _bfs_graph_core(
                graph.callers_of,
                target_function,
                target_file,
                (source_function, source_file),
                max_depth,
                max_paths,
                backward_paths,
                _graph_bwd_state,
                _graph_bwd_hop,
                prepend=True,
            )
            seen = {_path_signature(p.hops) for p in paths}
            for chain in backward_paths:
                if len(paths) >= max_paths:
                    break
                sig = _path_signature(chain.hops)
                if sig not in seen:
                    seen.add(sig)
                    paths.append(chain)

        return CallPathResult(
            source=source_function,
            target=target_function,
            paths=paths[:max_paths],
            data_source=self._data_source or "parse",
            truncated=len(paths) >= max_paths,
        )
