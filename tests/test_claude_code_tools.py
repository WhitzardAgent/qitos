"""Tests for Phase 2: tool aliases, sub-agents, cron, worktree manager."""

import os
import tempfile
import pytest
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

from qitos.kit import CodingToolSet
from qitos.kit.tool.internal.coding_utils import (
    is_image_file,
    is_pdf_file,
    is_notebook_file,
    read_image_as_base64,
    read_pdf_text,
    read_notebook_cells,
)


# ── Claude Code tool aliases ──────────────────────────────────────────────────


class TestToolAliases:
    """Verify that Claude Code modern-name aliases are registered."""

    def _get_tool_names(self, **kwargs):
        ts = CodingToolSet(workspace_root=".", expose_modern_names=True, **kwargs)
        tools = ts.tools()
        names = set()
        for t in tools:
            if hasattr(t, "name"):
                names.add(t.name)
            elif hasattr(t, "__name__"):
                names.add(t.__name__)
        return names

    def test_read_alias_exists(self):
        names = self._get_tool_names()
        assert "Read" in names

    def test_edit_alias_exists(self):
        names = self._get_tool_names()
        assert "Edit" in names

    def test_write_alias_exists(self):
        names = self._get_tool_names()
        assert "Write" in names

    def test_glob_alias_exists(self):
        names = self._get_tool_names()
        assert "Glob" in names

    def test_grep_alias_exists(self):
        names = self._get_tool_names()
        assert "Grep" in names

    def test_bash_alias_exists(self):
        names = self._get_tool_names()
        assert "Bash" in names

    def test_webfetch_alias_exists(self):
        names = self._get_tool_names(enable_web=True)
        assert "WebFetch" in names

    def test_askuserquestion_alias_exists(self):
        names = self._get_tool_names()
        assert "AskUserQuestion" in names

    def test_no_modern_names_by_default(self):
        ts = CodingToolSet(workspace_root=".", expose_modern_names=False)
        tools = ts.tools()
        names = set()
        for t in tools:
            if hasattr(t, "name"):
                names.add(t.name)
            elif hasattr(t, "__name__"):
                names.add(t.__name__)
        assert "Read" not in names
        assert "Edit" not in names
        assert "Write" not in names

    def test_legacy_and_modern_coexist(self):
        ts = CodingToolSet(
            workspace_root=".",
            expose_modern_names=True,
            expose_legacy_aliases=True,
        )
        tools = ts.tools()
        names = set()
        for t in tools:
            if hasattr(t, "name"):
                names.add(t.name)
            elif hasattr(t, "__name__"):
                names.add(t.__name__)
        # Both old and new should exist
        assert "file_read_v2" in names or "read_file" in names
        assert "Read" in names


# ── File detection helpers ────────────────────────────────────────────────────


class TestFileDetection:
    def test_image_extensions(self):
        assert is_image_file("photo.png")
        assert is_image_file("photo.jpg")
        assert is_image_file("photo.jpeg")
        assert is_image_file("photo.gif")
        assert is_image_file("photo.webp")
        assert is_image_file("photo.svg")
        assert not is_image_file("photo.txt")
        assert not is_image_file("photo.py")

    def test_pdf_extensions(self):
        assert is_pdf_file("doc.pdf")
        assert not is_pdf_file("doc.txt")
        assert not is_pdf_file("doc.py")

    def test_notebook_extensions(self):
        assert is_notebook_file("analysis.ipynb")
        assert not is_notebook_file("analysis.py")
        assert not is_notebook_file("analysis.json")

    def test_case_insensitive(self):
        assert is_image_file("Photo.PNG")
        assert is_pdf_file("Doc.PDF")
        assert is_notebook_file("Analysis.IPYNB")


# ── Image reading ─────────────────────────────────────────────────────────────


class TestImageReading:
    def test_read_image_as_base64_nonexistent(self):
        result = read_image_as_base64("/nonexistent/path/image.png")
        assert result is None

    def test_read_image_as_base64_real_file(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            # Write a minimal PNG header
            f.write(b"\x89PNG\r\n\x1a\n")
            f.flush()
            path = f.name

        try:
            result = read_image_as_base64(path)
            assert result is not None
            assert result.startswith("data:image/png;base64,")
        finally:
            os.unlink(path)


# ── CronScheduler ─────────────────────────────────────────────────────────────


class TestCronScheduler:
    def test_create_job(self):
        from qitos.kit.tool.cron import CronScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(workspace_root=tmpdir)
            job = scheduler.create_job(
                cron="*/5 * * * *",
                prompt="test prompt",
                recurring=True,
                durable=False,
            )
            assert job.id.startswith("cron-")
            assert job.cron == "*/5 * * * *"
            assert job.prompt == "test prompt"
            assert job.recurring is True

    def test_list_jobs(self):
        from qitos.kit.tool.cron import CronScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(workspace_root=tmpdir)
            scheduler.create_job(cron="0 9 * * *", prompt="morning check")
            scheduler.create_job(cron="0 17 * * *", prompt="evening check")
            jobs = scheduler.list_jobs()
            assert len(jobs) == 2

    def test_delete_job(self):
        from qitos.kit.tool.cron import CronScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(workspace_root=tmpdir)
            job = scheduler.create_job(cron="0 9 * * *", prompt="test")
            assert scheduler.delete_job(job.id) is True
            assert scheduler.delete_job(job.id) is False
            assert len(scheduler.list_jobs()) == 0

    def test_durable_job_persistence(self):
        from qitos.kit.tool.cron import CronScheduler

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create and persist
            scheduler1 = CronScheduler(workspace_root=tmpdir)
            job = scheduler1.create_job(
                cron="0 9 * * *",
                prompt="persistent task",
                recurring=True,
                durable=True,
            )
            job_id = job.id

            # Load in new scheduler instance
            scheduler2 = CronScheduler(workspace_root=tmpdir)
            jobs = scheduler2.list_jobs()
            assert len(jobs) == 1
            assert jobs[0].id == job_id
            assert jobs[0].prompt == "persistent task"

    def test_fire_callback(self):
        from qitos.kit.tool.cron import CronScheduler

        fired_prompts = []

        def on_fire(prompt: str):
            fired_prompts.append(prompt)

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(
                workspace_root=tmpdir, on_fire=on_fire
            )
            job = scheduler.create_job(cron="0 9 * * *", prompt="test prompt")
            scheduler._fire_job(job.id)
            assert len(fired_prompts) == 1
            assert fired_prompts[0] == "test prompt"


# ── CronCreateTool / CronDeleteTool / CronListTool ─────────────────────────────


class TestCronTools:
    def test_create_tool(self):
        from qitos.kit.tool.cron import CronScheduler, CronCreateTool

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(workspace_root=tmpdir)
            tool = CronCreateTool(scheduler)
            result = tool.call({"cron": "0 9 * * *", "prompt": "test"})
            assert result["status"] == "success"
            assert result["created"] is True

    def test_create_tool_missing_params(self):
        from qitos.kit.tool.cron import CronScheduler, CronCreateTool

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(workspace_root=tmpdir)
            tool = CronCreateTool(scheduler)
            result = tool.call({})
            assert result["status"] == "error"

    def test_delete_tool(self):
        from qitos.kit.tool.cron import CronScheduler, CronCreateTool, CronDeleteTool

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(workspace_root=tmpdir)
            create_tool = CronCreateTool(scheduler)
            delete_tool = CronDeleteTool(scheduler)

            result = create_tool.call({"cron": "0 9 * * *", "prompt": "test"})
            job_id = result["job"]["id"]

            del_result = delete_tool.call({"job_id": job_id})
            assert del_result["deleted"] is True

    def test_list_tool(self):
        from qitos.kit.tool.cron import CronScheduler, CronCreateTool, CronListTool

        with tempfile.TemporaryDirectory() as tmpdir:
            scheduler = CronScheduler(workspace_root=tmpdir)
            create_tool = CronCreateTool(scheduler)
            list_tool = CronListTool(scheduler)

            create_tool.call({"cron": "0 9 * * *", "prompt": "task1"})
            create_tool.call({"cron": "0 17 * * *", "prompt": "task2"})

            result = list_tool.call({})
            assert result["status"] == "success"
            assert result["count"] == 2


# ── WorktreeManager ───────────────────────────────────────────────────────────


class TestWorktreeManager:
    def test_list_empty(self):
        from qitos.kit.agent.worktree_manager import WorktreeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(workspace_root=tmpdir)
            assert wm.list_worktrees() == []

    def test_fallback_copy_creates_directory(self):
        from qitos.kit.agent.worktree_manager import WorktreeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(workspace_root=tmpdir)
            path = wm._fallback_copy("test-wt")
            assert os.path.isdir(path)
            assert "test-wt" in path

    def test_remove_worktree(self):
        from qitos.kit.agent.worktree_manager import WorktreeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(workspace_root=tmpdir)
            wm._fallback_copy("test-wt")
            assert wm.remove_worktree("test-wt") is True
            assert wm.list_worktrees() == []

    def test_remove_nonexistent(self):
        from qitos.kit.agent.worktree_manager import WorktreeManager

        with tempfile.TemporaryDirectory() as tmpdir:
            wm = WorktreeManager(workspace_root=tmpdir)
            assert wm.remove_worktree("nonexistent") is False


# ── AgentTool ─────────────────────────────────────────────────────────────────


class TestAgentTool:
    def test_import(self):
        from qitos.kit.tool.agent import AgentTool

        assert AgentTool is not None

    def test_call_without_prompt(self):
        from qitos.kit.tool.agent import AgentTool

        with tempfile.TemporaryDirectory() as tmpdir:
            tool = AgentTool(workspace_root=tmpdir)
            result = tool.call({"subagent_type": "explore"})
            assert result["status"] == "error"

    def test_call_unknown_agent_type(self):
        from qitos.kit.tool.agent import AgentTool

        with tempfile.TemporaryDirectory() as tmpdir:
            tool = AgentTool(workspace_root=tmpdir)
            result = tool.call(
                {"prompt": "test", "subagent_type": "nonexistent_type"}
            )
            # Should return error about unknown type
            assert result.get("error") is not None or result.get("status") == "error"

    def test_register_agent_type(self):
        from qitos.kit.tool.agent import AgentTool

        class FakeAgent:
            pass

        AgentTool.register_agent_type("test_type", FakeAgent)
        assert "test_type" in AgentTool._agent_types
        # Cleanup
        del AgentTool._agent_types["test_type"]

    def test_background_execution(self):
        from qitos.kit.tool.agent import AgentTool

        with tempfile.TemporaryDirectory() as tmpdir:
            tool = AgentTool(workspace_root=tmpdir)
            result = tool.call(
                {
                    "prompt": "test",
                    "subagent_type": "explore",
                    "run_in_background": True,
                }
            )
            assert result["status"] == "running"
            assert "task_id" in result


# ── Sub-agents import ─────────────────────────────────────────────────────────


class TestSubAgents:
    def test_explore_agent_import(self):
        from qitos.kit.tool.internal.subagents import ExploreAgent, ExploreState

        assert ExploreAgent is not None
        assert ExploreState is not None

    def test_plan_agent_import(self):
        from qitos.kit.tool.internal.subagents import PlanAgent, PlanState

        assert PlanAgent is not None
        assert PlanState is not None

    def test_general_agent_import(self):
        from qitos.kit.tool.internal.subagents import GeneralAgent, GeneralState

        assert GeneralAgent is not None
        assert GeneralState is not None
