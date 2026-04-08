from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

import qitos
from qitos import Action, AgentModule, Decision, Engine, StateSchema, StopReason
from qitos.core.memory import Memory, MemoryRecord
from qitos.engine import RuntimeBudget


@dataclass
class _S(StateSchema):
    pass


class _FinalAgent(AgentModule[_S, Dict[str, Any], Action]):
    def init_state(self, task: str, **kwargs: Any) -> _S:
        return _S(task=task, max_steps=3)

    def decide(self, state: _S, observation: Dict[str, Any]):
        return Decision.final(f"done:{observation['task']}")

    def reduce(
        self, state: _S, observation: Dict[str, Any], decision: Decision[Action]
    ) -> _S:
        return state


class _CountingMemory(Memory):
    def __init__(self):
        self.records: list[MemoryRecord] = []
        self.reset_count = 0

    def append(self, record: MemoryRecord) -> None:
        self.records.append(record)

    def retrieve(self, query=None, state=None, observation=None):
        return []

    def summarize(self, max_items: int = 5) -> str:
        return ""

    def evict(self) -> int:
        return 0

    def reset(self, run_id=None) -> None:
        self.reset_count += 1
        self.records = []


def test_engine_resets_runtime_state_between_runs():
    memory = _CountingMemory()
    agent = _FinalAgent()
    agent.memory = memory
    engine = Engine(agent=agent, budget=RuntimeBudget(max_steps=3))

    r1 = engine.run("a")
    r2 = engine.run("b")

    assert r1.step_count == 1
    assert r2.step_count == 1
    assert len(r2.records) == 1
    assert len(r2.events) > 0
    assert memory.reset_count == 2


def test_state_rejects_unknown_stop_reason():
    s = _S(task="x", max_steps=2)
    try:
        s.set_stop("unknown_reason")
        assert False, "expected ValueError"
    except ValueError:
        pass
    s.set_stop(StopReason.FINAL)
    assert s.stop_reason == StopReason.FINAL.value


def test_public_api_surface_is_core_first():
    assert hasattr(qitos, "Engine")
    assert hasattr(qitos, "AgentModule")
    assert not hasattr(qitos, "Parser")
    assert not hasattr(qitos, "Search")


def test_engine_package_does_not_export_internal_strategy_types():
    import qitos.engine as qe

    assert hasattr(qe, "Engine")
    assert hasattr(qe, "RuntimeBudget")
    assert not hasattr(qe, "Parser")
    assert not hasattr(qe, "Search")
    assert not hasattr(qe, "Critic")
    assert not hasattr(qe, "RecoveryPolicy")
