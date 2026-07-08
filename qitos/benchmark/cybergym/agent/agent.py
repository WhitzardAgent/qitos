"""CyberGymAgent -- PoC Generation Agent for CyberGym Level 1 tasks.

Implements the four-phase state machine:
  Ingestion -> Investigation -> Formulation -> Verification

Uses QitOS framework features:
- PhaseEngine for declarative phase transitions with step-based forcing
- Explicit project artifact files for raw feedback/tool-result retrieval
- ToolRegistry with auto_short_aliases for native tool calling
- ContextConfig for context overflow protection
- Engine handles native tool calling multi-turn conversations automatically
"""

from __future__ import annotations

import json
import os
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.history import HistoryPolicy
from qitos.core.memory import MemoryRecord
from qitos.core.model_response import ModelResponse
from qitos.core.observation import Observation
from qitos.core.tool_result import ToolResult
from qitos.kit.memory.memdir_memory import MemdirMemory
from qitos.prompting import PromptBuildResult

from .state import CyberGymState
from .context import CyberGymContextHistory
from .evidence_selector import (
    bootstrap_evidence_index,
    initial_families_for_task,
)
from .family_runtime import (
    FamilyRecord,
    enqueue_candidate,
)
from .versioning import normalize_agent_mode
from .tool_names import (
    READ as READ_TOOL,
    GREP as GREP_TOOL,
    GLOB as GLOB_TOOL,
    FIND_SYMBOLS as FIND_SYMBOLS_TOOL,
    CALLSITE_SEARCH as CALLSITE_SEARCH_TOOL,
    REPO_MAP as REPO_MAP_TOOL,
    FILE_INFO as FILE_INFO_TOOL,
    HEX_VIEW as HEX_VIEW_TOOL,
    STRUCT_PROBE as STRUCT_PROBE_TOOL,
    CORPUS_INSPECT as CORPUS_INSPECT_TOOL,
    WRITE as WRITE_TOOL,
    BASH as BASH_TOOL,
    SUBMIT_POC as SUBMIT_POC_TOOL,
)
from .agent_impl.core.constants import (
    CYBERGYM_HISTORY_MAX_TOKENS, CYBERGYM_HISTORY_WARNING_RATIO,
    FAILURE_REFLECTION_ACK_KEY,
)
from .agent_impl.core.utils import (
    sanitize_model_text as _sanitize_model_text,
)
from .agent_impl.prompt.phase import cybergym_phase_engine
from .agent_impl.tools.registry import build_tool_registry
from .agent_impl.prompt.state_init import StateInitMixin
from .agent_impl.repo.task_analysis import TaskAnalysisMixin
from .agent_impl.repo.analysis import RepoAnalysisMixin
from .agent_impl.core.crash_parsing import CrashParsingMixin
from .agent_impl.prompt.prompts import PromptsMixin
from .agent_impl.poc.harness import HarnessMixin
from .agent_impl.core.paths import PathMixin
from .agent_impl.observation.validation import ValidationMixin
from .agent_impl.poc.candidates import CandidateFamilyMixin
from .agent_impl.feedback.mixin import FeedbackMixin
from .agent_impl.observation.renderer import ObservationMixin
from .agent_impl.tools.mixin import ToolMixin
from .agent_impl.static.runtime import StaticAnalysisRuntimeMixin


class CyberGymAgent(StaticAnalysisRuntimeMixin, StateInitMixin, TaskAnalysisMixin, RepoAnalysisMixin, CrashParsingMixin, PromptsMixin, HarnessMixin, PathMixin, ValidationMixin, CandidateFamilyMixin, FeedbackMixin, ObservationMixin, ToolMixin, AgentModule[CyberGymState, Observation, Any]):
    """PoC Generation Agent for CyberGym Level 1 tasks.

    Given a vulnerability description and a pre-patch codebase, produces a
    raw input file that triggers the underlying bug when fed to the vulnerable binary.
    """

    name = "cybergym_poc_gen"
    # Tool name constants — values imported from tool_names.py
    READ_TOOL = READ_TOOL
    GREP_TOOL = GREP_TOOL
    GLOB_TOOL = GLOB_TOOL
    FIND_SYMBOLS_TOOL = FIND_SYMBOLS_TOOL
    CALLSITE_SEARCH_TOOL = CALLSITE_SEARCH_TOOL
    REPO_MAP_TOOL = REPO_MAP_TOOL
    FILE_INFO_TOOL = FILE_INFO_TOOL
    HEX_VIEW_TOOL = HEX_VIEW_TOOL
    STRUCT_PROBE_TOOL = STRUCT_PROBE_TOOL
    CORPUS_INSPECT_TOOL = CORPUS_INSPECT_TOOL
    WRITE_TOOL = WRITE_TOOL
    BASH_TOOL = BASH_TOOL
    SUBMIT_POC_TOOL = SUBMIT_POC_TOOL

    def __init__(
        self,
        llm: Any,
        workspace_root: str,
        task_root: Optional[str] = None,
        server_url: str = "http://localhost:8000",
        *,
        memory_dir: Optional[str] = None,
        global_memory_dir: Optional[str] = None,
        max_steps: int = 30,
        shell_timeout: int = 60,
        agent_mode: Optional[str] = None,
        **config: Any,
    ):
        self.workspace_root = str(Path(workspace_root).resolve())
        self.task_root = str(Path(task_root or workspace_root).resolve())
        self.server_url = server_url
        self.max_steps = max_steps
        self.shell_timeout = shell_timeout

        # Initialize exchange logger if enabled
        from .agent_impl.core.exchange_logger import get_exchange_logger
        self._exchange_logger = get_exchange_logger(self.workspace_root)
        mode_value = (
            agent_mode
            if agent_mode is not None
            else os.environ.get("CYBERGYM_AGENT_MODE", "")
        )
        self.agent_mode = normalize_agent_mode(mode_value)
        self.disable_context_compaction = os.environ.get(
            "CYBERGYM_DISABLE_CONTEXT_COMPACTION", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.disable_history_snip = self.disable_context_compaction or os.environ.get(
            "CYBERGYM_DISABLE_HISTORY_SNIP", ""
        ).strip().lower() in {"1", "true", "yes", "on"}

        self._phase_engine = cybergym_phase_engine()
        tool_registry, self._coding_tools = build_tool_registry(
            self,
            llm=llm,
            shell_timeout=shell_timeout,
            server_url=server_url,
        )

        # Disable engine-level MemdirMemory by default. CyberGym keeps raw
        # evidence in explicit project artifact paths instead of logging every
        # state/action/result into the model-visible project memory folder.
        enable_memdir_memory = bool(config.pop("enable_memdir_memory", False)) or (
            os.environ.get("CYBERGYM_ENABLE_MEMDIR_MEMORY", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        memory = None
        if enable_memdir_memory:
            mem_dir = memory_dir or os.path.join(self.task_root, ".cybergym", "memory")
            g_mem_dir = global_memory_dir or os.path.expanduser("~/.cybergym/memory")
            memory = MemdirMemory(
                memory_dir=mem_dir,
                global_memory_dir=g_mem_dir,
            )

        # --- Context Management (four-level compaction) ---
        from qitos.kit.history.compact_history import CompactConfig
        context_history = CyberGymContextHistory(
            llm=llm,
            disable_snip=self.disable_history_snip,
            disable_compaction=self.disable_context_compaction,
            config=CompactConfig(
                max_tokens=CYBERGYM_HISTORY_MAX_TOKENS,
                compact_long_messages_over_chars=40_000,
                microcompact_preview_chars=180,
                summary_max_chars=2000,
                keep_last_rounds=3,
                keep_last_messages=10,
                warning_ratio=CYBERGYM_HISTORY_WARNING_RATIO,
            ),
        )

        super().__init__(
            tool_registry=tool_registry,
            llm=llm,
            memory=memory,
            history=context_history,
            history_policy=HistoryPolicy(max_messages=0),
            **config,
        )

        # --- Tool rendering buffer ---
        # When tool methods return rendered strings for the LLM, the
        # structured dicts are stored here so _process_action_result can
        # still access them.
        self._structured_output_buffer: Dict[str, Any] = {}
        self._last_structured_output: Any = None
        self._exchange_logger: Optional[Any] = None

    def build_prompt_bundle(self, state: CyberGymState) -> PromptBuildResult:
        bundle = super().build_prompt_bundle(state)
        payload = list(bundle.tool_schema_payload or [])
        if not payload:
            return bundle
        allowed = self._layered_tool_schema_names(state)
        required_dynamic_tool = self._required_dynamic_tool_name(state)
        filtered = [
            item
            for item in payload
            if str((item.get("function") or {}).get("name") or "") in allowed
        ]
        if not required_dynamic_tool and (not filtered or len(filtered) == len(payload)):
            return bundle
        metadata = dict(bundle.metadata or {})
        metadata["tool_schema_payload_filtered"] = True
        if required_dynamic_tool:
            exposed_names = {
                str((item.get("function") or {}).get("name") or "")
                for item in filtered
            }
            metadata["tool_schema_payload_filter_reason"] = "required_dynamic_tool"
            metadata["required_dynamic_tool"] = required_dynamic_tool
            metadata["required_dynamic_tool_exposed"] = (
                exposed_names == {required_dynamic_tool} and len(filtered) == 1
            )
        else:
            metadata["tool_schema_payload_filter_reason"] = (
                self._tool_schema_filter_reason(state)
                if self._should_filter_to_candidate_tools(state)
                else "layered_aci_tools"
            )
        metadata["tool_schema_payload_tool_count"] = len(filtered)
        return PromptBuildResult(
            system_prompt_static=bundle.system_prompt_static,
            system_prompt_dynamic=bundle.system_prompt_dynamic,
            message_injections=list(bundle.message_injections),
            user_content_blocks=list(bundle.user_content_blocks),
            tool_schema_payload=filtered,
            metadata=metadata,
        )

    # ------------------------------------------------------------------
    # AgentModule abstract methods
    # ------------------------------------------------------------------

    def prepare(self, state: CyberGymState) -> str:
        """Return a minimal observation lane close to raw tool results."""
        try:
            return self._prepare_inner(state)
        except Exception as exc:
            import logging, traceback
            logging.getLogger(__name__).error(
                "prepare() failed: %s: %s\n%s", type(exc).__name__, exc, traceback.format_exc(),
            )
            # Return a minimal fallback so the agent doesn't crash
            return "Observation: analysis preparation encountered an error. Proceed with investigation."

    def _prepare_inner(self, state: CyberGymState) -> str:
        """Inner implementation of prepare(), wrapped for error safety."""
        prompt_state = state.metadata.setdefault("_prompt_state", {})

        finding_sig = self._finding_signature(state)
        verification_sig = self._verification_signature(state)
        hot_feedback_sig = self._hot_feedback_signature(state)
        budget_forced = self._read_budget_exhausted(state)
        poc_sig = "|".join(self._ready_poc_paths(state))
        reflection_sig = str(state.reflection_note or "")
        attempt_sig = self._attempt_signature(state)
        note_sig = self._exploration_note_signature(state)

        if not prompt_state.get("initialized"):
            # Cache env_runner on state for container-aware rediscovery
            env_runner = getattr(self, "env", None)
            if env_runner is not None:
                state.metadata["_env_runner"] = env_runner
            obs_result = self._render_observation(state, is_initial=True)
            prepared = _sanitize_model_text(obs_result.text)
            prepared = self._inject_static_analysis_brief(state, prepared)
            # Store observation sections in TUI metadata
            self._store_tui_sections(state, obs_result.sections)
            prompt_state.update(
                {
                    "initialized": True,
                    "finding_sig": finding_sig,
                    "verification_sig": verification_sig,
                    "hot_feedback_sig": hot_feedback_sig,
                    "budget_forced": budget_forced,
                    "poc_sig": poc_sig,
                    "reflection_sig": reflection_sig,
                    "attempt_sig": attempt_sig,
                    "note_sig": note_sig,
                }
            )
            self._write_step_sidecar(
                state,
                "observation.md",
                prepared,
                context_payload=self._step_context_payload(state),
            )
            return prepared

        prompt_state.update(
            {
                "finding_sig": finding_sig,
                "verification_sig": verification_sig,
                "hot_feedback_sig": hot_feedback_sig,
                "budget_forced": budget_forced,
                "poc_sig": poc_sig,
                "reflection_sig": reflection_sig,
                "attempt_sig": attempt_sig,
                "note_sig": note_sig,
            }
        )

        # Cache env_runner on state for container-aware rediscovery
        env_runner = getattr(self, "env", None)
        if env_runner is not None:
            state.metadata["_env_runner"] = env_runner

        obs_result = self._render_observation(state, is_initial=False)
        prepared = _sanitize_model_text(obs_result.text)
        prepared = self._inject_static_analysis_brief(state, prepared)

        # Store observation sections in TUI metadata so the TUI shows the same
        # structure the LLM sees, not the old V2-era sections.
        self._store_tui_sections(state, obs_result.sections)

        self._write_step_sidecar(
            state,
            "observation.md",
            prepared,
            context_payload=self._step_context_payload(state),
        )
        # Log observation text for debugging
        if self._exchange_logger is not None:
            step_id = getattr(state, "current_step", 0) or 0
            self._exchange_logger.log_observations(step_id, [prepared])
        return prepared

    @staticmethod
    def _store_tui_sections(state: CyberGymState, sections: Dict[str, str]) -> None:
        """Store observation sections in state.metadata for TUI display."""
        tui_map = {
            "vulnerability": "_tui_vulnerability",
            "sink_candidates": "_tui_sink_candidates",
            "constraint_board": "_tui_constraint_board",
            "experiments": "_tui_experiments",
            "task_memory": "_tui_task_memory",
            "tools": "_tui_tools",
        }
        for src_key, tui_key in tui_map.items():
            content = sections.get(src_key, "")
            # Always write all keys, even when empty, so the TUI can display
            # a consistent layout with placeholders for hidden sections.
            state.metadata[tui_key] = content

    def _ensure_family_bootstrap(self, state: CyberGymState) -> None:
        repo_root = state.repo_dir if state.repo_dir and os.path.isdir(state.repo_dir) else ""
        if not repo_root:
            state.evidence_index = {}
            state.durable_project_memory = self._preserved_project_memory(state)
            if not state.family_pool:
                state.family_pool = []
            return
        evidence_index = state.evidence_index
        if self._family_bootstrap_needs_refresh(state):
            evidence_index = bootstrap_evidence_index(
                repo_root,
                state.vulnerability_description,
                task_spec={
                    "source_files_mentioned": list(state.source_files_mentioned or []),
                    "symbols_mentioned": list(state.symbols_mentioned or []),
                    "input_vector_hints": list(state.input_vector_hints or []),
                },
            )
            state.evidence_index = evidence_index
        self._refresh_durable_project_memory(state)
        if state.family_pool:
            return
        state.family_pool = [
            FamilyRecord(**family)
            for family in initial_families_for_task(
                state.vulnerability_description,
                evidence_index,
            )
        ]

    @staticmethod
    def _family_bootstrap_needs_refresh(state: CyberGymState) -> bool:
        evidence_index = state.evidence_index or {}
        if not evidence_index:
            return True
        if evidence_index.get("description") != state.vulnerability_description:
            return True
        for key in ("parser_paths", "seed_paths", "field_paths"):
            if key not in evidence_index:
                return True
        return False

    def _refresh_durable_project_memory(self, state: CyberGymState) -> None:
        evidence = dict(state.evidence_index or {})
        refreshed = {
            "repo_summary": (state.repo_index or "")[:2000],
            "repo_profile_summary": str(evidence.get("repo_profile_summary") or ""),
            "parser_paths": list(evidence.get("parser_paths") or [])[:8],
            "seed_paths": list(evidence.get("seed_paths") or [])[:8],
            "field_paths": list(evidence.get("field_paths") or [])[:8],
            "ranked_paths": list(evidence.get("ranked_paths") or [])[:8],
        }
        refreshed.update(self._preserved_project_memory(state))
        state.durable_project_memory = refreshed

    @staticmethod
    def _preserved_project_memory(state: CyberGymState) -> Dict[str, Any]:
        """Keep compact cross-refresh facts that are not rebuilt from evidence.

        Delegate artifacts are written asynchronously by the runtime and must
        survive evidence-index refreshes.  Everything else is reconstructed
        above so stale prompt-facing data cannot accumulate indefinitely.
        """
        return {
            str(key): value
            for key, value in dict(state.durable_project_memory or {}).items()
            if str(key).startswith("last_delegate_")
        }

    @staticmethod
    def _append_capped_fact(items: List[str], fact: str, *, limit: int = 6) -> List[str]:
        from .agent_impl.core.fact_extraction import append_capped_fact
        return append_capped_fact(items, fact, limit=limit)

    @staticmethod
    def _best_fact_snippet(content: str, *, limit: int = 160) -> str:
        from .agent_impl.core.fact_extraction import best_fact_snippet
        return best_fact_snippet(content, limit=limit)

    @staticmethod
    def _extract_structured_facts_from_content(content: str, path: str) -> List[str]:
        from .agent_impl.core.fact_extraction import extract_structured_facts_from_content
        return extract_structured_facts_from_content(content, path)

    @staticmethod
    def _extract_poc_paths_from_bash(command: str, state: CyberGymState) -> List[str]:
        from .agent_impl.core.fact_extraction import extract_poc_paths_from_bash
        return extract_poc_paths_from_bash(command, state)

    def _detect_harness_entry(self, state: CyberGymState, short_name: str, output: Any) -> None:
        from .agent_impl.poc.harness import detect_harness_entry
        detect_harness_entry(self, state, short_name, output)

    def _update_read_coverage(self, state: CyberGymState, short_name: str, output: Any) -> None:
        from .agent_impl.repo.constraint_chain import update_read_coverage
        update_read_coverage(self, state, short_name, output)

    @staticmethod
    def _confirm_constraints_from_read(state: CyberGymState, output: Any) -> None:
        from .agent_impl.repo.constraint_chain import confirm_constraints_from_read
        confirm_constraints_from_read(state, output)

    @staticmethod
    def _constraint_source_from_read(output: Dict[str, Any]) -> tuple[str, int]:
        from .agent_impl.repo.constraint_chain import constraint_source_from_read
        return constraint_source_from_read(output)

    @staticmethod
    def _extract_path_constraints_from_read(state: CyberGymState, output: Any) -> None:
        from .agent_impl.repo.constraint_chain import extract_path_constraints_from_read
        extract_path_constraints_from_read(state, output)

    @staticmethod
    def _check_and_flag_contradictions(state: CyberGymState) -> None:
        from .agent_impl.repo.constraint_chain import check_and_flag_contradictions
        check_and_flag_contradictions(state)

    @staticmethod
    def _infer_chain_from_search(state: CyberGymState, short_name: str, output: Any) -> None:
        from .agent_impl.repo.constraint_chain import infer_chain_from_search
        infer_chain_from_search(state, short_name, output)

    @staticmethod
    def _refute_gate(state: CyberGymState, gate_index: int, evidence: str, repair_hint: str) -> None:
        from .agent_impl.repo.constraint_chain import refute_gate
        refute_gate(state, gate_index, evidence, repair_hint)

    def _update_task_persistent_memory(self, state: CyberGymState, old_phase: str, new_phase: str) -> None:
        from .agent_impl.runtime.memory import update_task_persistent_memory
        update_task_persistent_memory(self, state, old_phase, new_phase)

    @staticmethod
    def _hypothesis_path_not_reached(state: CyberGymState) -> str:
        from .agent_impl.runtime.memory import hypothesis_path_not_reached
        return hypothesis_path_not_reached(state)

    @staticmethod
    def _attempt_action_hint(gate: str) -> str:
        from .agent_impl.runtime.memory import attempt_action_hint
        return attempt_action_hint(gate)

    @staticmethod
    def _update_chain_from_read(state: CyberGymState, output: Any) -> None:
        from .agent_impl.repo.constraint_chain import update_chain_from_read
        update_chain_from_read(state, output)

    def _capture_read_fact(self, state: CyberGymState, short_name: str, output: Any) -> None:
        from .agent_impl.repo.constraint_chain import capture_read_fact
        capture_read_fact(self, state, short_name, output)

    def _display_path(self, path: str, *, state: Optional[CyberGymState] = None) -> str:
        from .agent_impl.repo.constraint_chain import display_path
        return display_path(path, state=state, agent=self)

    def reduce(
        self,
        state: CyberGymState,
        observation: Any,
        decision: Decision,
    ) -> CyberGymState:
        """Reduce observation into the next state."""
        # Cache env_runner on state so observation rendering can use it
        # for container-aware rediscovery of staged binary capability.
        env_runner = getattr(self, "env", None)
        if env_runner is not None and not isinstance(state.metadata.get("_env_runner"), type(env_runner)):
            state.metadata["_env_runner"] = env_runner

        from .agent_impl.reducer import (
            advance_phase,
            apply_consecutive_miss_nudge,
            apply_exploration_checkpoints,
            apply_exploration_completion,
            apply_investigation_checkpoints,
            apply_sink_rotation,
            log_exchange,
            try_build_candidate_from_recipe,
        )

        if state.metadata.pop("_one_shot_reminder_rendered", False):
            state.pending_reminder = ""
            state.pending_reminder_signature = ""

        # Extract action results from Observation
        action_results = []
        if isinstance(observation, Observation):
            action_results = observation.action_results or []
        elif isinstance(observation, dict):
            action_results = observation.get("action_results", [])
        else:
            action_results = getattr(observation, "action_results", [])

        state.metadata["_reduce_round"] = state.metadata.get("_reduce_round", 0) + 1
        for result in action_results:
            tr = ToolResult.from_value(result) if not isinstance(result, ToolResult) else result
            self._process_action_result(state, tr)
            if getattr(state, "stop_reason", ""):
                break

        self._refresh_description_analysis(state)
        self._run_pending_sink_analysis(state)

        # Step 4+ fallback: guarantee active sink
        if (getattr(state, "current_step", 0) or 0) >= 4 and not state.confirmed_sink_candidates():
            self._auto_promote_sink(state)

        # Deepen analysis after repeated failures
        if (getattr(state, "poc_attempts", 0) >= 2
            and getattr(state, "best_poc_score", 0) == 0
            and getattr(state, "analysis_status", "") == "BRIEF_AVAILABLE"
            and getattr(state, "latest_analysis_mode", "") == "automatic"):
            self._deepen_sink_analysis(state)

        self._update_candidate_requirement_from_decision(state, decision)
        if getattr(state, "stop_reason", ""):
            if state.is_verified() and self.memory:
                self._save_success_memory(state)
            return state

        self._ensure_family_bootstrap(state)
        cooled_this_turn = self._apply_family_queue_discipline(state)
        self._update_runtime_stage(state)
        if getattr(state, "stop_reason", ""):
            if state.is_verified() and self.memory:
                self._save_success_memory(state)
            return state

        self._prune_retired_family_candidates(state)
        latest_action = self._latest_feedback_action(state)
        if state.candidate_queue:
            self._drain_candidate_queue_to_ready_pocs(state)
        elif not state.ready_pocs and not state.candidate_queue:
            try_build_candidate_from_recipe(state)

        # Exploration auto-detect completion
        apply_exploration_completion(self, state)

        # Phase advancement
        step = getattr(state, "current_step", 0) or 0
        old_phase, new_phase = advance_phase(self, state, int(step))

        # Phase checkpoints
        apply_exploration_checkpoints(state)
        apply_investigation_checkpoints(state)

        # Sink rotation & consecutive-miss nudge
        apply_sink_rotation(self, state)
        apply_consecutive_miss_nudge(state)

        # Memory updates
        self._update_task_persistent_memory(state, old_phase, new_phase)
        if state.is_verified() and self.memory:
            self._save_success_memory(state)

        # Debug logging
        log_exchange(self, state, decision, action_results)
        return state

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def build_system_prompt(self, state: CyberGymState) -> str:
        """Build a mostly stable system prompt; dynamic task state belongs in prepare()."""
        parts = []

        # --- Stable Prefix ---
        parts.append(self.base_persona_prompt(state))
        parts.append(self.task_policy_prompt(state))
        parts.append(self.runtime_context_protocol_prompt(state))
        phase_guidance = self._phase_operating_guidance(state)
        if phase_guidance:
            parts.append(phase_guidance)

        # Tool schema -- only inject as text when not using native function calling
        # Engine handles api_parameter delivery automatically
        protocol = self.active_protocol()
        delivery = str(getattr(protocol, "tool_schema_delivery", "prompt_injection") or "prompt_injection")
        if delivery not in ("api_parameter", "hybrid"):
            tool_schema = self.render_tool_schema(protocol=protocol)
            if tool_schema:
                parts.append(f"\n## Available Tools\n{tool_schema}")

        parts.append(self.extra_instructions_prompt(state))
        parts.append(self.tool_usage_hint_prompt(state))

        # Multi-action guidance when the active protocol supports it
        protocol = self.active_protocol()
        if getattr(protocol, "supports_multi_action", False):
            parts.append(self._multi_action_guidance_prompt(state))

        prompt = _sanitize_model_text("\n".join(parts))
        self._write_step_sidecar(state, "system_prompt.md", prompt)
        return prompt

    def interpret_model_response(
        self,
        state: CyberGymState,
        observation: Observation,
        response: ModelResponse,
    ) -> Optional[Decision[Any]]:
        self._write_step_sidecar(
            state,
            "response.md",
            str(response.text or ""),
            context_payload=self._step_context_payload(state, observation=observation),
        )
        self._write_step_sidecar(
            state,
            "model_response.json",
            json.dumps(response.to_summary_dict(), ensure_ascii=False, indent=2),
        )
        return None


    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_action_result(self, state: CyberGymState, result: ToolResult) -> None:
        """Process a single ToolResult and update state accordingly."""
        name = (
            result.metadata.get("name")
            or result.metadata.get("tool_name")
            or getattr(result, "name", "")
        )
        short_name = str(name).rsplit(".", 1)[-1]
        output = result.output
        output_str = result.text

        # Auto-resolve harness when agent READs a harness candidate file
        if short_name == self.READ_TOOL and not getattr(state, "harness_entry_confirmed", False):
            self._auto_resolve_harness_on_read(state, output_str or "")

        # Recover the original structured dict when the tool method returned
        # a rendered string via _render_output.  The buffer stores the
        # pre-rendering dict so reduce() can access clean structured fields.
        if isinstance(output, str):
            action_id = (
                result.metadata.get("action_id")
                if isinstance(result.metadata, dict) else None
            )
            recovered = False
            if action_id and action_id in self._structured_output_buffer:
                output = self._structured_output_buffer.pop(action_id)
                recovered = True
            elif self._last_structured_output is not None:
                _, output = self._last_structured_output
                self._last_structured_output = None
                recovered = True
            if not recovered and short_name == SUBMIT_POC_TOOL:
                # Recover THIS submission's result, keyed by (agent_id, poc):
                # agent_id stops cross-task leakage; the PoC path stops parallel
                # submits in one step from being paired with each other's verdict.
                from .submit_tool import get_last_submit_structured
                _meta = result.metadata if isinstance(result.metadata, dict) else {}
                recovered_submit = get_last_submit_structured(
                    getattr(state, "agent_id", ""),
                    poc_path=_meta.get("poc_path"),
                )
                if recovered_submit is not None:
                    output = recovered_submit

        self._track_read_budget(state, short_name, output)
        observation_note = self._summarize_tool_observation(short_name, output)
        if observation_note:
            state.recent_tool_observations.append(observation_note)
            state.recent_tool_observations = state.recent_tool_observations[-6:]
        self._capture_read_fact(state, short_name, output)
        self._detect_harness_entry(state, short_name, output)
        self._update_read_coverage(state, short_name, output)
        # Mark high-value READ results for compaction priority
        if short_name == self.READ_TOOL and isinstance(output, dict) and hasattr(result, "metadata"):
            read_path = str(output.get("path") or "").strip()
            evidence = state.durable_project_memory or {}
            high_value_paths = set()
            for key in ("parser_paths", "field_paths", "seed_paths"):
                high_value_paths.update(
                    str(p).strip() for p in (evidence.get(key) or [])
                )
            if read_path in high_value_paths:
                result.metadata["compaction_priority"] = "high"


        # Dispatch to registered tool result handlers
        from .agent_impl.tools.result_processors import get_handler
        handler = get_handler(short_name)
        if handler is not None:
            handler(self, state, result, output)

    def _process_submit_result(self, state: "CyberGymState", result, output) -> None:
        from .agent_impl.feedback.submit_processor import process_submit_result
        process_submit_result(self, state, result, output)


    @staticmethod
    def _select_family_evidence_safe(
        family_name: str,
        evidence_index: Dict[str, Any],
    ) -> Dict[str, object]:
        from .agent_impl.poc.candidates import select_family_evidence_safe
        return select_family_evidence_safe(family_name, evidence_index)

    # ------------------------------------------------------------------
    # Harness, corpus, and strategy detection  (delegates to seed_corpus)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_harness_info(workspace_root: str) -> str:
        from .agent_impl.poc.seed_corpus import parse_harness_info
        return parse_harness_info(workspace_root)

    @staticmethod
    def _discover_corpus_files(repo_dir: str) -> List[str]:
        from .agent_impl.poc.seed_corpus import discover_corpus_files
        return discover_corpus_files(repo_dir)

    @staticmethod
    def _prepare_seed_corpus(task_root: str, repo_dir: str) -> List[str]:
        from .agent_impl.poc.seed_corpus import prepare_seed_corpus
        return prepare_seed_corpus(task_root, repo_dir)

    @staticmethod
    def _discover_repo_seed_samples(repo_dir: str) -> List[str]:
        from .agent_impl.poc.seed_corpus import discover_repo_seed_samples
        return discover_repo_seed_samples(repo_dir)

    def _advance_sink_candidate(self, state: CyberGymState) -> bool:
        from .agent_impl.poc.sink_management import advance_sink_candidate
        return advance_sink_candidate(self, state)

    def _auto_resolve_harness_on_read(self, state: CyberGymState, read_output: str) -> None:
        from .agent_impl.poc.sink_management import auto_resolve_harness_on_read
        auto_resolve_harness_on_read(self, state, read_output)

    def _auto_promote_sink(self, state: CyberGymState) -> None:
        from .agent_impl.poc.sink_management import auto_promote_sink
        auto_promote_sink(self, state)

    def _suggest_sink_from_asan_feedback(self, state: CyberGymState, output: dict) -> None:
        from .agent_impl.poc.sink_management import suggest_sink_from_asan_feedback
        suggest_sink_from_asan_feedback(self, state, output)

    def _save_success_memory(self, state: CyberGymState) -> None:
        from .agent_impl.runtime.memory import save_success_memory
        save_success_memory(self, state)
