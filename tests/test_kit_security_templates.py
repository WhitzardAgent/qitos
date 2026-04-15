from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from qitos.core.memory import MemoryRecord
from qitos.kit import MemdirMemory, SecurityAuditAgent
from qitos.kit.planning import PhaseEngine, PhaseSpec, TransitionRule
from qitos.kit.tool import WorkspaceAwareMixin


@dataclass
class _PhaseState:
    current_phase: str = "investigation"
    has_findings: bool = False


def test_phase_engine_prefers_high_priority_condition_over_force() -> None:
    engine = PhaseEngine(
        [
            PhaseSpec(
                name="investigation",
                transitions=[
                    TransitionRule(
                        target="verification",
                        condition=lambda s: bool(s.has_findings),
                        priority=10,
                    ),
                    TransitionRule(target="formulation", force_at_step=9, priority=1),
                ],
            ),
            PhaseSpec(name="formulation"),
            PhaseSpec(name="verification"),
        ]
    )
    state = _PhaseState(current_phase="investigation", has_findings=True)
    assert engine.advance(state, step=9) == "verification"
    state.has_findings = False
    state.current_phase = "investigation"
    assert engine.advance(state, step=9) == "formulation"


def test_memdir_memory_roundtrip(tmp_path: Path) -> None:
    memory = MemdirMemory(memory_dir=str(tmp_path / ".memdir"))
    memory.append(
        MemoryRecord(
            role="feedback",
            content="Use taint-flow tracing before writing PoC.",
            step_id=3,
            metadata={"type": "feedback"},
        )
    )
    items = memory.retrieve({"type": "feedback"})
    assert items
    assert "taint-flow tracing" in str(items[-1].content)
    summary = memory.summarize(max_items=20)
    assert "feedback" in summary
    assert (tmp_path / ".memdir" / "MEMORY.md").exists()


def test_workspace_aware_mixin_path_guard_and_recent_files(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    file_path = tmp_path / "src" / "app.py"
    file_path.write_text("print('ok')\n", encoding="utf-8")
    helper = WorkspaceAwareMixin(workspace_root=str(tmp_path))
    resolved = helper.resolve_path("src/app.py")
    assert resolved.endswith("src/app.py")
    helper.note_recent_file("src/app.py")
    summary = helper.workspace_summary(max_entries=10, max_depth=2)
    assert "src/app.py" in list(summary.get("sample_files", []))
    assert "src/app.py" in list(summary.get("recent_files", []))
    with pytest.raises(PermissionError):
        helper.resolve_path("../outside.txt")


class _StaticFinalModel:
    def __call__(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        _ = messages
        _ = kwargs
        return "Final Answer: audit complete"


def test_security_audit_agent_template_runs_minimal_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
    agent = SecurityAuditAgent(llm=_StaticFinalModel(), workspace_root=str(workspace))
    result = agent.run(
        task="audit this repo",
        workspace=str(workspace),
        max_steps=2,
        trace=False,
        render=False,
        return_state=True,
    )
    assert result.state.final_result == "audit complete"
    assert result.state.stop_reason == "final"
