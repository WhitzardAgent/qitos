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
import re
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
    select_family_evidence,
)
from .family_runtime import (
    CandidateRecord,
    FamilyRecord,
    enqueue_candidate,
)
from .subagent_runtime import (
    build_candidate_messages,
    build_insight_messages,
    parse_candidate_json,
    parse_insight_json,
    run_subagent_json,
)
from .delegate_agents import parse_explore_json
from .artifact_store import ArtifactStore
from .versioning import mode_uses_qitos_delegate, normalize_agent_mode
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
    APPEND as APPEND_TOOL,
    INSERT as INSERT_TOOL,
    REPLACE_LINES as REPLACE_LINES_TOOL,
    STR_REPLACE as STR_REPLACE_TOOL,
    SUBMIT_POC as SUBMIT_POC_TOOL,
    RECORD_HYPOTHESIS as RECORD_HYPOTHESIS_TOOL,
    RECORD_ATTEMPT as RECORD_ATTEMPT_TOOL,
    RECORD_REFLECTION as RECORD_REFLECTION_TOOL,
)
from .agent_impl.constants import (
    CYBERGYM_HISTORY_MAX_TOKENS, CYBERGYM_HISTORY_WARNING_RATIO,
    FAILURE_REFLECTION_ACK_KEY,
    DELEGATE_EXPLORATION_REPORT_SEEN_KEY, DELEGATE_TOOL_AGENT_NAMES,
)
from .agent_impl.utils import (
    clip as _clip,
    sanitize_model_text as _sanitize_model_text,
)
from .agent_impl.phase import cybergym_phase_engine, phase_local_steps
from .agent_impl.tool_registry import build_tool_registry
from .agent_impl.state_init import StateInitMixin
from .agent_impl.task_analysis import TaskAnalysisMixin
from .agent_impl.repo_analysis import RepoAnalysisMixin
from .agent_impl.crash_parsing import CrashParsingMixin
from .agent_impl.prompts import PromptsMixin
from .agent_impl.harness import HarnessMixin
from .agent_impl.paths import PathMixin
from .agent_impl.validation import ValidationMixin
from .agent_impl.candidates import CandidateFamilyMixin
from .agent_impl.feedback import FeedbackMixin
from .agent_impl.observations import ObservationMixin
from .agent_impl.tools import ToolMixin


class CyberGymAgent(StateInitMixin, TaskAnalysisMixin, RepoAnalysisMixin, CrashParsingMixin, PromptsMixin, HarnessMixin, PathMixin, ValidationMixin, CandidateFamilyMixin, FeedbackMixin, ObservationMixin, ToolMixin, AgentModule[CyberGymState, Observation, Any]):
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
    APPEND_TOOL = APPEND_TOOL
    INSERT_TOOL = INSERT_TOOL
    REPLACE_LINES_TOOL = REPLACE_LINES_TOOL
    STR_REPLACE_TOOL = STR_REPLACE_TOOL
    SUBMIT_POC_TOOL = SUBMIT_POC_TOOL
    RECORD_HYPOTHESIS_TOOL = RECORD_HYPOTHESIS_TOOL
    RECORD_ATTEMPT_TOOL = RECORD_ATTEMPT_TOOL
    RECORD_REFLECTION_TOOL = RECORD_REFLECTION_TOOL

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
        helper_subagents_enabled: bool = False,
        **config: Any,
    ):
        self.workspace_root = str(Path(workspace_root).resolve())
        self.task_root = str(Path(task_root or workspace_root).resolve())
        self.server_url = server_url
        self.max_steps = max_steps
        self.shell_timeout = shell_timeout
        # Initialize exchange logger if enabled
        from .agent_impl.exchange_logger import get_exchange_logger
        self._exchange_logger = get_exchange_logger(self.workspace_root)
        mode_value = (
            agent_mode
            if agent_mode is not None
            else os.environ.get("CYBERGYM_AGENT_MODE", "")
        )
        self.agent_mode = normalize_agent_mode(mode_value)
        self.qitos_delegate_enabled = mode_uses_qitos_delegate(self.agent_mode)
        self.helper_subagents_enabled = bool(helper_subagents_enabled)
        self.disable_context_compaction = os.environ.get(
            "CYBERGYM_DISABLE_CONTEXT_COMPACTION", ""
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.disable_history_snip = self.disable_context_compaction or os.environ.get(
            "CYBERGYM_DISABLE_HISTORY_SNIP", ""
        ).strip().lower() in {"1", "true", "yes", "on"}

        self._phase_engine = cybergym_phase_engine()
        tool_registry, self._coding_tools, self.agent_registry = build_tool_registry(
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
        filtered = [
            item
            for item in payload
            if str((item.get("function") or {}).get("name") or "") in allowed
        ]
        if not filtered or len(filtered) == len(payload):
            return bundle
        metadata = dict(bundle.metadata or {})
        metadata["tool_schema_payload_filtered"] = True
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
        prompt_state = state.metadata.setdefault("_prompt_state", {})

        finding_sig = self._finding_signature(state)
        verification_sig = self._verification_signature(state)
        hot_feedback_sig = self._hot_feedback_signature(state)
        budget_forced = self._read_budget_exhausted(state)
        poc_sig = "|".join(self._ready_poc_paths(state))
        reflection_sig = self._clip(str(state.reflection_note or ""), 260)
        attempt_sig = self._attempt_signature(state)
        note_sig = self._exploration_note_signature(state)

        if not prompt_state.get("initialized"):
            prepared = _sanitize_model_text(self._build_initial_brief(state))
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

        prepared = _sanitize_model_text(self._build_observation_packet(state))

        # Store constraint board and task memory text in state metadata
        # so the TUI can render the exact same text the LLM sees.
        constraint_lines = self._constraint_board_lines(state)
        if constraint_lines:
            state.metadata["_tui_constraint_board"] = "\n".join(constraint_lines)
        task_memory_lines = self._task_memory_lines(state)
        if task_memory_lines:
            state.metadata["_tui_task_memory"] = "\n".join(task_memory_lines)

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
        return {
            str(key): value
            for key, value in dict(state.durable_project_memory or {}).items()
            if str(key).startswith("last_delegate_")
        }

    @staticmethod
    def _append_capped_fact(items: List[str], fact: str, *, limit: int = 8) -> List[str]:
        text = " ".join(str(fact or "").split()).strip()
        if not text:
            return list(items or [])
        filtered = [entry for entry in list(items or []) if entry != text]
        filtered.append(text)
        return filtered[-limit:]

    @staticmethod
    def _best_fact_snippet(content: str, *, limit: int = 160) -> str:
        for raw_line in str(content or "").splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            if line.startswith(("//", "#", "/*", "*", "*/")):
                continue
            return _clip(line, limit)
        return _clip(" ".join(str(content or "").split()), limit)

    @staticmethod
    def _extract_structured_facts_from_content(content: str, path: str) -> List[str]:
        """Deterministically extract structured facts from READ content.

        Extracts #define constants with numeric values, buffer size
        declarations, and function signatures — the facts most likely
        to be lost in LLM-based context compaction.
        """
        if not content or not path:
            return []
        facts: List[str] = []
        # #define constants with numeric values
        for m in re.finditer(r'#define\s+(\w+)\s+(\d+)', content):
            facts.append(f"const: {m.group(1)} = {m.group(2)} (in {path})")
        # Buffer/size declarations: type name[SIZE]
        for m in re.finditer(r'(?:char|uint\d+_t|int|size_t|unsigned)\s+\w+\[(\d+)\]', content):
            facts.append(f"buffer_size: {m.group(1)} (in {path})")
        # Function signatures (simplified)
        for m in re.finditer(r'(?:static\s+)?(?:inline\s+)?(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*\{', content):
            fname = m.group(1)
            if fname not in ("if", "for", "while", "switch", "return", "sizeof"):
                facts.append(f"func: {fname} (in {path})")
        return facts[:8]

    @staticmethod
    def _extract_poc_paths_from_bash(command: str, state: CyberGymState) -> List[str]:
        """Extract PoC file paths mentioned in a BASH command string.

        Only matches paths under pocs/ that look like output targets,
        not source paths.  Avoids registering paths that the command
        reads from (e.g., ``cp source target``).
        """
        if not command or not state.workspace_root:
            return []
        # Match output redirection targets and python write paths
        paths: List[str] = []
        seen: set[str] = set()
        # Redirection: > pocs/foo or >> pocs/foo
        for m in re.finditer(r'[>]\s*([^\s;&|]+pocs[^\s;&|]*)', command):
            p = m.group(1).strip("'\"")
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
        # Python open/write patterns: open("pocs/foo", "w")
        for m in re.finditer(r'open\(["\']([^"\']*pocs[^"\']*)["\']', command):
            p = m.group(1)
            if p and p not in seen:
                seen.add(p)
                paths.append(p)
        return paths

    def _detect_harness_entry(self, state: CyberGymState, short_name: str, output: Any) -> None:
        """Auto-detect harness entry function in READ/FindSymbols results."""
        if state.harness_entry_confirmed or state.metadata.get("harness_entry_confirmed"):
            return
        normalized_name = str(short_name or "").upper()
        if normalized_name not in (self.READ_TOOL, self.FIND_SYMBOLS_TOOL.upper()):
            return
        content = ""
        if isinstance(output, dict):
            content = str(output.get("content") or output.get("raw_output") or "")
        if not content:
            return
        harness_patterns = [
            r'LLVMFuzzerTestOneInput',
            r'int\s+main\s*\(',
        ]
        for pattern in harness_patterns:
            m = re.search(pattern, content)
            if m:
                state.harness_entry_confirmed = True
                state.metadata["harness_entry_confirmed"] = True  # backward compat
                entry_name = m.group(0)
                path = str(output.get("path") or "") if isinstance(output, dict) else ""
                loc = f"{entry_name} in {path}" if path else entry_name
                state.durable_code_facts = self._append_capped_fact(
                    state.durable_code_facts,
                    f"[confirmed] harness_entry: {loc}",
                )
                # Confirm input format model
                if hasattr(state, "input_format"):
                    state.input_format.entry_point = entry_name
                    if "LLVMFuzzerTestOneInput" in entry_name:
                        state.input_format.input_path = "buffer"
                    state.input_format.confirmed = True
                break

    def _update_read_coverage(self, state: CyberGymState, short_name: str, output: Any) -> None:
        """Track which file/line ranges have been READ to avoid re-reading."""
        normalized_name = str(short_name or "").upper()
        if normalized_name != self.READ_TOOL or not isinstance(output, dict):
            return
        path = str(output.get("path") or "").strip()
        if not path:
            return
        content = str(output.get("content", "") or "")
        offset = int(output.get("offset", 0) or 0)
        lines_read = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        if lines_read <= 0:
            return
        span = (offset + 1, offset + lines_read)
        if path not in state.read_coverage:
            state.read_coverage[path] = []
        state.read_coverage[path].append(span)
        # Keep only last 8 spans per file to bound growth
        if len(state.read_coverage[path]) > 8:
            state.read_coverage[path] = state.read_coverage[path][-8:]

    @staticmethod
    def _confirm_constraints_from_read(state: CyberGymState, output: Any) -> None:
        """P26: Promote hypothesized/unknown constraints to 'confirmed' when
        the agent READs code at a constraint's source_location and the content
        contains control-flow guard keywords (if/switch/assert/memcmp/strcmp).

        Also confirms matching ChainGate entries."""
        if not isinstance(output, dict):
            return
        read_path = str(output.get("path") or "").strip()
        content = str(output.get("content") or "")
        if not read_path or not content:
            return
        # Check for control-flow guard keywords that indicate a real condition
        guard_keywords = (
            "if", "switch", "case", "assert", "memcmp", "strcmp",
            "strncmp", "strncmp", "return", "continue", "break",
        )
        has_guard = any(kw in content for kw in guard_keywords)
        if not has_guard:
            return
        # Normalize the read path for matching
        display_path = read_path
        for constraint in list(getattr(state, "path_constraints", []) or []):
            status = str(getattr(constraint, "status", "") or "").strip().lower()
            if status == "confirmed":
                continue
            source_loc = str(getattr(constraint, "source_location", "") or "").strip()
            if not source_loc:
                continue
            # Match: the constraint's source_location should be a suffix or
            # substring of the read path (handles relative vs absolute paths)
            if source_loc in display_path or display_path.endswith(source_loc):
                constraint.status = "confirmed"
        # Also confirm matching ChainGate entries
        for gate in state.call_chain_gates:
            if gate.status == "confirmed":
                continue
            # Match gate evidence or description containing the read path
            gate_source = gate.evidence or gate.description
            if display_path in gate_source or any(
                seg in display_path for seg in gate_source.replace("/", " ").replace("\\", " ").split()
                if len(seg) > 4
            ):
                gate.status = "confirmed"
                gate.evidence = f"Confirmed by READ {read_path}"

    @staticmethod
    def _extract_path_constraints_from_read(state: CyberGymState, output: Any) -> None:
        """P36: Auto-extract ONLY format_gate constraints from READ content
        (memcmp/strcmp magic-byte comparisons).  These are high-confidence
        and rarely false-positive.

        path_gate and dispatch_gate constraints are NOT auto-extracted —
        they have too many false positives.  The LLM records them via
        the `record_gate` tool after understanding the code context.

        Legacy path_constraints list is still populated for backward compat.
        """
        if not isinstance(output, dict):
            return
        read_path = str(output.get("path") or "").strip()
        content = str(output.get("content") or "")
        if not read_path or not content:
            return
        existing_descriptions = {
            str(getattr(c, "description", "") or "").strip()
            for c in list(getattr(state, "path_constraints", []) or [])
        }
        from .state import ChainGate, PathConstraint

        # Determine node_order for new gates
        node_order = 0
        for node in state.call_chain_nodes:
            if read_path.endswith(node.location.split(":")[0]) or node.location.split(":")[0] in read_path:
                node_order = node.order
                break

        new_gates: List[ChainGate] = []

        # ONLY Pattern 1: memcmp/strcmp format checks — high confidence
        for m in re.finditer(
            r'(?:if|assert)\s*\([^)]*(?:memcmp|strcmp|strncmp|strncasecmp)\s*\(\s*[^,]+,\s*"([^"]+)"',
            content,
        ):
            magic = m.group(1)
            desc = f"Must match '{magic}' (comparison at {read_path})"
            if desc not in existing_descriptions:
                state.path_constraints.append(
                    PathConstraint(
                        description=desc,
                        source_location=read_path,
                        status="hypothesized",
                        constraint_type="format_gate",
                    )
                )
                existing_descriptions.add(desc)
                new_gates.append(ChainGate(
                    node_order=node_order,
                    gate_type="format_gate",
                    description=desc,
                    required_condition=f"Input must contain '{magic}' at the comparison offset",
                    status="inferred",
                    evidence=f"READ {read_path}",
                    repair_hint="",
                ))

        # Add deduplicated ChainGate entries
        existing_gate_descs = {g.description for g in state.call_chain_gates}
        for gate in new_gates:
            if gate.description not in existing_gate_descs:
                state.call_chain_gates.append(gate)
                existing_gate_descs.add(gate.description)
        # Cap total constraints to prevent unbounded growth
        if len(state.path_constraints) > 30:
            state.path_constraints = state.path_constraints[-30:]
        if len(state.call_chain_gates) > 40:
            state.call_chain_gates = state.call_chain_gates[-40:]

    @staticmethod
    def _infer_chain_from_search(
        state: CyberGymState,
        short_name: str,
        output: Any,
    ) -> None:
        """Auto-infer ChainNode entries from FindSymbols/CallsiteSearch results.

        The LLM's search query is the signal — if it searched for a function,
        it believes that function matters.  Only top-scoring function/definition
        hits are promoted to chain nodes with status="inferred".  The LLM must
        still confirm by reading the code.
        """
        from .state import ChainNode

        if not isinstance(output, dict):
            return

        # Extract the relevant result list based on tool type
        if short_name in ("FindSymbols", "find_symbols"):
            results = output.get("results") or []
            eligible = [
                r for r in results[:5]
                if str(r.get("kind", "") or "").lower() in ("function", "definition")
            ]
        elif short_name in ("CallsiteSearch", "callsite_search"):
            # definitions field contains function definition hits
            eligible = (output.get("definitions") or [])[:5]
        else:
            return

        if not eligible:
            return

        existing_keys = {
            f"{n.function}@{n.location}" for n in state.call_chain_nodes
        }
        max_order = max(
            (n.order for n in state.call_chain_nodes), default=-1
        )
        vulnerable = set(state.vulnerable_functions or [])
        _SKIP_NAMES = {"if", "while", "for", "switch", "return", "sizeof", "main"}

        for item in eligible:
            func = str(
                item.get("name") or item.get("symbol") or ""
            ).strip()
            if not func or func in _SKIP_NAMES:
                continue

            path = str(item.get("path") or "").strip()
            line_no = int(item.get("line_number") or 0)
            location = f"{path}:{line_no}" if line_no else path
            if not location:
                continue

            key = f"{func}@{location}"
            if key in existing_keys:
                continue

            # Infer role from position and function name
            if max_order < 0:
                role = "entry"
            elif func in vulnerable:
                role = "sink"
            elif any(
                kw in func.lower()
                for kw in ("parse", "read", "decode", "process")
            ):
                role = "parser"
            elif any(
                kw in func.lower()
                for kw in ("dispatch", "route", "handle", "select")
            ):
                role = "dispatch"
            else:
                role = "parser"

            max_order += 1
            state.call_chain_nodes.append(ChainNode(
                location=location,
                function=func,
                role=role,
                description=f"Found via {short_name} (inferred)",
                status="inferred",
                evidence=f"{short_name} query result",
                order=max_order,
            ))
            existing_keys.add(key)

        # Cap at 20 nodes
        if len(state.call_chain_nodes) > 20:
            state.call_chain_nodes = state.call_chain_nodes[-20:]

    @staticmethod
    def _refute_gate(state: CyberGymState, gate_index: int, evidence: str, repair_hint: str) -> None:
        """Mark a ChainGate as refuted with evidence and a repair hint.

        Refuted gates are never deleted — they carry learning that prevents
        the agent from retrying the same approach.
        """
        if 0 <= gate_index < len(state.call_chain_gates):
            gate = state.call_chain_gates[gate_index]
            gate.status = "refuted"
            gate.evidence = evidence
            gate.repair_hint = repair_hint

    def _update_task_persistent_memory(
        self, state: CyberGymState, old_phase: str, new_phase: str
    ) -> None:
        """Update the four task-persistent memory fields that survive compaction.

        Called once per step in reduce() after phase advancement.
        """
        # 1. Vulnerability analysis — updated when entering formulation
        #    or when trigger_hypothesis/crash details change.
        if new_phase == "formulation" and old_phase != "formulation":
            parts = []
            if state.bug_type:
                parts.append(f"Bug type: {state.bug_type}")
            if state.vulnerable_functions:
                parts.append(f"Sink: {', '.join(state.vulnerable_functions[:3])}")
            if state.trigger_hypothesis:
                parts.append(state.trigger_hypothesis[:300])
            # Include confirmed gate conditions
            confirmed = state.confirmed_gates() if hasattr(state, "confirmed_gates") else []
            for g in confirmed[:4]:
                parts.append(f"[gate] {g.required_condition}")
            if parts:
                analysis = ". ".join(parts)
                state.vulnerability_analysis = analysis[:600]

        # 2. Path trace — updated from chain nodes
        nodes = list(getattr(state, "call_chain_nodes", []) or [])
        if nodes:
            sorted_nodes = sorted(nodes, key=lambda n: n.order)
            trace = []
            for n in sorted_nodes[:8]:
                loc = n.location.split(":")[0] if ":" in n.location else n.location
                trace.append(f"{n.function} ({loc})")
            state.path_trace = trace

        # 3. Attempt history compact — append after each submit
        if state.last_verification_result and state.last_submitted_poc_path:
            poc_path = state.last_submitted_poc_path
            vul_exit = state.last_verification_result.get("vul_exit_code")
            accepted = state.last_verification_result.get("accepted") is True
            gate = self._classify_failed_gate(state.last_verification_result)
            if accepted:
                outcome = "SUCCESS"
            elif vul_exit and vul_exit != 0:
                outcome = f"vul_crash({vul_exit})"
            else:
                outcome = "no_trigger"
            # Versioned archive name (matches .cybergym/poc_archive/ files)
            version = state.poc_attempts
            suffix = Path(poc_path).suffix  # preserve original: .pcap, .png, .b2frame, etc.
            archived_name = f"poc_v{version}{suffix}"
            # Capture construction rationale from current_hypothesis
            hypothesis_snippet = ""
            if state.current_hypothesis:
                hypothesis_snippet = state.current_hypothesis[:120]
            entry = f"#{version} {archived_name}: {outcome}"
            if gate:
                entry += f" [{gate}]"
            if hypothesis_snippet:
                entry += f" — {hypothesis_snippet}"
            # Deduplicate by version number (#N at start of entry)
            existing_versions = set()
            for e in state.attempt_history_compact:
                # Extract "#N" from start of entry (before space)
                m = re.match(r'#(\d+)', e)
                if m:
                    existing_versions.add(m.group(0))
            if f"#{version}" not in existing_versions:
                state.attempt_history_compact.append(entry)
            state.attempt_history_compact = state.attempt_history_compact[-10:]

        # 4. Current hypothesis — updated after path_not_reached or post_submit_miss
        if state.last_verification_result and not state.is_verified():
            gate = self._classify_failed_gate(state.last_verification_result)
            if gate == "path_not_reached":
                first_open = state.first_open_gate() if hasattr(state, "first_open_gate") else None
                if first_open:
                    state.current_hypothesis = (
                        f"Path not reached — first open gate: {first_open.description}. "
                        f"Need to confirm: {first_open.required_condition}"
                    )[:400]
                else:
                    state.current_hypothesis = (
                        "Path not reached — identify and confirm the parser gate "
                        "that blocks input from reaching the vulnerable code."
                    )[:400]
            elif gate == "trigger_wrong_signature":
                state.current_hypothesis = (
                    f"ASAN detected corruption but wrong crash type. "
                    f"Crash: {state.crash_type} at {state.crash_location}. "
                    "Refine overflow parameters (size/offset/field values)."
                )[:400]

    @staticmethod
    def _update_chain_from_read(state: CyberGymState, output: Any) -> None:
        """Insert or update ChainNode entries from READ content.

        Detects function calls and control-flow structures that form the
        entry-to-sink call chain.
        """
        if not isinstance(output, dict):
            return
        read_path = str(output.get("path") or "").strip()
        content = str(output.get("content") or "")
        if not read_path or not content:
            return

        from .state import ChainNode

        existing_locs = {n.location for n in state.call_chain_nodes}
        max_order = max((n.order for n in state.call_chain_nodes), default=-1)

        # Detect function definitions — these are chain nodes
        for m in re.finditer(
            r'(?:(?:static\s+)?(?:inline\s+)?[\w:*&]+\s+)(\w+)\s*\([^)]*\)\s*(?:\{|$)',
            content,
        ):
            func_name = m.group(1)
            # Skip trivial C keywords
            if func_name in ("if", "while", "for", "switch", "return", "sizeof", "typedef"):
                continue
            loc = f"{read_path}"
            # Only add if not already present at this location
            key = f"{func_name}@{loc}"
            if key not in existing_locs:
                # Determine role based on position and context
                role = "parser"
                if max_order < 0:
                    role = "entry"
                elif "sink" in func_name.lower() or "vuln" in func_name.lower() or func_name in (state.vulnerable_functions or []):
                    role = "sink"
                state.call_chain_nodes.append(ChainNode(
                    location=loc,
                    function=func_name,
                    role=role,
                    description=f"Function {func_name} in {read_path}",
                    status="inferred",
                    evidence=f"READ {read_path}",
                    order=max_order + 1,
                ))
                existing_locs.add(key)
                max_order += 1

        # Cap chain nodes
        if len(state.call_chain_nodes) > 20:
            state.call_chain_nodes = state.call_chain_nodes[-20:]

    def _capture_read_fact(self, state: CyberGymState, short_name: str, output: Any) -> None:
        normalized_name = str(short_name or "").upper()
        if normalized_name != self.READ_TOOL or not isinstance(output, dict):
            return
        path = str(output.get("path") or "").strip()
        content = str(output.get("content") or "")
        if not path or not content.strip():
            return
        snippet = self._best_fact_snippet(content)
        if not snippet:
            return
        evidence = state.durable_project_memory or {}
        parser_paths = set(evidence.get("parser_paths") or [])
        field_paths = set(evidence.get("field_paths") or [])
        seed_paths = set(evidence.get("seed_paths") or [])
        if path in parser_paths:
            prefix = "parser_path"
        elif path in field_paths:
            prefix = "field_path"
        elif path in seed_paths:
            prefix = "seed_path"
        else:
            prefix = "read_fact"
        fact = f"{prefix}: {self._display_path(path, state=state)} -> {snippet}"
        state.durable_code_facts = self._append_capped_fact(state.durable_code_facts, fact)
        # Extract structured facts from parser/field/seed paths
        if prefix in ("parser_path", "field_path"):
            structured = self._extract_structured_facts_from_content(content, self._display_path(path, state=state))
            for sfact in structured:
                state.durable_code_facts = self._append_capped_fact(state.durable_code_facts, sfact)

    def _capture_feedback_fact(self, state: CyberGymState, output: Dict[str, Any]) -> None:
        result = dict(output or {})
        verdict = self._verification_outcome_label(result)
        crash_type = self._parse_crash_type(str(result.get("vul_stderr", "") or result.get("raw_output", "") or ""))
        crash_location = self._parse_crash_location(str(result.get("vul_stderr", "") or result.get("raw_output", "") or ""))
        hints = self._extract_verification_hints(result)
        facts: List[str] = [f"verification: {verdict}"]
        # Failed gate classification
        gate = self._classify_failed_gate(result)
        if gate:
            facts.append(f"failed_gate: {gate}")
        latest_feedback = state.hot_feedback_window[-1] if state.hot_feedback_window else None
        if latest_feedback and latest_feedback.storage_path:
            facts.append(f"feedback_file: {self._display_path(latest_feedback.storage_path, state=state)}")
        poc_path = str(getattr(latest_feedback, "poc_path", "") if latest_feedback else "").strip()
        if poc_path:
            facts.append(f"feedback_poc_path: {self._display_path(poc_path, state=state)}")
        if crash_type:
            facts.append(f"crash_type: {crash_type}")
        if crash_location:
            facts.append(f"crash_location: {crash_location}")
        for hint in hints[:2]:
            facts.append(f"feedback_hint: {self._clip(hint, 180)}")
        raw_output = str(result.get("raw_output") or result.get("output") or "").strip()
        if raw_output:
            facts.append(
                "feedback_analysis: latest full submit output is preserved; inspect Latest Hot Feedback or feedback_file before rereading code."
            )
        for fact in facts:
            state.durable_feedback_facts = self._append_capped_fact(
                state.durable_feedback_facts,
                fact,
            )

    @staticmethod
    def _failure_summary_lines(state: CyberGymState) -> List[str]:
        visible: List[str] = []
        for record in list(state.failure_history or [])[-2:]:
            if getattr(record, "internal_only", False):
                continue
            visible.append(f"- Recent Failure: {record.summary}")
        return visible

    def _display_path(self, path: str, *, state: Optional[CyberGymState] = None) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        workspace_root = str(
            (state.workspace_root if state is not None else "") or getattr(self, "workspace_root", "") or ""
        ).strip()
        if not workspace_root:
            return raw
        try:
            raw_path = Path(raw)
            if not raw_path.is_absolute():
                return raw
            root = Path(workspace_root).resolve()
            resolved = raw_path.resolve()
            return str(resolved.relative_to(root))
        except Exception:
            return raw

    def reduce(
        self,
        state: CyberGymState,
        observation: Any,
        decision: Decision,
    ) -> CyberGymState:
        """Reduce observation into the next state."""
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

        for result in action_results:
            # Normalize to ToolResult if needed
            tr = ToolResult.from_value(result) if not isinstance(result, ToolResult) else result
            self._process_action_result(state, tr)
            if getattr(state, "stop_reason", ""):
                break

        self._update_candidate_requirement_from_decision(state, decision)
        if getattr(state, "stop_reason", ""):
            if state.is_verified() and self.memory:
                self._save_success_memory(state)
            return state
        self._ensure_family_bootstrap(state)
        if self._helper_subagents_enabled():
            try:
                self._run_insight_pass(state)
            except Exception as exc:
                self._record_subagent_error(state, "insight", exc)
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
        elif (
            not state.candidate_queue
            and not cooled_this_turn
            and latest_action not in {"branch_family", "retire_family", "stop_task"}
            and self._helper_subagents_enabled()
        ):
            family = self._select_candidate_family(state)
            if family is not None:
                try:
                    self._dispatch_candidate_agent(
                        state,
                        family,
                        candidate_budget=self._candidate_budget_for_stage(state.runtime_stage),
                    )
                except Exception as exc:
                    self._record_subagent_error(state, "candidate", exc)
                if state.candidate_queue:
                    self._drain_candidate_queue_to_ready_pocs(state)

        # Advance phase via PhaseEngine
        step = getattr(state, "current_step", 0) or 0
        try:
            state.current_step = int(step)
        except Exception:
            pass
        state.phase_local_steps = phase_local_steps(state)
        old_phase = state.current_phase
        new_phase = self._phase_engine.advance(state, step)
        state.current_phase = new_phase
        if new_phase != old_phase:
            state.phase_enter_step = int(step)
            state.phase_local_steps = 0
            state.phase_submissions = 0
            if old_phase == "verification" and new_phase == "investigation":
                state.reinvestigate_requested = False
            state.phase_read_actions = 0
            state.repeated_read_target = ""
            state.repeated_read_count = 0
        else:
            state.phase_local_steps = phase_local_steps(state)
        self._update_control_mode(state, int(step))

        # --- Constraint checkpoint during investigation ---
        # If the constraint board is empty after N phase-local steps, force
        # the LLM to record at least one chain node before continuing.
        if (state.current_phase == "investigation"
            and not state.call_chain_nodes
            and not state.call_chain_gates
            and not state.pending_chain_checkpoint):
            pl_steps = phase_local_steps(state)
            if pl_steps > 0 and pl_steps % 5 == 0:
                state.pending_chain_checkpoint = True

        # --- Gates checkpoint during investigation ---
        if (state.current_phase == "investigation"
            and state.call_chain_nodes
            and not state.call_chain_gates
            and not state.pending_gates_checkpoint
            and not state.pending_chain_checkpoint):
            pl_steps = phase_local_steps(state)
            if pl_steps > 0 and pl_steps % 7 == 0:
                state.pending_gates_checkpoint = True

        # --- Empty constraint board soft reminder ---
        if (state.current_phase == "investigation"
            and not state.call_chain_nodes
            and not state.call_chain_gates
            and not state.pending_chain_checkpoint
            and phase_local_steps(state) >= 4
            and not state.pending_reminder):
            state.pending_reminder = (
                "No chain nodes recorded yet. Use FindSymbols to find the "
                "vulnerable function, then record_chain_node to add it to the "
                "chain, or record_gate to add a path constraint."
            )
            state.pending_reminder_signature = "empty-constraint-board"

        # --- Consecutive-miss reinvestigation nudge ---
        if (state.consecutive_misses >= 4
            and not state.pending_reflection
            and not state.pending_reminder):
            state.pending_reminder = (
                f"{state.consecutive_misses} consecutive NO_TRIGGER submissions. "
                "Your PoCs are not reaching the vulnerable code path. "
                "STOP submitting variants — READ the harness entry and trace the "
                "call chain to understand which path-gating condition is blocking "
                "input from reaching the sink. Use record_gate to capture each constraint."
            )
            state.pending_reminder_signature = "consecutive-miss-reinvestigate"

        # --- Task-persistent memory updates ---
        # These fields survive context compaction and are rendered in every
        # observation as "## Task Memory".
        self._update_task_persistent_memory(state, old_phase, new_phase)

        # On successful verification, save feedback memory
        if state.is_verified() and self.memory:
            self._save_success_memory(state)

        # Log exchange for debugging (messages, response, observations)
        if self._exchange_logger is not None:
            step_id = getattr(state, "current_step", 0) or 0
            # Log model response
            if isinstance(decision, Decision):
                resp = {}
                if decision.tool_calls:
                    resp["tool_calls"] = [
                        {"function": {"name": tc.name, "arguments": str(tc.args or "")[:500]}}
                        for tc in decision.tool_calls
                    ]
                if decision.text:
                    resp["text"] = decision.text[:1000]
                self._exchange_logger.log_response(step_id, resp)
            # Log observations (tool results)
            obs_texts = []
            for tr in action_results:
                if isinstance(tr, ToolResult):
                    obs_texts.append(tr.text[:4000])
                elif isinstance(tr, dict):
                    obs_texts.append(json.dumps(tr, ensure_ascii=False, default=str)[:4000])
            self._exchange_logger.log_observations(step_id, obs_texts)
            self._exchange_logger.flush()

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
        normalized_name = short_name.upper()
        output = result.output
        output_str = result.text

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
                from .submit_tool import _last_submit_structured_output
                if _last_submit_structured_output is not None:
                    output = _last_submit_structured_output

        self._track_read_budget(state, short_name, output)
        observation_note = self._summarize_tool_observation(short_name, output)
        if observation_note:
            state.recent_tool_observations.append(observation_note)
            state.recent_tool_observations = state.recent_tool_observations[-6:]
        self._capture_read_fact(state, short_name, output)
        self._detect_harness_entry(state, short_name, output)
        self._update_read_coverage(state, short_name, output)
        # Mark high-value READ results for compaction priority
        if normalized_name == self.READ_TOOL and isinstance(output, dict) and hasattr(result, "metadata"):
            read_path = str(output.get("path") or "").strip()
            evidence = state.durable_project_memory or {}
            high_value_paths = set()
            for key in ("parser_paths", "field_paths", "seed_paths"):
                high_value_paths.update(
                    str(p).strip() for p in (evidence.get(key) or [])
                )
            if read_path in high_value_paths:
                result.metadata["compaction_priority"] = "high"

        if short_name in DELEGATE_TOOL_AGENT_NAMES or short_name.startswith("delegate_to_"):
            self._handle_delegate_result(state, short_name, output)
            return

        # Handle submit_poc result
        if short_name == SUBMIT_POC_TOOL:
            # Mark submit_poc results as critical for compaction priority
            if hasattr(result, "metadata") and isinstance(result.metadata, dict):
                result.metadata["compaction_priority"] = "critical"
            duplicate_error = self._submit_duplicate_error_message(result)
            if duplicate_error and not isinstance(output, dict):
                submit_context = self._submitted_candidate_context(state, result.metadata)
                submitted_path = str(submit_context.get("poc_path") or "")
                duplicate_output = {
                    "status": "error",
                    "error": duplicate_error,
                    "raw_output": duplicate_error,
                }
                self._append_feedback_record(
                    state,
                    duplicate_output,
                    result.metadata,
                    submit_context,
                )
                state.last_verification_result = duplicate_output
                state.last_error_trace = duplicate_error
                state.poc_attempts += 1
                if not self._ready_poc_paths(state):
                    state.candidate_required = True
                self._record_verification_attempt(state, duplicate_output, poc_path=submitted_path)
                self._update_failure_counters(state, duplicate_output)
                if not state.is_verified():
                    state.pending_attempt_record = False
                return
            if isinstance(output, dict):
                submit_metadata = dict(result.metadata or {})
                for key in (
                    "poc_path",
                    "content_fingerprint",
                    "candidate_id",
                    "family_id",
                ):
                    if key not in submit_metadata and output.get(key):
                        submit_metadata[key] = output.get(key)
                submit_context = self._submitted_candidate_context(state, submit_metadata)
                submitted_path = str(submit_context.get("poc_path") or "")
                self._append_feedback_record(state, output, submit_metadata, submit_context)
                state.last_verification_result = output
                vul_code = output.get("vul_exit_code")
                accepted = output.get("accepted") is True
                self._capture_feedback_fact(state, output)
                # Parse sanitizer output for crash details
                # The real /submit-vul server puts ASAN trace in `output`
                # (mapped to raw_output), not vul_stderr. Fall back when
                # vul_stderr is empty so crash info is always captured.
                vul_stderr = output.get("vul_stderr", "")
                raw_output = str(output.get("raw_output") or "")
                crash_source = vul_stderr if vul_stderr else raw_output
                state.crash_type = self._parse_crash_type(crash_source)
                state.crash_location = self._parse_crash_location(crash_source)

                if output.get("status") == "error":
                    state.last_error_trace = output.get("error", "Unknown error")
                    # Track consecutive submission errors.  After N errors in
                    # a row, clear the ready_pocs queue so the agent can
                    # escape candidate_ready and return to investigation.
                    state.consecutive_submit_errors += 1
                    if state.consecutive_submit_errors >= 3:
                        cleared = len(state.ready_pocs)
                        state.ready_pocs.clear()
                        state.candidate_required = True
                        state.last_error_trace += (
                            f"\n\n{state.consecutive_submit_errors} consecutive submission errors — "
                            f"cleared {cleared} queued PoC(s). Return to investigation, "
                            "fix the underlying issue, then generate a new PoC."
                        )
                elif state.is_verified():
                    # SUCCESS: full differential confirmation accepted the candidate.
                    state.pending_attempt_record = False
                    state.pending_reflection = False
                    state.consecutive_submit_errors = 0
                    state.metadata.pop(FAILURE_REFLECTION_ACK_KEY, None)
                    state.set_stop(
                        "success",
                        final_result=submitted_path or "verified",
                    )
                    self._update_best_poc_for_path(state, 2, submitted_path)
                elif vul_code is not None and vul_code != 0:
                    # The vulnerable binary crashed. Determine whether we
                    # have fix-side data to decide if this is a true
                    # acceptance or needs refinement.
                    fix_code = output.get("fix_exit_code")
                    scope = str(output.get("verification_scope") or "")
                    state.consecutive_misses = 0
                    state.consecutive_submit_errors = 0
                    self._update_best_poc_for_path(state, 1, submitted_path)

                    if accepted:
                        # Full verification accepted the candidate.
                        state.discriminant_failed = False
                        state.last_error_trace = "Candidate accepted but stop criteria did not fire."
                    elif fix_code is not None and fix_code != 0:
                        # Discriminant failure: fix binary ALSO crashes.
                        # The PoC is too aggressive — the fix can't prevent it.
                        state.discriminant_failed = True
                        state.last_error_trace = (
                            f"Candidate triggered the vulnerable run (exit={vul_code}) "
                            "but was not accepted — the FIXED binary ALSO crashed. "
                            "This means your overflow is too aggressive: it bypasses the fix's "
                            "bounds check too. Make the overflow MORE PRECISE: reduce the "
                            "overflow magnitude (e.g., overflow by 1-4 bytes instead of hundreds), "
                            "target the exact vulnerable field, or use a smaller write size. "
                            "The fix must be able to catch and prevent the overflow."
                        )
                    elif scope == "vul_only":
                        # VUL-ONLY TRIGGER: no fix-side data available.
                        # This is a PARTIAL success — we don't know if the
                        # fix would pass. The agent should refine for precision.
                        state.discriminant_failed = False
                        state.last_error_trace = (
                            f"VUL-ONLY TRIGGER: Vulnerable binary crashed (exit={vul_code}) "
                            "but fix-side verification is unavailable. "
                            "This is a PARTIAL success — the PoC may or may not be precise "
                            "enough for acceptance. Refine the PoC for maximum precision: "
                            "reduce overflow to minimal bytes (1-4 past boundary), "
                            "target the exact vulnerable field/offset, and ensure only the "
                            "vulnerable code path is exercised. The fix must be able to "
                            "prevent the crash — if both binaries crash, the PoC is too aggressive."
                        )
                        if state.patch_diff:
                            patch_excerpt = state.patch_diff[:500].strip()
                            state.last_error_trace += (
                                f"\n\nPatch diff shows the fix:\n{patch_excerpt}\n"
                                "The PoC must trigger the bug BEFORE this fix takes effect. "
                                "Overflow must be small enough that the fix's bounds check "
                                "can still prevent it."
                            )
                        # Add a feedback fact about patch-diff-guided refinement
                        if state.patch_diff and hasattr(self, "_append_capped_fact"):
                            patch_lines = [
                                ln for ln in state.patch_diff.splitlines()
                                if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))
                            ]
                            patch_summary = "; ".join(patch_lines[:5]) if patch_lines else "see patch_diff"
                            state.durable_feedback_facts = self._append_capped_fact(
                                state.durable_feedback_facts,
                                f"patch_guided_refinement: fix changes [{patch_summary}]. PoC must crash before fix; overflow must be minimal.",
                            )
                    else:
                        # Full verification available but rejected.
                        state.discriminant_failed = False
                        state.last_error_trace = (
                            f"Candidate triggered the vulnerable run (exit={vul_code}) "
                            "but was not accepted by full verification. Refine the input "
                            "to match the described vulnerability more specifically."
                        )
                else:
                    # MISS: vul doesn't crash
                    state.discriminant_failed = False
                    state.consecutive_submit_errors = 0
                    state.consecutive_misses += 1
                    self._update_best_poc_for_path(state, 0, submitted_path)
                    raw_output = str(output.get("raw_output") or "")
                    feedback_hints = self._extract_verification_hints(output)
                    raw_excerpt = "\n".join(feedback_hints).strip()
                    if not raw_excerpt:
                        raw_excerpt = raw_output[:1200].strip()
                    state.last_error_trace = (
                        f"PoC did not trigger the vulnerability. "
                        f"vul_exit={vul_code}"
                    )
                    if raw_excerpt:
                        state.last_error_trace += f"\nServer output excerpt:\n{raw_excerpt}"
                state.poc_attempts += 1
                state.phase_submissions += 1
                self._record_verification_attempt(state, output, poc_path=submitted_path)
                self._update_failure_counters(state, output)
                if not state.is_verified():
                    state.pending_attempt_record = False
                    # Gate refutation: classify the failure and refute
                    # matching chain gates so the agent learns from failures.
                    gate = self._classify_failed_gate(output)
                    if gate:
                        self._refute_matching_gates(state, gate)
                        # Budget reset on path_not_reached: the feedback
                        # explicitly says "you need to understand the path
                        # better," so allow more reads.
                        if gate == "path_not_reached":
                            state.phase_read_actions = max(
                                0, state.phase_read_actions - 3
                            )

        # Track PoC file creation
        elif normalized_name == self.WRITE_TOOL or short_name in ("write_file", "create"):
            direct_paths: List[str] = []
            if isinstance(output, str) and "poc" in output.lower():
                direct_paths = [str(output)]
            elif isinstance(output, dict) and "path" in output:
                direct_paths = [str(output["path"])]
            if direct_paths:
                self._register_direct_candidates(state, direct_paths)

        # Track command execution for error traces and PoC creation
        elif normalized_name == self.BASH_TOOL or short_name in ("run_command", "bash_v2"):
            if isinstance(output, dict):
                rc = output.get("returncode", 0)
                stderr = output.get("stderr", "")
                if rc != 0 and stderr:
                    state.last_error_trace = stderr[:2000]
                elif rc != 0:
                    state.last_error_trace = f"Exit code: {rc}"
                # Register newly-created PoC files from BASH commands.
                # Only register paths explicitly mentioned in the command or
                # stdout, NOT by scanning the entire pocs/ directory.
                if rc == 0:
                    command = str(output.get("command", "") or "")
                    bash_paths = self._extract_poc_paths_from_bash(command, state)
                    if bash_paths:
                        self._register_direct_candidates(state, bash_paths)

        elif short_name == RECORD_ATTEMPT_TOOL:
            state.pending_attempt_record = False

        elif short_name == RECORD_REFLECTION_TOOL:
            state.pending_reflection = False
            self._mark_failure_signature_reflected(state)

        elif short_name in ("record_chain_node", "record_gate"):
            # Clear chain checkpoint once any node or gate is recorded
            if getattr(state, "pending_chain_checkpoint", False):
                if state.call_chain_nodes or state.call_chain_gates:
                    state.pending_chain_checkpoint = False
            if getattr(state, "pending_gates_checkpoint", False):
                if state.call_chain_gates:
                    state.pending_gates_checkpoint = False

        # Track file reads that reveal vulnerable code
        elif normalized_name == self.READ_TOOL or short_name in ("read_file", "view", "file_read_v2", "read_file_range"):
            self._track_match_read_follow(state, output)
            if output_str:
                self._extract_findings_from_read(state, output_str)
            # P26: confirm constraints whose source_location matches the read path
            self._confirm_constraints_from_read(state, output)
            # P36: extract path constraints from READ content (parser gates,
            # branch conditions, magic-number checks).
            self._extract_path_constraints_from_read(state, output)
            # Chain node recording is now the LLM's responsibility via
            # record_chain_node. Auto-extraction was removed because it
            # produced low-quality nodes (wrong roles, generic descriptions).

        # Track search results
        elif normalized_name == self.GREP_TOOL or short_name in ("grep", "grep_files", "grep_v2", "search"):
            if output_str:
                self._extract_findings_from_search(state, output_str)

        elif normalized_name in (self.FIND_SYMBOLS_TOOL, self.CALLSITE_SEARCH_TOOL):
            self._infer_chain_from_search(state, short_name, output)

        elif normalized_name == self.GLOB_TOOL:
            self._capture_glob_metrics(state, output)

    def _handle_delegate_result(
        self,
        state: CyberGymState,
        short_name: str,
        output: Any,
    ) -> None:
        if not isinstance(output, dict):
            return
        agent_name = str(
            output.get("agent")
            or DELEGATE_TOOL_AGENT_NAMES.get(short_name)
            or short_name.removeprefix("delegate_to_")
        )
        final_result = str(output.get("final_result") or "")
        payload: Dict[str, Any] = {
            "agent": agent_name,
            "status": output.get("status"),
            "steps": output.get("steps"),
            "stop_reason": output.get("stop_reason"),
            "final_result": final_result,
        }
        artifact_type = "delegate_result"
        summary = ""
        if agent_name == "insight_delegate":
            artifact_type = "delegate_insight"
            if final_result:
                try:
                    parsed = parse_insight_json(final_result)
                except Exception as exc:
                    payload["parse_error"] = str(exc)
                    summary = f"Insight delegate parse error: {exc}"
                else:
                    payload["parsed"] = parsed
                    summary = (
                        f"insight assessment={parsed.get('assessment', '')} "
                        f"action={parsed.get('suggested_action', '')}"
                    )
                    state.durable_feedback_facts.append(f"delegate_{summary}")
                    state.durable_feedback_facts = state.durable_feedback_facts[-20:]
        elif agent_name == "explore_delegate":
            artifact_type = "exploration_report"
            if final_result:
                try:
                    parsed = parse_explore_json(final_result)
                except Exception as exc:
                    artifact_type = "delegate_parse_error"
                    payload["parse_error"] = str(exc)
                    summary = f"Explore delegate parse error: {exc}"
                else:
                    payload["parsed"] = parsed
                    summary = self._summarize_explore_report(parsed)
                    self._ingest_explore_report(state, parsed)
            else:
                artifact_type = "delegate_parse_error"
                payload["parse_error"] = "missing final_result"
                summary = "Explore delegate parse error: missing final_result"
        artifact = ArtifactStore(
            state.workspace_root or self.workspace_root
        ).write_artifact(
            artifact_type=artifact_type,
            producer=agent_name,
            payload=payload,
            parent_refs=[],
            summary=summary,
        )
        if not isinstance(state.durable_project_memory, dict):
            state.durable_project_memory = {}
        state.durable_project_memory["last_delegate_artifact"] = artifact.path
        state.durable_project_memory["last_delegate_artifact_type"] = artifact.artifact_type
        if artifact.artifact_type == "exploration_report":
            state.durable_project_memory[DELEGATE_EXPLORATION_REPORT_SEEN_KEY] = True
        state.recent_tool_observations.append(
            f"- delegate_result: {agent_name} -> {artifact.path}"
        )
        state.recent_tool_observations = state.recent_tool_observations[-6:]

    def _ingest_explore_report(self, state: CyberGymState, parsed: Dict[str, Any]) -> None:
        if not isinstance(state.evidence_index, dict):
            state.evidence_index = {}

        def normalize_text(value: Any) -> str:
            if value is None:
                return ""
            return str(value).strip()

        def item_path(item: Any) -> str:
            if not isinstance(item, dict):
                return ""
            return normalize_text(item.get("path"))

        def evidence_value(item: Any) -> str:
            if isinstance(item, dict):
                item = item.get("path")
            return normalize_text(item)

        def merge_evidence_list(key: str, values: List[Any]) -> None:
            existing = state.evidence_index.get(key)
            candidates = list(existing) if isinstance(existing, list) else []
            candidates.extend(values)

            merged: List[str] = []
            seen: set[str] = set()
            for candidate in candidates:
                value = evidence_value(candidate)
                if not value or value in seen:
                    continue
                seen.add(value)
                merged.append(value)
                if len(merged) >= 20:
                    break
            state.evidence_index[key] = merged

        parser_paths = [
            item_path(item) for item in list(parsed.get("parser_paths") or [])
        ]
        entrypoints = [
            item_path(item) for item in list(parsed.get("entrypoints") or [])
        ]
        format_constraints = [
            normalize_text(item)
            for item in list(parsed.get("format_constraints") or [])
        ]

        merge_evidence_list("parser_paths", parser_paths)
        merge_evidence_list("entrypoints", entrypoints)
        merge_evidence_list("format_constraints", format_constraints)

        # Ingest related_locations as vulnerability anchors
        related_locations = list(parsed.get("related_locations") or [])
        if related_locations:
            if not isinstance(state.durable_project_memory, dict):
                state.durable_project_memory = {}
            state.durable_project_memory["vulnerability_anchors"] = related_locations

        if not isinstance(state.family_pool, list):
            state.family_pool = []
        existing_names = {
            str(getattr(family, "family_name", "") or "").strip()
            for family in state.family_pool
        }
        for item in list(parsed.get("candidate_families") or []):
            if not isinstance(item, dict):
                continue
            family_name = str(item.get("family_name") or "").strip()
            if not family_name or family_name in existing_names:
                continue
            generation_axes = item.get("generation_axes")
            if not isinstance(generation_axes, list):
                generation_axes = []
            state.family_pool.append(
                FamilyRecord(
                    family_id=(
                        "explore-"
                        + hashlib.sha1(family_name.encode("utf-8")).hexdigest()[:10]
                    ),
                    family_name=family_name,
                    parent_family_id="",
                    state="new",
                    hypothesis=str(item.get("hypothesis") or ""),
                    generation_axes=[str(axis) for axis in generation_axes],
                )
            )
            existing_names.add(family_name)

    @staticmethod
    def _summarize_explore_report(parsed: Dict[str, Any]) -> str:
        return (
            f"parser_paths={len(parsed.get('parser_paths', []) or [])} "
            f"candidate_families={len(parsed.get('candidate_families', []) or [])}"
        )

    @staticmethod
    def _select_family_evidence_safe(
        family_name: str,
        evidence_index: Dict[str, Any],
    ) -> Dict[str, object]:
        try:
            return select_family_evidence(family_name, evidence_index or {})
        except ValueError as exc:
            if "unknown family_name" not in str(exc):
                raise

        paths: List[str] = []
        seen: set[str] = set()
        source = evidence_index if isinstance(evidence_index, dict) else {}
        for key in ("parser_paths", "entrypoints", "seed_paths", "field_paths"):
            values = source.get(key)
            if not isinstance(values, list):
                continue
            for item in values:
                if isinstance(item, dict):
                    item = item.get("path")
                path = "" if item is None else str(item).strip()
                if not path or path in seen:
                    continue
                seen.add(path)
                paths.append(path)
                if len(paths) >= 4:
                    return {"family_name": family_name, "paths": paths}
        return {"family_name": family_name, "paths": paths}

    def _run_insight_agent(
        self,
        *,
        state: CyberGymState,
        family_snapshot: Dict[str, Any],
        candidate_snapshot: Dict[str, Any],
        latest_feedback_raw: str,
        previous_feedback_raw: str,
        evidence_pack: Dict[str, Any],
    ) -> Dict[str, Any]:
        messages = build_insight_messages(
            task_description=state.vulnerability_description,
            family_snapshot=family_snapshot,
            candidate_snapshot=candidate_snapshot,
            latest_feedback_raw=latest_feedback_raw,
            previous_feedback_raw=previous_feedback_raw,
            evidence_pack=evidence_pack,
        )
        text = run_subagent_json(self.llm, messages)
        self._record_subagent_activity(
            state,
            "insight_subagent_raw",
            self._subagent_output_preview(text),
        )
        return parse_insight_json(text)

    def _run_insight_pass(self, state: CyberGymState) -> None:
        if not getattr(self, "llm", None) or not state.hot_feedback_window or not state.family_pool:
            return
        latest_feedback = state.hot_feedback_window[-1]
        if latest_feedback.assessment and latest_feedback.suggested_action:
            return
        family = self._find_family(state, latest_feedback.family_id)
        if family is None:
            return
        evidence_pack = self._select_family_evidence_safe(
            family.family_name,
            state.evidence_index or {},
        )
        previous_feedback_raw = self._previous_family_feedback_raw(state, family.family_id, latest_feedback.poc_id)
        self._record_subagent_activity(
            state,
            "insight_subagent_dispatch",
            f"family={family.family_id} poc_id={latest_feedback.poc_id}",
        )
        judgement = self._run_insight_agent(
            state=state,
            family_snapshot=self._family_snapshot(family),
            candidate_snapshot={
                "candidate_id": latest_feedback.candidate_id,
                "family_id": latest_feedback.family_id,
                "poc_id": latest_feedback.poc_id,
            },
            latest_feedback_raw=latest_feedback.output,
            previous_feedback_raw=previous_feedback_raw,
            evidence_pack=evidence_pack,
        )
        self._record_subagent_activity(
            state,
            "insight_subagent_judgement",
            (
                f"family={family.family_id} assessment={judgement.get('assessment', '')} "
                f"action={judgement.get('suggested_action', '')}"
            ),
        )
        self._apply_insight_judgement(state, family, latest_feedback, judgement)

    def _dispatch_candidate_agent(
        self,
        state: CyberGymState,
        family: FamilyRecord,
        candidate_budget: int,
    ) -> None:
        if not getattr(self, "llm", None) or candidate_budget <= 0:
            return
        evidence_pack = self._select_family_evidence_safe(
            family.family_name,
            state.evidence_index or {},
        )
        latest_feedback = self._latest_family_feedback(state, family.family_id)
        latest_feedback_raw = latest_feedback.output if latest_feedback is not None else ""
        mutation_hints = self._family_mutation_hints(state, family.family_id)
        self._record_subagent_activity(
            state,
            "candidate_subagent_dispatch",
            f"family={family.family_id} budget={candidate_budget}",
        )
        messages = build_candidate_messages(
            task_description=state.vulnerability_description,
            family_spec=self._family_snapshot(family),
            latest_family_feedback_raw=latest_feedback_raw,
            mutation_hints=mutation_hints,
            evidence_pack=evidence_pack,
            candidate_budget=candidate_budget,
        )
        text = run_subagent_json(self.llm, messages)
        self._record_subagent_activity(
            state,
            "candidate_subagent_raw",
            self._subagent_output_preview(text),
        )
        payload = parse_candidate_json(text)
        submitted_fingerprints = tuple(
            str(item)
            for item in (state.submitted_fingerprints or state.metadata.get("submitted_candidate_fingerprints", []))
            if isinstance(item, str)
        )
        accepted = 0
        for raw_candidate in payload.get("candidates", []):
            normalized_candidate = dict(raw_candidate)
            normalized_candidate["family_id"] = family.family_id
            candidate = CandidateRecord(
                candidate_id=normalized_candidate["candidate_id"],
                family_id=normalized_candidate["family_id"],
                file_path=normalized_candidate["file_path"],
                content_fingerprint=self._candidate_fingerprint(normalized_candidate),
                mutation_summary=normalized_candidate["mutation_summary"],
                expected_signal=normalized_candidate["expected_signal"],
                novelty_note=normalized_candidate["novelty_note"],
                base_seed=normalized_candidate["base_seed"],
                generation_method=normalized_candidate["generation_method"],
                ready_to_submit=normalized_candidate["ready_to_submit"],
                priority=max(candidate_budget - accepted, 0),
                producer_agent=str(normalized_candidate.get("producer_agent") or "candidate_delegate"),
                created_at=str(normalized_candidate.get("created_at") or ""),
                artifact_ref=str(normalized_candidate.get("artifact_ref") or ""),
                hypothesis_ref=str(normalized_candidate.get("hypothesis_ref") or family.family_id),
                fingerprint_mode=str(normalized_candidate.get("fingerprint_mode") or "logical"),
                artifact_sha256=str(normalized_candidate.get("artifact_sha256") or ""),
            )
            if enqueue_candidate(state.candidate_queue, candidate, submitted_fingerprints=submitted_fingerprints):
                family.candidate_count += 1
                accepted += 1
                if accepted >= candidate_budget:
                    break
        self._record_subagent_activity(
            state,
            "candidate_subagent_result",
            f"family={family.family_id} accepted={accepted} queued={len(state.candidate_queue)}",
        )


    @staticmethod
    def _record_subagent_error(state: CyberGymState, role: str, exc: Exception) -> None:
        message = f"{role} subagent error: {exc}"
        state.last_error_trace = message
        state.recent_tool_observations.append(f"- {role}_subagent_error: {_clip(str(exc), 160)}")
        state.recent_tool_observations = state.recent_tool_observations[-6:]

    @staticmethod
    def _record_subagent_activity(state: CyberGymState, event: str, detail: str) -> None:
        state.recent_tool_observations.append(
            f"- {event}: {_clip(detail, 160)}"
        )
        state.recent_tool_observations = state.recent_tool_observations[-6:]

    @staticmethod
    def _subagent_output_preview(text: str) -> str:
        preview = str(text or "").strip().replace("\n", "\\n")
        return preview

    @staticmethod
    def _clip(text: str, limit: int) -> str:
        return _clip(text, limit)


    # ------------------------------------------------------------------
    # Harness, corpus, and strategy detection
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_harness_info(workspace_root: str) -> str:
        """Read submit.sh and extract harness info (binary path, arguments)."""
        submit_sh = os.path.join(workspace_root, "submit.sh")
        if not os.path.isfile(submit_sh):
            return ""
        try:
            content = Path(submit_sh).read_text(errors="replace")[:2000]
            return f"submit.sh content:\n{content}"
        except Exception:
            return ""

    @staticmethod
    def _discover_corpus_files(repo_dir: str) -> List[str]:
        """Find fuzzing corpus and sample input files in the repo."""
        corpus_files = []
        repo_path = Path(repo_dir)
        seen = set()
        sample_path_keywords = (
            "corpus", "seed", "sample", "samples", "testcase",
            "fuzz", "oss-fuzz", "test", "testdata", "test_input",
            "test_data", "input", "examples", "crash", "poc",
        )

        # Search for corpus directories (expanded patterns)
        corpus_dir_patterns = [
            "fuzzing/corpus", "corpus", "testcases", "seeds",
            "seed_corpus", "fuzz/corpus", "test_corpus",
            "test/data", "testdata", "test_input", "test/input",
            "testcases", "examples/input", "samples", "input",
        ]
        for pattern in corpus_dir_patterns:
            corpus_dir = repo_path / pattern
            if corpus_dir.is_dir():
                for f in corpus_dir.iterdir():
                    if (
                        f.is_file()
                        and f.stat().st_size < 1_000_000
                        and not CyberGymAgent._is_git_lfs_pointer(f)
                        and str(f) not in seen
                    ):  # < 1MB
                        rel = str(f.relative_to(repo_path))
                        corpus_files.append(rel)
                        seen.add(str(f))

        # Search for sample input files by extension
        sample_extensions = {
            ".png", ".jpg", ".jpeg", ".heic", ".heif",
            ".pdf", ".zip", ".gz", ".tar", ".bz2",
            ".bin", ".raw", ".dat", ".img",
            ".mng", ".gif", ".bmp", ".tiff", ".webp",
            ".input", ".poc", ".crash",
        }
        for f in repo_path.rglob("*"):
            if f.is_file() and f.suffix.lower() in sample_extensions:
                if str(f) in seen:
                    continue
                lowered = str(f.relative_to(repo_path)).lower()
                # Accept files in corpus-like directories OR small files anywhere
                in_corpus_dir = any(token in lowered for token in sample_path_keywords)
                is_small = f.stat().st_size < 100_000  # < 100KB
                if not in_corpus_dir and not is_small:
                    continue
                if (
                    f.stat().st_size < 1_000_000
                    and not CyberGymAgent._is_git_lfs_pointer(f)
                ):  # < 1MB
                    try:
                        rel = str(f.relative_to(repo_path))
                        corpus_files.append(rel)
                        seen.add(str(f))
                    except ValueError:
                        pass

        return corpus_files[:30]  # Cap at 30 files

    @staticmethod
    def _prepare_seed_corpus(task_root: str, repo_dir: str) -> List[str]:
        """Find seed-corpus zips/dirs near the task and extract them.

        oss-fuzz ships `<fuzzer>_seed_corpus.zip` of VALID inputs BESIDE the
        source tree (at repo-vul/), i.e. OUTSIDE repo_dir (= repo-vul/<project>),
        so the repo_dir-only discovery misses it. Scan the task root + repo_dir's
        parent, extract any seed/corpus zip into <task_root>/seeds/, and return
        workspace-relative paths to individual seed files (smallest first) so the
        agent can copy+mutate a real input rather than hand-craft raw bytes.
        """
        import zipfile

        if not task_root or not os.path.isdir(task_root):
            return []
        roots: List[str] = []
        for r in (task_root, os.path.dirname(repo_dir or ""), repo_dir):
            if r and os.path.isdir(r) and r not in roots:
                roots.append(r)
        out_base = os.path.join(task_root, "seeds")
        seed_paths: List[str] = []
        seen_zip: set = set()
        for root in roots:
            try:
                entries = sorted(os.listdir(root))
            except OSError:
                continue
            for name in entries:
                low = name.lower()
                if not low.endswith(".zip"):
                    continue
                if not any(tok in low for tok in ("seed_corpus", "corpus", "seed")):
                    continue
                full = os.path.join(root, name)
                if full in seen_zip or not os.path.isfile(full):
                    continue
                seen_zip.add(full)
                try:
                    if os.path.getsize(full) > 20_000_000:
                        continue
                    dest = os.path.join(
                        out_base, re.sub(r"[^A-Za-z0-9_.-]", "_", name[:-4])
                    )
                    if not os.path.isdir(dest):
                        os.makedirs(dest, exist_ok=True)
                        with zipfile.ZipFile(full) as zf:
                            members = [m for m in zf.namelist() if not m.endswith("/")][:200]
                            for m in members:
                                try:
                                    zf.extract(m, dest)
                                except Exception:
                                    continue
                    for dp, _dirs, files in os.walk(dest):
                        for f in files:
                            fp = os.path.join(dp, f)
                            try:
                                sz = os.path.getsize(fp)
                            except OSError:
                                continue
                            if 0 < sz < 2_000_000:
                                seed_paths.append(os.path.relpath(fp, task_root))
                except Exception:
                    continue
        seed_paths = sorted(
            set(seed_paths),
            key=lambda p: (
                os.path.getsize(os.path.join(task_root, p))
                if os.path.exists(os.path.join(task_root, p))
                else 1 << 30
            ),
        )
        return seed_paths[:20]

    # Source/text extensions that are NEVER fuzzer input samples.
    _NON_SAMPLE_EXT = frozenset({
        ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".inc", ".py", ".pyc",
        ".md", ".txt", ".rst", ".html", ".htm", ".js", ".ts", ".css", ".sh",
        ".cmake", ".in", ".am", ".ac", ".m4", ".mk", ".yml", ".yaml", ".cfg",
        ".ini", ".toml", ".go", ".rs", ".java", ".kt", ".rb", ".pl", ".php",
        ".po", ".pot", ".map", ".def", ".sym", ".ld", ".s", ".asm", ".o", ".a",
        ".lo", ".la", ".so", ".dll", ".dylib", ".gitignore", ".gitattributes",
        ".cs", ".swift", ".lua", ".tcl", ".bat", ".ps1", ".dox", ".1", ".3",
        ".json", ".tests", ".test", ".mak", ".supp", ".dist", ".svg", ".diff",
        ".patch", ".log", ".csv", ".tsv", ".expected", ".out", ".err", ".ref",
        ".dat.txt", ".am.in", ".cmake.in", ".gperf", ".vcxproj", ".sln",
    })

    # Text formats that are themselves fuzzer INPUTS (not source/build/config).
    # Targets like libxslt/libxml2/JS/SQL/regex consume text, so their useful
    # mutation seeds are text files (.xml/.xsl/.js/...) that the binary-only
    # filter would otherwise drop. These override the _NON_SAMPLE_EXT exclusion
    # and are kept even though they are not binary.
    _TEXT_INPUT_EXT = frozenset({
        ".xml", ".xsl", ".xslt", ".html", ".htm", ".xhtml", ".svg", ".js",
        ".mjs", ".json", ".css", ".sql", ".csv", ".tsv", ".ps", ".eps",
        ".rtf", ".vtt", ".srt", ".wkt", ".gml", ".kml", ".geojson", ".dtd",
    })

    @staticmethod
    def _file_looks_binary(path: str) -> bool:
        """True if the file content is binary (a real fuzzer input), not ASCII
        text (a spec/config/build file). Real fonts/images/CAD/etc. contain NUL
        bytes or a high fraction of non-text bytes in their header."""
        try:
            with open(path, "rb") as fh:
                chunk = fh.read(1024)
        except OSError:
            return False
        if not chunk:
            return False
        if b"\x00" in chunk:
            return True
        # printable-text bytes: tab/newline/CR + printable ASCII range
        text_bytes = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D, 0x0C}
        nonprint = sum(1 for b in chunk if b not in text_bytes)
        return (nonprint / len(chunk)) > 0.20

    @staticmethod
    def _discover_repo_seed_samples(repo_dir: str) -> List[str]:
        """Find REAL format sample files already in the repo to use as mutation seeds.

        Most oss-fuzz projects ship complex valid inputs in `test/`, `tests/`,
        `examples/`, `data/`, `fonts/`, `fixtures/` etc. (e.g. harfbuzz has 1000+
        real .ttf/.otf, libredwg has 100+ real .dwg). These are NOT in a
        `seed_corpus.zip`, have format-specific extensions the old corpus scan
        ignored, and live under `test/` (a path token the old scan missed) — so
        the agent never saw them and hand-crafted tiny invalid files that never
        reach the bug. Surface them so poc_strategy -> corpus_mutate and the
        agent mutates a real input. Returns ABSOLUTE paths (under repo_dir, which
        is inside the workspace, so the agent can READ/cp them).
        """
        if not repo_dir or not os.path.isdir(repo_dir):
            return []
        sample_dir_tokens = (
            "test", "sample", "example", "data", "font", "corpus", "seed",
            "fixture", "asset", "demo", "input", "regress", "case",
        )
        from collections import Counter

        cands: List[tuple] = []  # (path, size, ext)
        scanned_dirs = 0
        for dp, dirs, files in os.walk(repo_dir):
            # prune VCS / build dirs
            dirs[:] = [d for d in dirs if d not in (".git", "build", ".github", "node_modules", "__pycache__")]
            low = dp.lower()
            if not any(tok in low for tok in sample_dir_tokens):
                continue
            scanned_dirs += 1
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                is_text_input = ext in self._TEXT_INPUT_EXT
                if not ext:
                    continue
                # Skip source/build/config unless it is a known text INPUT format.
                if ext in self._NON_SAMPLE_EXT and not is_text_input:
                    continue
                fp = os.path.join(dp, f)
                try:
                    sz = os.path.getsize(fp)
                except OSError:
                    continue
                # Keep real binary inputs, OR text-format inputs (xml/xsl/js/...)
                # whose value as a seed does not depend on being binary.
                if 32 < sz < 2_000_000 and (
                    self._file_looks_binary(fp) or is_text_input
                ):
                    cands.append((fp, sz, ext))
            if len(cands) > 3000 or scanned_dirs > 4000:
                break
        if not cands:
            return []
        # Lock onto the dominant input format: the most common sample extensions
        # are almost always the fuzzer's input type (fonts, images, CAD, ...).
        ext_counts = Counter(e for _, _, e in cands)
        top_exts = {e for e, _ in ext_counts.most_common(3)}
        sel = [c for c in cands if c[2] in top_exts]
        sel.sort(key=lambda c: c[1])  # smallest first: easier to reason about + mutate
        out: List[str] = []
        per_ext: Dict[str, int] = {}
        for fp, _sz, ext in sel:
            if per_ext.get(ext, 0) >= 8:
                continue
            per_ext[ext] = per_ext.get(ext, 0) + 1
            out.append(fp)
            if len(out) >= 16:
                break
        return out

    @staticmethod
    def _is_git_lfs_pointer(path: Path) -> bool:
        try:
            if path.stat().st_size > 1024:
                return False
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False
        return (
            "version https://git-lfs.github.com/spec/v1" in content
            and "\noid sha256:" in content
        )

    def _save_success_memory(self, state: CyberGymState) -> None:
        """Save a feedback-type memory after successful PoC generation."""
        if not self.memory:
            return

        bug_type = state.bug_type or "unknown"
        name = f"{bug_type}_poc_strategy"
        description = f"Proven strategy for {bug_type} input PoCs"

        content_parts = [
            f"Successfully generated PoC for task {state.task_id}",
            f"Bug type: {bug_type}",
            f"Affected component: {state.affected_component}",
        ]
        if state.vulnerable_functions:
            content_parts.append(f"Vulnerable functions: {', '.join(state.vulnerable_functions[:5])}")
        if state.trigger_hypothesis:
            content_parts.append(f"Trigger hypothesis: {state.trigger_hypothesis}")
        content_parts.append(f"Attempts needed: {state.poc_attempts}")

        content = "\n".join(content_parts)

        self.memory.append(
            MemoryRecord(
                role="feedback",
                content=content,
                step_id=state.current_step,
                metadata={
                    "type": "feedback",
                    "name": name,
                    "description": description[:150],
                },
            )
        )
