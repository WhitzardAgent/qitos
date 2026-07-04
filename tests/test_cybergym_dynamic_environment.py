from __future__ import annotations

import json
import os
from pathlib import Path
from unittest import mock

from qitos.benchmark.cybergym.dynamic_environment import (
    parse_task_id_from_submit_script,
    prepare_dynamic_environment,
    resolve_dataset_entry,
)


def _write_dataset(root: Path, task_id: str = "arvo:43641") -> Path:
    dataset_key = task_id.replace(":", "_")
    case_dir = root / "cases" / dataset_key
    case_dir.mkdir(parents=True)
    (case_dir / "Dockerfile").write_text("FROM cage/claude-code:cyberdebug\n", encoding="utf-8")
    return case_dir


def test_parse_task_id_from_submit_script_reads_submit_template_field(tmp_path: Path) -> None:
    submit = tmp_path / "submit.sh"
    submit.write_text('curl -F "task_id=arvo:43641"\n', encoding="utf-8")
    assert parse_task_id_from_submit_script(submit) == "arvo:43641"


def test_resolve_dataset_entry_prefers_submit_script_task_id(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    _write_dataset(dataset_root, task_id="arvo:43641")
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "submit.sh").write_text('curl -F "task_id=arvo:43641"\n', encoding="utf-8")

    resolved = resolve_dataset_entry(
        task_dir=task_dir,
        requested_task_id="arvo:00000",
        dataset_root=dataset_root,
    )

    assert resolved.task_id == "arvo:43641"
    assert resolved.dataset_key == "arvo_43641"
    assert resolved.dockerfile_path == dataset_root / "cases" / "arvo_43641" / "Dockerfile"
    assert not hasattr(resolved, "binary_name")


def test_resolve_dataset_entry_uses_dataset_root_from_config_file(tmp_path: Path) -> None:
    configured_dataset_root = tmp_path / "external_dataset"
    dockerfile = configured_dataset_root / "cases" / "oss-fuzz_42537014" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True, exist_ok=True)
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    config_path = tmp_path / "dynamic_environment_config.json"
    config_path.write_text(
        json.dumps({"dataset_root": str(configured_dataset_root)}),
        encoding="utf-8",
    )
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    with mock.patch.dict(os.environ, {"CYBERGYM_DYNAMIC_ENVIRONMENT_CONFIG": str(config_path)}, clear=False):
        entry = resolve_dataset_entry(
            task_dir=task_dir,
            requested_task_id="oss-fuzz:42537014",
            dataset_root=None,
        )

    assert entry.task_id == "oss-fuzz:42537014"
    assert entry.dataset_key == "oss-fuzz_42537014"
    assert entry.dockerfile_path == dockerfile.resolve()
    assert entry.metadata_path is None


def test_prepare_dynamic_environment_builds_case_image_and_writes_sidecar_without_binary_name(tmp_path: Path) -> None:
    dataset_root = tmp_path / "dataset"
    _write_dataset(dataset_root, task_id="arvo:43641")
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "submit.sh").write_text('curl -F "task_id=arvo:43641"\n', encoding="utf-8")

    with mock.patch("subprocess.run") as run:
        result = prepare_dynamic_environment(
            task_dir=task_dir,
            requested_task_id="arvo:43641",
            dataset_root=dataset_root,
        )

    run.assert_called_once()
    cmd = run.call_args.args[0]
    assert cmd[:3] == ["docker", "build", "-t"]
    assert run.call_args.kwargs["env"]["DOCKER_BUILDKIT"] == "0"
    assert result["task_id"] == "arvo:43641"
    assert result["dataset_key"] == "arvo_43641"
    assert result["image_tag"] == "cybergym-dynamic-environment:arvo_43641"
    assert result["official_binary_path"] == "/in/official_vulnerable_binary"
    assert result["official_binary_alias_path"] == "/in/offcial_vulnerable_binary"
    assert "binary_name" not in result
    sidecar = task_dir / ".cybergym" / "dynamic_environment.json"
    assert sidecar.is_file()
    written = json.loads(sidecar.read_text(encoding="utf-8"))
    assert written["dockerfile_path"].endswith("cases/arvo_43641/Dockerfile")
    assert written["dataset_metadata_path"] is None
    assert "binary_name" not in written
