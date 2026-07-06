"""Dynamic execution tools for staged vulnerable binaries."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult

from ...tool_names import PROBE_RUNTIME_FRONTIER, RUN_CANDIDATE
from ..core.metadata_keys import INVOCATION_PROFILE, STAGED_BINARY_CAPABILITY
from ..runtime.candidate_runner import run_candidate_once
from ..runtime.gdb_frontier import DEFAULT_PROBE_ROLES, run_gdb_frontier_probe


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
            reason = profile.get("reason") if isinstance(profile, dict) else "missing_profile"
            return ToolValidationResult.fail(f"invocation profile unresolved: {reason}")

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
