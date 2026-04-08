from __future__ import annotations

from pathlib import Path

from examples._support import SequenceModel
from qitos.cli import main as qit_main
from qitos.demo import minimal as minimal_demo
from qitos.qita.cli import _discover_runs


def _react_fix_outputs() -> list[str]:
    verify_command = 'python -c "import buggy_module; assert buggy_module.add(20, 22) == 42"'
    return [
        'Thought: inspect target\nAction: view(path="buggy_module.py")',
        'Thought: patch logic\nAction: replace_lines(path="buggy_module.py", start_line=2, end_line=2, replacement="    return a + b")',
        f'Thought: verify change\nAction: run_command(command="{verify_command}")',
        "Final Answer: Patch applied and verification passed.",
    ]


def test_qit_demo_minimal_creates_qita_ready_run(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    workspace = tmp_path / "playground" / "minimal_coding_agent"
    logdir = tmp_path / "runs"
    monkeypatch.setattr(
        minimal_demo,
        "build_model",
        lambda **_: SequenceModel(_react_fix_outputs()),
    )

    rc = qit_main(
        [
            "demo",
            "minimal",
            "--workspace",
            str(workspace),
            "--logdir",
            str(logdir),
        ]
    )

    assert rc == 0
    output = capsys.readouterr().out
    assert "model_name: smoke-model" in output
    assert "workspace:" in output
    assert "target_file: buggy_module.py" in output
    assert "test_command:" in output
    assert "trace_run:" in output
    assert "final_result: Patch applied and verification passed." in output
    assert "stop_reason: final" in output
    assert "qita board --logdir" in output

    runs = _discover_runs(logdir)
    assert len(runs) == 1
    assert runs[0]["id"].startswith("qitos_minimal_coding_")
    assert (Path(runs[0]["path"]) / "manifest.json").exists()
    assert (workspace / "buggy_module.py").read_text(encoding="utf-8").strip().endswith(
        "return a + b"
    )
