from __future__ import annotations

from qitos.cli import main as qit_main


def test_qit_help_does_not_list_product_apps(capsys) -> None:
    rc = qit_main([])
    output = capsys.readouterr().out.lower()
    assert rc == 1
    assert "demo" in output
    assert "bench" in output
    assert "qitos-coder" not in output
    assert "qitos-cyber-agent" not in output
    assert "pentagi" not in output
    assert "claude" not in output


def test_qit_demo_minimal_help_is_canonical(capsys) -> None:
    try:
        qit_main(["demo", "minimal", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out.lower()
    assert "minimal" in output
    assert "qitos-coder" not in output
    assert "qitos-cyber-agent" not in output
    assert "pentagi" not in output


def test_qit_bench_help_remains_available(capsys) -> None:
    try:
        qit_main(["bench", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out.lower()
    assert "benchmark" in output
    assert "run" in output
    assert "eval" in output
