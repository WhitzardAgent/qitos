from types import SimpleNamespace

from qitos import Action, AgentModule, Decision, Engine, ToolRegistry, tool
from qitos.core.history import History, HistoryMessage
from qitos.core.state import StateSchema
from qitos.engine import RuntimeBudget
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
