from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "cybergym_success_rate.py"
    spec = importlib.util.spec_from_file_location("cybergym_success_rate", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(root: Path, run_id: str, stop_reason: str) -> None:
    run_dir = root / "traces" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"summary": {"stop_reason": stop_reason}}),
        encoding="utf-8",
    )


def test_counts_success_rate_from_cybergym_run_folder(tmp_path: Path) -> None:
    module = _load_script_module()
    _write_manifest(tmp_path, "run-success-1", "success")
    _write_manifest(tmp_path, "run-timeout", "budget_time")
    _write_manifest(tmp_path, "run-success-2", "success")

    stats = module.collect_success_rate(tmp_path)

    assert stats.total == 3
    assert stats.success == 2
    assert stats.rate == 2 / 3
    assert stats.stop_reasons == {"success": 2, "budget_time": 1}


def test_cli_prints_summary_for_run_folder(tmp_path: Path, capsys) -> None:
    module = _load_script_module()
    _write_manifest(tmp_path, "run-success", "success")
    _write_manifest(tmp_path, "run-failed", "final")

    rc = module.main([str(tmp_path)])

    assert rc == 0
    output = capsys.readouterr().out
    assert "success: 1/2 (50.00%)" in output
    assert "success: 1" in output
    assert "final: 1" in output
