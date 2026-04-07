"""Canonical agent module interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, TypeVar

from .decision import Decision
from .env import EnvSpec
from .history import History
from .memory import Memory
from .model_response import ModelResponse
from .task import Task, TaskBudget
from ..prompting import PromptBuildResult, PromptBuilder, PromptSpec


StateT = TypeVar("StateT")
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


class AgentModule(ABC, Generic[StateT, ObservationT, ActionT]):
    """Canonical policy contract for step-based agents."""

    name: str = "agent"

    def __init__(
        self,
        tool_registry: Any = None,
        toolset: Any = None,
        llm: Any = None,
        model_parser: Any = None,
        model_protocol: Any = None,
        memory: Memory | None = None,
        history: History | None = None,
        **config: Any,
    ):
        self.tool_registry = self._resolve_tool_registry(
            tool_registry=tool_registry, toolset=toolset
        )
        self.llm = llm
        self.model_parser = model_parser
        self.model_protocol = model_protocol
        self.memory = memory
        self.history = history
        self.config = config

    @abstractmethod
    def init_state(self, task: str, **kwargs: Any) -> StateT:
        """Create and return the initial typed state for a run."""

    def build_system_prompt(self, state: StateT) -> str | None:
        """Optional dynamic system prompt hook."""
        return self._build_default_prompt_bundle(state).system_prompt or None

    def base_persona_prompt(self, state: StateT) -> str:
        """Lightweight default authoring hook for agent persona and role."""
        _ = state
        return ""

    def task_policy_prompt(self, state: StateT) -> str:
        """Optional task-policy section appended after persona."""
        _ = state
        return ""

    def extra_instructions_prompt(self, state: StateT) -> str:
        """Optional extra instructions section appended near the end."""
        _ = state
        return ""

    def tool_usage_hint_prompt(self, state: StateT) -> str:
        """Optional lightweight tool-usage hint without overriding tool schema."""
        _ = state
        return ""

    def build_prompt_spec(self, state: StateT) -> PromptSpec:
        """Optional structured prompt hook used by the default prompt builder."""
        return PromptSpec(
            persona_prompt=self.base_persona_prompt(state),
            task_policy=self.task_policy_prompt(state),
            tool_usage_hint=self.tool_usage_hint_prompt(state),
            extra_instructions=self.extra_instructions_prompt(state),
            parser_feedback=self._state_prompt_attr(state, "parser_feedback"),
            continuation_feedback=self._state_prompt_attr(state, "timeout_feedback"),
            metadata={
                "agent_name": self.name,
                "prompt_authoring_mode": "default",
            },
        )

    def prepare(self, state: StateT) -> str:
        """Convert current state into model-ready text."""
        return str(state)

    def decide(
        self, state: StateT, observation: ObservationT
    ) -> Optional[Decision[ActionT]]:
        """Optional custom decision hook. Return None to use Engine model decision."""
        return None

    def interpret_model_response(
        self,
        state: StateT,
        observation: ObservationT,
        response: ModelResponse,
    ) -> Optional[Decision[ActionT]]:
        """Optional hook to interpret a normalized model response before parser execution."""
        _ = state
        _ = observation
        _ = response
        return None

    @abstractmethod
    def reduce(
        self,
        state: StateT,
        observation: ObservationT,
        decision: Decision[ActionT],
    ) -> StateT:
        """Reduce observation (including action/env outputs) into next state."""

    def should_stop(self, state: StateT) -> bool:
        """Optional additional stop condition."""
        return False

    def build_engine(self, **engine_kwargs: Any):
        """Create an Engine bound to this agent."""
        from ..engine.engine import Engine

        return Engine(agent=self, **engine_kwargs)

    def active_protocol(self) -> Any:
        runtime_protocol = getattr(self, "_runtime_protocol", None)
        if runtime_protocol is not None:
            return runtime_protocol
        return self.model_protocol

    def prompt_builder(self) -> PromptBuilder:
        """Return the prompt builder instance used for default prompt assembly."""
        return PromptBuilder()

    def render_tool_schema(self, protocol: Any = None) -> str:
        if self.tool_registry is None:
            return ""
        resolved = protocol if protocol is not None else self.active_protocol()
        if hasattr(self.tool_registry, "render_tool_schema"):
            return self.tool_registry.render_tool_schema(protocol=resolved)
        return self.tool_registry.get_tool_descriptions(protocol=resolved)

    def compose_system_prompt(self, base_prompt: str, protocol: Any = None) -> str:
        resolved = protocol if protocol is not None else self.active_protocol()
        try:
            spec = PromptSpec(persona_prompt=str(base_prompt or ""))
            result = self.prompt_builder().build(
                spec=spec,
                protocol=resolved,
                tool_registry=self.tool_registry,
                llm=self.llm,
                resolution_source="compose_system_prompt",
            )
            return result.system_prompt
        except Exception:
            return str(base_prompt or "")

    def build_prompt_bundle(self, state: StateT) -> PromptBuildResult:
        """Build the protocol-aware prompt bundle for the current model step."""
        if getattr(self, "_prompt_bundle_reentry_guard", False):
            return self._build_default_prompt_bundle(state)
        custom_system_prompt = (
            type(self).build_system_prompt is not AgentModule.build_system_prompt
        )
        if custom_system_prompt:
            setattr(self, "_prompt_bundle_reentry_guard", True)
            try:
                manual_prompt = type(self).build_system_prompt(self, state)
            finally:
                delattr(self, "_prompt_bundle_reentry_guard")
            return PromptBuildResult(
                system_prompt_static=str(manual_prompt or "").strip(),
                metadata={
                    "protocol": getattr(self.active_protocol(), "id", None),
                    "protocol_resolution_source": "agent_override",
                    "prompt_builder": "manual_build_system_prompt",
                    "prompt_builder_version": "manual",
                    "sections_used": ["manual_override"]
                    if str(manual_prompt or "").strip()
                    else [],
                    "tool_schema_style": getattr(
                        self.active_protocol(), "id", None
                    ),
                    "tool_schema_delivery": str(
                        getattr(
                            self.active_protocol(),
                            "tool_schema_delivery",
                            "prompt_injection",
                        )
                    ),
                    "repair_injected": False,
                    "continuation_injected": False,
                },
            )
        return self._build_default_prompt_bundle(state)

    def _build_default_prompt_bundle(self, state: StateT) -> PromptBuildResult:
        spec = self.build_prompt_spec(state)
        resolution_source = getattr(self, "_runtime_protocol_source", None)
        return self.prompt_builder().build(
            spec=spec,
            protocol=self.active_protocol(),
            tool_registry=self.tool_registry,
            llm=self.llm,
            state=state,
            resolution_source=str(resolution_source or "agent_default"),
        )

    def _state_prompt_attr(self, state: StateT, name: str) -> str:
        value = getattr(state, name, "")
        return str(value or "").strip() if value is not None else ""

    def _resolve_tool_registry(self, tool_registry: Any, toolset: Any) -> Any:
        if tool_registry is None and toolset is None:
            return None

        from .tool_registry import ToolRegistry

        if tool_registry is None:
            registry = ToolRegistry()
        elif isinstance(tool_registry, ToolRegistry):
            registry = tool_registry
        else:
            registry = ToolRegistry()
            registry.include_toolset(tool_registry)

        if toolset is not None:
            registry.include_toolset(toolset)
        return registry

    def run(
        self,
        task: str | Task,
        return_state: bool = False,
        hooks: List[Any] | None = None,
        render_hooks: List[Any] | None = None,
        engine_kwargs: Dict[str, Any] | None = None,
        workspace: str | None = None,
        max_steps: int | None = None,
        env: Any = None,
        parser: Any = None,
        protocol: Any = None,
        search: Any = None,
        critics: List[Any] | None = None,
        stop_criteria: List[Any] | None = None,
        history_policy: Any = None,
        context_config: Any = None,
        trace: Any = None,
        render: Any = None,
        trace_logdir: str = "./runs",
        trace_prefix: str | None = None,
        theme: str = "research",
        **state_kwargs: Any,
    ) -> Any:
        """Execute task with Engine using plain text objective or structured Task."""
        kwargs = dict(engine_kwargs or {})
        self._merge_run_defaults(
            kwargs=kwargs,
            task=task,
            workspace=workspace,
            env=env,
            parser=parser,
            protocol=protocol,
            search=search,
            critics=critics,
            stop_criteria=stop_criteria,
            history_policy=history_policy,
            context_config=context_config,
            trace=trace,
            render=render,
            trace_logdir=trace_logdir,
            trace_prefix=trace_prefix,
            theme=theme,
            hooks=hooks,
            render_hooks=render_hooks,
        )
        task = self._coerce_task(
            task=task, workspace=workspace, max_steps=max_steps, env=kwargs.get("env")
        )
        if max_steps is not None:
            state_kwargs.setdefault("max_steps", int(max_steps))
        engine = self.build_engine(**kwargs)
        result = engine.run(task, **state_kwargs)
        if return_state:
            return result
        return result.state.final_result

    def _coerce_task(
        self, task: str | Task, workspace: str | None, max_steps: int | None, env: Any
    ) -> str | Task:
        if isinstance(task, Task):
            if max_steps is None and (workspace is None or task.env_spec is not None):
                return task
            payload = task.to_dict()
            if max_steps is not None:
                budget = dict(payload.get("budget") or {})
                budget["max_steps"] = int(max_steps)
                payload["budget"] = budget
            if (
                workspace is not None
                and payload.get("env_spec") is None
                and env is not None
            ):
                payload["env_spec"] = {
                    "type": "host",
                    "config": {"workspace_root": workspace},
                }
            return Task.from_dict(payload)

        if max_steps is None and workspace is None:
            return task

        task_id = f"{self.name}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        return Task(
            id=task_id,
            objective=str(task),
            env_spec=(
                EnvSpec(type="host", config={"workspace_root": workspace})
                if workspace is not None and env is not None
                else None
            ),
            budget=TaskBudget(max_steps=max_steps),
        )

    def _merge_run_defaults(
        self,
        *,
        kwargs: Dict[str, Any],
        task: str | Task,
        workspace: str | None,
        env: Any,
        parser: Any,
        protocol: Any,
        search: Any,
        critics: List[Any] | None,
        stop_criteria: List[Any] | None,
        history_policy: Any,
        context_config: Any,
        trace: Any,
        render: Any,
        trace_logdir: str,
        trace_prefix: str | None,
        theme: str,
        hooks: List[Any] | None,
        render_hooks: List[Any] | None,
    ) -> None:
        hook_list = list(kwargs.get("hooks") or [])
        render_list = list(kwargs.get("render_hooks") or [])
        if hooks:
            hook_list.extend(hooks)
        if render_hooks:
            render_list.extend(render_hooks)

        if env is not None:
            kwargs["env"] = env
        elif workspace is not None and "env" not in kwargs:
            try:
                from ..kit.env import HostEnv

                kwargs["env"] = HostEnv(workspace_root=workspace)
            except Exception:
                pass

        if parser is not None:
            kwargs["parser"] = parser
        if protocol is not None:
            kwargs["protocol"] = protocol
        if search is not None:
            kwargs["search"] = search
        if critics is not None:
            kwargs["critics"] = critics
        if stop_criteria is not None:
            kwargs["stop_criteria"] = stop_criteria
        if history_policy is not None:
            kwargs["history_policy"] = history_policy
        if context_config is not None:
            kwargs["context_config"] = context_config

        trace_setting = trace
        if trace_setting is None and "trace_writer" not in kwargs:
            trace_setting = True
        if trace_setting:
            kwargs["trace_writer"] = self._trace_writer_from_input(
                trace=trace_setting,
                trace_logdir=trace_logdir,
                trace_prefix=trace_prefix,
            )

        render_setting = render
        if render_setting is None and "render_hooks" not in kwargs:
            render_setting = True
        if render_setting:
            render_obj = self._render_hook_from_input(
                render=render_setting, workspace=workspace, theme=theme
            )
            if isinstance(render_obj, list):
                render_list.extend(render_obj)
            elif render_obj is not None:
                render_list.append(render_obj)

        if hook_list:
            kwargs["hooks"] = hook_list
        if render_list:
            kwargs["render_hooks"] = render_list

    def _trace_writer_from_input(
        self, trace: Any, trace_logdir: str, trace_prefix: str | None
    ) -> Any:
        if trace is not True:
            return trace
        from ..trace import TraceWriter

        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        prefix = str(trace_prefix or self.name or self.__class__.__name__.lower())
        return TraceWriter(
            output_dir=str(Path(trace_logdir).expanduser().resolve()),
            run_id=f"{prefix}_{stamp}",
            strict_validate=True,
            metadata={"model_id": getattr(getattr(self, "llm", None), "model", None)},
        )

    def _render_hook_from_input(
        self, render: Any, workspace: str | None, theme: str
    ) -> Any:
        if render is not True:
            return render
        from ..render import ClaudeStyleHook

        output_jsonl = None
        if workspace:
            output_jsonl = str(
                Path(workspace).expanduser().resolve() / "render_events.jsonl"
            )
        return ClaudeStyleHook(output_jsonl=output_jsonl, theme=theme)


__all__ = ["AgentModule"]
