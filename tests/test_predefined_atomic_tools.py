from __future__ import annotations

import json
from pathlib import Path

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.advanced import AdvancedCodingToolSet
from qitos.kit.tool.experimental.security_research import (
    SecurityAuditToolSet,
    security_audit_tools,
    security_research_tools,
)
from qitos.kit.tool import (
    CodingToolSet,
    NotebookToolSet,
    ReportToolSet,
    TaskToolSet,
    math_tools,
    coding_tools,
    codebase_tools,
    notebook_tools,
    report_tools,
    task_tools,
    web_tools,
)
from qitos.kit.tool.tools import advanced_coding_tools


def test_codebase_toolset_glob_grep_read_append(tmp_path):
    root = tmp_path
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text(
        "def add(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (root / "src" / "b.md").write_text("hello world\nhello qitos\n", encoding="utf-8")

    toolset = CodingToolSet(
        workspace_root=str(root),
        include_notebook=False,
        enable_lsp=False,
        enable_tasks=False,
        enable_web=False,
        expose_modern_names=False,
        profile="codebase",
    )

    glob_out = toolset.glob_files(pattern="*.py")
    assert glob_out["status"] == "success"
    assert glob_out["files"] == ["src/a.py"]

    grep_out = toolset.grep_files(pattern="hello", glob="*.md", regex=False)
    assert grep_out["status"] == "success"
    assert grep_out["num_matches"] == 2
    assert grep_out["matches"][0]["path"] == "src/b.md"

    read_out = toolset.read_file_range(path="src/a.py", offset=1, limit=1)
    assert read_out["status"] == "success"
    assert read_out["offset"] == 1
    assert read_out["limit"] == 1
    assert "lines" not in read_out
    assert "return a + b" in read_out["content"]

    append_out = toolset.append_file(path="src/b.md", content="extra\n")
    assert append_out["status"] == "success"
    assert (root / "src" / "b.md").read_text(encoding="utf-8").endswith("extra\n")


def test_notebook_toolset_read_replace_insert(tmp_path):
    nb = {
        "cells": [
            {"cell_type": "markdown", "metadata": {}, "source": ["# Title\n"]},
            {
                "cell_type": "code",
                "metadata": {},
                "source": ["print('hi')\n"],
                "outputs": [],
                "execution_count": None,
            },
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

    replace_out = toolset.replace_notebook_cell.run(
        path="demo.ipynb", cell_index=1, source="print('bye')\n"
    )
    assert replace_out["status"] == "success"

    insert_out = toolset.insert_notebook_cell.run(
        path="demo.ipynb", cell_type="markdown", source="## Next\n", index=1
    )
    assert insert_out["status"] == "success"

    updated = json.loads(path.read_text(encoding="utf-8"))
    assert len(updated["cells"]) == 3
    assert "".join(updated["cells"][1]["source"]) == "## Next\n"
    assert "".join(updated["cells"][2]["source"]) == "print('bye')\n"


def test_web_fetch_extracts_text(monkeypatch):
    toolset = CodingToolSet(
        include_notebook=False, enable_lsp=False, enable_tasks=False, enable_web=True
    )

    def _fake_get(
        url: str,
        params=None,
        headers=None,
        timeout=None,
        verify_tls=True,
        allow_redirects=True,
    ):
        _ = params
        _ = headers
        _ = timeout
        _ = verify_tls
        _ = allow_redirects
        return {
            "status": "success",
            "url": url,
            "status_code": 200,
            "content_type": "text/html",
            "content": "<html><head><title>Demo</title></head><body><h1>Hello</h1><p>World</p></body></html>",
            "headers": {},
        }

    monkeypatch.setattr(toolset, "http_get", _fake_get)
    out = toolset.web_fetch(url="https://example.com")
    assert out["status"] == "success"
    assert out["title"] == "Demo"
    assert "Hello" in out["content"]


def test_predefined_registry_builders_expose_atomic_tools(tmp_path):
    code_registry = codebase_tools(str(tmp_path))
    notebook_registry = notebook_tools(str(tmp_path))
    web_registry = web_tools()
    coding_registry = coding_tools(str(tmp_path))
    advanced_registry = advanced_coding_tools(str(tmp_path))
    task_registry = task_tools(str(tmp_path))
    report_registry = report_tools(str(tmp_path))
    audit_registry = security_audit_tools(str(tmp_path))

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
    assert "todo_write" in coding_registry.list_tools()
    assert "tool_search" in coding_registry.list_tools()
    assert "bash_v2" not in coding_registry.list_tools()
    assert "bash_v2" not in advanced_registry.list_tools()
    assert "file_read_v2" not in advanced_registry.list_tools()
    assert "file_edit_v2" not in advanced_registry.list_tools()
    assert "tool_search" in advanced_registry.list_tools()
    assert "task_create" in task_registry.list_tools()
    assert "task_update" in task_registry.list_tools()
    assert "finding_add" in report_registry.list_tools()
    assert "generate_report" in report_registry.list_tools()
    assert "audit_inventory" in audit_registry.list_tools()
    assert "audit_hotspots" in audit_registry.list_tools()

    reg = ToolRegistry()
    reg.register_toolset(
        CodingToolSet(
            workspace_root=str(tmp_path),
            include_notebook=False,
            enable_lsp=False,
            enable_tasks=False,
            enable_web=False,
            expose_modern_names=False,
            profile="codebase",
        ),
        namespace="codebase",
    )
    assert (
        reg.describe_tool("codebase.read_file_range")["origin"]["source"] == "toolset"
    )


def test_coding_toolset_collects_editor_shell_and_codebase(tmp_path):
    toolset = CodingToolSet(workspace_root=str(tmp_path))
    names = []
    for item in toolset.tools():
        names.append(
            getattr(
                item, "name", getattr(getattr(item, "__func__", None), "__name__", "")
            )
        )
    assert "run_command" in names
    assert "view" in names
    assert "glob_files" in names
    assert "tool_search" in names
    assert "read_notebook" in names


def test_advanced_coding_toolset_registers_cleanly(tmp_path):
    toolset = AdvancedCodingToolSet(workspace_root=str(tmp_path))
    registry = ToolRegistry()
    registry.register_toolset(toolset, namespace="")
    assert "web_fetch" in registry.list_tools()
    assert "todo_write" in registry.list_tools()


def test_tool_descriptions_come_from_docstrings():
    write_file = coding_tools(".").describe_tool("write_file")
    assert "Write text content to a workspace file." in write_file["description"]
    assert ":param path:" in write_file["description"]

    registry = web_tools()
    spec = next(
        item
        for item in registry.get_all_specs()
        if item["function"]["name"] == "extract_web_text"
    )
    assert "Extract readable text from raw HTML." in spec["function"]["description"]
    assert ":param html:" in spec["function"]["description"]

    math_spec = math_tools().describe_tool("add")
    assert "Return the sum of two integers." in math_spec["description"]
    assert ":param a:" in math_spec["description"]


def test_coding_toolset_uses_one_canonical_schema_surface():
    registry = coding_tools(".")
    names = set(registry.list_tools())
    assert "file_read_v2" not in names
    assert "file_edit_v2" not in names
    assert "bash_v2" not in names
    assert "glob_v2" not in names
    assert "grep_v2" not in names
    assert "web_fetch_v2" not in names

    read_schema = registry.describe_tool("read_file")["input_schema"]["properties"]
    write_schema = registry.describe_tool("write_file")["input_schema"]["properties"]
    append_schema = registry.describe_tool("append_file")["input_schema"]["properties"]
    create_schema = registry.describe_tool("create")["input_schema"]["properties"]
    range_schema = registry.describe_tool("read_file_range")["input_schema"]["properties"]

    assert set(read_schema) == {"path"}
    assert set(write_schema) == {"path", "content"}
    assert set(append_schema) == {"path", "content"}
    assert set(create_schema) == {"path", "content"}
    assert set(range_schema) == {"path", "offset", "limit"}


def test_tool_package_only_exposes_canonical_toolsets():
    exported = set(__import__("qitos.kit.tool", fromlist=["__all__"]).__all__)
    assert "CodingToolSet" in exported
    assert "CodebaseToolSet" not in exported
    assert "EditorToolSet" not in exported
    assert "RunCommand" not in exported
    assert "WriteFile" not in exported


def test_task_toolset_persists_board_updates(tmp_path):
    toolset = TaskToolSet(workspace_root=str(tmp_path))

    create = toolset.task_create.run(
        subject="Implement planner", description="Break the work into phases"
    )
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

    note = toolset.task_append_note.run(
        task_id=task_id, text="Initial decomposition finished", kind="progress"
    )
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


def test_security_audit_toolset_registers_cleanly(tmp_path):
    toolset = SecurityAuditToolSet(workspace_root=str(tmp_path))
    registry = ToolRegistry()
    registry.register_toolset(toolset, namespace="")
    assert "audit_inventory" in registry.list_tools()
    assert "audit_notes_scan" in registry.list_tools()


def test_security_research_tools_require_explicit_import(tmp_path):
    registry = security_research_tools(
        str(tmp_path),
        include_authorized_ops=True,
        authorized_targets=["example.com"],
    )
    names = set(registry.list_tools())
    assert "audit_inventory" in names
    assert "port_scan" in names
    assert "msf_check" in names
    assert "john_crack" in names
    assert "nuclei_scan" in names
