"""Discovery for QitOS-staged vulnerable binaries.

QitOS owns Docker mounting.  The agent only detects whether the mounted
runtime is usable from inside the task environment and records a compact,
typed capability object for later dynamic tools.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


_TRUE_VALUES = {"1", "true", "yes", "on"}
_DEFAULT_BINARY_ROOT = "/out"
_DEFAULT_LIBRARY_ROOT = "/out-libs"


@dataclass(frozen=True)
class StagedBinaryCapability:
    """Capability summary for the vulnerable binary staged by QitOS."""

    available: bool
    binary_path: str | None = None
    binary_candidates: tuple[str, ...] = ()
    library_path: str | None = None
    gdb_available: bool = False
    reason: str = ""
    source: str = "unavailable"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def discover_staged_binary_capability(
    *,
    env: Mapping[str, str] | None = None,
    binary_root: str | None = None,
    library_root: str | None = None,
    probe_gdb: bool = True,
    gdb_timeout_seconds: float = 1.5,
) -> StagedBinaryCapability:
    """Return current staged-binary capability.

    The discovery intentionally fails closed:
    - staging disabled -> unavailable but quiet
    - no executable -> unavailable
    - multiple executables -> ambiguous, model must not guess
    - symlink escape -> ignored
    """

    env_map = dict(os.environ if env is None else env)
    enabled = str(env_map.get("CYBERGYM_STAGE_VUL_BINARY", "")).strip().lower()
    if enabled not in _TRUE_VALUES:
        return StagedBinaryCapability(
            available=False,
            reason="staging_disabled",
            source="unavailable",
        )

    root = Path(binary_root or env_map.get("CYBERGYM_STAGED_BINARY_ROOT") or _DEFAULT_BINARY_ROOT)
    lib_root = Path(library_root or env_map.get("CYBERGYM_STAGED_LIBRARY_ROOT") or _DEFAULT_LIBRARY_ROOT)

    if not root.exists():
        return StagedBinaryCapability(
            available=False,
            reason=f"binary_root_missing:{root}",
            source="qitos_env",
            gdb_available=_gdb_available(probe_gdb, timeout_seconds=gdb_timeout_seconds),
        )
    if not root.is_dir():
        return StagedBinaryCapability(
            available=False,
            reason=f"binary_root_not_directory:{root}",
            source="qitos_env",
            gdb_available=_gdb_available(probe_gdb, timeout_seconds=gdb_timeout_seconds),
        )

    candidates = _discover_executables(root)
    library_path = str(lib_root.resolve()) if lib_root.is_dir() else None
    gdb_available = _gdb_available(probe_gdb, timeout_seconds=gdb_timeout_seconds)

    if not candidates:
        return StagedBinaryCapability(
            available=False,
            binary_candidates=(),
            library_path=library_path,
            gdb_available=gdb_available,
            reason="no_executable_in_binary_root",
            source="qitos_env",
        )

    if len(candidates) > 1:
        return StagedBinaryCapability(
            available=False,
            binary_candidates=tuple(candidates),
            library_path=library_path,
            gdb_available=gdb_available,
            reason="ambiguous_executable_candidates",
            source="qitos_env",
        )

    return StagedBinaryCapability(
        available=True,
        binary_path=candidates[0],
        binary_candidates=tuple(candidates),
        library_path=library_path,
        gdb_available=gdb_available,
        reason="ok",
        source="qitos_env",
    )


def _discover_executables(root: Path) -> list[str]:
    try:
        root_real = root.resolve()
    except OSError:
        return []

    candidates: list[str] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if child.name.startswith("."):
            continue
        try:
            child_real = child.resolve()
        except OSError:
            continue
        if not _is_relative_to(child_real, root_real):
            continue
        if not child_real.is_file():
            continue
        if not os.access(str(child_real), os.X_OK):
            continue
        candidates.append(str(child_real))
    return candidates


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _gdb_available(probe: bool, *, timeout_seconds: float) -> bool:
    if not probe:
        return False
    try:
        completed = subprocess.run(
            ["gdb", "--version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0
