"""ClaudeCodeAgent — full Claude Code agent built on QitOS AgentModule."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from qitos import AgentModule, Decision, StateSchema
from qitos.kit import CodingToolSet
from qitos.kit.context import (
    build_coding_context,
    load_project_instructions,
)
from qitos.kit.permission import PermissionPipeline, PermissionMode

from .system_prompt import CLAUDE_CODE_SYSTEM_PROMPT


@dataclass
class ClaudeCodeState(StateSchema):
    """State for the Claude Code agent.

    Tracks:
    - mode: permission mode (default, plan, acceptEdits, bypassPermissions, auto)
    - todos: in-session todo list
    - active_tasks: task_id -> task dict for task management
    - cron_jobs: job_id -> job dict for scheduled tasks
    - worktree_path: path to isolated worktree (if any)
    - plan_mode: whether currently in plan mode (read-only)
    """

    mode: str = "default"
    todos: List[Dict[str, Any]] = field(default_factory=list)
    active_tasks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    cron_jobs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    worktree_path: Optional[str] = None
    plan_mode: bool = False


class ClaudeCodeAgent(AgentModule[ClaudeCodeState, Any, Any]):
    """Full Claude Code agent implemented as a QitOS AgentModule.

    This agent replicates Claude Code's core behavior:
    - Multi-step DECIDE → ACT → OBSERVE loop
    - Read-before-write enforcement
    - Plan mode (read-only, no write tools)
    - Project instructions from .qitos/instructions.md
    - Streaming token output
    - 30+ tools at Claude Code parity
    """

    def __init__(
        self,
        *,
        llm: Any = None,
        workspace_root: str = ".",
        max_steps: int = 50,
        model_parser: Any = None,
        model_protocol: Any = None,
        permission_mode: str = "default",
        include_mcp: bool = False,
    ):
        toolset = CodingToolSet(
            workspace_root=workspace_root,
            expose_modern_names=True,
        )
        super().__init__(
            llm=llm,
            toolset=[toolset],
            max_steps=max_steps,
            model_parser=model_parser,
            model_protocol=model_protocol,
        )
        self.max_steps = max_steps
        self.workspace_root = os.path.abspath(workspace_root)
        self.permission_mode = permission_mode
        self.include_mcp = include_mcp

        # Create permission pipeline and RBW enforcer
        mode_map = {
            "default": PermissionMode.DEFAULT,
            "plan": PermissionMode.PLAN,
            "acceptEdits": PermissionMode.ACCEPT_EDITS,
            "bypassPermissions": PermissionMode.BYPASS,
            "auto": PermissionMode.AUTO,
        }
        from qitos.kit.permission import ReadBeforeWriteEnforcer
        self._rbw_enforcer = ReadBeforeWriteEnforcer()

        # Create auto-classifier for AUTO mode
        auto_classifier = None
        if permission_mode == "auto":
            from qitos.kit.permission.auto_classifier import AutoPermissionClassifier
            auto_classifier = AutoPermissionClassifier(llm=llm)

        self.permission_pipeline = PermissionPipeline(
            mode=mode_map.get(permission_mode, PermissionMode.DEFAULT),
            rbw_enforcer=self._rbw_enforcer,
            auto_classifier=auto_classifier,
        )

    def init_state(self, task: str, **kwargs: Any) -> ClaudeCodeState:
        """Initialize state for a new run."""
        mode = kwargs.get("mode", self.permission_mode)
        return ClaudeCodeState(
            task=task,
            mode=mode,
            plan_mode=(mode == "plan"),
            max_steps=self.max_steps,
        )

    def build_system_prompt(self, state: ClaudeCodeState) -> str:
        """Build the system prompt with project instructions and dynamic context."""
        # STATIC: Core prompt + project instructions (cached across turns)
        static_parts = [CLAUDE_CODE_SYSTEM_PROMPT]

        instructions = load_project_instructions(self.workspace_root)
        if instructions:
            static_parts.append("\n\n## Project Instructions\n\n" + instructions)

        # DYNAMIC: Environment + git status + date (changes per turn)
        dynamic_parts = []
        dynamic_parts.append(build_coding_context(self.workspace_root))
        dynamic_parts.append(f"# Current Date\nToday's date is {date.today().isoformat()}.")

        # Mode-specific instructions
        if state.plan_mode:
            dynamic_parts.append(
                "## Plan Mode\n\n"
                "You are in plan mode. You may ONLY read files and search the codebase. "
                "Do NOT make any edits, writes, or execute any commands that modify the filesystem. "
                "Provide your analysis and plan, then use exit_plan_mode when done."
            )

        return "\n\n".join(static_parts + dynamic_parts)

    def reduce(
        self,
        state: ClaudeCodeState,
        observation: Any,
        decision: Optional[Decision] = None,
    ) -> ClaudeCodeState:
        """Update state after each step."""
        return state

    def should_stop(self, state: ClaudeCodeState) -> bool:
        """Check if the agent should stop."""
        return False  # Engine handles max_steps and stop criteria
