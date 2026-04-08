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
from .spec import ExperimentSpec, RunSpec
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
            builder = self.prompt_builder()
            scaffold_spec = PromptSpec(
                parser_feedback=self._state_prompt_attr(state, "parser_feedback"),
                continuation_feedback=self._state_prompt_attr(state, "timeout_feedback"),
                include_tool_schema=False,
                include_contract=False,
                metadata={
                    "agent_name": self.name,
                    "prompt_authoring_mode": "manual_build_system_prompt",
                },
            )
            scaffold = builder.build(
                spec=scaffold_spec,
                protocol=self.active_protocol(),
                tool_registry=self.tool_registry,
                llm=self.llm,
                state=state,
                resolution_source="agent_override",
            )
            manual_text = str(manual_prompt or "").strip()
            metadata = dict(scaffold.metadata or {})
            metadata.update(
                {
                    "protocol": getattr(self.active_protocol(), "id", None),
                    "protocol_resolution_source": "agent_override",
                    "prompt_builder": "manual_build_system_prompt",
                    "prompt_builder_version": "manual",
                    "sections_used": (
                        ["manual_override"] if manual_text else []
                    ),
                    "tool_schema_style": getattr(
                        self.active_protocol(), "id", None
                    ),
                    "prompt_hash_static": builder._hash(manual_text),
                    "prompt_hash_full": builder._hash(manual_text),
                    "estimated_tokens_static": builder._estimate_tokens(
                        self.llm, manual_text
                    ),
                    "estimated_tokens_full": builder._estimate_tokens(
                        self.llm, manual_text
                    ),
                }
            )
            return PromptBuildResult(
                system_prompt_static=manual_text,
                message_injections=list(scaffold.message_injections),
                user_content_blocks=list(scaffold.user_content_blocks),
                tool_schema_payload=scaffold.tool_schema_payload,
                metadata=metadata,
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
        run_spec: RunSpec | Dict[str, Any] | None = None,
        experiment_spec: ExperimentSpec | Dict[str, Any] | None = None,
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
        resolved_run_spec = self._resolve_run_spec(
            run_spec=run_spec,
            engine=engine,
            task=task if isinstance(task, Task) else None,
        )
        resolved_experiment_spec = self._resolve_experiment_spec(
            experiment_spec=experiment_spec,
            task=task if isinstance(task, Task) else None,
            run_spec=resolved_run_spec,
        )
        setattr(engine, "run_spec", resolved_run_spec)
        setattr(engine, "experiment_spec", resolved_experiment_spec)
        self._attach_trace_specs(
            engine=engine,
            run_spec=resolved_run_spec,
            experiment_spec=resolved_experiment_spec,
        )
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

    def _resolve_run_spec(
        self, *, run_spec: RunSpec | Dict[str, Any] | None, engine: Any, task: Task | None
    ) -> RunSpec:
        spec = RunSpec.from_value(run_spec)
        llm = getattr(self, "llm", None)
        model_name = getattr(llm, "model", None) or getattr(llm, "model_name", None)
        harness_metadata = (
            dict(getattr(llm, "qitos_harness_metadata", {}) or {})
            if llm is not None
            else {}
        )
        if not spec.model_name and model_name:
            spec.model_name = str(model_name)
        if (
            not spec.model_family
            and isinstance(harness_metadata.get("family_preset"), str)
            and harness_metadata.get("family_preset")
        ):
            spec.model_family = str(harness_metadata.get("family_preset"))
        if not spec.model_family and spec.model_name:
            spec.model_family = RunSpec.infer(model_name=spec.model_name).model_family
        if not spec.trace_schema_version:
            spec.trace_schema_version = str(
                getattr(getattr(engine, "trace_writer", None), "schema_version", "v1")
                or "v1"
            )
        if not spec.prompt_protocol:
            try:
                protocol = engine.resolve_protocol()
                spec.prompt_protocol = getattr(protocol, "id", None)
            except Exception:
                spec.prompt_protocol = None
        if not spec.parser_name:
            parser = getattr(engine, "parser", None) or getattr(self, "model_parser", None)
            spec.parser_name = parser.__class__.__name__ if parser is not None else None
        if not spec.toolset_name and getattr(engine, "tool_registry", None) is not None:
            spec.toolset_name = engine.tool_registry.__class__.__name__
        if not spec.tool_manifest:
            spec.tool_manifest = self._describe_tool_manifest(
                getattr(engine, "tool_registry", None)
            )
        if not spec.environment:
            spec.environment = self._environment_summary(
                env=getattr(engine, "env", None), task=task
            )
        if spec.seed is None and isinstance(getattr(self, "config", None), dict):
            seed = self.config.get("seed")
            spec.seed = int(seed) if seed is not None else None
        if not spec.stop_criteria:
            criteria = list(getattr(engine, "stop_criteria", []) or [])
            spec.stop_criteria = [item.__class__.__name__ for item in criteria]
        if harness_metadata:
            merged_metadata = dict(spec.metadata or {})
            for key in ("family_preset", "adapter_kind", "protocol", "parser"):
                value = harness_metadata.get(key)
                if value and key not in merged_metadata:
                    merged_metadata[key] = value
            if (
                "tool_policy" not in merged_metadata
                and isinstance(harness_metadata.get("tool_policy"), dict)
            ):
                merged_metadata["tool_policy"] = dict(
                    harness_metadata.get("tool_policy") or {}
                )
            if (
                "context_policy" not in merged_metadata
                and isinstance(harness_metadata.get("context_policy"), dict)
            ):
                merged_metadata["context_policy"] = dict(
                    harness_metadata.get("context_policy") or {}
                )
            if (
                "harness_policy" not in merged_metadata
                and isinstance(harness_metadata, dict)
            ):
                merged_metadata["harness_policy"] = dict(harness_metadata)
            spec.metadata = merged_metadata
        if task is not None:
            benchmark_name = (
                task.metadata.get("benchmark")
                or task.inputs.get("benchmark")
                or task.metadata.get("benchmark_name")
            )
            benchmark_split = (
                task.metadata.get("split")
                or task.inputs.get("split")
                or task.metadata.get("benchmark_split")
            )
            if not spec.benchmark_name and benchmark_name:
                spec.benchmark_name = str(benchmark_name)
            if not spec.benchmark_split and benchmark_split:
                spec.benchmark_split = str(benchmark_split)
        return spec

    def _resolve_experiment_spec(
        self,
        *,
        experiment_spec: ExperimentSpec | Dict[str, Any] | None,
        task: Task | None,
        run_spec: RunSpec,
    ) -> ExperimentSpec | None:
        spec = ExperimentSpec.from_value(experiment_spec)
        if spec is None and task is None and not run_spec.benchmark_name:
            return None
        if spec is None:
            spec = ExperimentSpec()
        if not spec.benchmark_name:
            spec.benchmark_name = run_spec.benchmark_name
        if not spec.benchmark_split:
            spec.benchmark_split = run_spec.benchmark_split
        if not spec.name and spec.benchmark_name:
            split = spec.benchmark_split or "unspecified"
            spec.name = f"{spec.benchmark_name}:{split}"
        if task is not None:
            spec.run_defaults.setdefault("max_steps", task.budget.max_steps)
            spec.run_defaults.setdefault(
                "max_runtime_seconds", task.budget.max_runtime_seconds
            )
            spec.run_defaults.setdefault("max_tokens", task.budget.max_tokens)
            benchmark_meta = dict(task.metadata or {})
            benchmark_meta.pop("raw_record", None)
            if benchmark_meta:
                spec.benchmark_metadata = {
                    **dict(spec.benchmark_metadata or {}),
                    **benchmark_meta,
                }
        return spec

    def _attach_trace_specs(
        self,
        *,
        engine: Any,
        run_spec: RunSpec,
        experiment_spec: ExperimentSpec | None,
    ) -> None:
        trace_writer = getattr(engine, "trace_writer", None)
        if trace_writer is None:
            return
        trace_writer.metadata.update(
            {
                "git_sha": run_spec.git_sha,
                "package_version": run_spec.package_version,
                "benchmark_name": run_spec.benchmark_name,
                "benchmark_split": run_spec.benchmark_split,
                "model_family": run_spec.model_family,
                "prompt_protocol": run_spec.prompt_protocol,
                "parser_name": run_spec.parser_name,
                "tool_manifest": list(run_spec.tool_manifest or []),
                "run_spec": run_spec.to_dict(),
                "experiment_spec": (
                    experiment_spec.to_dict() if experiment_spec is not None else None
                ),
                "official_run": run_spec.is_official_run(),
                "replay_mode": "best_effort",
                "replay_note": (
                    "QitOS records config, seed, git SHA, prompt/parser metadata, "
                    "and trace artifacts for research-grade replay, but remote "
                    "models and external systems may remain non-deterministic."
                ),
            }
        )

    def _describe_tool_manifest(self, tool_registry: Any) -> List[Dict[str, Any]]:
        if tool_registry is None or not hasattr(tool_registry, "list_tools"):
            return []
        out: List[Dict[str, Any]] = []
        try:
            for name in tool_registry.list_tools():
                if hasattr(tool_registry, "describe_tool"):
                    desc = tool_registry.describe_tool(name)
                    if isinstance(desc, dict):
                        out.append({str(k): desc[k] for k in desc})
                        continue
                out.append({"name": str(name)})
        except Exception:
            return []
        return out

    def _environment_summary(self, env: Any, task: Task | None) -> Dict[str, Any]:
        if env is not None:
            payload = {
                "class": env.__class__.__name__,
                "workspace_root": getattr(env, "workspace_root", None),
            }
            env_type = getattr(env, "type", None)
            if env_type:
                payload["type"] = env_type
            return {k: v for k, v in payload.items() if v is not None}
        if task is not None and task.env_spec is not None:
            return task.env_spec.to_dict() if hasattr(task.env_spec, "to_dict") else {
                "type": task.env_spec.type,
                "config": dict(task.env_spec.config or {}),
                "capabilities": list(task.env_spec.capabilities or []),
                "metadata": dict(task.env_spec.metadata or {}),
            }
        return {}


__all__ = ["AgentModule"]
