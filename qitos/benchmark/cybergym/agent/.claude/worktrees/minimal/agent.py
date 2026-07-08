"""CyberGymAgent -- Minimal PoC Generation Agent for CyberGym Level 1 tasks.

No phase machine — the model decides its own workflow.
Uses QitOS framework features:
- ToolRegistry with auto_short_aliases for native tool calling
- ContextConfig for context overflow protection
- Memory file system (.cybergym/memory/) for persistent state
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from qitos.core.agent_module import AgentModule
from qitos.core.decision import Decision
from qitos.core.history import HistoryPolicy
from qitos.core.model_response import ModelResponse
from qitos.core.observation import Observation
from qitos.core.tool_result import ToolResult
from qitos.prompting import PromptBuildResult

from .state import CyberGymState
from .context import CyberGymContextHistory
from .memory import MemoryManager
from .versioning import normalize_agent_mode
from .tool_names import (
    READ as READ_TOOL,
    GREP as GREP_TOOL,
    GLOB as GLOB_TOOL,
    WRITE as WRITE_TOOL,
    BASH as BASH_TOOL,
    SUBMIT_POC as SUBMIT_POC_TOOL,
)
from .agent_impl.core.constants import (
    CYBERGYM_HISTORY_MAX_TOKENS, CYBERGYM_HISTORY_WARNING_RATIO,
)
from .agent_impl.core.utils import (
    sanitize_model_text as _sanitize_model_text,
)
from .agent_impl.tools.registry import build_tool_registry
from .agent_impl.prompt.state_init import StateInitMixin
from .agent_impl.repo.task_analysis import TaskAnalysisMixin
from .agent_impl.core.crash_parsing import CrashParsingMixin
from .agent_impl.prompt.prompts import PromptsMixin
from .agent_impl.poc.harness import HarnessMixin
from .agent_impl.core.paths import PathMixin
from .agent_impl.observation.validation import ValidationMixin
from .agent_impl.observation.renderer import ObservationMixin
from .agent_impl.tools.mixin import ToolMixin


class CyberGymAgent(
    StateInitMixin, TaskAnalysisMixin, CrashParsingMixin,
    PromptsMixin, HarnessMixin, PathMixin, ValidationMixin,
    ObservationMixin, ToolMixin,
    AgentModule[CyberGymState, Observation, Any],
):
    """Minimal PoC Generation Agent for CyberGym Level 1 tasks.

    Given a vulnerability description and a pre-patch codebase, produces a
    raw input file that triggers the underlying bug when fed to the vulnerable binary.

    No phase machine, no tool gating, no candidate flow — the model decides.
    """

    name = "cybergym_poc_gen"
    # Tool name constants
    READ_TOOL = READ_TOOL
    GREP_TOOL = GREP_TOOL
    GLOB_TOOL = GLOB_TOOL
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

        # Memory manager for .cybergym/memory/ files
        self._memory_mgr = MemoryManager(self.workspace_root)

        tool_registry, self._coding_tools = build_tool_registry(
            self,
            llm=llm,
            shell_timeout=shell_timeout,
            server_url=server_url,
        )

        # Disable engine-level MemdirMemory by default
        enable_memdir_memory = bool(config.pop("enable_memdir_memory", False)) or (
            os.environ.get("CYBERGYM_ENABLE_MEMDIR_MEMORY", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        memory = None
        if enable_memdir_memory:
            from qitos.kit.memory.memdir_memory import MemdirMemory
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
        self._structured_output_buffer: Dict[str, Any] = {}
        self._last_structured_output: Any = None

    def build_prompt_bundle(self, state: CyberGymState) -> PromptBuildResult:
        """No tool filtering — always send all 9 tools."""
        return super().build_prompt_bundle(state)

    # ------------------------------------------------------------------
    # AgentModule abstract methods
    # ------------------------------------------------------------------

    def prepare(self, state: CyberGymState) -> str:
        """Return observation text from memory files + task description."""
        try:
            # Update memory files from current state
            self._memory_mgr.write_all(state)
            # Store memory manager on state for tool access
            state._memory_mgr = self._memory_mgr

            obs_result = self._render_observation(state, is_initial=True)
            prepared = _sanitize_model_text(obs_result.text)

            self._write_step_sidecar(
                state,
                "observation.md",
                prepared,
                context_payload=self._step_context_payload(state),
            )
            return prepared
        except Exception as exc:
            import logging, traceback
            logging.getLogger(__name__).error(
                "prepare() failed: %s: %s\n%s", type(exc).__name__, exc, traceback.format_exc(),
            )
            return "Observation: analysis preparation encountered an error. Proceed with investigation."

    def reduce(
        self,
        state: CyberGymState,
        observation: Any,
        decision: Decision,
    ) -> CyberGymState:
        """Reduce observation into the next state.

        Minimal: process action results, update basic state, no phase/checkpoint/family logic.
        """
        from .agent_impl.reducer import log_exchange

        # Extract action results from Observation
        action_results = []
        if isinstance(observation, Observation):
            action_results = observation.action_results or []
        elif isinstance(observation, dict):
            action_results = observation.get("action_results", [])
        else:
            action_results = getattr(observation, "action_results", [])

        for result in action_results:
            tr = ToolResult.from_value(result) if not isinstance(result, ToolResult) else result
            self._process_action_result(state, tr)
            if getattr(state, "stop_reason", ""):
                break

        # Update memory files from current state
        self._memory_mgr.write_all(state)

        # Debug logging
        log_exchange(self, state, decision, action_results)
        return state

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    def build_system_prompt(self, state: CyberGymState) -> str:
        """Build system prompt: persona + task + tools. No phase guidance."""
        parts = []

        parts.append(self.base_persona_prompt(state))
        parts.append(self.task_policy_prompt(state))
        parts.append(self.runtime_context_protocol_prompt(state))

        # Tool schema -- only inject as text when not using native function calling
        protocol = self.active_protocol()
        delivery = str(getattr(protocol, "tool_schema_delivery", "prompt_injection") or "prompt_injection")
        if delivery not in ("api_parameter", "hybrid"):
            tool_schema = self.render_tool_schema(protocol=protocol)
            if tool_schema:
                parts.append(f"\n## Available Tools\n{tool_schema}")

        parts.append(self.extra_instructions_prompt(state))
        parts.append(self.tool_usage_hint_prompt(state))

        # Multi-action guidance when the active protocol supports it
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

        # Recover structured dict from buffer
        if isinstance(output, str):
            action_id = (
                result.metadata.get("action_id")
                if isinstance(result.metadata, dict) else None
            )
            if action_id and action_id in self._structured_output_buffer:
                output = self._structured_output_buffer.pop(action_id)
            elif self._last_structured_output is not None:
                _, output = self._last_structured_output
                self._last_structured_output = None
            elif short_name == SUBMIT_POC_TOOL:
                from .submit_tool import get_last_submit_structured
                _meta = result.metadata if isinstance(result.metadata, dict) else {}
                recovered_submit = get_last_submit_structured(
                    getattr(state, "agent_id", ""),
                    poc_path=_meta.get("poc_path"),
                )
                if recovered_submit is not None:
                    output = recovered_submit

        # Dispatch to registered tool result handlers
        from .agent_impl.tools.result_processors import get_handler
        handler = get_handler(short_name)
        if handler is not None:
            handler(self, state, result, output)

    def _process_submit_result(self, state: CyberGymState, result, output) -> None:
        from .agent_impl.feedback.submit_processor import process_submit_result
        process_submit_result(self, state, result, output)

    # ------------------------------------------------------------------
    # Harness, corpus, and strategy detection
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
