from __future__ import annotations

from types import SimpleNamespace

from qitos.engine.action_executor import ActionExecutor


class _Hook:
    def __init__(self) -> None:
        self.context = None

    def on_after_tool_use(self, ctx, _engine) -> None:
        self.context = ctx


def test_native_tool_hook_uses_active_engine_step() -> None:
    """Tool provenance must identify the decision round that produced it."""
    hook = _Hook()
    engine = SimpleNamespace(hooks=[hook], _active_state=SimpleNamespace(current_step=17))
    executor = ActionExecutor.__new__(ActionExecutor)
    executor._engine = engine

    executor._dispatch_tool_hook("on_after_tool_use", "READ", {"path": "repo-vul/a.c"}, {"ok": True})

    assert hook.context is not None
    assert hook.context.step_id == 17
    assert hook.context.state is engine._active_state
