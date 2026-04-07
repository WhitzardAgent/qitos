from typing import Any

from qitos import Action, AgentModule, Decision, Engine, StateSchema, ToolRegistry, tool
from qitos.engine import RuntimeBudget
from qitos.kit import tool as tool_pkg
from qitos.kit.tool import CodebaseToolSet, CodingToolSet, EditorToolSet, EpubToolSet, NotebookToolSet, ReportToolSet, TaskToolSet, ThinkingToolSet


class _ToolState(StateSchema):
    pass


class _Agent(AgentModule[_ToolState, dict[str, Any], Action]):
    def __init__(self, tool_registry: ToolRegistry):
        super().__init__(tool_registry=tool_registry)

    def init_state(self, task: str, **kwargs: Any) -> _ToolState:
        return _ToolState(task=task, max_steps=2)

    def decide(self, state: _ToolState, observation: dict[str, Any]) -> Decision[Action]:
        if state.current_step == 0:
            return Decision.act([Action(name="math.add", args={"a": 1, "b": 2})])
        return Decision.final("done")

    def reduce(self, state: _ToolState, observation: dict[str, Any], decision: Decision[Action]) -> _ToolState:
        return state


def test_tool_registry_include_and_toolset_lifecycle(tmp_path):
    events: list[str] = []

    class MathToolSet:
        name = "math"
        version = "1"

        def setup(self, context: dict[str, Any]) -> None:
            events.append("setup")

        def teardown(self, context: dict[str, Any]) -> None:
            events.append("teardown")

        @tool(name="add")
        def add(self, a: int, b: int) -> int:
            return a + b

        def tools(self):
            return [self.add]

    registry = ToolRegistry()
    registry.register_toolset(MathToolSet())
    result = Engine(agent=_Agent(tool_registry=registry), budget=RuntimeBudget(max_steps=2)).run("x")
    assert result.state.stop_reason == "final"
    assert events == ["setup", "teardown"]

    editor = ToolRegistry()
    editor.include(EditorToolSet(workspace_root=str(tmp_path)))
    assert "view" in editor.list_tools()
    assert "str_replace" in editor.list_tools()


def test_curated_toolsets_register_cleanly(tmp_path):
    toolsets = [
        EditorToolSet(workspace_root=str(tmp_path)),
        CodebaseToolSet(workspace_root=str(tmp_path)),
        NotebookToolSet(workspace_root=str(tmp_path)),
        ReportToolSet(workspace_root=str(tmp_path)),
        TaskToolSet(workspace_root=str(tmp_path)),
        EpubToolSet(workspace_root=str(tmp_path)),
        ThinkingToolSet(),
        CodingToolSet(workspace_root=str(tmp_path)),
    ]
    for toolset in toolsets:
        registry = ToolRegistry()
        registry.register_toolset(toolset, namespace="")
        assert registry.list_tools(), f"{toolset.__class__.__name__} registered no tools"


def test_tool_package_does_not_export_uncurated_cyber_toolsets():
    exported = set(getattr(tool_pkg, "__all__", []))
    assert "ReportToolSet" in exported
    assert "ReconToolSet" not in exported
    assert "NetworkToolSet" not in exported
    assert "VulnScanToolSet" not in exported
    assert "WebTestToolSet" not in exported
    assert "ExploitToolSet" not in exported
    assert "PasswordToolSet" not in exported
    assert "ExploitToolSet" not in exported
    assert "PasswordToolSet" not in exported
