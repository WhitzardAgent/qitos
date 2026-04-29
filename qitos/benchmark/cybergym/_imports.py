"""Helpers for importing the local CyberGym source tree."""

from __future__ import annotations

import os
import sys
from pathlib import Path


_CYBERGYM_ENV_VARS = (
    "CYBERGYM_SOURCE_ROOT",
    "CYBERGYM_REPO_ROOT",
)


def _marker_path(root: Path) -> Path:
    return root / "src" / "cybergym" / "task" / "README.template"


def resolve_cybergym_source_root() -> Path:
    candidates: list[Path] = []
    for env_name in _CYBERGYM_ENV_VARS:
        raw = str(os.getenv(env_name) or "").strip()
        if raw:
            candidates.append(Path(raw).expanduser().resolve())

    workspace_dir = Path(__file__).resolve().parents[4]
    candidates.append((workspace_dir / "cybergym").resolve())

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _marker_path(candidate).exists():
            return candidate

    searched = ", ".join(str(path) for path in candidates) or "<none>"
    raise FileNotFoundError(
        "Unable to locate the CyberGym source tree with src/cybergym/task/README.template. "
        f"Searched: {searched}"
    )


def ensure_cybergym_source_importable() -> Path:
    source_root = resolve_cybergym_source_root()
    src_dir = str((source_root / "src").resolve())

    def _is_stale_cybergym_path(entry: object) -> bool:
        text = str(entry or "")
        return text.endswith("/cybergym/src") and text != src_dir

    sys.path[:] = [
        entry
        for entry in sys.path
        if str(entry or "") != src_dir and not _is_stale_cybergym_path(entry)
    ]
    sys.path.insert(0, src_dir)

    stale_modules: list[str] = []
    for name, module in list(sys.modules.items()):
        if name != "cybergym" and not name.startswith("cybergym."):
            continue
        module_file = getattr(module, "__file__", None)
        if not module_file:
            continue
        try:
            module_path = Path(str(module_file)).resolve()
        except Exception:
            stale_modules.append(name)
            continue
        if not str(module_path).startswith(src_dir):
            stale_modules.append(name)

    for name in stale_modules:
        sys.modules.pop(name, None)

    return source_root


__all__ = [
    "ensure_cybergym_source_importable",
    "resolve_cybergym_source_root",
]
