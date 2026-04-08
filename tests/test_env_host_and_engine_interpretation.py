from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from qitos import Action, AgentModule, Decision, Engine, StateSchema, ToolRegistry
from qitos.core.tool import BaseTool, ToolPermission, ToolSpec
from qitos.engine import RuntimeBudget
from qitos.kit.env import HostEnv


class _OpsWriteFile(BaseTool):
    def __init__(self):
        super().__init__(
            ToolSpec(
                name="write_file",
                description="Write a file through env file ops.",
                parameters={
                    "filename": {"type": "string"},
                    "content": {"type": "string"},
                },
                required=["filename", "content"],
                permissions=ToolPermission(filesystem_write=True),
                required_ops=["file"],
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        ctx = runtime_context or {}
        ops = dict(ctx.get("ops") or {})
        file_ops = ops.get("file")
        if file_ops is None:
            return {"status": "error", "message": "Missing file ops"}
        filename = str(args.get("filename", ""))
        content = str(args.get("content", ""))
        file_ops.write_text(filename, content)
        return {"status": "success", "path": filename, "size": len(content)}


def test_host_env_replace_lines_and_command(tmp_path: Path):
    target = tmp_path / "m.py"
    target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    env = HostEnv(workspace_root=str(tmp_path))

    out = env.execute_action(
        Action(
            name="replace_lines",
            args={
                "path": "m.py",
                "start_line": 2,
                "end_line": 2,
                "replacement": "    return a + b",
            },
        )
    )
    assert isinstance(out, dict) and out.get("status") == "success"
    assert "return a + b" in target.read_text(encoding="utf-8")

    run = env.execute_action(
        Action(name="run_command", args={"command": 'python -c "print(42)"'})
    )
    assert isinstance(run, dict)
    assert int(run.get("returncode", 1)) == 0


@dataclass
class _State(StateSchema):
    done: bool = False


class _EnvOnlyAgent(AgentModule[_State, Dict[str, Any], Action]):
    def __init__(self):
        super().__init__(tool_registry=None)

    def init_state(self, task: str, **kwargs: Any) -> _State:
        return _State(task=task, max_steps=2)

    def decide(self, state: _State, observation: Dict[str, Any]):
        if state.current_step == 0:
            return Decision.act(
                actions=[
                    Action(
                        name="write_file",
                        args={"filename": "x.txt", "content": "hello"},
                    )
                ]
            )
        return Decision.final("done")

    def reduce(
        self, state: _State, observation: Dict[str, Any], decision: Decision[Action]
    ) -> _State:
        if decision.mode == "final":
            state.done = True
        return state


def test_engine_executes_ops_aware_tool_with_env(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(_OpsWriteFile())

    class _EnvOpsAgent(_EnvOnlyAgent):
        def __init__(self):
            super().__init__()
            self.tool_registry = registry

    env = HostEnv(workspace_root=str(tmp_path))
    engine = Engine(agent=_EnvOpsAgent(), env=env, budget=RuntimeBudget(max_steps=3))
    result = engine.run("write file")
    assert result.state.final_result == "done"
    assert (tmp_path / "x.txt").exists()
    assert (tmp_path / "x.txt").read_text(encoding="utf-8") == "hello"


def test_engine_fails_when_required_ops_missing_env(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(_OpsWriteFile())

    class _NoEnvAgent(_EnvOnlyAgent):
        def __init__(self):
            super().__init__()
            self.tool_registry = registry

    result = Engine(agent=_NoEnvAgent(), budget=RuntimeBudget(max_steps=2)).run(
        "write file"
    )
    assert result.state.stop_reason == "env_capability_mismatch"
    assert result.step_count == 0
    assert result.events
    end_events = [e for e in result.events if e.phase.value == "END"]
    assert end_events
    issues = end_events[-1].payload.get("issues", [])
    assert issues and issues[0].get("code") == "ENV_REQUIRED_OPS_MISSING"
