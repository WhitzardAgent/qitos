from __future__ import annotations

from pathlib import Path
from typing import List

from examples.real._whitzard_memory import AuditBoardMemory
from examples.real.whitzard_agent import (
    WhitzardAgent,
    WhitzardState,
    _resolve_runtime_config,
)
from qitos.core import TerminalCapability
from qitos.harness import build_harness_policy
from qitos.kit import CompactHistory
from qitos.core.tool_registry import ToolRegistry
from qitos.kit import TmuxEnv
from qitos.kit.tool.experimental.security_research import SecurityAuditToolSet


class FakeTerminal(TerminalCapability):
    def __init__(self):
        self.alive = True
        self.screen = "$ "
        self.buffer = "$ "
        self.previous = None
        self.sent: list[str] = []
        self.waits: list[float] = []
        self.closed = False
        self.ts = 0.0

    def reset_session(self, cwd: str | None = None) -> None:
        self.screen = "$ "
        self.buffer = "$ "
        self.previous = None
        self.alive = True

    def close_session(self) -> None:
        self.closed = True
        self.alive = False

    def send_keys(
        self,
        keys: str | list[str],
        min_timeout_sec: float = 0.0,
        block: bool = False,
        max_timeout_sec: float = 180.0,
    ) -> dict:
        text = "".join(keys) if isinstance(keys, list) else str(keys)
        self.sent.append(text)
        self.waits.append(float(min_timeout_sec))
        self.ts += 1.0
        update = f"executed: {text.strip()}\n$ " if text.strip() else self.screen
        self.buffer += update
        self.screen = update
        return {
            "status": "success",
            "keys": text,
            "waited_seconds": min_timeout_sec,
            "block": block,
        }

    def capture_screen(self) -> str:
        return self.screen

    def capture_buffer(self) -> str:
        return self.buffer

    def get_incremental_output(self) -> str:
        current = self.buffer
        if self.previous is None:
            self.previous = current
            return f"Current Terminal Screen:\n{self.screen}"
        if self.previous in current:
            idx = current.index(self.previous) + len(self.previous)
            delta = current[idx:].lstrip("\n")
        else:
            delta = self.screen
        self.previous = current
        return (
            f"New Terminal Output:\n{delta}"
            if delta.strip()
            else f"Current Terminal Screen:\n{self.screen}"
        )

    def is_session_alive(self) -> bool:
        return self.alive

    def get_timestamp(self) -> float | None:
        return self.ts


class DummyModel:
    def __init__(self, outputs: List[str]):
        self.outputs = list(outputs)
        self.calls: list[list[dict[str, str]]] = []
        self.model = "dummy-whitzard"

    def __call__(self, messages):
        self.calls.append(list(messages))
        return self.outputs.pop(0)


def _seed_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "app.py").write_text(
        "from flask import Flask, request\n"
        "import subprocess\n\n"
        "app = Flask(__name__)\n"
        "SECRET_KEY = 'hardcoded-secret-123456'\n\n"
        "@app.route('/run')\n"
        "def run():\n"
        "    subprocess.run(request.args.get('cmd'), shell=True)\n"
        "    return 'ok'\n",
        encoding="utf-8",
    )
    (root / "requirements.txt").write_text("flask\n", encoding="utf-8")


def test_security_audit_toolset_is_available_from_flat_surface(tmp_path: Path) -> None:
    toolset = SecurityAuditToolSet(workspace_root=str(tmp_path), include_external=False)
    registry = ToolRegistry().register_toolset(toolset, namespace="")
    names = registry.list_tools()
    assert "audit_inventory" in names
    assert "audit_hotspots" in names


def test_whitzard_agent_roundtrip_collects_findings_and_requires_double_completion(
    tmp_path: Path,
) -> None:
    _seed_repo(tmp_path)
    llm = DummyModel(
        outputs=[
            "not valid json",
            '{"analysis":"Inventory the repository","plan":"Run inventory and entrypoint scans","tools":[{"name":"audit_inventory","args":{}},{"name":"audit_entrypoints","args":{}}]}',
            '{"analysis":"Rank hotspots","plan":"Run hotspot analysis","tools":[{"name":"audit_hotspots","args":{}}]}',
            '{"analysis":"Record confirmed issue","plan":"Add a finding for the command injection sink","tools":[{"name":"finding_add","args":{"title":"Potential command injection via subprocess shell","severity":"high","description":"User-controlled command reaches subprocess.run(..., shell=True).","evidence":"app.py:8 subprocess.run(request.args.get(\'cmd\'), shell=True)","affected_component":"app.py","remediation":"Avoid shell=True and validate/allowlist commands."}}]}',
            '{"analysis":"Write the audit report","plan":"Generate a markdown report","tools":[{"name":"generate_report","args":{"format":"markdown","output_file":"security_report.md"}}]}',
            '{"analysis":"The report is written and findings are ranked","plan":"Request completion","task_complete":true}',
            '{"analysis":"Confirmed completion","plan":"Finish","task_complete":true}',
        ]
    )
    terminal = FakeTerminal()
    env = TmuxEnv(
        workspace_root=str(tmp_path),
        session_name="whitzard-loop",
        terminal=terminal,
        auto_kill=False,
    )
    agent = WhitzardAgent(llm=llm, workspace_root=str(tmp_path))

    result = agent.run(
        task="Audit this repository and write a report.",
        workspace=str(tmp_path),
        env=env,
        protocol="terminus_json_v1",
        max_steps=10,
        parser_format="json",
        render=False,
        trace=False,
        return_state=True,
    )

    assert result.state.stop_reason in ("success", "final")
    assert "security_report.md" in result.state.final_result
    assert result.state.final_report_path == "security_report.md"
    assert any(
        "subprocess" in str(item.get("evidence", "")) for item in result.state.findings
    )
    assert any(item.get("file") == "app.py" for item in result.state.hotspots)
    assert len(llm.calls) == 7
    assert "Parser feedback" in llm.calls[1][-1]["content"]
    assert (
        "Are you sure you want to mark the task as complete?"
        in llm.calls[6][-1]["content"]
    )


def test_whitzard_runtime_config_supports_family_switching() -> None:
    config = _resolve_runtime_config(
        env={
            "QITOS_MODEL_FAMILY": "qwen",
            "QITOS_MODEL": "qwen-plus",
            "OPENAI_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "OPENAI_API_KEY": "demo-key",
            "QITOS_PROTOCOL": "",
            "QITOS_TERMINUS_FORMAT": "json",
        }
    )
    assert config["model_family"] == "qwen"
    assert config["model_name"] == "qwen-plus"
    assert config["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert config["protocol"] is None
    assert config["parser_format"] == "json"


def test_whitzard_consumes_inventory_entrypoint_candidates(tmp_path: Path) -> None:
    agent = WhitzardAgent(llm=DummyModel(outputs=[]), workspace_root=str(tmp_path))
    state = WhitzardState(task="Audit repository", max_steps=8)

    agent._consume_action_results(
        state,
        {
            "action_results": [
                {
                    "data": {
                        "entrypoint_candidates": [
                            {"file": "src/main.py"},
                            {"file": "plugin/loader.py"},
                        ]
                    }
                }
            ]
        },
    )

    assert "src/main.py" in state.reviewed_files
    assert "plugin/loader.py" in state.reviewed_files
    assert "✅ Phase 1: Reconnaissance" in agent._render_phase_progress(state)


def test_whitzard_tool_surface_omits_list_files(tmp_path: Path) -> None:
    agent = WhitzardAgent(llm=DummyModel(outputs=[]), workspace_root=str(tmp_path))
    names = set(agent.tool_registry.list_tools())

    assert "list_files" not in names
    assert "audit_inventory" in names
    assert "audit_entrypoints" in names
    assert "audit_hotspots" in names
    assert "grep_files" in names
    assert "read_file_range" in names


def test_whitzard_uses_compact_history_and_audit_board_memory(tmp_path: Path) -> None:
    agent = WhitzardAgent(llm=DummyModel(outputs=[]), workspace_root=str(tmp_path))

    assert isinstance(agent.history, CompactHistory)
    assert isinstance(agent.audit_memory, AuditBoardMemory)


def test_audit_board_ranks_core_targets_above_test_noise() -> None:
    memory = AuditBoardMemory()
    memory.ingest_inventory(
        [
            {"file": "runtime/syntax/testdir/input/python_strings_bytes.py"},
            {"file": "src/buffer.c"},
        ],
        step_id=1,
    )
    memory.ingest_hotspots(
        [
            {"file": "src/buffer.c", "score": 91, "categories": ["modeline"]},
            {
                "file": "runtime/syntax/testdir/input/python_strings_bytes.py",
                "score": 10,
                "categories": ["fixture"],
            },
        ],
        step_id=2,
    )

    top = memory.top_targets(limit=2)
    assert top[0]["path"] == "src/buffer.c"
    assert top[1]["path"] == "runtime/syntax/testdir/input/python_strings_bytes.py"


def test_whitzard_prepare_surfaces_regex_retry_guidance(tmp_path: Path) -> None:
    agent = WhitzardAgent(llm=DummyModel(outputs=[]), workspace_root=str(tmp_path))
    state = WhitzardState(task="Audit repository", max_steps=8)
    agent.audit_memory.ingest_grep_result(
        {
            "status": "error",
            "pattern": "system(",
            "message": "Invalid regex: missing ), unterminated subpattern",
            "context": {"regex": True},
        },
        step_id=2,
    )
    agent._refresh_audit_board(state)

    prompt = agent.prepare(state)
    assert "regex=false" in prompt
    assert "system(" in prompt


def test_whitzard_prepare_prefers_focused_read_after_core_hit(tmp_path: Path) -> None:
    agent = WhitzardAgent(llm=DummyModel(outputs=[]), workspace_root=str(tmp_path))
    state = WhitzardState(task="Audit repository", max_steps=8)
    agent.audit_memory.ingest_grep_result(
        {
            "status": "success",
            "pattern": "modeline",
            "matches": [
                {"path": "runtime/syntax/testdir/input/sample.vim", "line": 5},
                {"path": "src/buffer.c", "line": 5950},
            ],
            "context": {"regex": False},
        },
        step_id=3,
    )
    agent._refresh_audit_board(state)

    prompt = agent.prepare(state)
    assert "read_file_range on src/buffer.c" in prompt


def test_whitzard_records_findings_and_report_in_audit_board(tmp_path: Path) -> None:
    agent = WhitzardAgent(llm=DummyModel(outputs=[]), workspace_root=str(tmp_path))
    state = WhitzardState(task="Audit repository", max_steps=8)
    agent._consume_action_results(
        state,
        {
            "action_results": [
                {
                    "tool": "finding_add",
                    "data": {
                        "finding": {
                            "title": "Modeline command execution",
                            "severity": "critical",
                            "file": "src/buffer.c",
                            "line": 6032,
                            "evidence": "unsafe modeline execution path",
                        }
                    },
                },
                {
                    "tool": "generate_report",
                    "data": {"output_file": "security_report.md"},
                },
            ]
        },
    )
    agent._refresh_audit_board(state)

    board = state.audit_board_snapshot
    assert board["confirmed_findings"][0]["file"] == "src/buffer.c"
    assert board["report_path"] == "security_report.md"


def test_whitzard_minimax_family_keeps_special_harness() -> None:
    harness = build_harness_policy(
        family_id="minimax",
        model_name="MiniMax-M2.5",
        protocol=None,
        resolution_source="test",
    )
    assert harness.family_preset.id == "minimax"
    assert harness.protocol.id == "minimax_tool_call_v1"
