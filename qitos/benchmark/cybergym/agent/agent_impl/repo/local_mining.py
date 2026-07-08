"""Local mining — finds tests, corpus files, and local git commits
relevant to the vulnerability task.

Does NOT depend on network. Uses filesystem, rg/grep, and local git.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path
from typing import Any


_TEST_DIRS = {"test", "tests", "unittest", "unittests", "regression", "fuzz", "oss-fuzz"}
_CORPUS_DIRS = {"corpus", "testdata", "testdata/fuzz", "tests/files", "seeds"}


def mine_local_references(
    *,
    repo_root: str,
    vulnerability_description: str = "",
    suspect_symbols: list[str] | None = None,
    project_name: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """Find tests, corpus files, examples, and local commits relevant to the task."""
    root = Path(repo_root)
    if not root.is_dir():
        return {"status": "empty", "refs": [], "queries": []}

    refs: list[dict[str, Any]] = []
    queries = _build_queries(vulnerability_description, suspect_symbols or [], project_name)

    # 1. Search for regression tests
    _search_tests(root, queries, refs, limit)

    # 2. Search for corpus seeds
    _search_corpus(root, queries, refs, limit)

    # 3. Search local git history
    _search_git_history(root, queries, refs, limit)

    # Sort by confidence
    refs.sort(key=lambda r: r.get("confidence", 0), reverse=True)

    return {
        "status": "success" if refs else "empty",
        "refs": refs[:limit],
        "queries": queries[:5],
    }


def _stable_ref_id(material: str) -> str:
    h = hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
    return f"lref_{h}"


def _build_queries(
    description: str,
    symbols: list[str],
    project_name: str,
) -> list[str]:
    """Build search queries from description, symbols, and project name."""
    queries: list[str] = []

    # Extract function-like tokens from description
    if description:
        # CamelCase and snake_case identifiers
        tokens = re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', description)
        queries.extend(tokens[:8])

        # Format keywords
        format_keywords = re.findall(
            r'\b(rar5|zip|pdf|png|jpeg|tiff|elf|pe|wasm|av1|zstd|pcap|cap|blosc|jxl|json|xml|font|otf|ttf|sfnt)\b',
            description.lower(),
        )
        queries.extend(format_keywords[:4])

    # Add suspect symbols
    for sym in symbols[:4]:
        if len(sym) > 2:
            queries.append(sym)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        ql = q.lower()
        if ql not in seen and len(ql) > 2:
            seen.add(ql)
            unique.append(ql)

    return unique[:12]


def _search_tests(
    root: Path,
    queries: list[str],
    refs: list[dict[str, Any]],
    limit: int,
) -> None:
    """Search for regression test files matching queries."""
    test_dirs = _find_dirs(root, _TEST_DIRS, max_depth=2)
    if not test_dirs:
        return

    for query in queries:
        if len(refs) >= limit:
            break
        if len(query) < 3:
            continue

        results = _rg_search(root, query, test_dirs, max_results=3)
        for path, line_num, context in results:
            if len(refs) >= limit:
                break
            rel = str(Path(path).relative_to(root)) if Path(path).is_relative_to(root) else path
            ref_id = _stable_ref_id(f"test|{rel}|{query}")
            refs.append({
                "ref_id": ref_id,
                "kind": "regression_test",
                "path": rel,
                "line": line_num,
                "symbol": query,
                "reason": f"matches {query} in test file",
                "confidence": 0.6 + min(0.3, len(query) / 20),
                "next_action": f"READ test and convert fixture/builder into PoC recipe",
            })


def _search_corpus(
    root: Path,
    queries: list[str],
    refs: list[dict[str, Any]],
    limit: int,
) -> None:
    """Search for corpus seed files."""
    corpus_dirs = _find_dirs(root, _CORPUS_DIRS, max_depth=3)
    for cdir in corpus_dirs:
        if len(refs) >= limit:
            break
        # List files in corpus directory
        try:
            for fpath in sorted(cdir.rglob("*"))[:20]:
                if not fpath.is_file():
                    continue
                if fpath.stat().st_size > 1_000_000:
                    continue  # Skip large files
                rel = str(fpath.relative_to(root)) if fpath.is_relative_to(root) else str(fpath)
                # Check if filename matches any query
                fname_lower = fpath.name.lower()
                matched_query = ""
                for q in queries:
                    if q in fname_lower:
                        matched_query = q
                        break
                ref_id = _stable_ref_id(f"corpus|{rel}")
                refs.append({
                    "ref_id": ref_id,
                    "kind": "corpus_seed",
                    "path": rel,
                    "line": 0,
                    "symbol": matched_query,
                    "reason": f"corpus file{' matching ' + matched_query if matched_query else ''}",
                    "confidence": 0.7 if matched_query else 0.4,
                    "next_action": "use as seed for PoC recipe carrier",
                })
        except (OSError, ValueError):
            pass


def _search_git_history(
    root: Path,
    queries: list[str],
    refs: list[dict[str, Any]],
    limit: int,
) -> None:
    """Search local git history for relevant commits."""
    git_dir = root / ".git"
    if not git_dir.exists():
        return

    for query in queries:
        if len(refs) >= limit:
            break
        if len(query) < 3:
            continue
        try:
            result = subprocess.run(
                ["git", "log", "--all", "--format=%H%x09%s", "--max-count=5", "--all-match", "--grep", query],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                continue
            for line in result.stdout.strip().split("\n"):
                if not line or "\t" not in line:
                    continue
                commit_hash, subject = line.split("\t", 1)
                ref_id = _stable_ref_id(f"git|{commit_hash}")
                refs.append({
                    "ref_id": ref_id,
                    "kind": "git_commit",
                    "path": commit_hash[:12],
                    "line": 0,
                    "symbol": query,
                    "reason": f"git commit matching {query}: {subject[:100]}",
                    "confidence": 0.5,
                    "next_action": "inspect_local_diff: check if commit adds test or fix for the vulnerability",
                })
        except (subprocess.TimeoutExpired, OSError):
            pass


def _find_dirs(root: Path, names: set[str], max_depth: int = 2) -> list[Path]:
    """Find directories matching any of the given names under root."""
    found: list[Path] = []
    try:
        for d in _walk_dirs(root, max_depth):
            if d.name.lower() in names:
                found.append(d)
    except OSError:
        pass
    return found


def _walk_dirs(root: Path, max_depth: int) -> list[Path]:
    """Walk directory tree up to max_depth."""
    dirs: list[Path] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        d, depth = queue.pop(0)
        if depth > max_depth:
            continue
        try:
            for entry in d.iterdir():
                if entry.is_dir() and not entry.name.startswith("."):
                    dirs.append(entry)
                    queue.append((entry, depth + 1))
        except (OSError, PermissionError):
            pass
    return dirs


def _rg_search(
    root: Path,
    query: str,
    search_dirs: list[Path],
    max_results: int = 3,
) -> list[tuple[str, int, str]]:
    """Search using rg or fallback to grep."""
    results: list[tuple[str, int, str]] = []
    dir_args = [str(d) for d in search_dirs[:5]]

    # Try ripgrep first
    try:
        proc = subprocess.run(
            ["rg", "-n", "--max-count", str(max_results), "-i", query] + dir_args,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().split("\n")[:max_results]:
                if ":" not in line:
                    continue
                parts = line.split(":", 2)
                if len(parts) >= 2:
                    path = parts[0]
                    try:
                        line_num = int(parts[1])
                    except ValueError:
                        continue
                    context = parts[2][:100] if len(parts) > 2 else ""
                    results.append((path, line_num, context))
            return results
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Fallback: no search tool available
    return results
