"""Validation, bash classification, tool gating, and budget tracking mixin.

Extracted from CyberGymAgent for maintainability.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from qitos.core.decision import Decision

from ...state import CyberGymState

from ..core.constants import (
    NO_CANDIDATE_READ_ACTION_LIMIT,
    ACTIVE_CANDIDATE_READ_ACTION_LIMIT,
    REINVESTIGATE_ENABLED,
    REINVESTIGATE_AFTER_SUBMITS,
    POC_OUTPUT_DIR,
    LOOP_REMINDER_TEXT,
    FAILURE_REFLECTION_ACK_KEY,
    REPEATED_FAILURE_REFLECTION_THRESHOLD,
    REFLECTION_ATTEMPT_COOLDOWN,
    FAILURE_REFLECTION_ATTEMPT_KEY,
    ACTIVE_CANDIDATE_TARGETED_READ_LIMIT,
    CANDIDATE_REQUIRED_REMINDER_TEXT,
)
from ..core.metadata_keys import LAST_FEEDBACK_ACTION
from ...tool_names import (
    SUBMIT_POC,
    RUN_CANDIDATE,
    PROBE_RUNTIME_FRONTIER,
    GDB_DEBUG,
    READ,
    GREP,
    GLOB,
    BASH,
    WRITE,
    EVIDENCE_TOOLS,
    WRITE_TOOLS,
    READ_ONLY_TOOLS,
    RECORD_CHAIN_NODE,
    RECORD_GATE,
    RECORD_SINK_CANDIDATE,
    ANALYSIS_QUERY_TOOLS,
    CONFIRM_FORMAT,
)


class ValidationMixin:
    """Validation, bash classification, tool gating, and budget tracking.

    All methods were originally defined on CyberGymAgent and have been
    moved here verbatim.  Cross-references to other CyberGymAgent methods
    that live in *this* mixin use ``ValidationMixin._XXX``; references to
    methods in other mixins (e.g. PathMixin) remain as ``self._XXX()``.
    """

    # ------------------------------------------------------------------
    # Loop reminder
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_set_loop_reminder(state: CyberGymState, signature: str, message: str = LOOP_REMINDER_TEXT) -> None:
        signature = str(signature or "").strip()
        if not signature:
            return
        message_text = str(message or LOOP_REMINDER_TEXT).strip()
        if not message_text:
            return
        try:
            current_step = int(getattr(state, "current_step", 0) or 0)
        except Exception:
            current_step = 0
        try:
            cooldown_until = int((state.reminder_cooldowns or {}).get(signature, 0) or 0)
        except Exception:
            cooldown_until = 0
        if current_step < cooldown_until:
            return
        existing_lines = [
            line.strip()
            for line in str(getattr(state, "pending_reminder", "") or "").splitlines()
            if line.strip()
        ]
        if message_text not in existing_lines:
            existing_lines.append(message_text)
        existing_sigs = [
            item
            for item in str(getattr(state, "pending_reminder_signature", "") or "").split("|")
            if item
        ]
        if signature not in existing_sigs:
            existing_sigs.append(signature)
        state.pending_reminder = "\n".join(existing_lines)
        state.pending_reminder_signature = "|".join(existing_sigs)
        state.reminder_cooldowns[signature] = current_step + 8

    # ------------------------------------------------------------------
    # Read budget / reinvestigation / failure reflection
    # ------------------------------------------------------------------

    @staticmethod
    def _read_budget_exhausted(state: CyberGymState) -> bool:
        return (
            state.current_phase in ("formulation", "verification")
            and not ValidationMixin._ready_poc_paths(state)
            and state.phase_read_actions >= NO_CANDIDATE_READ_ACTION_LIMIT
        )

    @staticmethod
    def _should_reinvestigate(state: CyberGymState) -> bool:
        """True when the agent has blind-sprayed many candidates with zero
        crashes and should stop spraying to re-read the vulnerable code. Gates
        OFF the FORCE_SUBMIT_HARD read-block so investigation is possible again."""
        if not REINVESTIGATE_ENABLED:
            return False
        try:
            attempts = int(state.poc_attempts or 0)
            best = int(state.best_poc_score or 0)
        except Exception:
            return False
        return attempts >= REINVESTIGATE_AFTER_SUBMITS and best <= 0

    @staticmethod
    def _constraint_reinvestigation_allowed(state: CyberGymState) -> bool:
        """Allow targeted evidence checks after misses while path gates are open."""
        if not getattr(state, "last_verification_result", None):
            return False
        try:
            if int(getattr(state, "best_poc_score", 0) or 0) > 0:
                return False
        except Exception:
            return False
        for item in list(getattr(state, "path_constraints", []) or []):
            status = str(getattr(item, "status", "") or "").strip().lower()
            if status in {"unknown", "hypothesized", "open"}:
                return True
        return False

    @staticmethod
    def _failure_reflection_acknowledged(state: CyberGymState) -> bool:
        signature = str(state.repeated_failure_signature or "")
        return bool(signature) and str(state.metadata.get(FAILURE_REFLECTION_ACK_KEY) or "") == signature

    @staticmethod
    def _mark_failure_signature_reflected(state: CyberGymState) -> None:
        signature = str(state.repeated_failure_signature or "")
        if signature and state.repeated_failure_count >= REPEATED_FAILURE_REFLECTION_THRESHOLD:
            state.metadata[FAILURE_REFLECTION_ACK_KEY] = signature
            state.metadata[FAILURE_REFLECTION_ATTEMPT_KEY] = int(state.poc_attempts or 0)

    @staticmethod
    def _failure_reflection_on_cooldown(state: CyberGymState) -> bool:
        try:
            last_attempt = int(state.metadata.get(FAILURE_REFLECTION_ATTEMPT_KEY, 0) or 0)
        except Exception:
            last_attempt = 0
        return int(state.poc_attempts or 0) - last_attempt < REFLECTION_ATTEMPT_COOLDOWN

    @staticmethod
    def _derive_control_mode(state: CyberGymState) -> str:
        if getattr(state, "pending_chain_checkpoint", False):
            return "chain_checkpoint_pending"
        if getattr(state, "pending_gates_checkpoint", False):
            return "gates_checkpoint_pending"
        if getattr(state, "pending_diagnosis", False) and not getattr(state, "gdb_unavailable", False):
            return "diagnosis_required"
        if getattr(state, "pending_reproduction", False) and not getattr(state, "gdb_unavailable", False):
            return "diagnosis_required"
        # P43: add a 2-step cooldown after post_submit_miss so the agent
        # actually sees gate-specific repair guidance before the mode
        # switches to candidate_ready.  Without this, writing a new PoC
        # file immediately supersedes the miss guidance.
        current_mode = str(getattr(state, "control_mode", "") or "")
        if current_mode == "post_submit_miss":
            try:
                mode_local = int(getattr(state, "mode_local_steps", 0) or 0)
            except Exception:
                mode_local = 0
            # Keep post_submit_miss for at least 2 steps so the guidance
            # can influence the agent's next action. After 2 steps, allow
            # transition to candidate_ready if a PoC is ready.
            if mode_local < 2:
                return "post_submit_miss"
        if ValidationMixin._ready_poc_paths(state):
            return "candidate_ready"
        if state.last_verification_result and not state.is_verified():
            return "post_submit_miss"
        if state.candidate_required:
            return "candidate_required"
        if ValidationMixin._read_budget_exhausted(state):
            return "candidate_required"
        if state.current_phase == "ingestion":
            return "orienting"
        return "no_candidate"

    def _update_control_mode(self, state: CyberGymState, step: int) -> None:
        mode = self._derive_control_mode(state)
        if mode != str(getattr(state, "control_mode", "") or ""):
            state.control_mode = mode
            state.mode_enter_step = int(step)
            state.mode_local_steps = 0
            return
        try:
            state.mode_local_steps = max(0, int(step) - int(state.mode_enter_step or 0))
        except Exception:
            state.mode_local_steps = 0

    # ------------------------------------------------------------------
    # Candidate read budget / path resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _candidate_reads_used(state: CyberGymState) -> int:
        try:
            return int(state.metadata.get("candidate_targeted_reads_used", 0) or 0)
        except Exception:
            return 0

    @staticmethod
    def _candidate_targeted_read_limit(state: CyberGymState) -> int:
        if ValidationMixin._ready_poc_paths(state):
            return 0
        return ACTIVE_CANDIDATE_TARGETED_READ_LIMIT

    def _candidate_targeted_read_allowed(self, state: CyberGymState) -> bool:
        return self._candidate_reads_used(state) < self._candidate_targeted_read_limit(state)

    @staticmethod
    def _resolve_candidate_path(state: CyberGymState, path: str) -> Path:
        candidate = Path(str(path or ""))
        if candidate.is_absolute():
            return candidate
        workspace_root = str(state.workspace_root or "").strip()
        if workspace_root:
            return Path(workspace_root) / candidate
        return candidate

    @staticmethod
    def _candidate_file_exists(state: CyberGymState, path: str) -> bool:
        if not str(path or "").strip():
            return False
        try:
            return ValidationMixin._resolve_candidate_path(state, path).is_file()
        except (OSError, ValueError):
            return False

    @staticmethod
    def _candidate_ready_file_missing(state: CyberGymState) -> bool:
        return bool(ValidationMixin._missing_ready_poc_paths(state))

    @staticmethod
    def _ready_poc_paths(state: CyberGymState) -> List[str]:
        paths: List[str] = []
        seen: set[str] = set()
        for candidate in list(getattr(state, "ready_pocs", []) or []):
            if not getattr(candidate, "ready_to_submit", True):
                continue
            file_path = str(getattr(candidate, "file_path", "") or "").strip()
            if not file_path or file_path in seen:
                continue
            seen.add(file_path)
            paths.append(file_path)
        return paths

    @staticmethod
    def _missing_ready_poc_paths(state: CyberGymState) -> List[str]:
        missing: List[str] = []
        for path in ValidationMixin._ready_poc_paths(state):
            if not ValidationMixin._candidate_file_exists(state, path):
                missing.append(path)
        return missing

    @staticmethod
    def _candidate_ready_submit_paths(
        state: CyberGymState,
        *,
        include_active: bool = True,
    ) -> List[str]:
        _ = include_active
        paths: List[str] = ValidationMixin._ready_poc_paths(state)

        deduped: List[str] = []
        seen: set[str] = set()
        for path in paths:
            key = path.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(path)
        return deduped

    @staticmethod
    def _render_candidate_path_chunks(paths: List[str], *, chunk_size: int = 8) -> List[str]:
        lines: List[str] = []
        for index in range(0, len(paths), chunk_size):
            chunk = paths[index:index + chunk_size]
            rendered = ", ".join(f"`{path}`" for path in chunk)
            lines.append(rendered)
        return lines

    # ------------------------------------------------------------------
    # Candidate-ready non-submit validation
    # ------------------------------------------------------------------

    def _validate_candidate_ready_non_submit(
        self,
        *,
        runtime_context: Optional[Dict[str, Any]],
        tool_name: str,
        path: str = "",
    ) -> str:
        # P34: fail closed — if runtime_context is missing or has no state,
        # block access rather than silently allowing it.
        state = runtime_context.get("state") if isinstance(runtime_context, dict) else None
        if not isinstance(state, CyberGymState):
            return (
                f"Cannot validate tool access: runtime context unavailable. "
                f"`{tool_name}` is blocked until the framework provides valid state."
            )
        if not ValidationMixin._ready_poc_paths(state):
            return ""
        if self._candidate_ready_file_missing(state):
            return ""
        # Allow WRITE to pocs/ for new PoC construction after a NO TRIGGER.
        if tool_name == WRITE and path and "pocs/" in str(path).lower():
            return ""
        return (
            f"Candidate is ready for submission. Call submit_poc now; "
            f"`{tool_name}` is blocked until every ready candidate has been submitted."
        )

    # ------------------------------------------------------------------
    # Tool access validation
    # ------------------------------------------------------------------

    def _validate_tool_access(
        self,
        *,
        runtime_context: Optional[Dict[str, Any]],
        tool_label: str = "",
        action_verb: str = "using",
    ) -> str:
        """Unified validation for read/grep/evidence tool access gating.

        Returns a blocking message or empty string to allow.
        ``tool_label`` is the human-facing name (e.g. "read", "grep",
        a specific evidence tool name). ``action_verb`` customises the
        "do not ... before submitting" phrase.
        """
        # P34: fail closed — if runtime_context is missing or has no state,
        # block access rather than silently allowing it.
        state = runtime_context.get("state") if isinstance(runtime_context, dict) else None
        if not isinstance(state, CyberGymState):
            return (
                f"Cannot validate tool access: runtime context unavailable. "
                f"`{tool_label}` is blocked until the framework provides valid state."
            )
        # Chain checkpoint: only allow search + recording tools
        if getattr(state, "pending_chain_checkpoint", False):
            _allowed_during_checkpoint = READ_ONLY_TOOLS | {
                "find_symbols", "callsite_search",
                "record_chain_node", "record_gate",
            }
            if tool_label not in _allowed_during_checkpoint:
                return (
                    "Constraint checkpoint active — record at least one chain node "
                    f"via record_chain_node before {action_verb}. "
                    "read/grep/find_symbols are still allowed."
                )
        # Gates checkpoint: only allow search + gate recording tools
        if getattr(state, "pending_gates_checkpoint", False):
            _allowed_during_gates_checkpoint = READ_ONLY_TOOLS | {
                "find_symbols", "callsite_search",
                "record_chain_node", "record_gate",
            }
            if tool_label not in _allowed_during_gates_checkpoint:
                return (
                    "Gates checkpoint active — record at least one path constraint "
                    f"via record_gate before {action_verb}. "
                    "read/grep/find_symbols are still allowed."
                )
        if ValidationMixin._ready_poc_paths(state):
            if self._candidate_ready_file_missing(state):
                return (
                    f"A ready PoC path is missing. Create or regenerate the file "
                    f"with BASH/WRITE before {action_verb}."
                )
            # Escape hatch: when consecutive_misses >= 4, the reminder tells
            # the agent to stop submitting and re-investigate.  Allow
            # read-only tools so it can actually follow that guidance.
            # Same for consecutive_submit_errors >= 3 (server errors).
            if tool_label in READ_ONLY_TOOLS and (
                state.consecutive_misses >= 4 or state.consecutive_submit_errors >= 3
            ):
                return ""
            # Soft nudge for evidence/reading tools — the agent may need to
            # verify a constraint before submitting.  Don't hard-block.
            if tool_label in READ_ONLY_TOOLS:
                return (
                    "A candidate PoC exists — submit it with submit_poc. "
                    "If you need to verify a specific constraint first, you may "
                    f"{action_verb}, but submit promptly afterward."
                )
            return (
                "Candidate is ready for submission. Call submit_poc now; "
                f"do not {action_verb} before submitting."
            )
        # Hard-block READ/GREP when candidate_required and no PoC exists.
        # The agent has been reading long enough — force it to build.
        if (
            getattr(state, "candidate_required", False)
            and tool_label in READ_ONLY_TOOLS
        ):
            return (
                "candidate_required is active — stop reading and build a PoC "
                f"using BASH/WRITE. {action_verb.capitalize()} is blocked "
                "until a PoC is submitted or candidate_required clears."
            )
        return ""

    # ------------------------------------------------------------------
    # Bash command classification
    # ------------------------------------------------------------------

    @staticmethod
    def _bash_is_file_browse_command(command: str) -> bool:
        text = str(command or "").strip().lower()
        if re.search(r"(?:^|[;&|]\s*)cat\s*(?:>{1,2}|<<)", text):
            return False
        # Any command redirecting to pocs/ is PoC construction, not browsing
        if ValidationMixin._bash_is_poc_construction_command(command):
            return False
        browse_patterns = (
            r"(?:^|[;&|]\s*)(?:cat|bat|less|more|nl|strings|hexdump|xxd)\b",
            r"(?:^|[;&|]\s*)sed\s+-n\b",
            r"(?:^|[;&|]\s*)awk\b",
        )
        if any(re.search(pattern, text) for pattern in browse_patterns):
            return True
        head_tail_starts = (
            r"^\s*head\b",
            r"^\s*tail\b",
        )
        if any(re.search(pattern, text) for pattern in head_tail_starts):
            return True
        return False

    @staticmethod
    def _bash_is_search_command(command: str) -> bool:
        text = str(command or "").strip().lower()
        search_patterns = (
            r"(?:^|[;&|]\s*)(?:rg|grep|find|fd|ls|tree)\b",
        )
        return any(re.search(pattern, text) for pattern in search_patterns)

    @staticmethod
    def _bash_is_candidate_ready_maintenance_command(command: str) -> bool:
        text = str(command or "").strip().lower()
        if not text:
            return False
        if re.search(r"(?:^|[;&|]\s*)(?:rg|grep|find|fd|tree)\b", text):
            return False
        if ValidationMixin._bash_is_python_source_browse_command(command):
            return False
        # PoC construction commands are always allowed when a candidate
        # exists -- the agent may need to create a new PoC after a NO TRIGGER.
        if ValidationMixin._bash_is_poc_construction_command(command):
            return True
        relocation_patterns = (
            r"(?:^|[;&|]\s*)(?:cp|mv|chmod)\b",
        )
        inspection_patterns = (
            r"(?:^|[;&|]\s*)ls\s+-[a-z]*l[a-z]*\b",
            r"(?:^|[;&|]\s*)(?:stat|file|wc|sha1sum|sha256sum|md5sum)\b",
            r"(?:^|[;&|]\s*)(?:xxd|hexdump)\b",
            r"(?:^|[;&|]\s*)(?:head|tail)\b",
            r"(?:^|[;&|]\s*)(?:cmp|diff)\b",
        )
        return any(re.search(pattern, text) for pattern in relocation_patterns + inspection_patterns)

    @staticmethod
    def _bash_is_poc_construction_command(command: str) -> bool:
        """True when the command writes to or creates a file in pocs/."""
        text = str(command or "").strip().lower()
        if not text:
            return False
        poc_output_patterns = (
            # Shell redirection to pocs/
            r"[>]\s*['\"]?[^;\s&|]*pocs[^;\s&|]*",
            # dd of=pocs/...
            r"\bof=['\"]?[^;\s&|]*pocs[^;\s&|]*",
            # Python open("pocs/...", "w"/"wb"/"a")
            r"""open\(\s*['"][^'"]*pocs[^'"]*['"]\s*,\s*['"][wa]""",
            # printf/echo redirecting to pocs/
            r"(?:printf|echo)\s+.*[>]\s*['\"]?[^;\s&|]*pocs[^;\s&|]*",
            # xxd -r writing to pocs/
            r"xxd\s+-r\s+.*[>]\s*['\"]?[^;\s&|]*pocs[^;\s&|]*",
            # tee to pocs/
            r"\btee\s+['\"]?[^;\s&|]*pocs[^;\s&|]*",
            # cp/mv destination is pocs/
            r"(?:^|[;&|]\s*)(?:cp|mv)\s+(?:-\S+\s+)*\S+\s+['\"]?[^;\s&|]*pocs[^;\s&|]*",
        )
        return any(re.search(pattern, text) for pattern in poc_output_patterns)

    @staticmethod
    def _bash_is_candidate_file_sanity_command(command: str, state: CyberGymState) -> bool:
        text = str(command or "").strip().lower()
        if not text:
            return False
        if re.search(r"(?:^|[;&|]\s*)(?:rg|grep|find|fd|tree)\b", text):
            return False
        if ValidationMixin._bash_is_python_source_browse_command(command):
            return False
        candidate_paths = ValidationMixin._ready_poc_paths(state)
        candidate_paths = [path for path in candidate_paths if path]
        if not candidate_paths:
            return False
        if not any(path.lower() in text or Path(path).name.lower() in text for path in candidate_paths):
            return False
        inspection_patterns = (
            r"(?:^|[;&|]\s*)ls\s+-[a-z]*l[a-z]*\b",
            r"(?:^|[;&|]\s*)(?:stat|file|wc|sha1sum|sha256sum|md5sum)\b",
            r"(?:^|[;&|]\s*)(?:xxd|hexdump)\b",
            r"(?:^|[;&|]\s*)(?:head|tail)\b",
        )
        return any(re.search(pattern, text) for pattern in inspection_patterns)

    @staticmethod
    def _bash_is_python_source_browse_command(command: str) -> bool:
        text = str(command or "").strip().lower()
        if not re.search(r"(?:^|[;&|]\s*)(?:python3?|python)\b", text):
            return False
        if not any(marker in text for marker in ("repo-vul/", "repo-vul", "src/")):
            return False
        # Exception: if the Python command writes to pocs/, it's PoC
        # construction, not source browsing.
        if ValidationMixin._bash_is_poc_construction_command(command):
            return False
        browse_markers = (
            "open(",
            ".read_text(",
            ".read_bytes(",
            ".readlines(",
            ".readline(",
            ".read(",
            "for line in",
        )
        output_markers = ("print(", "sys.stdout", "write(")
        return any(marker in text for marker in browse_markers) and any(marker in text for marker in output_markers)

    @staticmethod
    def _bash_prints_generated_diagnostic(command: str) -> bool:
        text = str(command or "").strip().lower()
        generation_markers = (
            r"(?:^|[;&|]\s*)(?:gcc|g\+\+|cc|clang|make|cmake|python3?|perl|ruby|node|bash)\b",
            r"(?:^|[;&|]\s*)\./[a-z0-9_.-]+",
        )
        if not any(re.search(pattern, text) for pattern in generation_markers):
            return False
        diagnostic_read_patterns = (
            r"(?:^|[;&|]\s*)cat\s+['\"]?([a-z0-9_.-]*(?:err|error|log|out|stdout|stderr|txt)[a-z0-9_.-]*)['\"]?(?:\s|$)",
            r"(?:^|[;&|]\s*)(?:head|tail)\s+(?:-\S+\s+)?['\"]?([a-z0-9_.-]*(?:err|error|log|out|stdout|stderr|txt)[a-z0-9_.-]*)['\"]?(?:\s|$)",
        )
        return any(re.search(pattern, text) for pattern in diagnostic_read_patterns)

    # ------------------------------------------------------------------
    # Bash validation
    # ------------------------------------------------------------------

    def _validate_bash_command(
        self,
        *,
        runtime_context: Optional[Dict[str, Any]],
        command: str,
        blocking_question: str,
    ) -> str:
        state = runtime_context.get("state") if isinstance(runtime_context, dict) else None
        if not isinstance(state, CyberGymState):
            # P34: fail closed
            return (
                "Cannot validate bash command: runtime context unavailable. "
                "bash is blocked until the framework provides valid state."
            )
        if ValidationMixin._ready_poc_paths(state):
            if not self._candidate_ready_file_missing(state):
                # Escape hatch: when consecutive_misses >= 4 or
                # consecutive_submit_errors >= 3, allow search/browse
                # commands so the agent can re-investigate.
                if state.consecutive_misses >= 4 or state.consecutive_submit_errors >= 3:
                    return ""
                if self._bash_is_candidate_ready_maintenance_command(command):
                    return ""
                # Allow PoC construction commands even when a candidate exists.
                if ValidationMixin._bash_is_poc_construction_command(command):
                    return ""
                return (
                    "Candidate is ready for submission. Call submit_poc now; "
                    "only PoC construction in pocs/, byte-level sanity checks, or moving "
                    "the existing candidate into place are allowed before submitting."
                )
        if self._bash_is_candidate_file_sanity_command(command, state):
            return ""
        if self._bash_is_python_source_browse_command(command):
            return (
                "bash cannot be used to extract source code with Python. "
                "Use grep for search or read(path, offset=..., limit=...) for exact source ranges."
            )
        if self._bash_is_file_browse_command(command):
            return (
                "bash is not the file-reading tool. Use read(path) when you need file contents."
            )
        return ""

    # ------------------------------------------------------------------
    # Candidate requirement / tool schema gating
    # ------------------------------------------------------------------

    def _update_candidate_requirement_from_decision(
        self,
        state: CyberGymState,
        decision: Decision | Any,
    ) -> None:
        if ValidationMixin._ready_poc_paths(state):
            state.candidate_required = False
            return
        if state.current_phase not in ("investigation", "formulation", "verification"):
            return
        # Check negative evidence: if same mutation axis has failed repeatedly,
        # set candidate_required but also set a hint to revise mapping or rotate
        negative_evidence = list(
            (state.metadata or {}).get("negative_evidence", [])
            if isinstance(state.metadata, dict) else []
        )
        recipe = (state.metadata or {}).get("poc_recipe", {}) if isinstance(state.metadata, dict) else {}
        if negative_evidence and isinstance(recipe, dict):
            active_evidence = [ev for ev in negative_evidence if ev.get("ttl", 0) > 0]
            trigger_evidence = [ev for ev in active_evidence if ev.get("kind") in ("path_reached_no_trigger", "no_crash_unknown")]
            if len(trigger_evidence) >= 2:
                # Same family repeated no-trigger → allow targeted READ/replan
                # rather than just "SUBMIT NOW" pressure
                state.candidate_required = True
                state.metadata["_replan_hint"] = (
                    "Repeated no-trigger — revise mapping offset/strategy or "
                    "rotate to a different sink candidate before generating another PoC"
                )
                return
        if self._read_budget_exhausted(state):
            state.candidate_required = True
            self._maybe_set_loop_reminder(
                state,
                "candidate-required:read-budget",
                CANDIDATE_REQUIRED_REMINDER_TEXT,
            )

    def _candidate_construction_tool_names(self, state: CyberGymState) -> set[str]:
        # When candidate_required is set and no PoC exists, hard-block
        # READ/GREP to force the agent to build a PoC instead of reading
        # more code.
        if getattr(state, "candidate_required", False) and not ValidationMixin._ready_poc_paths(state):
            return {
                BASH,
                WRITE,
                SUBMIT_POC,
                CONFIRM_FORMAT,
            }
        if ValidationMixin._ready_poc_paths(state):
            if self._candidate_ready_file_missing(state):
                return {
                    BASH,
                    WRITE,
                    SUBMIT_POC,
                }
            # Escape hatch: when consecutive_misses >= 4 or
            # consecutive_submit_errors >= 3, allow investigation tools so
            # the agent can re-read source code and trace the path-gating
            # condition as the reminder advises.
            if state.consecutive_misses >= 4 or state.consecutive_submit_errors >= 3:
                return {
                    *READ_ONLY_TOOLS,
                    "find_symbols", "callsite_search",
                    BASH,
                    WRITE,
                    SUBMIT_POC,
                    RECORD_CHAIN_NODE,
                    RECORD_GATE,
                }
            # After a NO TRIGGER, allow construction tools so the agent can
            # build a new PoC variant.  The post_submit_miss mode lasts 2 steps.
            current_mode = str(getattr(state, "control_mode", "") or "")
            if current_mode == "post_submit_miss":
                return {
                    BASH,
                    WRITE,
                    SUBMIT_POC,
                }
            return self._submit_ready_tool_names()
        names = {
            *READ_ONLY_TOOLS,
            WRITE,
            BASH,
            RECORD_CHAIN_NODE,
            RECORD_GATE,
            RECORD_SINK_CANDIDATE,
        }
        # Only offer submit_poc when there are ready PoCs to submit.
        # Without this guard, the model calls submit_poc() with empty
        # or stale paths and enters a dead loop.
        if ValidationMixin._ready_poc_paths(state):
            names.add(SUBMIT_POC)
        return names

    def _layered_tool_schema_names(self, state: CyberGymState) -> set[str]:
        required_dynamic_tool = self._required_dynamic_tool_name(state)
        if required_dynamic_tool:
            # A dynamic feedback hard block is the authoritative next action.
            # Keep the schema to that single tool so candidate-ready and
            # post-submit construction lanes cannot offer submit/read/write
            # alternatives that contradict Runtime Context.
            # Explicitly exclude submit_poc to prevent GLM from bypassing
            # the hard block by salvaging a textual submit_poc call.
            return {required_dynamic_tool}
        if self._should_filter_to_candidate_tools(state):
            return self._candidate_construction_tool_names(state)
        names = {
            READ,
            GREP,
            GLOB,
            BASH,
            WRITE,
            SUBMIT_POC,
            RECORD_CHAIN_NODE,
            RECORD_GATE,
        }
        advanced_context = (
            state.current_phase in {"investigation", "formulation", "verification"}
            or bool(getattr(state, "harness_signals", None))
            or bool(getattr(state, "path_constraints", None))
            or bool(getattr(state, "last_verification_result", None))
        )
        if advanced_context:
            names.update(EVIDENCE_TOOLS)
        names.add(RECORD_SINK_CANDIDATE)
        desc_analysis = getattr(state, "description_analysis", None)
        desc_status = str(getattr(desc_analysis, "status", "") or "pending")
        if state.current_phase in {"ingestion", "exploration", "investigation"} and desc_status in {"", "pending", "recorded"}:
            names.add("analyze_description")
        if state.current_phase in {"ingestion", "exploration", "investigation"}:
            names.add("discover_sink_navigation_leads")
        if getattr(state, "active_sink_candidate_id", ""):
            pass  # sink analysis is auto-triggered by record_sink_candidate
        brief = dict(getattr(state, "latest_sink_analysis_brief", {}) or {})
        for query in brief.get("suggested_queries", []):
            tool_name = str(query.get("tool") or "")
            if tool_name in ANALYSIS_QUERY_TOOLS:
                names.add(tool_name)
        if brief:
            pass  # analysis result details available in observation sections
        # confirm_format: available when format is not yet confirmed
        pack_mode = getattr(state, "pack_mode", {}) or {}
        if pack_mode.get("mode", "unconfirmed") != "confirmed":
            names.add(CONFIRM_FORMAT)
        return names

    @staticmethod
    def _required_dynamic_tool_name(state: CyberGymState) -> str:
        """Return the dynamic tool mandated by feedback arbitration, if any."""
        # Clear stale pending_diagnosis/pending_reproduction (run_candidate removed)
        if getattr(state, "pending_diagnosis", False):
            state.pending_diagnosis = False
        if getattr(state, "pending_reproduction", False):
            state.pending_reproduction = False
        metadata = getattr(state, "metadata", {}) or {}
        feedback_action = metadata.get(LAST_FEEDBACK_ACTION) or {}
        if not isinstance(feedback_action, dict) or not feedback_action.get("blocks_submit"):
            return ""
        action = str(feedback_action.get("action") or "")
        if action == GDB_DEBUG:
            return action
        return ""

    def _submit_ready_tool_names(self) -> set[str]:
        return {
            SUBMIT_POC,
            BASH,
            WRITE,
        }

    def _should_filter_to_candidate_tools(self, state: CyberGymState) -> bool:
        if ValidationMixin._ready_poc_paths(state):
            return True
        return False

    @staticmethod
    def _tool_schema_filter_reason(state: CyberGymState) -> str:
        if ValidationMixin._ready_poc_paths(state):
            return "candidate_submit_ready"
        return "candidate_required"

    # ------------------------------------------------------------------
    # Read budget tracking
    # ------------------------------------------------------------------

    def _track_read_budget(
        self,
        state: CyberGymState,
        short_name: str,
        output: Any,
    ) -> None:
        normalized_name = str(short_name or "")
        readish = {
            *READ_ONLY_TOOLS,
            "read_file",
            "read_file_range",
            "grep",
            "grep_files",
            "search",
            "glob_files",
            "list_files",
            "list_tree",
            "view",
        }
        if normalized_name == BASH and isinstance(output, dict):
            command = str(output.get("command", "") or "")
            if self._bash_is_search_command(command):
                readish.add(BASH)
        if short_name not in readish and normalized_name not in readish:
            return
        if state.current_phase not in ("formulation", "verification"):
            return

        state.phase_read_actions += 1
        target = self._read_target_from_output(output)
        if not target:
            return
        if target == state.repeated_read_target:
            state.repeated_read_count += 1
        else:
            state.repeated_read_target = target
            state.repeated_read_count = 1
        if state.repeated_read_count >= 4 and self._has_submit_feedback(state):
            self._maybe_set_loop_reminder(state, f"repeated-read:{target}")

    @staticmethod
    def _read_target_from_output(output: Any) -> str:
        if isinstance(output, dict):
            for key in ("path", "pattern", "command"):
                value = output.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _has_submit_feedback(state: CyberGymState) -> bool:
        return bool(
            state.last_verification_result
            or state.feedback_history
            or state.hot_feedback_window
            or state.poc_attempts
            or state.last_submitted_poc_path
        )
