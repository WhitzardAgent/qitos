from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "cybergym_run_report.py"
    spec = importlib.util.spec_from_file_location("cybergym_run_report", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_manifest(
    run_root: Path,
    trace_id: str,
    task_id: str,
    stop_reason: str,
    *,
    success: bool = False,
    steps: int = 3,
) -> Path:
    trace_dir = run_root / "traces" / trace_id
    trace_dir.mkdir(parents=True)
    path = trace_dir / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "summary": {
                    "stop_reason": stop_reason,
                    "steps": steps,
                    "latency_seconds": 12.5,
                    "token_usage": 1234,
                    "final_result": "poc.bin" if success else None,
                    "task_meta": {"task_id": task_id},
                    "task_result": {"task_id": task_id, "success": success},
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_collect_run_report_uses_success_as_final_result(tmp_path: Path) -> None:
    module = _load_script_module()
    run_root = tmp_path / "run-a"
    _write_manifest(run_root, "trace-old", "arvo:1", "budget_time")
    _write_manifest(run_root, "trace-success", "arvo:1", "success", success=True, steps=5)
    _write_manifest(run_root, "trace-miss", "arvo:2", "final")

    report = module.collect_run_report(run_root)

    assert report.name == "run-a"
    assert report.manifest_count == 3
    assert report.total == 2
    assert report.success_count == 1
    assert report.tasks["arvo:1"].stop_reason == "success"
    assert report.tasks["arvo:1"].steps == 5
    assert report.stop_reasons == {"success": 1, "final": 1}


def test_cli_writes_markdown_and_csv_for_multiple_runs(tmp_path: Path) -> None:
    module = _load_script_module()
    runs_root = tmp_path / "runs"
    run_a = runs_root / "run-a"
    run_b = runs_root / "run-b"
    _write_manifest(run_a, "trace-a1", "arvo:1", "success", success=True)
    _write_manifest(run_b, "trace-b1", "arvo:1", "budget_time")
    _write_manifest(run_b, "trace-b2", "arvo:2", "success", success=True)
    task_file = tmp_path / "tasks.txt"
    task_file.write_text("arvo:1\narvo:2\narvo:3\n", encoding="utf-8")
    md_path = tmp_path / "report.md"
    csv_path = tmp_path / "report.csv"

    rc = module.main(
        [
            str(run_a),
            str(run_b),
            "--task-file",
            str(task_file),
            "-o",
            str(md_path),
            "--csv",
            str(csv_path),
        ]
    )

    assert rc == 0
    md = md_path.read_text(encoding="utf-8")
    assert "| `run-a` | 1 | 1 | 100.00%" in md
    assert "| `run-b` | 1 | 2 | 50.00%" in md
    assert "| `arvo:3` | - | - |" in md
    csv_text = csv_path.read_text(encoding="utf-8")
    assert "task_id,run,stop_reason,success" in csv_text
    assert "arvo:2,run-a,missing,false" in csv_text
