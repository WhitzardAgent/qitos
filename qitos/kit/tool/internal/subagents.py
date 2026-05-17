"""Built-in sub-agent types for the Agent tool.

Each sub-agent is an AgentModule subclass optimized for a specific task:
- ExploreAgent: Fast codebase search (Read, Glob, Grep only, low max_steps)
- PlanAgent: Read-only architecture analysis (plan mode, no write tools)
- GeneralAgent: General-purpose agent with full tool access
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos import AgentModule, Decision, StateSchema
from qitos.kit import CodingToolSet


@dataclass
class ExploreState(StateSchema):
    """State for the Explore sub-agent."""

    findings: List[str] = field(default_factory=list)
    files_checked: List[str] = field(default_factory=list)


class ExploreAgent(AgentModule[ExploreState, Any, Any]):
    """Fast codebase search agent with only read tools.

    Optimized for speed: Read, Glob, Grep only, max_steps=8.
    """

    name = "explore"

    def __init__(
        self,
        *,
        llm: Any = None,
        workspace_root: str = ".",
        max_steps: int = 8,
        model_parser: Any = None,
        model_protocol: Any = None,
    ):
        toolset = CodingToolSet(
            workspace_root=workspace_root,
            profile="codebase",
            expose_modern_names=True,
        )
        super().__init__(
            llm=llm,
            toolset=[toolset],
            model_parser=model_parser,
            model_protocol=model_protocol,
        )
        self.workspace_root = workspace_root
        self.max_steps = max_steps

    def init_state(self, task: str, **kwargs: Any) -> ExploreState:
        return ExploreState()

    def build_system_prompt(self, state: ExploreState) -> str:
        return (
            "You are a fast codebase search agent. Your job is to quickly find "
            "relevant code and information. Use Glob to find files, Grep to search "
            "content, and Read to inspect files. Be concise and focused. "
            "Report your findings clearly. Do NOT edit any files.\n\n"
            "When reporting findings:\n"
            "- Include file paths and line numbers for every relevant result\n"
            "- Quote the specific code that's relevant, not just the file name\n"
            "- If you find nothing, say so explicitly — don't guess or fabricate\n"
            "- Focus on what was asked, not tangentially related code"
        )

    def reduce(
        self,
        state: ExploreState,
        observation: Any,
        decision: Optional[Decision] = None,
    ) -> ExploreState:
        return state

    def should_stop(self, state: ExploreState) -> bool:
        return False


@dataclass
class PlanState(StateSchema):
    """State for the Plan sub-agent."""

    plan_sections: List[str] = field(default_factory=list)
    files_analyzed: List[str] = field(default_factory=list)


class PlanAgent(AgentModule[PlanState, Any, Any]):
    """Read-only architecture and planning agent.

    In plan mode: no write tools, returns structured plan.
    """

    name = "plan"

    def __init__(
        self,
        *,
        llm: Any = None,
        workspace_root: str = ".",
        max_steps: int = 12,
        model_parser: Any = None,
        model_protocol: Any = None,
    ):
        toolset = CodingToolSet(
            workspace_root=workspace_root,
            profile="codebase",
            expose_modern_names=True,
        )
        super().__init__(
            llm=llm,
            toolset=[toolset],
            model_parser=model_parser,
            model_protocol=model_protocol,
        )
        self.workspace_root = workspace_root
        self.max_steps = max_steps

    def init_state(self, task: str, **kwargs: Any) -> PlanState:
        return PlanState()

    def build_system_prompt(self, state: PlanState) -> str:
        return (
            "You are an architecture planning agent. Analyze the codebase and "
            "provide a structured implementation plan. You are in read-only mode — "
            "do NOT make any changes to files.\n\n"
            "Your plan should include:\n"
            "1. Context — why this change is being made (the problem or need)\n"
            "2. Files to modify — specific file paths and what changes are needed in each\n"
            "3. Implementation steps — in order, with dependencies between steps noted\n"
            "4. Risks — what could go wrong and how to mitigate\n\n"
            "Rules:\n"
            "- Only recommend modifying files you have actually read\n"
            "- Include specific function names, class names, and line numbers\n"
            "- Do not suggest speculative changes \"while you're at it\"\n"
            "- If the task is unclear, state what assumptions you're making\n\n"
            "Present your final plan as a structured document."
        )

    def reduce(
        self,
        state: PlanState,
        observation: Any,
        decision: Optional[Decision] = None,
    ) -> PlanState:
        return state

    def should_stop(self, state: PlanState) -> bool:
        return False


@dataclass
class GeneralState(StateSchema):
    """State for the General-purpose sub-agent."""

    files_read: List[str] = field(default_factory=list)


class GeneralAgent(AgentModule[GeneralState, Any, Any]):
    """General-purpose agent with full tool access.

    Used for complex multi-step tasks that require reading,
    editing, and executing commands.
    """

    name = "general"

    def __init__(
        self,
        *,
        llm: Any = None,
        workspace_root: str = ".",
        max_steps: int = 15,
        model_parser: Any = None,
        model_protocol: Any = None,
    ):
        toolset = CodingToolSet(
            workspace_root=workspace_root,
            expose_modern_names=True,
        )
        super().__init__(
            llm=llm,
            toolset=[toolset],
            model_parser=model_parser,
            model_protocol=model_protocol,
        )
        self.workspace_root = workspace_root
        self.max_steps = max_steps

    def init_state(self, task: str, **kwargs: Any) -> GeneralState:
        return GeneralState()

    def build_system_prompt(self, state: GeneralState) -> str:
        return (
            "You are a general-purpose coding agent. You can read files, search code, "
            "edit files, and run commands. Complete the task you are given efficiently.\n\n"
            "Rules:\n"
            "- Read files before editing them\n"
            "- Make targeted changes, not sweeping refactors\n"
            "- Report what you did clearly and concisely\n"
            "- If you encounter errors, diagnose and fix them"
        )

    def reduce(
        self,
        state: GeneralState,
        observation: Any,
        decision: Optional[Decision] = None,
    ) -> GeneralState:
        return state

    def should_stop(self, state: GeneralState) -> bool:
        return False


__all__ = ["ExploreAgent", "PlanAgent", "GeneralAgent"]
