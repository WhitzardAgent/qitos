from dataclasses import dataclass
from typing import Any

from qitos import Action, AgentModule, Decision, Engine, Observation, StateSchema, ToolRegistry, tool
from qitos.engine import RuntimeBudget
from qitos.kit import ReActTextParser


@dataclass
class _State(StateSchema):
    pass


class _NativeToolModel:
    model = "test-native"
    max_tokens = 256
    context_window = 8192

    def __init__(self):
        self.calls = 0
        self.seen_messages: list[list[dict[str, Any]]] = []
        self.qitos_harness_metadata = {
            "tool_policy": {"native_tool_call_preferred": True},
            "parser": "ReActTextParser",
            "protocol": "react_text_v1",
        }

    def call_raw(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        _ = kwargs
        self.seen_messages.append(list(messages))
        if self.calls == 0:
            self.calls += 1
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_native_1",
                                    "type": "function",
                                    "function": {"name": "weird_tool", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }
        self.calls += 1
        return {"choices": [{"message": {"content": "Final Answer: done"}}]}


class _NativeToolAgent(AgentModule[_State, Observation, Action]):
    def __init__(self, llm: Any):
        registry = ToolRegistry()

        @tool(name="weird_tool")
        def weird_tool() -> dict[str, Any]:
            return {"payload": {1, 2}}

        registry.register(weird_tool)
        super().__init__(tool_registry=registry, llm=llm, model_parser=ReActTextParser())

    def init_state(self, task: str, **kwargs: Any) -> _State:
        return _State(task=task, max_steps=3)

    def decide(self, state: _State, observation: Observation) -> Decision[Action] | None:
        _ = state
        _ = observation
        return None

    def reduce(self, state: _State, observation: Observation, decision: Decision[Action]) -> _State:
        _ = observation
        _ = decision
        return state


class _HarnessAwareModel:
    def __init__(self):
        self.qitos_harness_metadata = {
            "parser": "ReActTextParser",
            "protocol": "react_text_v1",
        }

    def __call__(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        _ = messages
        _ = kwargs
        return "Final Answer: auto harness parser worked"


class _HarnessAgent(AgentModule[_State, Observation, Action]):
    def __init__(self):
        super().__init__(llm=_HarnessAwareModel())

    def init_state(self, task: str, **kwargs: Any) -> _State:
        _ = kwargs
        return _State(task=task, max_steps=2)

    def decide(self, state: _State, observation: Observation) -> Decision[Action] | None:
        _ = state
        _ = observation
        return None

    def reduce(self, state: _State, observation: Observation, decision: Decision[Action]) -> _State:
        _ = observation
        _ = decision
        return state


def test_native_tool_chain_preserves_tool_call_history_and_non_json_result() -> None:
    llm = _NativeToolModel()
    agent = _NativeToolAgent(llm=llm)
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=3)).run("native")
    assert result.state.final_result == "done"
    assert len(llm.seen_messages) >= 2
    second_call = llm.seen_messages[1]
    assistant_msgs = [msg for msg in second_call if msg.get("role") == "assistant"]
    tool_msgs = [msg for msg in second_call if msg.get("role") == "tool"]
    assert assistant_msgs
    assert tool_msgs
    assert assistant_msgs[-1].get("tool_calls")
    assert tool_msgs[-1].get("tool_call_id") == "call_native_1"
    tool_content = str(tool_msgs[-1].get("content", ""))
    assert "1" in tool_content and "2" in tool_content


def test_agent_run_auto_applies_harness_parser_defaults() -> None:
    agent = _HarnessAgent()
    output = agent.run("auto-parser", trace=False, render=False)
    assert output == "auto harness parser worked"
