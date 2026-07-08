"""Offline pack/toolbox/sanity evaluation against CyberGym ground truth.

This module is evaluation-only.  Ground-truth PoCs and labels must never be
copied into runtime task workspaces, prompt context, or seed selection.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from agent_impl.knowledge.evidence import EvidenceView
from agent_impl.knowledge.registry import get_knowledge_registry
from agent_impl.poc.sanity import inspect_poc_bytes


_TOOLBOX_FORMATS = {
    "bmp": ("toolbox.formats.bmp", "bmp"),
    "jpeg": ("toolbox.formats.jpeg", "jpeg"),
    "jpg": ("toolbox.formats.jpeg", "jpeg"),
    "pdf": ("toolbox.formats.pdf", "pdf"),
    "png": ("toolbox.formats.png", "png"),
    "wav": ("toolbox.formats.wav", "wav"),
    "zip": ("toolbox.formats.zipfmt", "zip"),
}

_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"BM", "bmp"),
    (b"%PDF", "pdf"),
    (b"PK\x03\x04", "zip"),
    (b"RIFF", "wav"),
    (b"\x7fELF", "elf"),
    (b"GIF8", "gif"),
    (b"\x1f\x8b", "gzip"),
    (b"II\x2a\x00", "tiff"),
    (b"MM\x00\x2a", "tiff"),
    (b"\x00\x01\x00\x00", "ttf"),
    (b"OTTO", "otf"),
    (b"true", "ttf"),
    (b"wOFF", "woff"),
    (b"wOF2", "woff2"),
    (b"\x28\xb5\x2f\xfd", "zstd"),
    (b"\x00asm", "wasm"),
)


@dataclass(frozen=True)
class PackPrediction:
    pack_id: str
    decision: str
    score: float
    positive_evidence: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackGroundTruthRow:
    task_id: str
    project_name: str = ""
    fuzz_driver: str = ""
    gt_format: str = ""
    gt_pack: str = ""
    gt_file_type: str = ""
    poc_path: str = ""
    poc_exists: bool = False
    poc_size: int = 0
    poc_magic: str = ""
    predicted_pack: str = ""
    predicted_decision: str = ""
    predicted_score: float = 0.0
    prediction_exact: bool = False
    predictions: list[dict[str, Any]] = field(default_factory=list)
    toolbox_status: str = "unsupported"
    toolbox_error: str = ""
    sanity_passed: bool | None = None
    sanity_summary: str = ""
    sanity_fail_count: int = 0
    sanity_warn_count: int = 0
    gt_pack_parse_status: str = "not_run"
    gt_pack_parse_warnings: list[str] = field(default_factory=list)
    predicted_pack_parse_status: str = "not_run"
    predicted_pack_parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PackGroundTruthSummary:
    total: int
    rows_with_poc: int
    exact_detection: int
    exact_detection_rate: float
    by_gt_pack: dict[str, dict[str, Any]]
    by_gt_format: dict[str, dict[str, Any]]
    toolbox: dict[str, int]
    sanity: dict[str, int]
    parse_status: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def evaluate_pack_ground_truth(
    *,
    tasks_json: str | Path,
    format_ground_truth_json: str | Path,
    pack_ground_truth_json: str | Path,
    pocs_root: str | Path,
    limit: int | None = None,
) -> tuple[list[PackGroundTruthRow], PackGroundTruthSummary]:
    """Evaluate current pack/toolbox/sanity behavior against GT labels."""
    tasks = load_json(tasks_json)
    if not isinstance(tasks, list):
        raise ValueError("tasks_json must contain a list")
    format_gt = load_json(format_ground_truth_json)
    pack_gt = load_json(pack_ground_truth_json)
    if not isinstance(format_gt, dict) or not isinstance(pack_gt, dict):
        raise ValueError("ground truth files must contain objects keyed by task_id")

    registry = get_knowledge_registry()
    root = Path(pocs_root)
    rows: list[PackGroundTruthRow] = []

    selected_tasks = tasks[:limit] if limit is not None else tasks
    for task in selected_tasks:
        if not isinstance(task, Mapping):
            continue
        row = _evaluate_one(task, format_gt, pack_gt, root, registry)
        rows.append(row)

    return rows, summarize_rows(rows)


def summarize_rows(rows: list[PackGroundTruthRow]) -> PackGroundTruthSummary:
    total = len(rows)
    exact = sum(1 for row in rows if row.prediction_exact)
    rows_with_poc = sum(1 for row in rows if row.poc_exists)

    by_pack: dict[str, dict[str, Any]] = {}
    for pack_id, group in _group_rows(rows, key=lambda r: r.gt_pack or "unknown").items():
        count = len(group)
        pack_exact = sum(1 for row in group if row.prediction_exact)
        by_pack[pack_id] = {
            "total": count,
            "exact": pack_exact,
            "recall": round(pack_exact / count, 4) if count else 0.0,
            "top_predictions": Counter(row.predicted_pack or "NO_MATCH" for row in group).most_common(8),
        }

    by_format: dict[str, dict[str, Any]] = {}
    for fmt, group in _group_rows(rows, key=lambda r: r.gt_format or "unknown").items():
        count = len(group)
        by_format[fmt] = {
            "total": count,
            "toolbox": Counter(row.toolbox_status for row in group).most_common(8),
            "sanity": Counter(_sanity_bucket(row) for row in group).most_common(8),
            "poc_magic": Counter(row.poc_magic or "unknown" for row in group).most_common(8),
        }

    return PackGroundTruthSummary(
        total=total,
        rows_with_poc=rows_with_poc,
        exact_detection=exact,
        exact_detection_rate=round(exact / total, 4) if total else 0.0,
        by_gt_pack=by_pack,
        by_gt_format=by_format,
        toolbox=dict(Counter(row.toolbox_status for row in rows)),
        sanity=dict(Counter(_sanity_bucket(row) for row in rows)),
        parse_status=dict(Counter(row.gt_pack_parse_status for row in rows)),
    )


def write_jsonl(path: str | Path, rows: Iterable[PackGroundTruthRow]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")


def write_summary(path: str | Path, summary: PackGroundTruthSummary) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _evaluate_one(
    task: Mapping[str, Any],
    format_gt: Mapping[str, Any],
    pack_gt: Mapping[str, Any],
    pocs_root: Path,
    registry: Any,
) -> PackGroundTruthRow:
    task_id = str(task.get("task_id") or "")
    fmt_info = format_gt.get(task_id) or {}
    pack_info = pack_gt.get(task_id) or {}
    if not isinstance(fmt_info, Mapping):
        fmt_info = {}
    if not isinstance(pack_info, Mapping):
        pack_info = {}

    poc_path = pocs_root / task_id / "poc"
    row = PackGroundTruthRow(
        task_id=task_id,
        project_name=str(task.get("project_name") or pack_info.get("repo") or ""),
        fuzz_driver=str(pack_info.get("fuzz_driver") or ""),
        gt_format=str(fmt_info.get("format") or ""),
        gt_pack=str(pack_info.get("pack") or ""),
        gt_file_type=str(pack_info.get("file_type") or ""),
        poc_path=str(poc_path),
        poc_exists=poc_path.is_file(),
    )

    evidence = EvidenceView(
        task_id=task_id,
        project_name=row.project_name,
        vulnerability_description=str(task.get("vulnerability_description") or ""),
    )
    predictions = _select_packs(registry, evidence)
    row.predictions = [pred.to_dict() for pred in predictions]
    if predictions:
        best = predictions[0]
        row.predicted_pack = best.pack_id
        row.predicted_decision = best.decision
        row.predicted_score = best.score
        row.prediction_exact = bool(row.gt_pack and best.pack_id == row.gt_pack)

    if not row.poc_exists:
        return row

    data = poc_path.read_bytes()
    row.poc_size = len(data)
    row.poc_magic = detect_magic(data)
    row.toolbox_status, row.toolbox_error = inspect_with_toolbox(poc_path, row.gt_format)
    _fill_sanity(row, poc_path)
    _fill_parse_status(row, data, registry, row.gt_pack, prefix="gt")
    if row.predicted_pack and row.predicted_pack != row.gt_pack:
        _fill_parse_status(row, data, registry, row.predicted_pack, prefix="predicted")
    elif row.predicted_pack:
        row.predicted_pack_parse_status = row.gt_pack_parse_status
        row.predicted_pack_parse_warnings = list(row.gt_pack_parse_warnings)
    return row


def _select_packs(registry: Any, evidence: EvidenceView) -> list[PackPrediction]:
    values: list[PackPrediction] = []
    try:
        selected = registry.select_packs(evidence, limit=5)
    except Exception:
        return values
    for pack, result in selected:
        values.append(PackPrediction(
            pack_id=str(pack.descriptor.pack_id),
            decision=str(result.decision),
            score=float(result.score),
            positive_evidence=tuple(str(item) for item in result.positive_evidence_ids),
            missing_evidence=tuple(str(item) for item in result.missing_evidence),
        ))
    return values


def _fill_sanity(row: PackGroundTruthRow, poc_path: Path) -> None:
    try:
        with _suppress_native_output():
            result = inspect_poc_bytes(str(poc_path), expected_format=row.gt_format)
    except Exception as exc:
        row.sanity_passed = None
        row.sanity_summary = f"error:{type(exc).__name__}:{str(exc)[:160]}"
        return
    row.sanity_passed = bool(result.passed)
    row.sanity_summary = str(result.summary)
    row.sanity_fail_count = sum(1 for issue in result.issues if issue.severity == "fail")
    row.sanity_warn_count = sum(1 for issue in result.issues if issue.severity == "warn")


def _fill_parse_status(
    row: PackGroundTruthRow,
    data: bytes,
    registry: Any,
    pack_id: str,
    *,
    prefix: str,
) -> None:
    attr_status = f"{prefix}_pack_parse_status"
    attr_warnings = f"{prefix}_pack_parse_warnings"
    if not pack_id:
        setattr(row, attr_status, "no_pack_label")
        return
    pack = registry.get_pack(pack_id)
    if pack is None:
        setattr(row, attr_status, "pack_not_registered")
        return
    try:
        with _suppress_native_output():
            result = pack.parse(data)
        setattr(row, attr_status, str(result.status))
        setattr(row, attr_warnings, [str(item) for item in result.parse_warnings[:8]])
    except Exception as exc:
        setattr(row, attr_status, f"error:{type(exc).__name__}")
        setattr(row, attr_warnings, [str(exc)[:200]])


def inspect_with_toolbox(path: Path, fmt: str) -> tuple[str, str]:
    normalized = (fmt or "").lower()
    if normalized not in _TOOLBOX_FORMATS:
        return "unsupported", ""
    module_path, _ = _TOOLBOX_FORMATS[normalized]
    try:
        import importlib
        mod = importlib.import_module(module_path)
        with _suppress_native_output():
            result = mod.inspect(str(path))
    except Exception as exc:
        return "error", f"{type(exc).__name__}: {str(exc)[:200]}"
    if not isinstance(result, dict):
        return "error", "inspect_returned_non_dict"
    if result.get("error"):
        return "inspect_fail", str(result.get("error"))[:200]
    if result.get("valid_signature") is False:
        return "inspect_fail", "invalid_signature"
    return "inspect_pass", ""


def detect_magic(data: bytes) -> str:
    for magic, name in _MAGIC_SIGNATURES:
        if data.startswith(magic):
            return name
    return "unknown"


def _group_rows(rows: list[PackGroundTruthRow], *, key: Any) -> dict[str, list[PackGroundTruthRow]]:
    grouped: dict[str, list[PackGroundTruthRow]] = defaultdict(list)
    for row in rows:
        grouped[str(key(row))].append(row)
    return grouped


def _sanity_bucket(row: PackGroundTruthRow) -> str:
    if row.sanity_passed is None:
        return "not_run"
    if row.sanity_passed:
        return "pass_with_warn" if row.sanity_warn_count else "pass"
    return "fail"


@contextmanager
def _suppress_native_output() -> Iterable[None]:
    """Suppress noisy parser output, including native writes to stdout/stderr."""
    saved_stdout = os.dup(1)
    saved_stderr = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved_stdout, 1)
        os.dup2(saved_stderr, 2)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(devnull)
