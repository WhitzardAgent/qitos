"""FanOutTool: parallel delegation of multiple subtasks to sub-agents."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ...core.agent_spec import AgentSpec, AgentRegistry, ContextStrategy
from ...core.tool import BaseTool, ToolSpec

if TYPE_CHECKING:
    from ...engine.engine import Engine
    from ...trace.writer import TraceWriter

MAX_DELEGATE_DEPTH = 3


class FanOutTool(BaseTool):
    """Parallel delegation: fan out multiple subtasks, fan in aggregated results.

    The parent agent calls this tool with a list of subtasks. Each subtask is
    dispatched to a sub-agent in parallel via ThreadPoolExecutor. All sub-agents
    run independently. Results are collected and aggregated before returning.
    """

    def __init__(self, agent_registry: AgentRegistry, max_workers: int = 4, per_task_timeout: float = 120.0):
        self.agent_registry = agent_registry
        self._max_workers = max_workers
        self._per_task_timeout = per_task_timeout
        tool_spec = ToolSpec(
            name="fanout",
            description=(
                "Delegate multiple subtasks to sub-agents in parallel. "
                "Each subtask runs independently. Returns aggregated results "
                "from all sub-agents."
            ),
            parameters={
                "tasks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent": {
                                "type": "string",
                                "description": "Agent name from registry",
                            },
                            "task": {
                                "type": "string",
                                "description": "Subtask description",
                            },
                        },
                        "required": ["agent", "task"],
                    },
                    "description": "List of subtasks to delegate in parallel",
                },
            },
            required=["tasks"],
            timeout_s=300.0,
            max_retries=0,
            concurrency_safe=True,
            supports_background=True,
        )
        super().__init__(tool_spec)
        # Override description after BaseTool.__init__
        self.spec.description = tool_spec.description

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        runtime_context = runtime_context or {}
        tasks = args.get("tasks", [])
        if not tasks:
            return {"status": "error", "message": "tasks is required and must be non-empty"}

        current_depth = int(runtime_context.get("delegate_depth", 0))
        if current_depth >= MAX_DELEGATE_DEPTH:
            return {
                "status": "error",
                "message": f"Maximum delegate depth ({MAX_DELEGATE_DEPTH}) exceeded",
            }

        trace_writer = runtime_context.get("trace_writer")

        self._emit_event(trace_writer, "FANOUT_START", {"task_count": len(tasks)})

        results: Dict[str, Any] = {}
        overall_deadline = time.monotonic() + self.spec.timeout_s
        with ThreadPoolExecutor(max_workers=self._max_workers) as pool:
            futures = {}
            for i, task_spec in enumerate(tasks):
                agent_name = task_spec.get("agent", "")
                task_text = str(task_spec.get("task", "")).strip()
                if not agent_name or not task_text:
                    results[f"invalid_{i}"] = {
                        "status": "error",
                        "message": "Each task requires 'agent' and 'task' fields",
                    }
                    continue
                try:
                    spec = self.agent_registry.resolve(agent_name)
                except KeyError:
                    results[f"{agent_name}_{i}"] = {
                        "status": "error",
                        "message": f"Agent '{agent_name}' not found in registry",
                    }
                    continue
                prepared_task = self._prepare_task(spec, task_text, runtime_context)
                task_deadline = time.monotonic() + self._per_task_timeout
                future = pool.submit(
                    self._run_sub_agent,
                    spec,
                    prepared_task,
                    runtime_context,
                    current_depth,
                    i,
                    task_deadline,
                )
                futures[future] = (agent_name, i)

            remaining_timeout = max(0.0, overall_deadline - time.monotonic())
            try:
                for future in as_completed(futures, timeout=remaining_timeout):
                    agent_name, idx = futures[future]
                    key = f"{agent_name}_{idx}"
                    try:
                        results[key] = future.result(timeout=0.1)
                    except TimeoutError:
                        results[key] = {
                            "status": "error",
                            "message": f"Task timed out after {self._per_task_timeout}s",
                        }
                    except Exception as exc:
                        results[key] = {"status": "error", "message": str(exc)}
            except TimeoutError:
                pass  # Overall deadline reached; collect what we have

            # Cancel any futures that didn't complete in time
            for future in futures:
                if not future.done():
                    future.cancel()
                    agent_name, idx = futures[future]
                    key = f"{agent_name}_{idx}"
                    if key not in results:
                        results[key] = {
                            "status": "error",
                            "message": f"Task timed out (overall timeout {self.spec.timeout_s}s)",
                        }

        self._emit_event(trace_writer, "FANOUT_END", {
            "total": len(results),
            "succeeded": sum(1 for r in results.values() if r.get("status") == "success"),
        })

        return {
            "status": "success" if any(r.get("status") == "success" for r in results.values()) else "error",
            "results": results,
            "summary": self._aggregate(results),
        }

    def _run_sub_agent(
        self,
        spec: AgentSpec,
        task: str,
        runtime_context: Dict[str, Any],
        depth: int,
        idx: int,
        task_deadline: float = 0.0,
    ) -> Dict[str, Any]:
        """Run a single sub-agent synchronously (called from thread pool)."""
        try:
            if task_deadline > 0 and time.monotonic() >= task_deadline:
                return {
                    "status": "error",
                    "agent": spec.name,
                    "message": f"Task timed out after {self._per_task_timeout}s",
                }
            sub_engine = self._build_sub_engine(spec, runtime_context, depth, idx)
            result = sub_engine.run(task)
            return {
                "status": "success" if result.state.stop_reason == "final" else "partial",
                "agent": spec.name,
                "final_result": result.state.final_result or "",
                "steps": result.step_count,
                "stop_reason": str(result.state.stop_reason or ""),
            }
        except Exception as exc:
            return {"status": "error", "agent": spec.name, "message": str(exc)}

    def _build_sub_engine(
        self,
        spec: AgentSpec,
        runtime_context: Dict[str, Any],
        depth: int,
        idx: int,
    ) -> Engine:
        from ...engine.engine import Engine
        from ...engine.states import RuntimeBudget

        sub_agent = spec.agent
        env = runtime_context.get("env") if spec.shared_env else None
        budget = RuntimeBudget(max_steps=spec.max_steps_override or 10)
        trace_writer = self._build_sub_trace_writer(
            runtime_context.get("trace_writer"), spec.name, idx
        )
        return Engine(
            agent=sub_agent,
            budget=budget,
            env=env,
            trace_writer=trace_writer,
            delegate_depth=depth + 1,
            shared_memory=spec.shared_memory,
        )

    def _build_sub_trace_writer(
        self,
        parent_trace_writer: Optional[TraceWriter],
        agent_name: str,
        idx: int,
    ) -> Optional[TraceWriter]:
        if parent_trace_writer is None:
            return None

        from ...trace.writer import TraceWriter

        parent_run_id = getattr(parent_trace_writer, "run_id", "")
        output_dir = getattr(parent_trace_writer, "output_dir", "runs")
        sub_run_id = f"{parent_run_id}__fanout_{agent_name}_{idx}"
        metadata = dict(getattr(parent_trace_writer, "metadata", {}) or {})
        metadata["parent_run_id"] = parent_run_id
        metadata["agent_name"] = agent_name
        metadata["fanout_index"] = idx
        return TraceWriter(
            output_dir=output_dir,
            run_id=sub_run_id,
            metadata=metadata,
        )

    def _prepare_task(
        self,
        spec: AgentSpec,
        task: str,
        runtime_context: Dict[str, Any],
    ) -> str:
        """Apply ContextStrategy to the task before passing to sub-agent."""
        if spec.context_strategy == ContextStrategy.ISOLATED:
            return task
        state = runtime_context.get("state")
        if state is None:
            return task
        scratchpad = getattr(state, "scratchpad", None)
        if not scratchpad:
            return task
        if spec.context_strategy == ContextStrategy.FULL:
            prefix = "Parent agent context:\n" + "\n".join(scratchpad[-16:]) + "\n\nYour task:\n"
            return prefix + task
        if spec.context_strategy == ContextStrategy.SUMMARY:
            prefix = "Parent agent summary:\n" + "\n".join(scratchpad[-4:]) + "\n\nYour task:\n"
            return prefix + task
        return task

    def _aggregate(self, results: Dict[str, Any]) -> str:
        """Reduce sub-agent results into a summary string."""
        successful = [r for r in results.values() if r.get("status") == "success"]
        partial = [r for r in results.values() if r.get("status") == "partial"]
        errors = [r for r in results.values() if r.get("status") == "error"]
        lines = [
            f"Total: {len(results)} tasks, "
            f"{len(successful)} succeeded, "
            f"{len(partial)} partial, "
            f"{len(errors)} failed."
        ]
        for key, r in results.items():
            result_text = r.get("final_result", r.get("message", ""))
            lines.append(f"- {key}: {str(result_text)[:200]}")
        return "\n".join(lines)

    def _emit_event(
        self,
        trace_writer: Optional[TraceWriter],
        phase: str,
        payload: Dict[str, Any],
    ) -> None:
        if trace_writer is None:
            return
        from ...trace.events import TraceEvent
        from datetime import datetime, timezone

        event = TraceEvent(
            run_id=getattr(trace_writer, "run_id", ""),
            step_id=0,
            phase=phase,
            ok=True,
            payload=payload,
            error=None,
            ts=datetime.now(timezone.utc).isoformat(),
        )
        trace_writer.write_event(event)
