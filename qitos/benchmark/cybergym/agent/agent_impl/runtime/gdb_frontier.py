"""Safe GDB frontier probe generation and parsing."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runtime_artifacts import file_digest, tail_text, write_runtime_artifact


DEFAULT_PROBE_ROLES = (
    "harness_entry",
    "parser_accept",
    "dispatch",
    "pre_sink",
    "sink",
    "trigger_condition",
)

_FORBIDDEN_GDB_COMMANDS = (
    "shell",
    "source",
    "python",
    "pi",
    "attach",
    "target remote",
    "set logging file",
    "dump",
    "restore",
    "call",
    "compile",
)


@dataclass(frozen=True)
class FrontierProbePoint:
    probe_id: str
    role: str
    function: str | None = None
    file: str | None = None
    line: int | None = None
    evidence_id: str = ""
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeFrontierResult:
    candidate_digest: str
    objective_id: str
    path_id: str | None
    hit_probe_ids: tuple[str, ...]
    last_hit_role: str | None
    first_unreached_role: str | None
    status: str
    evidence_ref: str
    stdout_tail: str = ""
    stderr_tail: str = ""
    elapsed_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_frontier_probe_points(
    state: Any,
    *,
    probe_roles: list[str] | tuple[str, ...] | None = None,
) -> tuple[FrontierProbePoint, ...]:
    requested = tuple(role for role in (probe_roles or DEFAULT_PROBE_ROLES) if role in DEFAULT_PROBE_ROLES)
    points: list[FrontierProbePoint] = []

    nodes = list(getattr(state, "call_chain_nodes", []) or [])
    for role in requested:
        point = _point_from_chain(nodes, role)
        if point is None and role == "harness_entry":
            point = _point_from_harness(state)
        if point is None and role == "sink":
            point = _point_from_sink(state)
        if point is not None:
            points.append(point)

    # Deduplicate by location/function while preserving order.
    seen: set[tuple[str, str, int | None]] = set()
    deduped: list[FrontierProbePoint] = []
    for point in points:
        key = (point.function or "", point.file or "", point.line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(point)
    return tuple(deduped[:6])


def build_gdb_frontier_script(
    *,
    probe_points: tuple[FrontierProbePoint, ...],
    candidate_path: str,
    mode: str,
) -> str:
    lines = [
        "set pagination off",
        "set confirm off",
    ]
    for point in probe_points:
        target = _breakpoint_target(point)
        if not target:
            continue
        lines.append(f"break {target}")
        lines.append("commands")
        lines.append("silent")
        lines.append(f'printf "CYBERGYM_PROBE|{point.probe_id}|hit|{point.role}\\n"')
        lines.append("continue")
        lines.append("end")
    if mode == "stdin":
        lines.append(f"run < {candidate_path}")
    else:
        lines.append(f"set args {candidate_path}")
        lines.append("run")
    lines.append("bt 8")
    lines.append("quit")
    script = "\n".join(lines) + "\n"
    _assert_safe_script(script)
    return script


def parse_gdb_frontier_output(
    *,
    text: str,
    probe_points: tuple[FrontierProbePoint, ...],
    candidate_digest: str,
    objective_id: str,
    path_id: str | None,
    evidence_ref: str,
    elapsed_ms: int = 0,
    error: str = "",
) -> RuntimeFrontierResult:
    hit_ids: list[str] = []
    for line in (text or "").splitlines():
        if not line.startswith("CYBERGYM_PROBE|"):
            continue
        parts = line.strip().split("|")
        if len(parts) >= 4 and parts[2] == "hit":
            hit_ids.append(parts[1])

    ordered_ids = [point.probe_id for point in probe_points]
    ordered_roles = {point.probe_id: point.role for point in probe_points}
    hit_set = set(hit_ids)
    last_hit = None
    first_unreached = None
    for probe_id in ordered_ids:
        if probe_id in hit_set:
            last_hit = probe_id
            continue
        first_unreached = probe_id
        break

    last_hit_role = ordered_roles.get(last_hit or "") if last_hit else None
    first_unreached_role = ordered_roles.get(first_unreached or "") if first_unreached else None
    status = _frontier_status(last_hit_role, first_unreached_role, text=text, error=error)
    stdout_tail, _ = tail_text(text, 4000)
    return RuntimeFrontierResult(
        candidate_digest=candidate_digest,
        objective_id=objective_id,
        path_id=path_id,
        hit_probe_ids=tuple(pid for pid in ordered_ids if pid in hit_set),
        last_hit_role=last_hit_role,
        first_unreached_role=first_unreached_role,
        status=status,
        evidence_ref=evidence_ref,
        stdout_tail=stdout_tail,
        stderr_tail="",
        elapsed_ms=elapsed_ms,
        error=error,
    )


def run_gdb_frontier_probe(
    *,
    state: Any,
    candidate_path: str,
    invocation_profile: dict[str, Any],
    objective_id: str,
    path_id: str | None = None,
    probe_roles: list[str] | tuple[str, ...] | None = None,
    env_runner: Any = None,
    timeout_seconds: int = 20,
) -> RuntimeFrontierResult:
    start = time.monotonic()
    workspace_root = str(getattr(state, "workspace_root", "") or ".")
    candidate_file = _resolve_candidate(candidate_path, workspace_root)
    candidate_digest = file_digest(candidate_file) if candidate_file.exists() else "missing"
    points = build_frontier_probe_points(state, probe_roles=probe_roles)
    binary_path = str(invocation_profile.get("binary_path") or "")
    mode = str(invocation_profile.get("mode") or "unknown")
    if not candidate_file.exists():
        return _capability_result(candidate_digest, objective_id, path_id, workspace_root, start, "candidate_not_found")
    if not points:
        return _capability_result(candidate_digest, objective_id, path_id, workspace_root, start, "no_source_backed_probe_points")
    if not binary_path or mode not in {"argv_file", "stdin"}:
        return _capability_result(candidate_digest, objective_id, path_id, workspace_root, start, "profile_unresolved")

    candidate_display = _candidate_display_path(candidate_file, workspace_root)
    script = build_gdb_frontier_script(
        probe_points=points,
        candidate_path=candidate_display,
        mode=mode,
    )
    evidence_payload = {
        "candidate_digest": candidate_digest,
        "objective_id": objective_id,
        "path_id": path_id,
        "probe_points": [point.to_dict() for point in points],
        "script": script,
    }
    evidence_ref = write_runtime_artifact(
        workspace_root=workspace_root,
        candidate_digest=f"{candidate_digest}_frontier",
        payload=evidence_payload,
    )
    script_path = Path(workspace_root).resolve() / evidence_ref / "frontier.gdb"
    script_path.write_text(script, encoding="utf-8")

    try:
        if env_runner is not None:
            output, error = _run_gdb_env(
                env_runner=env_runner,
                binary_path=binary_path,
                script_path=_candidate_display_path(script_path, workspace_root),
                timeout_seconds=timeout_seconds,
                library_path=str(invocation_profile.get("library_path") or ""),
            )
        else:
            output, error = _run_gdb_local(
                binary_path=binary_path,
                script_path=script_path,
                timeout_seconds=timeout_seconds,
                library_path=str(invocation_profile.get("library_path") or ""),
            )
    except Exception as exc:
        output, error = "", f"{type(exc).__name__}:{str(exc)[:160]}"

    elapsed_ms = int((time.monotonic() - start) * 1000)
    return parse_gdb_frontier_output(
        text=output,
        probe_points=points,
        candidate_digest=candidate_digest,
        objective_id=objective_id,
        path_id=path_id,
        evidence_ref=evidence_ref,
        elapsed_ms=elapsed_ms,
        error=error,
    )


def _point_from_chain(nodes: list[Any], role: str) -> FrontierProbePoint | None:
    role_aliases = {
        "harness_entry": {"entry", "harness", "harness_entry"},
        "parser_accept": {"parser", "parse", "parser_accept"},
        "dispatch": {"dispatch", "selector"},
        "pre_sink": {"guard", "pre_sink", "bounds"},
        "sink": {"sink", "crash", "target"},
        "trigger_condition": {"trigger", "trigger_condition"},
    }
    wanted = role_aliases.get(role, {role})
    for idx, node in enumerate(nodes):
        node_role = str(getattr(node, "role", "") or "").lower()
        if node_role not in wanted:
            continue
        file, line = _split_location(str(getattr(node, "location", "") or ""))
        function = str(getattr(node, "function", "") or "") or None
        if not function and not (file and line):
            continue
        return FrontierProbePoint(
            probe_id=f"probe_{role}_{idx}",
            role=role,
            function=function,
            file=file,
            line=line,
            evidence_id=str(getattr(node, "evidence", "") or ""),
            confidence=0.8 if file or function else 0.0,
        )
    return None


def _point_from_harness(state: Any) -> FrontierProbePoint | None:
    candidates = list(getattr(state, "harness_candidates", []) or [])
    for idx, candidate in enumerate(candidates):
        function = str(getattr(candidate, "entry_function", "") or "")
        file = str(getattr(candidate, "source_path", "") or "") or None
        line = int(getattr(candidate, "line", 0) or 0) or None
        if function or (file and line):
            return FrontierProbePoint(
                probe_id=f"probe_harness_entry_{idx}",
                role="harness_entry",
                function=function or None,
                file=file,
                line=line,
                evidence_id=str(getattr(candidate, "candidate_id", "") or ""),
                confidence=0.7,
            )
    return None


def _point_from_sink(state: Any) -> FrontierProbePoint | None:
    sinks = []
    if hasattr(state, "confirmed_sink_candidates"):
        try:
            sinks = list(state.confirmed_sink_candidates() or [])
        except Exception:
            sinks = []
    if not sinks:
        sinks = list(getattr(state, "sink_candidates", []) or [])
    for idx, sink in enumerate(sinks):
        function = str(getattr(sink, "function", "") or "")
        file = str(getattr(sink, "file", "") or "") or None
        line = int(getattr(sink, "line", 0) or 0) or None
        if not file and getattr(sink, "location", ""):
            file, line = _split_location(str(getattr(sink, "location") or ""))
        if function or (file and line):
            return FrontierProbePoint(
                probe_id=f"probe_sink_{idx}",
                role="sink",
                function=function or None,
                file=file,
                line=line,
                evidence_id=str(getattr(sink, "candidate_id", "") or ""),
                confidence=float(getattr(sink, "confidence", 0.6) or 0.6),
            )
    return None


def _breakpoint_target(point: FrontierProbePoint) -> str:
    if point.file and point.line:
        return f"{point.file}:{int(point.line)}"
    if point.function:
        return point.function
    return ""


def _split_location(location: str) -> tuple[str | None, int | None]:
    raw_file, sep, raw_line = location.rpartition(":")
    if sep and raw_line.isdigit():
        return raw_file or None, int(raw_line)
    return (location or None), None


def _frontier_status(last_hit_role: str | None, first_unreached_role: str | None, *, text: str, error: str) -> str:
    if error:
        return "capability_error"
    lowered = (text or "").lower()
    if "addresssanitizer" in lowered or "sigsegv" in lowered or "received signal" in lowered:
        return "crash_observed"
    if not last_hit_role:
        return "harness_not_reached"
    if first_unreached_role == "parser_accept":
        return "parser_rejected"
    if first_unreached_role == "dispatch":
        return "dispatch_not_selected"
    if first_unreached_role in {"pre_sink", "sink"}:
        return "sink_not_reached"
    if first_unreached_role == "trigger_condition" or last_hit_role == "sink":
        return "sink_reached_trigger_unmet"
    if first_unreached_role is None:
        return "probe_inconclusive"
    return "probe_inconclusive"


def _assert_safe_script(script: str) -> None:
    lowered = script.lower()
    for forbidden in _FORBIDDEN_GDB_COMMANDS:
        if re.search(rf"(^|\n)\s*{re.escape(forbidden)}\b", lowered):
            raise ValueError(f"forbidden gdb command generated: {forbidden}")


def _resolve_candidate(candidate_path: str, workspace_root: str) -> Path:
    root = Path(workspace_root or ".").resolve()
    raw = Path(candidate_path)
    resolved = (raw if raw.is_absolute() else root / raw).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("candidate_path must stay inside workspace") from exc
    return resolved


def _candidate_display_path(path: Path, workspace_root: str) -> str:
    root = Path(workspace_root or ".").resolve()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)


def _run_gdb_local(*, binary_path: str, script_path: Path, timeout_seconds: int, library_path: str) -> tuple[str, str]:
    env = dict(os.environ)
    if library_path:
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = library_path if not current else f"{library_path}:{current}"
    completed = subprocess.run(
        ["gdb", "-nx", "-q", "-batch", "-x", str(script_path), binary_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(1, int(timeout_seconds)),
        env=env,
        check=False,
    )
    return (
        completed.stdout.decode("utf-8", errors="replace"),
        completed.stderr.decode("utf-8", errors="replace") if completed.returncode not in (0, None) else "",
    )


def _run_gdb_env(*, env_runner: Any, binary_path: str, script_path: str, timeout_seconds: int, library_path: str) -> tuple[str, str]:
    env_prefix = ""
    if library_path:
        env_prefix = f"LD_LIBRARY_PATH={shlex.quote(library_path)} "
    command = f"{env_prefix}gdb -nx -q -batch -x {shlex.quote(script_path)} {shlex.quote(binary_path)}"
    result = env_runner.cmd.run(command, timeout=max(1, int(timeout_seconds)) + 2)
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    rc = result.get("returncode")
    return stdout, stderr if rc not in (0, None) else ""


def _capability_result(
    candidate_digest: str,
    objective_id: str,
    path_id: str | None,
    workspace_root: str,
    start: float,
    error: str,
) -> RuntimeFrontierResult:
    evidence_ref = write_runtime_artifact(
        workspace_root=workspace_root,
        candidate_digest=f"{candidate_digest}_frontier",
        payload={"error": error, "status": "capability_error"},
    )
    return RuntimeFrontierResult(
        candidate_digest=candidate_digest,
        objective_id=objective_id,
        path_id=path_id,
        hit_probe_ids=(),
        last_hit_role=None,
        first_unreached_role=None,
        status="capability_error",
        evidence_ref=evidence_ref,
        elapsed_ms=int((time.monotonic() - start) * 1000),
        error=error,
    )
