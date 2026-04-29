from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from qitos.benchmark.cybergym.agent.agent import CyberGymAgent
from qitos.benchmark.cybergym.agent.state import CyberGymState


def test_allowed_tools_prompt_mentions_parallel_read_only_tools():
    with tempfile.TemporaryDirectory() as tmpdir:
        llm = SimpleNamespace(model="stub")
        workspace = Path(tmpdir)
        with mock.patch("qitos.benchmark.cybergym.agent.agent.bootstrap_evidence_index", return_value=None):
            agent = CyberGymAgent(llm=llm, workspace_root=str(workspace), task_root=str(workspace))

        state = CyberGymState(task="demo", max_steps=10, workspace_root=str(workspace))
        lines = agent._allowed_tool_lines(state)
        prompt = "\n".join(lines)

        assert "parallel" in prompt.lower()
        assert "read-only" in prompt.lower()
        assert "`READ(path, offset?, limit?)`" in prompt
        assert "4" in prompt
