from __future__ import annotations

from pathlib import Path

from qitos.core.tool_result import ToolResult


def _make_repo(root: Path) -> Path:
    repo = root / "repo-vul"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "include").mkdir()
    (repo / "samples").mkdir()
    (repo / "src" / "parser_decode.c").write_text(
        "int parse_record(const unsigned char *buf, int len) {\n"
        "    if (len < 3) return -1;\n"
        "    return buf[2];\n"
        "}\n",
        encoding="utf-8",
    )
    (repo / "include" / "parser_fields.h").write_text(
        "struct record_header { int len; int off; };\n",
        encoding="utf-8",
    )
    (repo / "samples" / "seed.omf").write_bytes(b"OMF")
    return repo


def _make_agent(tmp_path: Path):
    from qitos.benchmark.cybergym.agent.agent import CyberGymAgent

    (tmp_path / "submit.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    return CyberGymAgent(
        llm=object(),
        workspace_root=str(tmp_path),
        task_root=str(tmp_path),
        server_url="http://127.0.0.1:8698",
    )


def test_cybergym_state_initializes_durable_memory_fields() -> None:
    from qitos.benchmark.cybergym.agent.state import CyberGymState

    state = CyberGymState(task="demo")

    assert state.durable_project_memory == {}
    assert state.durable_code_facts == []
    assert state.durable_feedback_facts == []


def test_init_state_populates_durable_project_memory(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    agent = _make_agent(tmp_path)

    state = agent.init_state(
        "demo task",
        description="Parser bug in a truncated OMF record",
        source_root=str(repo),
    )

    memory = state.durable_project_memory
    assert "parser_decode.c" in " ".join(memory.get("parser_paths", []))
    assert "seed.omf" in " ".join(memory.get("seed_paths", []))
    assert "parser_fields.h" in " ".join(memory.get("field_paths", []))


def test_read_result_populates_durable_code_facts(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    agent = _make_agent(tmp_path)
    state = agent.init_state(
        "demo task",
        description="Parser bug in a truncated OMF record",
        source_root=str(repo),
    )

    result = ToolResult(
        output={
            "path": "src/parser_decode.c",
            "content": "if (len < 3) return -1;\nreturn buf[2];\n",
        },
        metadata={"name": "READ"},
    )

    agent._process_action_result(state, result)

    assert state.durable_code_facts
    assert any("src/parser_decode.c" in fact for fact in state.durable_code_facts)


def test_submit_feedback_populates_durable_feedback_facts(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    agent = _make_agent(tmp_path)
    state = agent.init_state(
        "demo task",
        description="Parser bug in a truncated OMF record",
        source_root=str(repo),
    )
    poc = tmp_path / "poc.bin"
    poc.write_bytes(b"abc")
    state.poc_path = str(poc)

    result = ToolResult(
        output={
            "exit_code": 0,
            "vul_exit_code": 0,
            "verification_scope": "vul_only",
            "raw_output": "Invalid record (too short)\n",
        },
        metadata={"name": "submit_poc"},
    )

    agent._process_action_result(state, result)

    assert state.durable_feedback_facts
    assert any("Invalid record" in fact or "no_trigger" in fact for fact in state.durable_feedback_facts)


def test_prompt_and_trace_payload_include_working_memory(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    agent = _make_agent(tmp_path)
    state = agent.init_state(
        "demo task",
        description="Parser bug in a truncated OMF record",
        source_root=str(repo),
    )
    state.durable_code_facts = ["parser_path: src/parser_decode.c -> if (len < 3) return -1;"]
    state.durable_feedback_facts = ["feedback_hint: Invalid record (too short)"]

    system_prompt = agent.build_system_prompt(state)
    observation = agent.prepare(state)
    payload = agent._step_context_payload(state)

    assert "Older tool results may later be cleared from context." in system_prompt
    assert (
        "When working with tool results, write down any important information you might need later in your response"
        in system_prompt
    )
    assert "## Stable Task Facts" not in system_prompt
    assert "Working Directory (cwd)" not in system_prompt
    assert "cybergym" not in system_prompt.lower()
    assert "cybergym" not in observation.lower()
    assert "## Working Memory" not in observation
    assert "### Project Index" not in observation
    assert payload["durable_project_memory"]
    assert payload["durable_code_facts"]
    assert payload["durable_feedback_facts"]


def test_find_pipeline_with_head_is_not_treated_as_file_browsing(tmp_path: Path) -> None:
    agent = _make_agent(tmp_path)

    assert (
        agent._bash_is_file_browse_command(
            'find repo-vul -type f -name "*.c" | xargs grep -l -i "omf" 2>/dev/null | head -30'
        )
        is False
    )
    assert agent._bash_is_file_browse_command("head README.md") is True
