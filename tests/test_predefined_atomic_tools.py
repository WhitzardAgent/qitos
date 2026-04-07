from __future__ import annotations

import json
from pathlib import Path

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool import (
    CodebaseToolSet,
    CodingToolSet,
    NotebookToolSet,
    ReportToolSet,
    TaskToolSet,
    WebFetch,
    WriteFile,
    math_tools,
    coding_tools,
    codebase_tools,
    notebook_tools,
    report_tools,
    task_tools,
    web_tools,
)


def test_codebase_toolset_glob_grep_read_append(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (root / "src" / "b.md").write_text("hello world\nhello qitos\n", encoding="utf-8")

    toolset = CodebaseToolSet(workspace_root=str(root))

    glob_out = toolset.glob_files.run(pattern="*.py")
    assert glob_out["status"] == "success"
    assert glob_out["files"] == ["src/a.py"]

    grep_out = toolset.grep_files.run(pattern="hello", glob="*.md", regex=False)
    assert grep_out["status"] == "success"
    assert grep_out["num_matches"] == 2
    assert grep_out["matches"][0]["path"] == "src/b.md"

    read_out = toolset.read_file_range.run(filename="src/a.py", offset=1, limit=1)
    assert read_out["status"] == "success"
    assert read_out["lines"][0]["line"] == 2
    assert "return a + b" in read_out["content"]

    append_out = toolset.append_file.run(filename="src/b.md", content="extra\n")
    assert append_out["status"] == "success"
    assert (root / "src" / "b.md").read_text(encoding="utf-8").endswith("extra\n")


def test_notebook_toolset_read_replace_insert(tmp_path):
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Title\n"]},
            {"cell_type": "code", "metadata": {}, "source": ["print('hi')\n"], "outputs": [], "execution_count": None},
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path = tmp_path / "demo.ipynb"
    path.write_text(json.dumps(nb), encoding="utf-8")

    toolset = NotebookToolSet(workspace_root=str(tmp_path))
    read_out = toolset.read_notebook.run(path="demo.ipynb")
    assert read_out["status"] == "success"
    assert read_out["cells"][0]["cell_type"] == "markdown"

    replace_out = toolset.replace_notebook_cell.run(path="demo.ipynb", cell_index=1, source="print('bye')\n")
    assert replace_out["status"] == "success"

    insert_out = toolset.insert_notebook_cell.run(path="demo.ipynb", cell_type="markdown", source="## Next\n", index=1)
    assert insert_out["status"] == "success"

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert len(updated["cells"]) == 3
    assert "".join(updated["cells"][1]["source"]) == "## Next\n"
    assert "".join(updated["cells"][2]["source"]) == "print('bye')\n"


def test_web_fetch_extracts_text(monkeypatch):
    tool = WebFetch()

    def _fake_get(url: str, timeout=None):
        return {
            "status": "success",
            "url": url,
            "status_code": 200,
            "content_type": "text/html",
            "content": "<html><head><title>Demo</title></head><body><h1>Hello</h1><p>World</p></body></html>",
        }

    monkeypatch.setattr(tool._http, "run", _fake_get)
    out = tool.run(url="https://example.com")
    assert out["status"] == "success"
    assert out["title"] == "Demo"
    assert "Hello" in out["content"]


def test_predefined_registry_builders_expose_atomic_tools(tmp_path):
    code_registry = codebase_tools(str(tmp_path))
    notebook_registry = notebook_tools(str(tmp_path))
    web_registry = web_tools()
    coding_registry = coding_tools(str(tmp_path))
    task_registry = task_tools(str(tmp_path))
    report_registry = report_tools(str(tmp_path))

    assert "codebase.glob_files" in code_registry.list_tools()
    assert "codebase.grep_files" in code_registry.list_tools()
    assert "read_file" in code_registry.list_tools()
    assert "write_file" in code_registry.list_tools()
    assert "notebook.read_notebook" in notebook_registry.list_tools()
    assert "http_get" in web_registry.list_tools()
    assert "extract_web_text" in web_registry.list_tools()
    assert "web_fetch" in web_registry.list_tools()
    assert "view" in coding_registry.list_tools()
    assert "glob_files" in coding_registry.list_tools()
    assert "run_command" in coding_registry.list_tools()
    assert "task_create" in task_registry.list_tools()
    assert "task_update" in task_registry.list_tools()
    assert "finding_add" in report_registry.list_tools()
    assert "generate_report" in report_registry.list_tools()

    reg = ToolRegistry()
    reg.register_toolset(CodebaseToolSet(workspace_root=str(tmp_path)))
    assert reg.describe_tool("codebase.read_file_range")["origin"]["source"] == "toolset"


def test_coding_toolset_collects_editor_shell_and_codebase(tmp_path):
    toolset = CodingToolSet(workspace_root=str(tmp_path))
    names = []
    for item in toolset.tools():
        names.append(getattr(item, "name", getattr(getattr(item, "__func__", None), "__name__", "")))
    assert "run_command" in names
    assert "view" in names
    assert "glob_files" in names
    assert "read_notebook" in names


def test_tool_descriptions_come_from_docstrings():
    write_file = WriteFile()
    assert "Write text content to a file under the workspace root." in write_file.spec.description
    assert ":param filename:" in write_file.spec.description

    registry = web_tools()
    spec = next(item for item in registry.get_all_specs() if item["function"]["name"] == "extract_web_text")
    assert "Extract readable text from raw HTML." in spec["function"]["description"]
    assert ":param html:" in spec["function"]["description"]

    math_spec = math_tools().describe_tool("add")
    assert "Return the sum of two integers." in math_spec["description"]
    assert ":param a:" in math_spec["description"]


def test_task_toolset_persists_board_updates(tmp_path):
    toolset = TaskToolSet(workspace_root=str(tmp_path))

    create = toolset.task_create.run(subject="Implement planner", description="Break the work into phases")
    assert create["status"] == "success"
    task_id = create["task"]["id"]

    update = toolset.task_update.run(
        task_id=task_id,
        status="in_progress",
        add_blocks=["child-a"],
        metadata={"priority": "high"},
    )
    assert update["status"] == "success"
    assert update["task"]["status"] == "in_progress"
    assert update["task"]["blocks"] == ["child-a"]
    assert update["task"]["metadata"]["priority"] == "high"

    note = toolset.task_append_note.run(task_id=task_id, text="Initial decomposition finished", kind="progress")
    assert note["status"] == "success"

    fetched = toolset.task_get.run(task_id=task_id)
    assert fetched["status"] == "success"
    assert fetched["task"]["notes"][0]["kind"] == "progress"

    listing = toolset.task_list.run(include_completed=False)
    assert listing["status"] == "success"
    assert listing["count"] == 1


def test_report_toolset_registers_and_writes_outputs(tmp_path):
    toolset = ReportToolSet(workspace_root=str(tmp_path))

    added = toolset.finding_add(
        title="Missing security header",
        severity="medium",
        description="The application does not send Content-Security-Policy.",
        remediation="Add a restrictive CSP header.",
    )
    assert added["status"] == "success"

    summary = toolset.summary_generate(target="example.internal")
    assert summary["status"] == "success"
    assert "example.internal" in summary["stdout"]

    report = toolset.generate_report(format="markdown")
    assert report["status"] == "success"
    assert Path(report["data"]["output_file"]).exists()
