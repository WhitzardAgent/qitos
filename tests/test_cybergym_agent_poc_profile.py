from pathlib import Path

from qitos.core.tool_registry import ToolRegistry


def test_poc_gen_profile_detects_and_registers_submit_tool(tmp_path: Path) -> None:
    submit = tmp_path / "submit.sh"
    submit.write_text(
        "#!/bin/bash\n"
        'curl -X POST http://127.0.0.1:8698/submit-vul -F "file=@${1}"\n',
        encoding="utf-8",
    )

    from qitos.benchmark.cybergym.agent.profiles import PocGenProfile, detect_profile
    from qitos.benchmark.cybergym.agent.state import SecurityState

    profile = detect_profile(
        "CyberGym task",
        task_profile="poc_gen",
        server_url="http://127.0.0.1:8698",
    )
    assert isinstance(profile, PocGenProfile)

    state = SecurityState(task="CyberGym task", workspace_root=str(tmp_path))
    profile.init_state(
        state,
        description="A crash occurs when parsing a truncated file.",
        task_id="arvo:15003",
        agent_id="agent-x",
        checksum="checksum-x",
        server_url="http://127.0.0.1:8698",
        repo_dir=str(tmp_path),
    )

    registry = ToolRegistry(auto_short_aliases=True)
    profile.register_tools(
        registry,
        workspace_root=str(tmp_path),
        shell_timeout=60,
        server_url="http://127.0.0.1:8698",
    )

    assert state.task_profile == "poc_gen"
    assert state.task_id == "arvo:15003"
    assert state.poc_strategy in {"text", "binary_python", "corpus_mutate", "hex"}
    assert "submit.sh content:" in state.harness_info
    assert "submit_poc" in registry.list_tools()


def test_cybergym_adapter_accepts_qitos_runner_keyword_args(tmp_path: Path) -> None:
    (tmp_path / "description.txt").write_text(
        "A crash occurs when parsing a truncated file.\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("README\n", encoding="utf-8")
    (tmp_path / "submit.sh").write_text(
        "#!/bin/bash\n"
        'curl -X POST http://127.0.0.1:8698/submit-vul -F "file=@${1}"\n',
        encoding="utf-8",
    )
    repo_dir = tmp_path / "repo-vul"
    repo_dir.mkdir()
    (repo_dir / "sample.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")

    from qitos.benchmark.cybergym.agent.adapter import CyberGymAdapter

    adapter = CyberGymAdapter(server_url="http://127.0.0.1:8698")
    task = adapter.from_task_dir(
        str(tmp_path),
        task_id="arvo:15003",
        max_steps=7,
        max_runtime_seconds=120,
    )

    assert task.id == "arvo:15003"
    assert task.inputs["task_root"] == str(tmp_path.resolve())
    assert task.inputs["source_root"] == str(repo_dir.resolve())
    model_visible_task_text = "\n".join([task.objective, *task.success_criteria])
    assert "fix_exit" not in model_visible_task_text
    assert "patched" not in model_visible_task_text.lower()
    assert "fixed" not in model_visible_task_text.lower()


def test_build_agent_accepts_task_root_keyword(monkeypatch, tmp_path: Path) -> None:
    from qitos.benchmark.cybergym.agent import cli

    monkeypatch.setattr(cli, "_create_llm", lambda model, llm_config=None: object())

    agent = cli.build_agent(
        model="GLM-5.1",
        workspace_root=str(tmp_path),
        task_root=str(tmp_path),
        server_url="http://127.0.0.1:8698",
        llm_config={"api_key": "x", "base_url": "y"},
    )

    assert agent.workspace_root == str(tmp_path.resolve())
