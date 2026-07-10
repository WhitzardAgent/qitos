from types import SimpleNamespace

from qitos.core.message_builder import MessageBuildRequest
from qitos import Engine
from qitos.engine import RuntimeBudget
from qitos.prompting import PromptBuildResult

from cybergym_agent.agent import CyberGymAgent
from cybergym_agent.message_builder import CyberGymMessageBuilder


def test_legacy_cybergym_builder_uses_task_then_assistant_tool_history(monkeypatch):
    monkeypatch.delenv("CYBERGYM_OBSERVATION_DELIVERY", raising=False)
    builder = CyberGymMessageBuilder()
    bundle = PromptBuildResult(system_prompt_static="CyberGym system")
    state = SimpleNamespace(task="produce a PoC")

    initial = builder.build_messages(
        MessageBuildRequest(
            step_id=0,
            state=state,
            observation=None,
            prompt_bundle=bundle,
            prepared="initial task analysis",
            history=[],
            record=SimpleNamespace(),
        )
    )
    assert initial.runtime_context is None
    assert [message["role"] for message in initial.messages] == ["system", "user"]
    assert initial.messages[-1]["content"] == "initial task analysis"

    follow_up = builder.build_messages(
        MessageBuildRequest(
            step_id=1,
            state=state,
            observation=None,
            prompt_bundle=bundle,
            prepared="fresh working state",
            history=[
                {"role": "user", "content": "stale runtime state"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": "call_1", "function": {"name": "READ", "arguments": "{}"}}],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "source output"},
            ],
            record=SimpleNamespace(),
        )
    )

    assert [message["role"] for message in follow_up.messages] == [
        "system", "user", "assistant", "tool"
    ]
    assert follow_up.messages[1]["content"] == "produce a PoC"
    assert follow_up.runtime_context == "fresh working state"
    assert follow_up.runtime_context_delivery == "merge_tool"


def test_legacy_cybergym_agent_keeps_native_tool_chain_and_folds_runtime_context(
    tmp_path,
):
    class _Model:
        model = "test-native"
        qitos_harness_metadata = {
            "tool_policy": {"native_tool_call_preferred": True}
        }

        def __init__(self):
            self.calls = []

        def call_raw(self, messages, **kwargs):
            _ = kwargs
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return {
                    "tool_calls": [{
                        "id": "call_glob",
                        "type": "function",
                        "function": {
                            "name": "GLOB",
                            "arguments": '{"pattern": "*.txt"}',
                        },
                    }]
                }
            return "Final Answer: done"

    (tmp_path / "sample.txt").write_text("sample", encoding="utf-8")
    model = _Model()
    agent = CyberGymAgent(
        llm=model, workspace_root=str(tmp_path), task_root=str(tmp_path)
    )
    result = Engine(agent=agent, budget=RuntimeBudget(max_steps=3)).run(
        "find the sample file"
    )

    assert result.state.final_result == "done"
    second_request = model.calls[1]
    assert [message["role"] for message in second_request] == [
        "system", "user", "assistant", "tool"
    ]
    last_tool = second_request[-1]
    assert last_tool["tool_call_id"] == "call_glob"
    assert "NOT part of the tool result" in last_tool["content"]
    assert "<RUNTIME_CONTEXT" in last_tool["content"]
    assert not any(
        message.role == "user" and "RUNTIME_CONTEXT" in str(message.content)
        for message in agent.history.messages
    )


def test_legacy_cybergym_builder_honors_user_delivery_escape_hatch(monkeypatch):
    monkeypatch.setenv("CYBERGYM_OBSERVATION_DELIVERY", "user")
    result = CyberGymMessageBuilder().build_messages(
        MessageBuildRequest(
            step_id=1,
            state=SimpleNamespace(task="produce a PoC"),
            observation=None,
            prompt_bundle=PromptBuildResult(system_prompt_static="CyberGym system"),
            prepared="fresh working state",
            history=[],
            record=SimpleNamespace(),
        )
    )

    assert result.runtime_context_delivery == "user"
