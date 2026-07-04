from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TASK_ID_PATTERN = re.compile(r"task_id=([A-Za-z0-9_.:-]+)")
_CONFIG_FILENAME = "dynamic_environment_config.json"


@dataclass(frozen=True)
class DynamicEnvironmentDatasetEntry:
    task_id: str
    dataset_key: str
    dockerfile_path: Path
    metadata_path: Path | None


def normalize_task_id(task_id: str) -> str:
    return str(task_id).replace(":", "_")


def default_dataset_root() -> Path:
    return (Path(__file__).resolve().parent / "agent" / "dynamic_environment_dataset").resolve()


def default_dataset_config_path() -> Path:
    return (Path(__file__).resolve().parent / "agent" / _CONFIG_FILENAME).resolve()


def parse_task_id_from_submit_script(submit_path: str | Path) -> str | None:
    path = Path(submit_path).expanduser().resolve()
    if not path.is_file():
        return None
    match = _TASK_ID_PATTERN.search(path.read_text(encoding="utf-8"))
    if match is None:
        return None
    return match.group(1)


def _read_dataset_root_from_config(config_path: str | Path | None) -> Path | None:
    if config_path is None:
        return None
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    dataset_root = str(payload.get("dataset_root") or "").strip()
    if not dataset_root:
        return None
    return Path(dataset_root).expanduser().resolve()


def resolve_dataset_root(
    *,
    dataset_root: str | Path | None = None,
    dataset_config_path: str | Path | None = None,
) -> Path:
    explicit_root = str(dataset_root or os.getenv("CYBERGYM_DYNAMIC_ENVIRONMENT_DATASET", "")).strip()
    if explicit_root:
        return Path(explicit_root).expanduser().resolve()

    candidates: list[str | Path | None] = [
        dataset_config_path,
        os.getenv("CYBERGYM_DYNAMIC_ENVIRONMENT_CONFIG", "").strip() or None,
        default_dataset_config_path(),
    ]
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in (None, ""):
            continue
        path = Path(candidate).expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        resolved = _read_dataset_root_from_config(path)
        if resolved is not None:
            return resolved

    return default_dataset_root()


def resolve_dataset_entry(
    *,
    task_dir: str | Path,
    requested_task_id: str,
    dataset_root: str | Path | None = None,
    dataset_config_path: str | Path | None = None,
) -> DynamicEnvironmentDatasetEntry:
    root = resolve_dataset_root(dataset_root=dataset_root, dataset_config_path=dataset_config_path)
    task_root = Path(task_dir).expanduser().resolve()
    submit_task_id = parse_task_id_from_submit_script(task_root / "submit.sh")
    task_id = str(submit_task_id or requested_task_id or "").strip()
    if not task_id:
        raise ValueError("task_id is required to resolve dynamic environment dataset entry")
    dataset_key = normalize_task_id(task_id)
    dockerfile_path = (root / "cases" / dataset_key / "Dockerfile").resolve()
    if not dockerfile_path.is_file():
        raise KeyError(f"task_id not found in dynamic environment dataset: {task_id}")
    metadata_path = (root / "cases" / dataset_key / "metadata.json").resolve()
    return DynamicEnvironmentDatasetEntry(
        task_id=task_id,
        dataset_key=dataset_key,
        dockerfile_path=dockerfile_path,
        metadata_path=metadata_path if metadata_path.is_file() else None,
    )


def _build_sidecar_payload(entry: DynamicEnvironmentDatasetEntry, image_tag: str) -> dict[str, Any]:
    dataset_metadata: dict[str, Any] = {}
    if entry.metadata_path and entry.metadata_path.is_file():
        dataset_metadata = json.loads(entry.metadata_path.read_text(encoding="utf-8"))
    return {
        "task_id": entry.task_id,
        "dataset_key": entry.dataset_key,
        "dockerfile_path": str(entry.dockerfile_path),
        "dataset_metadata_path": str(entry.metadata_path) if entry.metadata_path else None,
        "image_tag": image_tag,
        "official_binary_path": "/in/official_vulnerable_binary",
        "official_binary_alias_path": "/in/offcial_vulnerable_binary",
        "dataset_metadata": dataset_metadata,
    }


def prepare_dynamic_environment(
    *,
    task_dir: str | Path,
    requested_task_id: str,
    dataset_root: str | Path | None = None,
    dataset_config_path: str | Path | None = None,
    docker_bin: str = "docker",
) -> dict[str, Any]:
    entry = resolve_dataset_entry(
        task_dir=task_dir,
        requested_task_id=requested_task_id,
        dataset_root=dataset_root,
        dataset_config_path=dataset_config_path,
    )
    image_tag = f"cybergym-dynamic-environment:{entry.dataset_key}"
    build_context = entry.dockerfile_path.parent
    build_env = dict(os.environ)
    build_env.setdefault("DOCKER_BUILDKIT", "0")
    subprocess.run(
        [docker_bin, "build", "-t", image_tag, "-f", str(entry.dockerfile_path), str(build_context)],
        check=True,
        env=build_env,
    )
    payload = _build_sidecar_payload(entry, image_tag)
    sidecar_dir = Path(task_dir).expanduser().resolve() / ".cybergym"
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = sidecar_dir / "dynamic_environment.json"
    sidecar_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    payload["metadata_path"] = str(sidecar_path)
    return payload
