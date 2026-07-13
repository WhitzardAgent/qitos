from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qitos import (
    Action,
    AgentModule,
    Decision,
    Engine,
    HistoryPolicy,
    StateSchema,
    ToolRegistry,
    tool,
)
from qitos.engine import RuntimeBudget
from qitos.kit.history import CompactHistory, MessageGrouper
from qitos.kit.parser import ReActTextParser


def test_message_grouper_prefers_step_rounds() -> None:
    from qitos.core.history import HistoryMessage

    grouper = MessageGrouper()
    groups = grouper.group(
        [
            HistoryMessage(role="system", content="s0", step_id=0),
            HistoryMessage(role="user", content="u0", step_id=0),
            HistoryMessage(role="assistant", content="a0", step_id=0),
            HistoryMessage(role="user", content="u1", step_id=1),
            HistoryMessage(role="assistant", content="a1", step_id=1),
        ]
    )

    assert len(groups) == 2
    assert [len(group) for group in groups] == [3, 2]
    assert [msg.step_id for msg in groups[-1]] == [1, 1]


def test_compact_history_emits_microcompact_and_summary_events() -> None:
    from qitos.core.history import HistoryMessage

    history = CompactHistory(
        max_tokens=90, keep_last_rounds=1, keep_last_messages=4, hard_window=20
    )
    for idx in range(6):
        role = "user" if idx % 2 == 0 else "assistant"
        history.append(
            HistoryMessage(
                role=role,
                content=(f"message {idx} " + "with verbose context " * 80).strip(),
                step_id=idx,
                metadata={"source": "engine"},
            )
        )

    retrieved = history.retrieve(
        query={
            "roles": ["user", "assistant"],
            "max_items": 12,
            "max_tokens": 90,
            "pending_content": "next prompt with another long continuation",
        }
    )
    events = history.consume_runtime_events()
    metadata = history.get_last_message_metadata()

    assert retrieved
    assert retrieved[0].metadata.get("summary") is True
    assert any(
        event.get("stage") == "context_history"
        and (event.get("context") or {}).get("stage") == "warning"
        for event in events
    )
    assert any(
        event.get("stage") == "context_history"
        and (event.get("context") or {}).get("stage") == "microcompact_applied"
        for event in events
    )
    assert any(
        event.get("stage") == "context_history"
        and (event.get("context") or {}).get("stage") == "summary_compact_applied"
        for event in events
    )
    assert metadata[0].get("summary") is True
    assert metadata[0].get("source") == "compact_history"


@dataclass
class CompactDemoState(StateSchema):
    logs: list[str] = field(default_factory=list)


class CompactDemoAgent(AgentModule[CompactDemoState, dict[str, Any], Action]):
    def __init__(self):
        registry = ToolRegistry()

        @tool(name="add")
        def add(a: int, b: int) -> int:
            return a + b

        registry.register(add)
        super().__init__(tool_registry=registry)

    def init_state(self, task: str, **kwargs: Any) -> CompactDemoState:
        return CompactDemoState(task=task, max_steps=3)

    def reduce(
        self,
        state: CompactDemoState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> CompactDemoState:
        action_results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if action_results:
            state.logs.append(str(action_results[0]))
        return state


def test_engine_surfaces_compact_events_and_history_metadata() -> None:
    calls: list[list[dict[str, str]]] = []

    class _DummyModel:
        model = "dummy-compact"

        def __call__(self, messages):
            calls.append(list(messages))
            if len(calls) == 1:
                return "Action: add(a=20, b=22)"
            return "Final Answer: 42"

    class LLMCompactAgent(CompactDemoAgent):
        def __init__(self):
            super().__init__()
            self.llm = _DummyModel()
            self.model_parser = ReActTextParser()
            self.history = CompactHistory(
                max_tokens=110, keep_last_rounds=1, keep_last_messages=4, hard_window=24
            )

        def build_system_prompt(self, state: CompactDemoState) -> str | None:
            return "Compact system prompt"

        def prepare(self, state: CompactDemoState) -> str:
            return (
                f"Task={state.task}\n"
                f"Step={state.current_step}\n"
                + ("Observation context and scratchpad detail. " * 50)
            ).strip()

        def decide(self, state: CompactDemoState, observation: dict[str, Any]):
            return None

    result = Engine(
        agent=LLMCompactAgent(),
        budget=RuntimeBudget(max_steps=3),
        history_policy=HistoryPolicy(max_messages=10, max_tokens=110),
    ).run("compute")

    assert result.state.final_result == "42"
    assert len(calls) == 2
    compact_stages = [
        (event.payload.get("context") or {}).get("stage")
        for event in result.events
        if getattr(event.phase, "value", event.phase) == "DECIDE"
        and event.payload.get("stage") == "context_history"
    ]
    assert "warning" in compact_stages
    assert any(
        stage in {"microcompact_applied", "summary_compact_applied"}
        for stage in compact_stages
    )

    model_input_events = [
        event for event in result.events if event.payload.get("stage") == "model_input"
    ]
    assert model_input_events
    history_meta = model_input_events[-1].payload.get("history_messages_meta", [])
    assert isinstance(history_meta, list)
    assert history_meta
    assert any(item.get("summary") or item.get("compacted") for item in history_meta)
    context = model_input_events[-1].payload.get("context", {})
    assert context.get("input_tokens_total", 0) > 0


def test_native_tool_history_keeps_only_complete_atomic_transactions() -> None:
    from qitos.engine._model_runtime import _ModelRuntime
    runtime = object.__new__(_ModelRuntime)
    history = [
        {"role": "assistant", "_step_id": 1, "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "_step_id": 1, "tool_call_id": "a", "content": "one"},
        {"role": "tool", "_step_id": 1, "tool_call_id": "b", "content": "two"},
        {"role": "assistant", "_step_id": 2, "tool_calls": [{"id": "dangling"}]},
        {"role": "assistant", "_step_id": 3, "tool_calls": [{"id": "c"}]},
        {"role": "tool", "_step_id": 3, "tool_call_id": "c", "content": "three"},
    ]
    result = runtime._trim_native_tool_history(history, max_rounds=16)
    assert {m.get("_step_id") for m in result} == {1, 3}
    consistent = runtime._ensure_chain_consistency(result)
    assert all("Tool execution was interrupted" not in str(m.get("content", "")) for m in consistent)
