"""AgentTool — generic sub-agent spawning tool for QitOS.

This tool spawns a sub-agent (AgentModule subclass) and runs it with a
specific task. It supports:
- Multiple sub-agent types via a registry
- Background execution via ThreadPoolExecutor
- Worktree isolation for parallel work

Usage::

    from qitos.kit.tool.agent import AgentTool
    from qitos.kit.agent.worktree_manager import WorktreeManager

    # Register agent types
    AgentTool.register_agent_type("explore", ExploreAgent)
    AgentTool.register_agent_type("plan", PlanAgent)

    # Use in toolset
    tool = AgentTool(workspace_root=".")
"""

from __future__ import annotations

import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

from ....core.tool import BaseTool, ToolPermission, ToolSpec
from ....core.agent_module import AgentModule


@dataclass
class AgentResult:
    """Result from a sub-agent run."""

    agent_type: str
    task: str
    success: bool
    output: Any = None
    error: Optional[str] = None
    workspace_root: Optional[str] = None
    run_id: Optional[str] = None


class AgentTool(BaseTool):
    """Generic sub-agent spawning tool.

    Spawns a sub-agent (AgentModule subclass) with a given prompt,
    runs it, and returns the result. Supports background execution
    and worktree isolation.
    """

    # Agent type registry (class-level)
    _agent_types: Dict[str, Type[AgentModule]] = {}

    def __init__(
        self,
        workspace_root: str = ".",
        model_factory: Optional[Callable[..., Any]] = None,
        max_background_workers: int = 4,
    ):
        self.workspace_root = workspace_root
        self.model_factory = model_factory
        self._executor = ThreadPoolExecutor(max_workers=max_background_workers)
        self._background_tasks: Dict[str, Future] = {}
        self._background_results: Dict[str, AgentResult] = {}

        spec = ToolSpec(
            name="Agent",
            description=(
                "Launch a sub-agent to handle a specific task. "
                "Sub-agent types: general-purpose (full tools), "
                "Explore (fast codebase search), Plan (read-only architecture), "
                "claude-code-guide (documentation help)."
            ),
            permissions=ToolPermission(),
        )
        super().__init__(spec=spec)

    @classmethod
    def register_agent_type(
        cls, name: str, agent_class: Type[AgentModule]
    ) -> None:
        """Register an agent type by name."""
        cls._agent_types[name] = agent_class

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        prompt = args.get("prompt", "")
        subagent_type = args.get("subagent_type", "general-purpose")
        description = args.get("description", "")
        run_in_background = args.get("run_in_background", False)
        isolation = args.get("isolation", None)

        if not prompt:
            return {"status": "error", "error": "prompt is required"}

        # Resolve workspace
        workspace = self.workspace_root
        if isolation == "worktree":
            try:
                from ....kit.agent.worktree_manager import WorktreeManager

                wm = WorktreeManager(workspace_root=workspace)
                wt_name = f"agent-{uuid.uuid4().hex[:8]}"
                workspace = wm.create_worktree(wt_name)
            except Exception as exc:
                return {
                    "status": "error",
                    "error": f"Failed to create worktree: {exc}",
                }

        # Create and run sub-agent
        if run_in_background:
            task_id = f"agent-{uuid.uuid4().hex[:8]}"
            future = self._executor.submit(
                self._run_agent, subagent_type, prompt, workspace
            )
            self._background_tasks[task_id] = future

            # Attach callback to store result
            def _on_done(fut: Future, tid: str = task_id) -> None:
                try:
                    self._background_results[tid] = fut.result()
                except Exception as exc:
                    self._background_results[tid] = AgentResult(
                        agent_type=subagent_type,
                        task=prompt,
                        success=False,
                        error=str(exc),
                    )

            future.add_done_callback(_on_done)

            return {
                "status": "running",
                "task_id": task_id,
                "agent_type": subagent_type,
                "description": description,
                "workspace": workspace if isolation == "worktree" else None,
            }

        # Synchronous execution
        result = self._run_agent(subagent_type, prompt, workspace)
        return {
            "status": "success" if result.success else "error",
            "agent_type": result.agent_type,
            "output": result.output,
            "error": result.error,
        }

    def _run_agent(
        self, agent_type: str, prompt: str, workspace_root: str
    ) -> AgentResult:
        """Instantiate and run a sub-agent."""
        agent_class = self._agent_types.get(agent_type)
        if agent_class is None:
            return AgentResult(
                agent_type=agent_type,
                task=prompt,
                success=False,
                error=f"Unknown agent type: {agent_type}. "
                f"Available: {list(self._agent_types.keys())}",
            )

        try:
            # Build the agent
            kwargs: Dict[str, Any] = {"workspace_root": workspace_root}
            if self.model_factory:
                kwargs["llm"] = self.model_factory()

            agent = agent_class(**kwargs)
            result = agent.run(task=prompt)

            output = None
            if hasattr(result, "final_answer"):
                output = result.final_answer
            elif hasattr(result, "output"):
                output = result.output
            elif hasattr(result, "state") and hasattr(result.state, "final_result"):
                output = result.state.final_result

            return AgentResult(
                agent_type=agent_type,
                task=prompt,
                success=True,
                output=output,
                workspace_root=workspace_root,
            )
        except Exception as exc:
            return AgentResult(
                agent_type=agent_type,
                task=prompt,
                success=False,
                error=str(exc),
            )

    def get_background_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Check the result of a background agent task."""
        result = self._background_results.get(task_id)
        if result is None:
            future = self._background_tasks.get(task_id)
            if future is not None and not future.done():
                return {"status": "running", "task_id": task_id}
            return None
        return {
            "status": "success" if result.success else "error",
            "agent_type": result.agent_type,
            "output": result.output,
            "error": result.error,
        }
