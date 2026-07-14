from dataclasses import dataclass, field
from typing import Any

from qitos import (
    Action,
    AgentModule,
    Decision,
    Engine,
    HistoryPolicy,
    ModelResponse,
    StateSchema,
    ToolRegistry,
    tool,
)
from qitos.core.history import History, HistoryMessage
from qitos.kit.memory import WindowMemory
from qitos.kit.env import ScreenshotEnv
from qitos.kit.history import WindowHistory
from qitos.kit.parser import ReActTextParser
from qitos.core.memory import Memory, MemoryRecord
from qitos.engine import RuntimeBudget
from qitos.engine.states import ContextConfig
from qitos.trace import runtime_step_to_trace


@dataclass
class DemoState(StateSchema):
    logs: list[str] = field(default_factory=list)


class DemoAgent(AgentModule[DemoState, dict[str, Any], Action]):
    def __init__(self):
        registry = ToolRegistry()

        @tool(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        super().__init__(tool_registry=registry)

    def init_state(self, task: str, **kwargs: Any) -> DemoState:
        return DemoState(task=task, max_steps=3)

    def decide(self, state: DemoState, observation: dict[str, Any]) -> Decision[Action]:
        if state.current_step == 0:
            return Decision.act(
                actions=[Action(name="add", args={"a": 19, "b": 23})],
                rationale="use tool",
            )
        return Decision.final("42")

    def reduce(
        self,
        state: DemoState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> DemoState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if action_results:
            state.logs.append(str(action_results[0]))
        return state


def test_engine_happy_path():
    result = Engine(agent=DemoAgent(), budget=RuntimeBudget(max_steps=3)).run("compute")
    assert result.state.final_result == "42"
    assert result.state.stop_reason == "final"
    assert len(result.records[0].action_results) == 1
    first_result = result.records[0].action_results[0]
    assert first_result.status == "success"
    assert first_result.output == 42
    assert result.step_summaries
    assert result.step_summaries[0].tool_name == "add"
    assert result.to_dict()["tool_calls_by_name"]["add"] == 1


def test_tool_loop_detection_can_be_disabled_for_long_running_agents():
    class RepeatingAgent(AgentModule[DemoState, dict[str, Any], Action]):
        def __init__(self) -> None:
            registry = ToolRegistry()
            self.calls = 0

            @tool(name="GLOB")
            def glob() -> dict[str, str]:
                self.calls += 1
                return {
                    "status": "no_match",
                    "model_summary": "[GLOB:no_match]\n\nEnumeration complete: yes",
                }

            registry.register(glob)
            super().__init__(tool_registry=registry)

        def init_state(self, task: str, **kwargs: Any) -> DemoState:
            _ = kwargs
            return DemoState(task=task, max_steps=6)

        def decide(
            self, state: DemoState, observation: dict[str, Any]
        ) -> Decision[Action]:
            _ = observation
            if state.current_step < 5:
                return Decision.act(actions=[Action(name="GLOB", args={})])
            return Decision.final("done")

        def reduce(
            self,
            state: DemoState,
            observation: dict[str, Any],
            decision: Decision[Action],
        ) -> DemoState:
            _ = observation, decision
            return state

    agent = RepeatingAgent()
    result = Engine(
        agent=agent,
        budget=RuntimeBudget(max_steps=6),
        context_config=ContextConfig(tool_call_loop_detection_enabled=False),
    ).run("repeat")

    assert result.state.final_result == "done"
    assert agent.calls == 5
    assert all(
        record.action_results[0].status == "success"
        for record in result.records[:5]
    )
    assert not any(
        event.payload.get("stage") == "tool_call_loop_detected"
        for event in result.events
    )


def test_tool_loop_detection_remains_enabled_by_default():
    class RepeatingAgent(AgentModule[DemoState, dict[str, Any], Action]):
        def __init__(self) -> None:
            registry = ToolRegistry()
            self.calls = 0

            @tool(name="GLOB")
            def glob() -> dict[str, str]:
                self.calls += 1
                return {"model_summary": "[GLOB:no_match]"}

            registry.register(glob)
            super().__init__(tool_registry=registry)

        def init_state(self, task: str, **kwargs: Any) -> DemoState:
            _ = kwargs
            return DemoState(task=task, max_steps=5)

        def decide(
            self, state: DemoState, observation: dict[str, Any]
        ) -> Decision[Action]:
            _ = observation
            if state.current_step < 4:
                return Decision.act(actions=[Action(name="GLOB", args={})])
            return Decision.final("done")

        def reduce(
            self,
            state: DemoState,
            observation: dict[str, Any],
            decision: Decision[Action],
        ) -> DemoState:
            _ = observation, decision
            return state

    agent = RepeatingAgent()
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=5)).run("repeat")

    assert agent.calls == 3
    assert result.records[3].action_results[0].error == "tool_call_loop_detected"


def test_agent_run_shortcut():
    agent = DemoAgent()
    assert agent.run("compute", trace=False, render=False) == "42"


def test_agent_condition_stop_is_not_automatic_success():
    class StopAgent(DemoAgent):
        def init_state(self, task: str, **kwargs: Any) -> DemoState:
            _ = kwargs
            return DemoState(task=task, max_steps=3)

        def decide(self, state: DemoState, observation: dict[str, Any]) -> Decision[Action]:
            _ = observation
            return Decision.act(
                actions=[Action(name="add", args={"a": 1, "b": 1})],
                rationale="take one action then stop",
            )

        def reduce(
            self,
            state: DemoState,
            observation: dict[str, Any],
            decision: Decision[Action],
        ) -> DemoState:
            _ = observation, decision
            return state

        def should_stop(self, state: DemoState) -> bool:
            _ = state
            return True

    result = Engine(agent=StopAgent(), budget=RuntimeBudget(max_steps=3)).run("compute")
    assert result.state.stop_reason == "agent_condition"
    assert result.state.final_result is None
    assert result.task_result is not None
    assert result.task_result.success is False
    assert all(item.passed is False for item in result.task_result.criteria)


def test_agent_run_enables_trace_and_render_by_default(tmp_path):
    workspace = tmp_path / "workspace"
    logdir = tmp_path / "runs"
    workspace.mkdir(parents=True, exist_ok=True)

    agent = DemoAgent()
    result = agent.run(
        "compute",
        workspace=str(workspace),
        trace_logdir=str(logdir),
        return_state=True,
    )

    assert result.state.final_result == "42"
    assert (workspace / "render_events.jsonl").exists()
    run_dirs = [p for p in logdir.iterdir() if p.is_dir()]
    assert run_dirs


def test_agent_run_can_disable_default_trace_and_render(tmp_path):
    workspace = tmp_path / "workspace"
    logdir = tmp_path / "runs"
    workspace.mkdir(parents=True, exist_ok=True)

    agent = DemoAgent()
    result = agent.run(
        "compute",
        workspace=str(workspace),
        trace_logdir=str(logdir),
        trace=False,
        render=False,
        return_state=True,
    )

    assert result.state.final_result == "42"
    assert not (workspace / "render_events.jsonl").exists()
    assert not logdir.exists() or not any(logdir.iterdir())


def test_engine_injects_memory_context_into_env_view():
    agent = DemoAgent()
    agent.memory = WindowMemory(window_size=20)
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=3)).run("compute")
    assert result.state.final_result == "42"
    assert hasattr(agent, "memory")
    assert agent.memory is not None


def test_engine_default_model_decide_with_prepare():
    seen_messages: list[dict[str, str]] = []

    class _DummyModel:
        def __call__(self, messages):
            seen_messages.extend(messages)
            return "Action: add(a=20, b=22)"

    class LLMDrivenDemo(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _DummyModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "System prompt"

        def prepare(self, state: DemoState) -> str:
            return f"Task={state.task} Step={state.current_step}"

        def decide(self, state: DemoState, observation: dict[str, Any]):
            if state.current_step == 0:
                return None
            return Decision.final("42")

    result = Engine(agent=LLMDrivenDemo(), budget=RuntimeBudget(max_steps=3)).run(
        "compute"
    )
    assert result.state.final_result == "42"
    assert len(seen_messages) == 2
    assert seen_messages[0]["role"] == "system"
    assert seen_messages[1]["role"] == "user"


def test_engine_includes_current_step_visual_input_in_user_message(tmp_path):
    png_path = tmp_path / "screen.png"
    png_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f\x00\x02\xeb\x01\xf5i\xf6\x81\xb7\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    seen_messages: list[dict[str, Any]] = []

    class _VisualModel:
        model = "gpt-4.1-mini"

        def __call__(self, messages, **kwargs):
            _ = kwargs
            seen_messages.extend(messages)
            return "Final Answer: visual complete"

        def supports_multimodal_input(self) -> bool:
            return True

    class VisualDemo(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _VisualModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "Inspect the screenshot and answer."

        def prepare(self, state: DemoState) -> str:
            return "What is visible in the current screenshot?"

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = observation
            if state.current_step == 0:
                return None
            return Decision.final("done")

    env = ScreenshotEnv(
        screenshot_path=str(png_path),
        text="The screenshot shows a login page.",
    )
    result = Engine(agent=VisualDemo(), env=env, budget=RuntimeBudget(max_steps=2)).run(
        "inspect"
    )
    assert result.state.final_result == "visual complete"
    user_message = seen_messages[-1]
    assert user_message["role"] == "user"
    assert isinstance(user_message["content"], list)
    assert user_message["content"][0]["type"] == "text"
    assert user_message["content"][1]["type"] == "image_file"
    record = result.records[0]
    assert record.model_input_visual_count == 1
    assert record.has_screenshot is True
    assert record.observation_modalities == ["text", "screenshot"]


def test_engine_uses_history_messages_for_next_llm_call():
    calls: list[list[dict[str, str]]] = []

    class _DummyModel:
        def __call__(self, messages):
            calls.append(list(messages))
            return "Action: add(a=1, b=1)"

    class MultiTurnLLMDemo(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _DummyModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "System prompt"

        def prepare(self, state: DemoState) -> str:
            return f"Task={state.task} Step={state.current_step}"

        def decide(self, state: DemoState, observation: dict[str, Any]):
            if state.current_step < 2:
                return None
            return Decision.final("42")

    agent = MultiTurnLLMDemo()
    agent.history = WindowHistory(window_size=50)
    result = Engine(
        agent=agent,
        budget=RuntimeBudget(max_steps=4),
        history_policy=HistoryPolicy(max_messages=4),
    ).run("compute")
    assert result.state.final_result == "42"
    assert len(calls) == 2
    assert calls[0][0]["role"] == "system"
    assert calls[0][-1]["role"] == "user"
    # second call should include history (previous user+assistant)
    assert len(calls[1]) >= 4
    assert calls[1][1]["role"] == "user"
    assert calls[1][2]["role"] == "assistant"


def test_engine_emits_parser_events_and_records_step_diagnostics():
    class _DummyModel:
        def __init__(self):
            self.outputs = [
                "Thought only without action",
                "Action: add(a=20, b=22)",
                "Final Answer: 42",
            ]

        def __call__(self, messages):
            return self.outputs.pop(0)

    class ParserDiagDemo(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _DummyModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "System prompt"

        def prepare(self, state: DemoState) -> str:
            return f"Task={state.task} Step={state.current_step}"

        def decide(self, state: DemoState, observation: dict[str, Any]):
            if state.current_step < 3:
                return None
            return Decision.final("42")

    result = Engine(agent=ParserDiagDemo(), budget=RuntimeBudget(max_steps=5)).run(
        "compute"
    )
    assert result.state.final_result == "42"
    parser_result_events = [
        e
        for e in result.events
        if getattr(e.phase, "value", e.phase) == "DECIDE"
        and (e.payload or {}).get("stage") == "parser_result"
    ]
    parser_diag_events = [
        e
        for e in result.events
        if getattr(e.phase, "value", e.phase) == "DECIDE"
        and (e.payload or {}).get("stage") == "parser_diagnostics"
    ]
    assert parser_result_events
    assert parser_diag_events
    assert result.records[0].parser_diagnostics["code"] == "missing_action_or_final"
    assert result.records[0].parser_contract == "react_text_v1"
    assert result.records[0].parser_salvage_applied is False


def test_engine_interpret_model_response_bypasses_parser_and_records_summary():
    seen: list[ModelResponse] = []

    class _ResponseModel:
        model = "demo-model"
        provider = "demo-provider"

        def __call__(self, messages):
            _ = messages
            return {
                "content": "model said to use the add tool",
                "usage": {
                    "prompt_tokens": 12,
                    "completion_tokens": 5,
                    "total_tokens": 17,
                },
                "finish_reason": "stop",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                    }
                ],
            }

    class _NeverParser:
        def parse(self, raw_output, context=None):
            _ = raw_output
            _ = context
            raise AssertionError(
                "parser should not be called when interpret_model_response returns Decision"
            )

    class _InterpretAgent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _ResponseModel()
            self.model_parser = _NeverParser()

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = observation
            if state.current_step > 0:
                return Decision.final("42")
            return None

        def interpret_model_response(
            self,
            state: DemoState,
            observation: dict[str, Any],
            response: ModelResponse,
        ) -> Decision[Action] | None:
            _ = state
            _ = observation
            seen.append(response)
            return Decision.act(
                actions=[Action(name="add", args={"a": 20, "b": 22})],
                rationale=response.text,
            )

    result = Engine(agent=_InterpretAgent(), budget=RuntimeBudget(max_steps=3)).run(
        "compute"
    )
    assert result.state.final_result == "42"
    assert seen
    response = seen[0]
    assert response.text == "model said to use the add tool"
    assert response.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 5,
        "total_tokens": 17,
    }
    assert response.finish_reason == "stop"
    assert response.model_name == "demo-model"
    assert response.provider == "demo-provider"
    assert result.records[0].model_response["text"] == "model said to use the add tool"
    assert "raw" not in result.records[0].model_response
    model_output_events = [
        e
        for e in result.events
        if getattr(e.phase, "value", e.phase) == "DECIDE"
        and (e.payload or {}).get("stage") == "model_output"
    ]
    assert model_output_events
    assert (
        model_output_events[0].payload["raw_output"] == "model said to use the add tool"
    )
    assert model_output_events[0].payload["model_response"]["finish_reason"] == "stop"
    traced = runtime_step_to_trace(result.records[0]).to_dict()
    assert traced["model_response"]["model_name"] == "demo-model"
    assert "raw" not in traced["model_response"]


def test_engine_interpret_model_response_can_fall_back_to_parser():
    seen: list[ModelResponse] = []

    class _ResponseModel:
        model = "demo-model"

        def __call__(self, messages):
            _ = messages
            return {
                "content": "Final Answer: 42",
                "usage": {
                    "prompt_tokens": 9,
                    "completion_tokens": 3,
                    "total_tokens": 12,
                },
                "finish_reason": "stop",
            }

    class _TrackingParser(ReActTextParser):
        def __init__(self):
            super().__init__()
            self.calls: list[Any] = []

        def parse(self, raw_output: Any, context=None):
            self.calls.append(raw_output)
            return super().parse(raw_output, context=context)

    parser = _TrackingParser()

    class _InterpretAgent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _ResponseModel()
            self.model_parser = parser

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = state
            _ = observation
            return None

        def interpret_model_response(
            self,
            state: DemoState,
            observation: dict[str, Any],
            response: ModelResponse,
        ) -> Decision[Action] | None:
            _ = state
            _ = observation
            seen.append(response)
            return None

    result = Engine(agent=_InterpretAgent(), budget=RuntimeBudget(max_steps=2)).run(
        "compute"
    )
    assert result.state.final_result == "42"
    assert seen and isinstance(seen[0], ModelResponse)
    assert parser.calls == ["Final Answer: 42"]
    assert result.records[0].model_response["usage"]["total_tokens"] == 12


def test_engine_uses_history_retrieve_contract():
    class ContractHistory(History):
        def __init__(self):
            self._messages: list[HistoryMessage] = []
            self.retrieve_called = 0

        def append(self, message: HistoryMessage) -> None:
            self._messages.append(message)

        def retrieve(self, query=None, state=None, observation=None):
            self.retrieve_called += 1
            return [{"role": "assistant", "content": "history_hint"}]

        def summarize(self, max_items: int = 5) -> str:
            return ""

        def evict(self) -> int:
            return 0

        def reset(self, run_id=None) -> None:
            self._messages = []

    class ContractMemory(Memory):
        def __init__(self):
            self._records: list[MemoryRecord] = []
            self.retrieve_called = 0

        def append(self, record: MemoryRecord) -> None:
            self._records.append(record)

        def retrieve(self, query=None, state=None, observation=None):
            self.retrieve_called += 1
            return []

        def summarize(self, max_items: int = 5) -> str:
            return ""

        def evict(self) -> int:
            return 0

        def reset(self, run_id=None) -> None:
            self._records = []

    seen_messages: list[dict[str, str]] = []

    class _DummyModel:
        def __call__(self, messages):
            seen_messages.extend(messages)
            return "Final Answer: 42"

    class LLMOnceAgent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _DummyModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "System prompt"

        def prepare(self, state: DemoState) -> str:
            return "solve"

        def decide(self, state: DemoState, observation: dict[str, Any]):
            return None

    mem = ContractMemory()
    hist = ContractHistory()
    agent = LLMOnceAgent()
    agent.memory = mem
    agent.history = hist
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=2)).run("compute")
    assert result.state.final_result == "42"
    assert hist.retrieve_called >= 1
    assert mem.retrieve_called == 0
    assert any(m.get("content") == "history_hint" for m in seen_messages)


def test_memory_and_history_streams_are_strictly_separated():
    class CaptureMemory(Memory):
        def __init__(self):
            self.records: list[MemoryRecord] = []

        def append(self, record: MemoryRecord) -> None:
            self.records.append(record)

        def retrieve(self, query=None, state=None, observation=None):
            return list(self.records)

        def summarize(self, max_items: int = 5) -> str:
            return ""

        def evict(self) -> int:
            return 0

        def reset(self, run_id=None) -> None:
            self.records = []

    class CaptureHistory(History):
        def __init__(self):
            self.messages: list[HistoryMessage] = []

        def append(self, message: HistoryMessage) -> None:
            self.messages.append(message)

        def retrieve(self, query=None, state=None, observation=None):
            return list(self.messages)

        def summarize(self, max_items: int = 5) -> str:
            return ""

        def evict(self) -> int:
            return 0

        def reset(self, run_id=None) -> None:
            self.messages = []

    class _DummyModel:
        def __call__(self, messages):
            return "Final Answer: ok"

    class OneShotLLMAgent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _DummyModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "System prompt"

        def prepare(self, state: DemoState) -> str:
            return "solve"

        def decide(self, state: DemoState, observation: dict[str, Any]):
            return None

    mem = CaptureMemory()
    hist = CaptureHistory()
    agent = OneShotLLMAgent()
    agent.memory = mem
    agent.history = hist
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=2)).run("compute")
    assert result.state.stop_reason == "final"

    mem_roles = {r.role for r in mem.records}
    assert {"task", "state", "decision", "next_state", "observation"}.issubset(
        mem_roles
    )
    assert "message" not in mem_roles

    hist_roles = [m.role for m in hist.messages]
    assert "user" in hist_roles
    assert "assistant" in hist_roles


def test_engine_records_context_telemetry_and_defaults_to_compact_runtime_history():
    class _DummyModel:
        model = "dummy-context"
        max_tokens = 128
        context_window = 4096

        def __call__(self, messages):
            return "Final Answer: ok"

    class _Agent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _DummyModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "System prompt"

        def prepare(self, state: DemoState) -> str:
            return f"Task={state.task}\n" + ("verbose context " * 20)

        def decide(self, state: DemoState, observation: dict[str, Any]):
            return None

    engine = Engine(agent=_Agent(), budget=RuntimeBudget(max_steps=2))
    result = engine.run("demo")
    assert result.state.final_result == "ok"
    assert engine._runtime_history.__class__.__name__ == "CompactHistory"
    assert result.records
    assert result.records[0].context.get("input_tokens_total", 0) > 0
    assert result.records[0].context.get("context_window") == 4096


def test_engine_prefers_provider_usage_for_context_totals():
    class _UsageModel:
        model = "dummy-usage"
        max_tokens = 128
        context_window = 8192

        def __init__(self):
            self._used = False

        def __call__(self, messages):
            self._used = True
            return "Final Answer: exact"

        def count_tokens(self, payload):
            return 10

        def extract_usage(self):
            if not self._used:
                return None
            return {"prompt_tokens": 123, "completion_tokens": 17, "total_tokens": 140}

    class _Agent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _UsageModel()
            self.model_parser = ReActTextParser()

        def build_system_prompt(self, state: DemoState) -> str | None:
            return "System prompt"

        def prepare(self, state: DemoState) -> str:
            return "Hello"

        def decide(self, state: DemoState, observation: dict[str, Any]):
            return None

    result = Engine(agent=_Agent(), budget=RuntimeBudget(max_steps=2)).run("demo")
    ctx = result.records[0].context
    assert result.state.final_result == "exact"
    # Provider usage is authoritative after completion; the pre-request
    # estimate remains separately auditable when a meter provided one.
    assert ctx["counting_mode"] == "provider_usage"
    assert ctx["input_tokens_total"] == 123
    assert ctx["provider_prompt_tokens"] == 123
    assert ctx["provider_completion_tokens"] == 17
    assert ctx["provider_total_tokens"] == 140
    assert ctx["output_tokens"] == 17
    assert ctx["tokens_total"] == 140


def test_engine_uses_native_tool_call_lane_before_parser():
    class _RawResponseModel:
        model = "qwen-plus"
        provider = "openai-compatible"

        def __init__(self):
            self.qitos_harness_metadata = {
                "family_preset": "qwen",
                "tool_policy": {
                    "primary_delivery": "api_parameter",
                    "fallback_delivery": "prompt_injection",
                    "native_tool_call_preferred": True,
                },
            }

        def call_raw(self, messages):
            _ = messages
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "add",
                                        "arguments": '{"a": 20, "b": 22}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
                "model": "qwen-plus",
            }

    class _NeverParser:
        def parse(self, raw_output, context=None):
            _ = raw_output
            _ = context
            raise AssertionError("parser should be bypassed when native tool calls are used")

    class _Agent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _RawResponseModel()
            self.model_parser = _NeverParser()

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = observation
            if state.current_step > 0:
                return Decision.final("42")
            return None

    result = Engine(agent=_Agent(), budget=RuntimeBudget(max_steps=3)).run("compute")
    assert result.state.final_result == "42"
    record = result.records[0]
    assert record.decision_source == "native_tool_calls"
    assert record.native_tool_call_used is True
    assert record.native_tool_call_fallback_reason is None
    assert record.actions[0].name == "add"
    assert record.actions[0].args == {"a": 20, "b": 22}
    assert record.model_response["tool_calls"][0]["function"]["name"] == "add"
    native_events = [
        e
        for e in result.events
        if getattr(e.phase, "value", e.phase) == "DECIDE"
        and (e.payload or {}).get("stage") == "native_tool_calls_decision"
    ]
    assert native_events
    traced = runtime_step_to_trace(record).to_dict()
    assert traced["decision_source"] == "native_tool_calls"
    assert traced["native_tool_call_used"] is True


def test_engine_sanitizes_submit_poc_native_tool_history_without_mutating_result():
    seen_messages: list[list[dict[str, Any]]] = []

    class _SubmitModel:
        model = "GLM-5.1"
        provider = "openai-compatible"

        def __init__(self):
            self.qitos_harness_metadata = {
                "family_preset": "glm",
                "tool_policy": {
                    "primary_delivery": "api_parameter",
                    "fallback_delivery": "prompt_injection",
                    "native_tool_call_preferred": True,
                },
            }
            self.calls = 0

        def call_raw(self, messages):
            self.calls += 1
            seen_messages.append(list(messages))
            if self.calls == 1:
                return {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_submit",
                                        "type": "function",
                                        "function": {
                                            "name": "submit_poc",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "model": "GLM-5.1",
                }
            return {
                "choices": [
                    {
                        "message": {"content": "Final Answer: done"},
                        "finish_reason": "stop",
                    }
                ],
                "model": "GLM-5.1",
            }

    class _SubmitAgent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _SubmitModel()

            @tool(name="submit_poc")
            def submit_poc() -> dict[str, Any]:
                return {
                    "status": "success",
                    "vul_exit_code": 0,
                    "fix_exit_code": 0,
                    "poc_id": "p1",
                    "flag": None,
                    "raw_output": "wrong number of function inputs",
                    "verification_scope": "full",
                    "vul_stderr": "target stderr",
                    "fix_stderr": "hidden stderr",
                    "vul_stdout": "target stdout",
                    "fix_stdout": "hidden stdout",
                    "model_summary": "[submit_poc] attempt.bin\n\nNo vulnerable-target trigger was observed.\nVulnerable target exit: 0\nServer: wrong number of function inputs",
                }

            self.tool_registry.register(submit_poc)

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = observation
            return None

        def reduce(
            self,
            state: DemoState,
            observation: dict[str, Any],
            decision: Decision[Action],
        ) -> DemoState:
            _ = observation
            _ = decision
            return state

    result = Engine(agent=_SubmitAgent(), budget=RuntimeBudget(max_steps=3)).run("compute")

    assert result.records[0].action_results[0].output["fix_exit_code"] == 0
    assert len(seen_messages) >= 2
    second_call_text = "\n".join(str(message) for message in seen_messages[1])
    assert "wrong number of function inputs" in second_call_text
    assert "vul_exit_code" not in second_call_text
    assert "fix_exit_code" not in second_call_text
    assert "fix_stderr" not in second_call_text
    assert "fix_stdout" not in second_call_text
    assert "verification_scope" not in second_call_text
    act_events = [
        e for e in result.events if getattr(e.phase, "value", e.phase) == "ACT"
    ]
    act_event_text = "\n".join(str(e.payload) for e in act_events)
    assert "wrong number of function inputs" in act_event_text
    assert "vul_exit_code" not in act_event_text
    assert "fix_exit_code" not in act_event_text
    assert "fix_stderr" not in act_event_text
    assert "fix_stdout" not in act_event_text
    assert "verification_scope" not in act_event_text


def test_engine_agent_can_block_disallowed_actions_before_execution():
    executed = {"value": False}

    class _RawResponseModel:
        model = "qwen-plus"
        provider = "openai-compatible"

        def __init__(self):
            self.qitos_harness_metadata = {
                "family_preset": "qwen",
                "tool_policy": {
                    "primary_delivery": "api_parameter",
                    "fallback_delivery": "prompt_injection",
                    "native_tool_call_preferred": True,
                },
            }

        def call_raw(self, messages):
            _ = messages
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_blocked",
                                    "type": "function",
                                    "function": {
                                        "name": "blocked_tool",
                                        "arguments": "{}",
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "model": "qwen-plus",
            }

    class _BlockAgent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _RawResponseModel()

            @tool(name="blocked_tool")
            def blocked_tool() -> str:
                executed["value"] = True
                return "should not run"

            self.tool_registry.register(blocked_tool)

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = observation
            if state.current_step > 0:
                return Decision.final("done")
            return None

        def block_action(self, state: DemoState, action: Action) -> str | None:
            _ = state
            if action.name == "blocked_tool":
                return "blocked for this state"
            return None

    result = Engine(agent=_BlockAgent(), budget=RuntimeBudget(max_steps=3)).run("compute")

    assert executed["value"] is False
    first_result = result.records[0].action_results[0]
    assert first_result.status == "error"
    assert first_result.error == "action_blocked"
    assert first_result.metadata["error_category"] == "action_blocked"
    assert "blocked for this state" in str(first_result.output)


def test_engine_salvages_glm_text_tool_call_markup_before_parser():
    class _GLMMarkupModel:
        model = "GLM-5.1"
        provider = "openai-compatible"

        def __init__(self):
            self.qitos_harness_metadata = {
                "family_preset": "glm",
                "tool_policy": {
                    "primary_delivery": "api_parameter",
                    "fallback_delivery": "prompt_injection",
                    "native_tool_call_preferred": True,
                },
            }

        def call_raw(self, messages):
            _ = messages
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<tool_call>add"
                                "<arg_key>a</arg_key><arg_value>20</arg_value>"
                                "<arg_key>b</arg_key><arg_value>22</arg_value>"
                                "</tool_call>"
                            ),
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "model": "GLM-5.1",
            }

    class _NeverParser:
        def parse(self, raw_output, context=None):
            _ = raw_output
            _ = context
            raise AssertionError("GLM text tool-call markup should bypass the parser")

    class _Agent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _GLMMarkupModel()
            self.model_parser = _NeverParser()

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = observation
            if state.current_step > 0:
                return Decision.final("42")
            return None

    result = Engine(agent=_Agent(), budget=RuntimeBudget(max_steps=3)).run("compute")
    assert result.state.final_result == "42"
    record = result.records[0]
    assert record.decision_source == "native_tool_calls"
    assert record.native_tool_call_used is True
    assert record.actions[0].name == "add"
    assert record.actions[0].args == {"a": 20, "b": 22}
    assert record.model_response["tool_calls"][0]["function"]["name"] == "add"


def test_engine_native_tool_call_lane_falls_back_to_parser_on_bad_arguments():
    class _BadArgsModel:
        model = "qwen-plus"

        def __init__(self):
            self.qitos_harness_metadata = {
                "family_preset": "qwen",
                "tool_policy": {
                    "primary_delivery": "api_parameter",
                    "fallback_delivery": "prompt_injection",
                    "native_tool_call_preferred": True,
                },
            }

        def call_raw(self, messages):
            _ = messages
            return {
                "content": "Final Answer: recovered",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "add", "arguments": "{not-json"},
                    }
                ],
            }

    class _Agent(DemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _BadArgsModel()
            self.model_parser = ReActTextParser()

        def decide(self, state: DemoState, observation: dict[str, Any]):
            _ = observation
            if state.current_step > 0:
                return Decision.final("done")
            return None

    result = Engine(agent=_Agent(), budget=RuntimeBudget(max_steps=3)).run("compute")
    assert result.state.final_result == "recovered"
    record = result.records[0]
    assert record.decision_source == "parser"
    assert record.native_tool_call_used is False
    assert record.native_tool_call_fallback_reason == "tool_call_arguments_invalid"
    fallback_events = [
        e
        for e in result.events
        if getattr(e.phase, "value", e.phase) == "DECIDE"
        and (e.payload or {}).get("stage") == "native_tool_call_fallback"
    ]
    assert fallback_events
