"""Dynamic execution tools for staged vulnerable binaries."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult

from ...tool_names import GDB_DEBUG, PROBE_RUNTIME_FRONTIER, RUN_CANDIDATE
from ..core.metadata_keys import INVOCATION_PROFILE, STAGED_BINARY_CAPABILITY
from ..runtime.candidate_runner import run_candidate_once
from ..runtime.gdb_frontier import DEFAULT_PROBE_ROLES, run_gdb_frontier_probe
from ..runtime.runtime_artifacts import tail_text  # used by ProbeRuntimeFrontierTool

# --- GDB debug constants ---
_DEFAULT_GDB_COMMANDS = ("run", "bt")
_MAX_GDB_OUTPUT_CHARS = 12000
_ASAN_OPTIONS = "abort_on_error=1:detect_leaks=0:symbolize=1:allocator_may_return_null=1"
_UBSAN_OPTIONS = "print_stacktrace=1:symbolize=1"
# ASLR warning that appears in every container GDB invocation — harmless
_ASLR_WARNING_RE = re.compile(
    r"^warning:\s*Error disabling address space randomization", re.MULTILINE
)


def _maybe_rediscover_from_container(state, runtime_context) -> None:
    """Lazy container-aware re-discovery of staged binary capability.

    Called from validate_input when host-side discovery failed with
    binary_root_missing.  Uses env_runner.cmd to probe /out inside
    the Docker container.
    """
    metadata = getattr(state, "metadata", None)
    if not isinstance(metadata, dict):
        return
    if not metadata.get("_need_container_rediscovery"):
        return

    env_runner = (runtime_context or {}).get("env")
    if env_runner is None or not hasattr(env_runner, "cmd"):
        return

    try:
        from ..runtime.staged_binary import discover_staged_binary_capability_from_env
        from ..runtime.invocation_profile import build_invocation_profile

        capability = discover_staged_binary_capability_from_env(env_runner)
        metadata[STAGED_BINARY_CAPABILITY] = capability.to_dict()
        profile = build_invocation_profile(state, capability)
        metadata[INVOCATION_PROFILE] = profile.to_dict()
    except Exception:
        pass  # Keep existing metadata; validation will report the original failure
    finally:
        metadata.pop("_need_container_rediscovery", None)


def dynamic_tools_enabled() -> bool:
    """Return whether dynamic tools should be exposed in the tool schema.

    Dynamic tools are now always registered.  Individual executions still
    fail closed in ``validate_input`` when staged binaries, invocation
    profiles, or GDB are unavailable.
    """
    return True


class RunCandidateTool(BaseTool):
    """Run one generated candidate against the staged vulnerable target."""

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name=RUN_CANDIDATE,
                description=(
                    "Run a single generated candidate against the staged vulnerable "
                    "binary using the resolved invocation profile. Advisory only: "
                    "submit_poc remains the benchmark verdict. Use after NO_TRIGGER "
                    "or as a quick sanity check; do not fuzz or loop mutations."
                ),
                parameters={
                    "candidate_path": {
                        "type": "string",
                        "description": "Path to the candidate input file within the workspace.",
                    },
                    "objective_id": {
                        "type": "string",
                        "description": "Optional active objective id this run is diagnosing.",
                    },
                    "purpose": {
                        "type": "string",
                        "enum": ["check_reproduction", "classify_no_trigger", "verify_repair"],
                        "description": "Why this bounded run is being performed.",
                    },
                },
                required=["candidate_path"],
                permissions=ToolPermission(filesystem_read=True, filesystem_write=True, command=True),
                concurrency_safe=False,
            )
        )

    def validate_input(
        self,
        args: dict[str, Any],
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> ToolValidationResult:
        state = (runtime_context or {}).get("state")
        if state is None:
            return ToolValidationResult.fail("runtime state is required")
        candidate_path = str(args.get("candidate_path") or "").strip()
        if not candidate_path:
            return ToolValidationResult.fail("candidate_path is required")

        _maybe_rediscover_from_container(state, runtime_context)

        metadata = getattr(state, "metadata", {}) or {}
        capability = metadata.get(STAGED_BINARY_CAPABILITY) or {}
        if not isinstance(capability, dict) or not capability.get("available"):
            reason = capability.get("reason") if isinstance(capability, dict) else "missing_capability"
            return ToolValidationResult.fail(f"staged vulnerable binary unavailable: {reason}")

        profile = metadata.get(INVOCATION_PROFILE) or {}
        if not isinstance(profile, dict) or profile.get("mode") not in {"argv_file", "stdin"}:
            # Graceful fallback: if capability has a binary_path, allow
            # execution with default "argv_file" mode. The model can
            # provide the mode via args if needed.
            binary_path = str((capability.get("binary_path") if isinstance(capability, dict) else None) or "").strip()
            if not binary_path:
                reason = profile.get("reason") if isinstance(profile, dict) else "missing_profile"
                return ToolValidationResult.fail(f"invocation profile unresolved: {reason}")
            # Build a minimal profile from capability
            metadata[INVOCATION_PROFILE] = {
                "binary_path": binary_path,
                "mode": "argv_file",
                "candidate_arg_index": 0,
                "fixed_args": (),
                "cwd": "/",
                "library_path": None,
                "confidence": 0.5,
                "reason": "best_effort_fallback",
                "digest": "fallback",
            }

        workspace_root = str(getattr(state, "workspace_root", "") or "")
        try:
            candidate = _resolve_candidate(candidate_path, workspace_root)
        except ValueError as exc:
            return ToolValidationResult.fail(str(exc))
        if not candidate.exists():
            return ToolValidationResult.fail(f"candidate file does not exist: {candidate}")
        if not candidate.is_file():
            return ToolValidationResult.fail(f"candidate path is not a regular file: {candidate}")
        if candidate.stat().st_size == 0:
            return ToolValidationResult.fail("candidate file is empty")

        purpose = str(args.get("purpose") or "check_reproduction")
        if purpose not in {"check_reproduction", "classify_no_trigger", "verify_repair"}:
            return ToolValidationResult.fail("purpose must be check_reproduction, classify_no_trigger, or verify_repair")

        return ToolValidationResult.ok()

    def execute(
        self,
        args: dict[str, Any],
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        state = (runtime_context or {}).get("state")
        metadata = getattr(state, "metadata", {}) if state is not None else {}
        profile = metadata.get(INVOCATION_PROFILE) if isinstance(metadata, dict) else {}
        if not isinstance(profile, dict):
            profile = {}

        result = run_candidate_once(
            candidate_path=str(args.get("candidate_path") or ""),
            workspace_root=str(getattr(state, "workspace_root", "") or "."),
            invocation_profile=profile,
            objective_id=str(args.get("objective_id") or "") or None,
            env_runner=(runtime_context or {}).get("env"),
            timeout_seconds=12,
        )
        payload = result.to_dict()
        payload["status"] = "success"
        payload["purpose"] = str(args.get("purpose") or "check_reproduction")
        return payload


class GdbDebugTool(BaseTool):
    """Run model-chosen GDB commands against the staged vulnerable target.

    The model chooses the GDB commands (e.g. run, bt, info registers,
    break function, print var). The tool wires the target binary and PoC
    input, sets LD_LIBRARY_PATH and ASAN_OPTIONS, and returns the raw
    captured output. No auto-classification — just raw facts.

    Advisory only: submit_poc remains the benchmark verdict.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name=GDB_DEBUG,
                description=(
                    "Debug a PoC under GDB. You choose the GDB commands to run "
                    "(e.g. run, bt, break function, info registers, print var, "
                    "x/16xb $sp); the tool finds the target binary, wires the "
                    "PoC input, sets LD_LIBRARY_PATH, and returns the raw GDB "
                    "output. Targets the vulnerable binary staged at /out when "
                    "present, else the one from invocation profile. Advisory only "
                    "-- submit_poc remains the sole verdict."
                ),
                parameters={
                    "poc_path": {
                        "type": "string",
                        "description": "Path to the PoC input file within the workspace.",
                    },
                    "commands": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            'GDB commands to run in order, e.g. ["run","bt","info registers"]. '
                            'Defaults to ["run","bt"] if omitted.'
                        ),
                    },
                    "binary_path": {
                        "type": "string",
                        "description": (
                            "Target binary to debug. Auto-detected from staged capability "
                            "if omitted; pass it explicitly when auto-detection fails."
                        ),
                    },
                    "input_mode": {
                        "type": "string",
                        "enum": ["arg", "stdin"],
                        "description": (
                            'How the PoC is fed: "arg" (file path as argv[1], default) '
                            'or "stdin". Defaults from invocation profile if available.'
                        ),
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Seconds before GDB is killed (default 30, max 300).",
                    },
                    "objective_id": {
                        "type": "string",
                        "description": "Optional active objective id being diagnosed.",
                    },
                },
                required=["poc_path"],
                permissions=ToolPermission(filesystem_read=True, filesystem_write=True, command=True),
                concurrency_safe=False,
            )
        )

    def validate_input(
        self,
        args: dict[str, Any],
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> ToolValidationResult:
        state = (runtime_context or {}).get("state")
        if state is None:
            return ToolValidationResult.fail("runtime state is required")
        poc_path = str(args.get("poc_path") or "").strip()
        if not poc_path:
            return ToolValidationResult.fail("poc_path is required")
        input_mode = str(args.get("input_mode") or "")
        if input_mode and input_mode not in ("arg", "stdin"):
            return ToolValidationResult.fail("input_mode must be 'arg' or 'stdin'")

        _maybe_rediscover_from_container(state, runtime_context)

        metadata = getattr(state, "metadata", {}) or {}
        capability = metadata.get(STAGED_BINARY_CAPABILITY) or {}

        # When rediscovery is pending, the binary exists inside the container
        # at /out but host-side checks cannot verify it.  Allow best-effort
        # execution — the container GDB invocation will fail closed if the
        # binary is truly absent.
        rediscovery_pending = bool(metadata.get("_need_container_rediscovery"))

        # Allow best-effort when staged binary is available even if gdb_available
        # is not yet confirmed (container may have gdb even if host-side check
        # failed). Only fail closed when capability is clearly missing.
        if not isinstance(capability, dict) or not capability.get("available"):
            # If model provided binary_path explicitly, allow execution anyway
            binary_path = str(args.get("binary_path") or "").strip()
            if not binary_path and not rediscovery_pending:
                reason = capability.get("reason") if isinstance(capability, dict) else "missing_capability"
                return ToolValidationResult.fail(f"staged vulnerable binary unavailable: {reason}")

        workspace_root = str(getattr(state, "workspace_root", "") or "")
        try:
            candidate = _resolve_candidate(poc_path, workspace_root)
        except ValueError as exc:
            return ToolValidationResult.fail(str(exc))
        if not candidate.exists():
            return ToolValidationResult.fail(f"PoC file does not exist: {candidate}")
        if not candidate.is_file():
            return ToolValidationResult.fail(f"PoC path is not a regular file: {candidate}")
        if candidate.stat().st_size == 0:
            return ToolValidationResult.fail("PoC file is empty")

        return ToolValidationResult.ok()

    def execute(
        self,
        args: dict[str, Any],
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        state = (runtime_context or {}).get("state")
        metadata = getattr(state, "metadata", {}) if state is not None else {}
        capability = metadata.get(STAGED_BINARY_CAPABILITY) or {}
        if not isinstance(capability, dict):
            capability = {}
        profile = metadata.get(INVOCATION_PROFILE) or {}
        if not isinstance(profile, dict):
            profile = {}

        poc_path = str(args.get("poc_path") or "").strip()
        commands = [str(c) for c in (args.get("commands") or _DEFAULT_GDB_COMMANDS) if str(c).strip()]
        commands, cmds_stripped = _sanitize_gdb_commands(commands)
        if not commands:
            commands = list(_DEFAULT_GDB_COMMANDS)

        # Resolve binary path: explicit > invocation profile > capability > /out fallback
        binary_path = str(args.get("binary_path") or "").strip()
        if not binary_path and isinstance(profile, dict):
            binary_path = str(profile.get("binary_path") or "")
        # When rediscovery_pending, capability/profile are unavailable but the
        # binary lives at /out inside the container.  If env_runner is present
        # we can discover the actual binary name from the container.
        if not binary_path and metadata.get("_need_container_rediscovery"):
            env_runner = (runtime_context or {}).get("env")
            if env_runner is not None and hasattr(env_runner, "cmd"):
                try:
                    rc, out = env_runner.cmd.run("ls /out/", timeout=5)
                    if rc == 0 and out.strip():
                        # Pick the first executable-looking name
                        for line in out.strip().splitlines():
                            name = line.strip()
                            if name and not name.startswith("."):
                                binary_path = f"/out/{name}"
                                break
                except Exception:
                    pass

        # Resolve input mode: explicit > invocation profile > default "arg"
        input_mode = str(args.get("input_mode") or "")
        if not input_mode:
            profile_mode = str(profile.get("mode") or "") if isinstance(profile, dict) else ""
            input_mode = "stdin" if profile_mode == "stdin" else "arg"

        try:
            timeout = int(args.get("timeout") or 30)
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(1, min(timeout, 300))

        objective_id = str(args.get("objective_id") or "") or None

        workspace_root = str(getattr(state, "workspace_root", "") or ".")
        poc_file = _resolve_candidate(poc_path, workspace_root)

        if not binary_path or not poc_file.exists():
            _settle_reproduction(state, latch=True)
            return {
                "status": "error",
                "error": f"Binary or PoC not found: binary={binary_path or '(missing)'}, poc={poc_path}",
                "poc_path": poc_path,
                "objective_id": objective_id or "",
            }

        # Resolve library path from profile
        library_path = str(profile.get("library_path") or "") if isinstance(profile, dict) else ""

        start = time.monotonic()
        try:
            env_runner = (runtime_context or {}).get("env")
            if env_runner is not None and hasattr(env_runner, "cmd"):
                output, stderr = _run_gdb_env(
                    env_runner=env_runner,
                    binary_path=binary_path,
                    poc_path=_candidate_display_path(poc_file, workspace_root),
                    commands=commands,
                    input_mode=input_mode,
                    timeout_seconds=timeout,
                    library_path=library_path,
                )
            else:
                output, stderr = _run_gdb_local(
                    binary_path=binary_path,
                    poc_path=poc_file,
                    commands=commands,
                    input_mode=input_mode,
                    timeout_seconds=timeout,
                    library_path=library_path,
                )
        except Exception as exc:
            _settle_reproduction(state, latch=True)
            return {
                "status": "error",
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
                "poc_path": poc_path,
                "binary_path": binary_path,
                "objective_id": objective_id or "",
            }

        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Filter harmless ASLR warning from stderr
        clean_stderr = _ASLR_WARNING_RE.sub("", stderr).strip()

        # Combine stdout + meaningful stderr
        combined = output
        if clean_stderr:
            combined = (combined + "\n" + clean_stderr) if combined.strip() else clean_stderr

        output_tail, truncated = _tail_gdb_output(combined, _MAX_GDB_OUTPUT_CHARS)

        # Determine if GDB timed out
        # Heuristic: timeout exit codes are 124 (timeout) or 137 (SIGKILL)
        timed_out = False
        rc = -1  # We don't have the exact return code from combined output
        # Check for timeout indicators in the output
        if "Timer expired" in combined or "Interrupted" in combined:
            timed_out = True

        # Successful execution — clear the reproduction checkpoint
        _settle_reproduction(state, latch=False)

        return {
            "status": "success",
            "poc_path": poc_path,
            "binary_path": binary_path,
            "input_mode": input_mode,
            "commands": commands,
            "commands_stripped": cmds_stripped,
            "output": output_tail,
            "output_truncated": truncated,
            "timed_out": timed_out,
            "elapsed_ms": elapsed_ms,
            "objective_id": objective_id or "",
        }


# ---------------------------------------------------------------------------
# GDB helper functions
# ---------------------------------------------------------------------------

def _sanitize_gdb_commands(commands: list[str]) -> tuple[list[str], bool]:
    """Neutralize fuzzing vectors. The target must always run with the wired
    single PoC, so re-specifying the program input is forbidden:

    - ``set args …`` → dropped (overrides the wired PoC)
    - ``run``/``start``/``r`` with extra args → stripped to bare command

    Everything else (bare run, breakpoints, bt, print, info locals,
    continue, x/…) passes through untouched.

    Returns (clean_commands, was_anything_stripped).
    """
    clean: list[str] = []
    stripped = False
    for cmd in commands:
        c = str(cmd).strip()
        low = c.lower()
        # Drop "set args" entirely
        if low == "set args" or low.startswith(("set args ", "set arg ")):
            stripped = True
            continue
        # Strip arguments from run/start/r
        head = low.split(None, 1)[0] if low else ""
        if head in ("run", "r", "start") and low != head:
            clean.append(head)  # e.g. "run corpus/" -> "run"
            stripped = True
            continue
        clean.append(c)
    return clean, stripped


def _build_gdb_command(
    *,
    gdb_bin: str,
    binary_path: str,
    poc_path: str,
    commands: list[str],
    input_mode: str,
    library_path: str = "",
) -> str:
    """Build the shell command for running GDB in batch mode."""
    parts = [
        shlex.quote(gdb_bin),
        "-nx", "-q", "-batch",
        "-ex", shlex.quote("set pagination off"),
    ]
    if input_mode == "stdin":
        for cmd in commands:
            parts += ["-ex", shlex.quote(_wire_stdin(cmd, poc_path))]
        tail = shlex.quote(binary_path)
    else:
        for cmd in commands:
            parts += ["-ex", shlex.quote(cmd)]
        tail = f"--args {shlex.quote(binary_path)} {shlex.quote(poc_path)}"
    env_pairs = [
        f"ASAN_OPTIONS={shlex.quote(_ASAN_OPTIONS)}",
        f"UBSAN_OPTIONS={shlex.quote(_UBSAN_OPTIONS)}",
    ]
    if library_path:
        env_pairs.insert(0, f"LD_LIBRARY_PATH={shlex.quote(library_path)}")
    return f"{' '.join(env_pairs)} {' '.join(parts)} {tail}"


def _wire_stdin(command: str, poc_path: str) -> str:
    """Redirect the PoC into the target when the model issues a bare run."""
    c = command.strip()
    low = c.lower()
    is_run = (
        low in ("run", "r", "start")
        or low.startswith(("run ", "r ", "start "))
    )
    if is_run and "<" not in c:
        return f"{c} < {shlex.quote(poc_path)}"
    return c


def _run_gdb_local(
    *,
    binary_path: str,
    poc_path: Path,
    commands: list[str],
    input_mode: str,
    timeout_seconds: int,
    library_path: str,
) -> tuple[str, str]:
    """Run GDB locally (host-side execution)."""
    gdb_bin = "gdb"
    # Check for gdb-multiarch first (ARM/SH targets)
    import shutil
    if shutil.which("gdb-multiarch"):
        gdb_bin = "gdb-multiarch"

    script_cmds = []
    for cmd in commands:
        if input_mode == "stdin" and cmd.strip().lower() in ("run", "r", "start"):
            script_cmds.append(_wire_stdin(cmd, str(poc_path)))
        else:
            script_cmds.append(cmd)

    shell_cmd = _build_gdb_command(
        gdb_bin=gdb_bin,
        binary_path=binary_path,
        poc_path=str(poc_path),
        commands=script_cmds if input_mode != "stdin" else commands,
        input_mode=input_mode,
        library_path=library_path,
    )

    env = dict(os.environ)
    if library_path:
        current = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = library_path if not current else f"{library_path}:{current}"
    env["ASAN_OPTIONS"] = _ASAN_OPTIONS
    env["UBSAN_OPTIONS"] = _UBSAN_OPTIONS

    completed = subprocess.run(
        shell_cmd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=max(1, int(timeout_seconds)) + 5,
        env=env,
        check=False,
    )
    stdout = completed.stdout.decode("utf-8", errors="replace")
    stderr = completed.stderr.decode("utf-8", errors="replace")
    # Filter harmless ASLR warning from stderr
    stderr = _ASLR_WARNING_RE.sub("", stderr).strip()
    # Only return stderr when there was a non-zero exit
    stderr_filtered = stderr if completed.returncode not in (0, None) else ""
    return stdout, stderr_filtered


def _run_gdb_env(
    *,
    env_runner: Any,
    binary_path: str,
    poc_path: str,
    commands: list[str],
    input_mode: str,
    timeout_seconds: int,
    library_path: str,
) -> tuple[str, str]:
    """Run GDB inside the task container via env_runner.cmd.run."""
    # Locate gdb binary inside container
    probe = env_runner.cmd.run("command -v gdb || command -v gdb-multiarch", timeout=10)
    gdb_out = str(probe.get("stdout") or "").strip()
    gdb_bin = gdb_out.splitlines()[0].strip() if gdb_out else "gdb"

    shell_cmd = _build_gdb_command(
        gdb_bin=gdb_bin,
        binary_path=binary_path,
        poc_path=poc_path,
        commands=commands,
        input_mode=input_mode,
        library_path=library_path,
    )
    result = env_runner.cmd.run(shell_cmd, timeout=max(1, int(timeout_seconds)) + 10)
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    rc = result.get("returncode")
    # Filter harmless ASLR warning from stderr
    stderr = _ASLR_WARNING_RE.sub("", stderr).strip()
    stderr_filtered = stderr if rc not in (0, None) else ""
    return stdout, stderr_filtered


def _tail_gdb_output(text: str, limit: int) -> tuple[str, bool]:
    """Tail-truncate GDB output to *limit* chars."""
    if len(text) <= limit:
        return text, False
    return "...[truncated head]...\n" + text[-limit:], True


def _settle_reproduction(state: Any, latch: bool) -> None:
    """Clear the reproduction checkpoint after a forced gdb_debug call.

    Only acts when the checkpoint is armed. When ``latch`` is True (fatal
    environment error), also latches ``gdb_unavailable`` so gdb is never
    force-required again for this task (fall back to static analysis).
    ``latch=False`` (success, or recoverable error) releases the checkpoint
    without latching.
    """
    if state is None:
        return
    if not getattr(state, "pending_reproduction", False):
        return
    try:
        if latch:
            state.gdb_unavailable = True
        state.pending_reproduction = False
    except Exception:
        pass


class ProbeRuntimeFrontierTool(BaseTool):
    """Run a safe, code-generated GDB frontier probe."""

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name=PROBE_RUNTIME_FRONTIER,
                description=(
                    "Run a bounded GDB frontier probe for one candidate. The tool "
                    "generates the GDB script from source-backed call-chain points; "
                    "the model cannot pass raw GDB commands. Advisory only."
                ),
                parameters={
                    "candidate_path": {
                        "type": "string",
                        "description": "Path to the candidate input file within the workspace.",
                    },
                    "objective_id": {
                        "type": "string",
                        "description": "Active objective id being diagnosed.",
                    },
                    "path_id": {
                        "type": "string",
                        "description": "Optional ranked/static path id being probed.",
                    },
                    "probe_roles": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(DEFAULT_PROBE_ROLES),
                        },
                        "description": "Optional ordered subset of frontier roles to probe.",
                    },
                },
                required=["candidate_path", "objective_id"],
                permissions=ToolPermission(filesystem_read=True, filesystem_write=True, command=True),
                concurrency_safe=False,
            )
        )

    def validate_input(
        self,
        args: dict[str, Any],
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> ToolValidationResult:
        state = (runtime_context or {}).get("state")
        if state is None:
            return ToolValidationResult.fail("runtime state is required")
        if not str(args.get("candidate_path") or "").strip():
            return ToolValidationResult.fail("candidate_path is required")
        if not str(args.get("objective_id") or "").strip():
            return ToolValidationResult.fail("objective_id is required")

        _maybe_rediscover_from_container(state, runtime_context)

        metadata = getattr(state, "metadata", {}) or {}
        capability = metadata.get(STAGED_BINARY_CAPABILITY) or {}
        if not isinstance(capability, dict) or not capability.get("available"):
            reason = capability.get("reason") if isinstance(capability, dict) else "missing_capability"
            return ToolValidationResult.fail(f"staged vulnerable binary unavailable: {reason}")
        if not capability.get("gdb_available"):
            return ToolValidationResult.fail("gdb is unavailable in staged runtime")

        profile = metadata.get(INVOCATION_PROFILE) or {}
        if not isinstance(profile, dict) or profile.get("mode") not in {"argv_file", "stdin"}:
            reason = profile.get("reason") if isinstance(profile, dict) else "missing_profile"
            return ToolValidationResult.fail(f"invocation profile unresolved: {reason}")

        roles = args.get("probe_roles") or []
        if roles:
            bad = [role for role in roles if str(role) not in DEFAULT_PROBE_ROLES]
            if bad:
                return ToolValidationResult.fail(f"unsupported probe role(s): {', '.join(map(str, bad))}")

        try:
            candidate = _resolve_candidate(str(args.get("candidate_path") or ""), str(getattr(state, "workspace_root", "") or ""))
        except ValueError as exc:
            return ToolValidationResult.fail(str(exc))
        if not candidate.exists():
            return ToolValidationResult.fail(f"candidate file does not exist: {candidate}")
        if not candidate.is_file():
            return ToolValidationResult.fail(f"candidate path is not a regular file: {candidate}")

        return ToolValidationResult.ok()

    def execute(
        self,
        args: dict[str, Any],
        runtime_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        state = (runtime_context or {}).get("state")
        metadata = getattr(state, "metadata", {}) if state is not None else {}
        profile = metadata.get(INVOCATION_PROFILE) if isinstance(metadata, dict) else {}
        if not isinstance(profile, dict):
            profile = {}
        result = run_gdb_frontier_probe(
            state=state,
            candidate_path=str(args.get("candidate_path") or ""),
            invocation_profile=profile,
            objective_id=str(args.get("objective_id") or ""),
            path_id=str(args.get("path_id") or "") or None,
            probe_roles=[str(role) for role in (args.get("probe_roles") or [])],
            env_runner=(runtime_context or {}).get("env"),
            timeout_seconds=20,
        )
        payload = result.to_dict()
        payload["candidate_path"] = str(args.get("candidate_path") or "")
        payload["tool_status"] = "success"
        return payload


def _resolve_candidate(candidate_path: str, workspace_root: str) -> Path:
    root = Path(workspace_root or ".").resolve()
    raw = Path(candidate_path)
    path = raw if raw.is_absolute() else root / raw
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("candidate_path must stay inside workspace") from exc
    return resolved


def _candidate_display_path(path: Path, workspace_root: str) -> str:
    """Return a display path relative to workspace root, or absolute."""
    root = Path(workspace_root or ".").resolve()
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return str(path)
