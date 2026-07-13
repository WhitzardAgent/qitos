from dataclasses import dataclass
from typing import Any

from qitos import Action, AgentModule, Decision, Engine, StateSchema, ToolRegistry, tool
from qitos.core.tool_result import ToolResult
from qitos.engine._action_runtime import _ActionRuntime
from qitos.engine._env_runtime import _EnvRuntime
from qitos.engine.hooks import EngineHook, HookContext
from qitos.engine.states import RuntimeBudget


def _summary_payload() -> dict[str, str]:
    return {
        "model_summary": "## STATIC_ROUTE · partial\n- answer: entry -> target",
        "artifact_path": ".agent/evidence/static/raw.json",
    }


def test_action_history_projects_model_summary_without_losing_raw_result() -> None:
    runtime = _ActionRuntime.__new__(_ActionRuntime)
    result = ToolResult(
        output=_summary_payload(), metadata={"tool_name": "STATIC_ROUTE"}
    )

    assert runtime._model_visible_tool_output("STATIC_ROUTE", result.output) == result.output["model_summary"]
    visible = runtime._model_visible_tool_result_dict(result, "STATIC_ROUTE")
    assert visible["output"] == result.output["model_summary"]
    assert "artifact_path" not in str(visible)
    assert result.output["artifact_path"].endswith("raw.json")


def test_large_raw_result_budgets_summary_before_truncation() -> None:
    runtime = _ActionRuntime.__new__(_ActionRuntime)
    payload = {
        **_summary_payload(),
        "findings": [{"body": "x" * 100_000}],
    }
    visible, has_summary = runtime._tool_output_for_budget(payload)
    assert has_summary is True
    assert visible == payload["model_summary"]
    assert "artifact_path" not in visible


def test_env_observation_projects_model_summary() -> None:
    runtime = _EnvRuntime.__new__(_EnvRuntime)
    result = ToolResult(
        output=_summary_payload(), metadata={"tool_name": "gdb_debug"}
    )
    visible = runtime._model_visible_tool_result_dict(result)
    assert visible["output"] == result.output["model_summary"]
    assert "artifact_path" not in str(visible)


def test_submit_redaction_takes_precedence_over_model_summary() -> None:
    runtime = _ActionRuntime.__new__(_ActionRuntime)
    payload = {
        "model_summary": "do not expose this",
        "status": "success",
        "raw_output": "safe visible result",
        "fixed_side_verdict": "private",
    }
    visible = runtime._model_visible_tool_output("submit_poc", payload)
    assert visible["output"] == "safe visible result"
    assert "model_summary" not in visible
    assert "fixed_side_verdict" not in visible


@dataclass
class _ProjectionState(StateSchema):
    pass


class _ProjectionAgent(AgentModule[_ProjectionState, dict[str, Any], Action]):
    def __init__(self) -> None:
        registry = ToolRegistry()

        @tool(name="gdb_debug")
        def gdb_debug() -> dict[str, str]:
            return {
                "raw_artifact_path": ".agent/evidence/gdb/raw.txt",
                "model_summary": "## gdb_debug · route_trace\n- Target hit: `True`",
            }

        registry.register(gdb_debug)
        super().__init__(tool_registry=registry)

    def init_state(self, task: str, **kwargs: Any) -> _ProjectionState:
        _ = kwargs
        return _ProjectionState(task=task, max_steps=2)

    def decide(
        self, state: _ProjectionState, observation: dict[str, Any]
    ) -> Decision[Action]:
        _ = observation
        if state.current_step == 0:
            return Decision.act(actions=[Action(name="gdb_debug", args={})])
        return Decision.final("done")

    def reduce(
        self,
        state: _ProjectionState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> _ProjectionState:
        _ = observation, decision
        return state


class _AfterActCapture(EngineHook):
    def __init__(self) -> None:
        self.results: list[Any] = []

    def on_after_act(self, ctx: HookContext, engine: Any) -> None:
        _ = engine
        self.results = list(ctx.action_results or [])


def test_after_act_hook_uses_same_gdb_projection_as_provider_history() -> None:
    hook = _AfterActCapture()
    Engine(
        agent=_ProjectionAgent(),
        budget=RuntimeBudget(max_steps=2),
        hooks=[hook],
    ).run("task")

    assert len(hook.results) == 1
    visible = hook.results[0]
    assert visible["output"] == "## gdb_debug · route_trace\n- Target hit: `True`"
    assert "raw_artifact_path" not in str(visible)
