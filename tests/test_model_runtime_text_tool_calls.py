from types import SimpleNamespace

from qitos import Action, AgentModule, Decision, Engine, ToolRegistry, tool
from qitos.core.history import History, HistoryMessage
from qitos.core.message_builder import MessageBuildRequest, MessageBuildResult
from qitos.core.state import StateSchema
from qitos.engine import RuntimeBudget
from qitos.engine.states import StepRecord
from qitos.kit.parser import ReActTextParser


class _HistoryCapture(History):
    def __init__(self):
        self.messages: list[HistoryMessage] = []

    def append(self, message: HistoryMessage) -> None:
        self.messages.append(message)

    def retrieve(self, query=None, state=None, observation=None):
        _ = query, state, observation
        return list(self.messages)

    def summarize(self, max_items: int = 5) -> str:
        _ = max_items
        return ""

    def evict(self) -> int:
        return 0

    def reset(self, run_id=None) -> None:
        _ = run_id
        self.messages = []


class _State(StateSchema):
    pass


class _ToolCallAgent(AgentModule[_State, dict, Action]):
    def __init__(self, llm):
        registry = ToolRegistry()

        @tool(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        super().__init__(tool_registry=registry, llm=llm)
        self.model_parser = ReActTextParser()
        self.history = _HistoryCapture()

    def init_state(self, task: str, **kwargs):
        _ = kwargs
        return _State(task=task, max_steps=2)

    def build_system_prompt(self, state: _State):
        _ = state
        return "System prompt"

    def prepare(self, state: _State) -> str:
        _ = state
        return "solve"

    def decide(self, state: _State, observation: dict):
        _ = observation
        if state.current_step > 0:
            return Decision.final("done")
        return None

    def reduce(self, state: _State, observation: dict, decision: Decision[Action]):
        _ = observation, decision
        return state


class _RuntimeContextBuilder:
    def __init__(self, delivery: str = "merge_tool"):
        self.delivery = delivery

    def build_messages(self, request: MessageBuildRequest) -> MessageBuildResult:
        messages = [{"role": "system", "content": "System prompt"}]
        if request.step_id == 0:
            messages.append({"role": "user", "content": request.prepared})
            return MessageBuildResult(messages=messages)
        messages.append({"role": "user", "content": request.state.task})
        messages.extend(
            message for message in request.history
            if message.get("role") in {"assistant", "tool"}
        )
        return MessageBuildResult(
            messages=messages,
            runtime_context="authoritative state for the next action",
            runtime_context_delivery=self.delivery,
        )


def test_extract_response_text_preserves_object_message_content_when_tool_calls_exist():
    engine = Engine(agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1))
    runtime = engine._model_runtime
    raw = SimpleNamespace(
        message=SimpleNamespace(
            content="Conclusion: likely 1-byte trigger. Next: write and submit.",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                }
            ],
        )
    )

    text = runtime._extract_response_text(raw)

    assert text == "Conclusion: likely 1-byte trigger. Next: write and submit."


def test_extract_response_text_uses_reasoning_content_when_content_is_empty():
    engine = Engine(agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1))
    runtime = engine._model_runtime
    raw = SimpleNamespace(
        message=SimpleNamespace(
            content=None,
            reasoning_content="Conclusion: the checksum logic is the trigger. Next: write a candidate.",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                }
            ],
        )
    )

    text = runtime._extract_response_text(raw)

    assert text == "Conclusion: the checksum logic is the trigger. Next: write a candidate."


def test_extract_response_text_empty_for_tool_calls_without_content_or_reasoning():
    engine = Engine(agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1))
    runtime = engine._model_runtime
    raw = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                        }
                    ],
                ),
            )
        ]
    )

    text = runtime._extract_response_text(raw)

    assert text == ""


def test_native_tool_call_history_keeps_assistant_text_and_tool_calls():
    class _ObjectResponseModel:
        model = "demo-model"
        qitos_harness_metadata = {
            "tool_policy": {"native_tool_call_preferred": True}
        }

        def __call__(self, messages):
            _ = messages
            return SimpleNamespace(
                message=SimpleNamespace(
                    content="Conclusion: likely 1-byte trigger. Next: use add.",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                        }
                    ],
                ),
                finish_reason="tool_calls",
            )

    agent = _ToolCallAgent(llm=_ObjectResponseModel())
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=2)).run("compute")

    assert result.state.final_result == "done"
    assistant_messages = [m for m in agent.history.messages if m.role == "assistant"]
    assert assistant_messages
    first = assistant_messages[0]
    assert first.content == "Conclusion: likely 1-byte trigger. Next: use add."
    assert first.tool_calls
    assert first.tool_calls[0]["function"]["name"] == "add"


def test_parser_tool_actions_use_the_same_assistant_tool_result_history_chain():
    class _ParserToolModel:
        model = "parser-model"

        def __init__(self):
            self.calls = 0
            self.seen_messages = []

        def call_raw(self, messages, **kwargs):
            _ = kwargs
            self.seen_messages.append(list(messages))
            self.calls += 1
            if self.calls == 1:
                return "Thought: calculate first\nAction: add(a=20, b=22)"
            return "Final Answer: done"

    class _ParserToolAgent(_ToolCallAgent):
        def decide(self, state, observation):
            _ = state, observation
            return None

    model = _ParserToolModel()
    agent = _ParserToolAgent(model)
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=3)).run("compute")

    assert result.state.final_result == "done"
    assert len(model.seen_messages) >= 2
    history = model.seen_messages[1]
    assistant = next(message for message in history if message.get("role") == "assistant")
    tool_result = next(message for message in history if message.get("role") == "tool")
    assert assistant["tool_calls"][0]["id"] == tool_result["tool_call_id"]
    assert assistant["tool_calls"][0]["function"]["name"] == "add"


def test_message_builder_merges_runtime_context_into_last_real_tool_result():
    class _Model:
        model = "demo-model"

        def __init__(self):
            self.calls = 0
            self.seen_messages = []
            self.qitos_harness_metadata = {
                "tool_policy": {"native_tool_call_preferred": True}
            }

        def call_raw(self, messages, **kwargs):
            _ = kwargs
            self.calls += 1
            self.seen_messages.append(list(messages))
            if self.calls == 1:
                return {
                    "tool_calls": [
                        {
                            "id": "call_first",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a": 1, "b": 2}'},
                        },
                        {
                            "id": "call_last",
                            "type": "function",
                            "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                        },
                    ]
                }
            return "Final Answer: done"

    class _Agent(_ToolCallAgent):
        def __init__(self, llm):
            super().__init__(llm)
            self.message_builder = _RuntimeContextBuilder()

        def decide(self, state, observation):
            _ = state, observation
            return None

    model = _Model()
    agent = _Agent(model)
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=3)).run("compute")

    assert result.state.final_result == "done"
    second = model.seen_messages[1]
    assert [message["role"] for message in second] == [
        "system", "user", "assistant", "tool", "tool"
    ]
    first_tool, last_tool = second[-2:]
    assert first_tool["tool_call_id"] == "call_first"
    assert "<RUNTIME_CONTEXT" not in first_tool["content"]
    assert last_tool["tool_call_id"] == "call_last"
    assert "42" in last_tool["content"]
    assert "NOT part of the tool result" in last_tool["content"]
    assert "authoritative state for the next action" in last_tool["content"]
    assert all(
        message.role != "user" or "RUNTIME_CONTEXT" not in str(message.content)
        for message in agent.history.messages
    )


def test_message_builder_can_request_legacy_runtime_user_delivery():
    class _Model:
        model = "demo-model"

        def __init__(self):
            self.calls = 0
            self.seen_messages = []
            self.qitos_harness_metadata = {
                "tool_policy": {"native_tool_call_preferred": True}
            }

        def call_raw(self, messages, **kwargs):
            _ = kwargs
            self.calls += 1
            self.seen_messages.append(list(messages))
            if self.calls == 1:
                return {
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "add", "arguments": '{"a": 20, "b": 22}'},
                    }]
                }
            return "Final Answer: done"

    class _Agent(_ToolCallAgent):
        def __init__(self, llm):
            super().__init__(llm)
            self.message_builder = _RuntimeContextBuilder(delivery="user")

        def decide(self, state, observation):
            _ = state, observation
            return None

    model = _Model()
    result = Engine(agent=_Agent(model), budget=RuntimeBudget(max_steps=3)).run("compute")

    assert result.state.final_result == "done"
    second = model.seen_messages[1]
    assert second[-1]["role"] == "user"
    assert "<RUNTIME_CONTEXT" in second[-1]["content"]
    assert "<RUNTIME_CONTEXT" not in second[-2]["content"]


def test_message_builder_falls_back_to_user_without_a_real_tool_result():
    class _Model:
        model = "demo-model"

        def __init__(self):
            self.seen_messages = []

        def call_raw(self, messages, **kwargs):
            _ = kwargs
            self.seen_messages.append(list(messages))
            return "Final Answer: done"

    class _Agent(_ToolCallAgent):
        def __init__(self, llm):
            super().__init__(llm)
            self.message_builder = _RuntimeContextBuilder()

    model = _Model()
    agent = _Agent(model)
    engine = Engine(agent=agent, budget=RuntimeBudget(max_steps=2))
    state = agent.init_state("compute")
    state.current_step = 1

    engine._model_runtime._run_llm_decide(state, {}, StepRecord(step_id=1))

    request = model.seen_messages[0]
    assert [message["role"] for message in request] == ["system", "user", "user"]
    assert "<RUNTIME_CONTEXT" in request[-1]["content"]


def test_runtime_context_merge_rejects_nontext_tool_content_and_detects_multimodal_input():
    runtime = Engine(
        agent=_ToolCallAgent(llm=None), budget=RuntimeBudget(max_steps=1)
    )._model_runtime
    merged, target = runtime._merge_runtime_context_into_last_tool(
        [{"role": "tool", "tool_call_id": "call_1", "content": [{"type": "text", "text": "ok"}]}],
        "state",
    )

    assert merged is False
    assert target is None
    assert runtime._current_user_has_multimodal_content(
        [{"type": "image_url", "url": "https://example.com/input.png"}],
        observation={},
        record=StepRecord(step_id=1),
    )
