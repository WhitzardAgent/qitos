"""Bounded execution of one generated candidate against the staged target."""

from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .crash_classification import classify_execution
from .runtime_artifacts import file_digest, tail_text, write_runtime_artifact


ExecutionOutcome = Literal[
    "clean_exit",
    "sanitizer_failure",
    "signal_failure",
    "timeout",
    "input_rejected",
    "profile_unresolved",
    "environment_error",
]


@dataclass(frozen=True)
class CandidateExecutionResult:
    candidate_digest: str
    objective_id: str | None
    invocation_digest: str
    outcome: ExecutionOutcome
    exit_code: int | None
    signal_name: str | None
    sanitizer_kind: str | None
    top_frame: str | None
    evidence_ref: str
    stdout_tail: str
    stderr_tail: str
    stdout_truncated: bool
    stderr_truncated: bool
    elapsed_ms: int
    candidate_path: str
    binary_path: str
    mode: str
    environment_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_candidate_once(
    *,
    candidate_path: str,
    workspace_root: str,
    invocation_profile: dict[str, Any],
    objective_id: str | None = None,
    env_runner: Any = None,
    timeout_seconds: int = 12,
    stdout_limit: int = 4000,
    stderr_limit: int = 4000,
) -> CandidateExecutionResult:
    """Execute a single candidate with a resolved invocation profile."""

    start = time.monotonic()
    try:
        candidate_file = _resolve_candidate(candidate_path, workspace_root)
    except ValueError as exc:
        return _error_result(
            candidate_digest="invalid_candidate_path",
            objective_id=objective_id,
            invocation_digest=str(invocation_profile.get("digest") or ""),
            candidate_path=str(candidate_path),
            binary_path=str(invocation_profile.get("binary_path") or ""),
            mode=str(invocation_profile.get("mode") or "unknown"),
            workspace_root=workspace_root,
            start=start,
            message=str(exc),
        )
    candidate_digest = file_digest(candidate_file) if candidate_file.exists() else "missing"
    invocation_digest = str(invocation_profile.get("digest") or "")
    binary_path = str(invocation_profile.get("binary_path") or "")
    mode = str(invocation_profile.get("mode") or "unknown")

    if not candidate_file.exists():
        return _error_result(
            candidate_digest=candidate_digest,
            objective_id=objective_id,
            invocation_digest=invocation_digest,
            candidate_path=str(candidate_file),
            binary_path=binary_path,
            mode=mode,
            workspace_root=workspace_root,
            start=start,
            message=f"candidate_not_found:{candidate_path}",
        )

    if mode not in {"argv_file", "stdin"} or not binary_path:
        return _error_result(
            candidate_digest=candidate_digest,
            objective_id=objective_id,
            invocation_digest=invocation_digest,
            candidate_path=str(candidate_file),
            binary_path=binary_path,
            mode=mode,
            workspace_root=workspace_root,
            start=start,
            message="profile_unresolved",
            outcome="profile_unresolved",
        )

    run_env = _execution_env(invocation_profile)
    try:
        if env_runner is not None:
            rc, stdout, stderr, timed_out = _run_with_qitos_env(
                env_runner=env_runner,
                binary_path=binary_path,
                mode=mode,
                candidate_display_path=_candidate_display_path(candidate_file, workspace_root),
                timeout_seconds=timeout_seconds,
                extra_env=run_env,
            )
        else:
            rc, stdout, stderr, timed_out = _run_with_subprocess(
                binary_path=binary_path,
                mode=mode,
                candidate_file=candidate_file,
                timeout_seconds=timeout_seconds,
                extra_env=run_env,
            )
        classification = classify_execution(
            returncode=rc,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )
    except Exception as exc:
        rc, stdout, stderr, timed_out = None, "", "", False
        classification = classify_execution(
            returncode=None,
            environment_error=f"{type(exc).__name__}:{str(exc)[:160]}",
        )

    stdout_tail, stdout_truncated = tail_text(stdout, stdout_limit)
    stderr_tail, stderr_truncated = tail_text(stderr, stderr_limit)
    payload = {
        "candidate_digest": candidate_digest,
        "objective_id": objective_id,
        "invocation_digest": invocation_digest,
        "binary_path": binary_path,
        "mode": mode,
        "exit_code": rc,
        "timed_out": timed_out,
        "classification": classification,
    }
    evidence_ref = write_runtime_artifact(
        workspace_root=workspace_root,
        candidate_digest=candidate_digest,
        payload=payload,
        stdout=stdout,
        stderr=stderr,
    )

    return CandidateExecutionResult(
        candidate_digest=candidate_digest,
        objective_id=objective_id,
        invocation_digest=invocation_digest,
        outcome=classification["outcome"],
        exit_code=rc,
        signal_name=classification.get("signal_name"),
        sanitizer_kind=classification.get("sanitizer_kind"),
        top_frame=classification.get("top_frame"),
        evidence_ref=evidence_ref,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        stdout_truncated=stdout_truncated,
        stderr_truncated=stderr_truncated,
        elapsed_ms=int((time.monotonic() - start) * 1000),
        candidate_path=str(candidate_file),
        binary_path=binary_path,
        mode=mode,
        environment_error=str(classification.get("environment_error") or ""),
    )


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


def _candidate_display_path(candidate_file: Path, workspace_root: str) -> str:
    root = Path(workspace_root or ".").resolve()
    try:
        rel = candidate_file.resolve().relative_to(root)
        return str(rel)
    except ValueError:
        return str(candidate_file)


def _execution_env(invocation_profile: dict[str, Any]) -> dict[str, str]:
    env = {
        "ASAN_OPTIONS": "abort_on_error=1:symbolize=1:detect_leaks=0",
        "UBSAN_OPTIONS": "halt_on_error=1:print_stacktrace=1",
        "MSAN_OPTIONS": "halt_on_error=1:exit_code=86",
    }
    library_path = str(invocation_profile.get("library_path") or "")
    if library_path:
        current = os.environ.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = library_path if not current else f"{library_path}:{current}"
    return env


def _run_with_subprocess(
    *,
    binary_path: str,
    mode: str,
    candidate_file: Path,
    timeout_seconds: int,
    extra_env: dict[str, str],
) -> tuple[int | None, str, str, bool]:
    if mode == "stdin":
        argv = [binary_path]
        stdin_handle = candidate_file.open("rb")
    else:
        argv = [binary_path, str(candidate_file)]
        stdin_handle = None
    env = dict(os.environ)
    env.update(extra_env)
    try:
        completed = subprocess.run(
            argv,
            stdin=stdin_handle,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=max(1, int(timeout_seconds)),
            env=env,
            check=False,
        )
        return (
            completed.returncode,
            completed.stdout.decode("utf-8", errors="replace"),
            completed.stderr.decode("utf-8", errors="replace"),
            False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        return None, stdout, stderr, True
    finally:
        if stdin_handle is not None:
            stdin_handle.close()


def _run_with_qitos_env(
    *,
    env_runner: Any,
    binary_path: str,
    mode: str,
    candidate_display_path: str,
    timeout_seconds: int,
    extra_env: dict[str, str],
) -> tuple[int | None, str, str, bool]:
    env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in extra_env.items())
    if mode == "stdin":
        command = f"{env_prefix} {shlex.quote(binary_path)} < {shlex.quote(candidate_display_path)}"
    else:
        command = f"{env_prefix} {shlex.quote(binary_path)} {shlex.quote(candidate_display_path)}"
    result = env_runner.cmd.run(command, timeout=max(1, int(timeout_seconds)) + 2)
    rc = result.get("returncode")
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    timed_out = bool(result.get("timed_out")) or rc in (124, 137)
    return rc, stdout, stderr, timed_out


def _error_result(
    *,
    candidate_digest: str,
    objective_id: str | None,
    invocation_digest: str,
    candidate_path: str,
    binary_path: str,
    mode: str,
    workspace_root: str,
    start: float,
    message: str,
    outcome: ExecutionOutcome = "environment_error",
) -> CandidateExecutionResult:
    evidence_ref = write_runtime_artifact(
        workspace_root=workspace_root,
        candidate_digest=candidate_digest,
        payload={"error": message, "outcome": outcome},
    )
    return CandidateExecutionResult(
        candidate_digest=candidate_digest,
        objective_id=objective_id,
        invocation_digest=invocation_digest,
        outcome=outcome,
        exit_code=None,
        signal_name=None,
        sanitizer_kind=None,
        top_frame=None,
        evidence_ref=evidence_ref,
        stdout_tail="",
        stderr_tail="",
        stdout_truncated=False,
        stderr_truncated=False,
        elapsed_ms=int((time.monotonic() - start) * 1000),
        candidate_path=candidate_path,
        binary_path=binary_path,
        mode=mode,
        environment_error=message,
    )
