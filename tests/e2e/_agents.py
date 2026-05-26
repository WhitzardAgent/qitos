"""E2E test utility agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qitos import AgentModule, Decision, StateSchema
from qitos.kit import ReActTextParser


@dataclass
class SimpleState(StateSchema):
    """Minimal state for E2E testing."""
    scratchpad: list[str] = field(default_factory=list)


class SimpleReActAgent(AgentModule[SimpleState, Any, Any]):
    """Minimal ReAct agent for E2E path verification."""

    name = "simple_react"

    def __init__(self, llm: Any = None, **kwargs):
        super().__init__(llm=llm, model_parser=ReActTextParser(), **kwargs)

    def init_state(self, task: str, **kwargs) -> SimpleState:
        return SimpleState(task=task, max_steps=int(kwargs.get("max_steps", 10)))

    def build_system_prompt(self, state: SimpleState) -> str | None:
        return "You are a helpful assistant. Use tools when needed, or provide a final answer directly."

    def prepare(self, state: SimpleState) -> str:
        return f"Task: {state.task}\nStep: {state.current_step}/{state.max_steps}"

    def reduce(self, state: SimpleState, observation: Any, decision: Decision) -> SimpleState:
        if decision.mode == "final":
            state.final_result = str(decision.final_answer or "")
        return state


@dataclass
class CalculatorState(StateSchema):
    """State for calculator agent."""
    last_result: str = ""


class CalculatorAgent(AgentModule[CalculatorState, Any, Any]):
    """Agent with calculator tools for tool calling verification."""

    name = "calculator_agent"

    def __init__(self, llm: Any = None, **kwargs):
        from ._tools import CalculatorToolSet
        super().__init__(llm=llm, toolset=[CalculatorToolSet()], model_parser=ReActTextParser(), **kwargs)

    def init_state(self, task: str, **kwargs) -> CalculatorState:
        return CalculatorState(task=task, max_steps=int(kwargs.get("max_steps", 10)))

    def build_system_prompt(self, state: CalculatorState) -> str | None:
        return "You are a math assistant. Use calculator tools to compute answers."

    def prepare(self, state: CalculatorState) -> str:
        return f"Task: {state.task}\nLast result: {state.last_result}"

    def reduce(self, state: CalculatorState, observation: Any, decision: Decision) -> CalculatorState:
        if decision.mode == "final":
            state.final_result = str(decision.final_answer or "")
        return state


@dataclass
class OrchestratorState(StateSchema):
    """State for handoff orchestrator."""
    delegated: bool = False


class HandoffOrchestrator(AgentModule[OrchestratorState, Any, Any]):
    """Orchestrator that hands off to specialist workers."""

    name = "handoff_orchestrator"

    handoff_targets = ["math_worker", "string_worker"]

    def __init__(self, llm: Any = None, **kwargs):
        super().__init__(llm=llm, model_parser=ReActTextParser(), **kwargs)

    def init_state(self, task: str, **kwargs) -> OrchestratorState:
        return OrchestratorState(task=task, max_steps=int(kwargs.get("max_steps", 8)))

    def build_system_prompt(self, state: OrchestratorState) -> str | None:
        return (
            "You are an orchestrator. Delegate math tasks to math_worker "
            "and string tasks to string_worker. Use transfer_to_math_worker "
            "or transfer_to_string_worker to delegate."
        )

    def prepare(self, state: OrchestratorState) -> str:
        return f"Task: {state.task}"

    def reduce(self, state: OrchestratorState, observation: Any, decision: Decision) -> OrchestratorState:
        if decision.mode == "final":
            state.final_result = str(decision.final_answer or "")
        if decision.mode == "handoff":
            state.delegated = True
        return state


@dataclass
class WorkerState(StateSchema):
    """State for handoff worker."""
    work_done: bool = False


class MathWorker(AgentModule[WorkerState, Any, Any]):
    """Math specialist worker."""

    name = "math_worker"

    def __init__(self, llm: Any = None, **kwargs):
        from ._tools import CalculatorToolSet
        super().__init__(llm=llm, toolset=[CalculatorToolSet()], model_parser=ReActTextParser(), **kwargs)

    def init_state(self, task: str, **kwargs) -> WorkerState:
        return WorkerState(task=task, max_steps=int(kwargs.get("max_steps", 5)))

    def build_system_prompt(self, state: WorkerState) -> str | None:
        return "You are a math specialist. Use calculator tools to solve math problems. Provide a clear final answer."

    def prepare(self, state: WorkerState) -> str:
        return f"Task: {state.task}"

    def reduce(self, state: WorkerState, observation: Any, decision: Decision) -> WorkerState:
        if decision.mode == "final":
            state.final_result = str(decision.final_answer or "")
            state.work_done = True
        return state


class StringWorker(AgentModule[WorkerState, Any, Any]):
    """String operations specialist worker."""

    name = "string_worker"

    def __init__(self, llm: Any = None, **kwargs):
        from ._tools import StringToolSet
        super().__init__(llm=llm, toolset=[StringToolSet()], model_parser=ReActTextParser(), **kwargs)

    def init_state(self, task: str, **kwargs) -> WorkerState:
        return WorkerState(task=task, max_steps=int(kwargs.get("max_steps", 5)))

    def build_system_prompt(self, state: WorkerState) -> str | None:
        return "You are a string operations specialist. Use string tools to process text."

    def prepare(self, state: WorkerState) -> str:
        return f"Task: {state.task}"

    def reduce(self, state: WorkerState, observation: Any, decision: Decision) -> WorkerState:
        if decision.mode == "final":
            state.final_result = str(decision.final_answer or "")
            state.work_done = True
        return state


# ---------------------------------------------------------------------------
# Weather agent
# ---------------------------------------------------------------------------


@dataclass
class WeatherState(StateSchema):
    """State for weather agent."""
    last_city: str = ""


class WeatherAgent(AgentModule[WeatherState, Any, Any]):
    """Agent with weather tools for E2E tool-calling tests."""

    name = "weather_agent"

    def __init__(self, llm: Any = None, **kwargs):
        from ._tools import WeatherToolSet
        super().__init__(llm=llm, toolset=[WeatherToolSet()], model_parser=ReActTextParser(), **kwargs)

    def init_state(self, task: str, **kwargs) -> WeatherState:
        return WeatherState(task=task, max_steps=int(kwargs.get("max_steps", 8)))

    def build_system_prompt(self, state: WeatherState) -> str | None:
        return "You are a weather assistant. Use weather tools to look up weather information."

    def prepare(self, state: WeatherState) -> str:
        return f"Task: {state.task}\nLast city: {state.last_city}"

    def reduce(self, state: WeatherState, observation: Any, decision: Decision) -> WeatherState:
        if decision.mode == "final":
            state.final_result = str(decision.final_answer or "")
        return state


# ---------------------------------------------------------------------------
# Delegating orchestrator
# ---------------------------------------------------------------------------


@dataclass
class DelegateOrchestratorState(StateSchema):
    """State for delegating orchestrator."""
    delegated_tasks: list[str] = field(default_factory=list)


class DelegatingOrchestrator(AgentModule[DelegateOrchestratorState, Any, Any]):
    """Orchestrator that uses delegate_to_* tools to delegate tasks."""

    name = "delegating_orchestrator"

    handoff_targets = ["math_worker", "string_worker"]

    def __init__(self, llm: Any = None, **kwargs):
        super().__init__(llm=llm, model_parser=ReActTextParser(), **kwargs)

    def init_state(self, task: str, **kwargs) -> DelegateOrchestratorState:
        return DelegateOrchestratorState(task=task, max_steps=int(kwargs.get("max_steps", 8)))

    def build_system_prompt(self, state: DelegateOrchestratorState) -> str | None:
        return (
            "You are an orchestrator. Use delegate_to_math_worker to delegate math tasks, "
            "and delegate_to_string_worker to delegate string tasks. "
            "You can also use the fanout tool to run tasks in parallel."
        )

    def prepare(self, state: DelegateOrchestratorState) -> str:
        return f"Task: {state.task}"

    def reduce(self, state: DelegateOrchestratorState, observation: Any, decision: Decision) -> DelegateOrchestratorState:
        if decision.mode == "final":
            state.final_result = str(decision.final_answer or "")
        return state
