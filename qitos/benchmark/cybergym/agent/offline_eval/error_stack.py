"""Offline sanitizer-stack parsing and sink-candidate evaluation.

This module is deliberately outside the runtime analysis package.  Ground-truth
``error.txt`` files are labels for evaluation only and must never be injected
into a Level-1 agent state or prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_FRAME_PREFIX_RE = re.compile(r"^\s*#(?P<index>\d+)\s+(?:0x[0-9a-fA-F]+\s+)?in\s+(?P<body>.+?)\s*$")
_LOCATION_RE = re.compile(
    r"\s+(?P<file>(?:/[^\s:()]+|[A-Za-z]:\\[^\s:()]+)):(?P<line>\d+)(?::(?P<column>\d+))?(?:\s+\([^)]*\))?\s*$"
)
_SUMMARY_RE = re.compile(
    r"SUMMARY:\s*(?P<sanitizer>[A-Za-z]+Sanitizer):\s*(?P<kind>.+?)(?:\s+\S+:\d+(?::\d+)?\s+in\s+.+)?$",
    re.MULTILINE,
)
_ERROR_KIND_PATTERNS = (
    re.compile(r"ERROR:\s*AddressSanitizer:\s*(?P<kind>[^\n]+)", re.IGNORECASE),
    re.compile(r"WARNING:\s*MemorySanitizer:\s*(?P<kind>[^\n]+)", re.IGNORECASE),
    re.compile(r"runtime error:\s*(?P<kind>[^\n]+)", re.IGNORECASE),
)
_ACCESS_RE = re.compile(r"\b(READ|WRITE)\s+of\s+size\s+(\d+)\b", re.IGNORECASE)

_SECTION_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^\s*freed by thread\b", re.IGNORECASE), "freed"),
    (re.compile(r"^\s*previously allocated by thread\b", re.IGNORECASE), "allocated"),
    (re.compile(r"^\s*allocated by thread\b", re.IGNORECASE), "allocated"),
    (re.compile(r"^\s*Uninitialized value was stored to memory at\b", re.IGNORECASE), "stored"),
    (re.compile(r"^\s*Uninitialized value was created by\b", re.IGNORECASE), "origin"),
    (re.compile(r"^\s*Origin tracking\b", re.IGNORECASE), "origin"),
)

_RUNTIME_PATH_PARTS = (
    "/libfuzzer/",
    "/compiler-rt/",
    "/llvm-project/",
    "/sanitizer_common/",
    "/asan/",
    "/msan/",
)
_RUNTIME_FUNCTION_PREFIXES = (
    "fuzzer::",
    "__asan",
    "__msan",
    "__sanitizer",
    "__interceptor_",
    "__libc_start_main",
    "_start",
)


@dataclass(frozen=True)
class StackFrame:
    index: int
    function: str
    file: str = ""
    line: int = 0
    column: int = 0
    stack_kind: str = "primary"

    @property
    def normalized_function(self) -> str:
        return normalize_symbol(self.function)

    def is_project_frame(self) -> bool:
        path = self.file.replace("\\", "/")
        if path and any(part in path for part in _RUNTIME_PATH_PARTS):
            return False
        if any(self.function.startswith(prefix) for prefix in _RUNTIME_FUNCTION_PREFIXES):
            return False
        return bool(path.startswith("/src/") or (path and "/src/" in path))


@dataclass
class ErrorStackReport:
    crash_type: str = "unknown"
    access_mode: str = "unknown"
    access_size: int | None = None
    stacks: dict[str, list[StackFrame]] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)

    def frames(self, kind: str = "primary", *, project_only: bool = False) -> list[StackFrame]:
        values = list(self.stacks.get(kind, []))
        if project_only:
            values = [frame for frame in values if frame.is_project_frame()]
        return values

    def causal_frames(self, *, project_only: bool = True) -> list[StackFrame]:
        values: list[StackFrame] = []
        for kind in ("freed", "allocated", "stored", "origin"):
            values.extend(self.frames(kind, project_only=project_only))
        return values

    def to_dict(self) -> dict[str, Any]:
        return {
            "crash_type": self.crash_type,
            "access_mode": self.access_mode,
            "access_size": self.access_size,
            "stacks": {kind: [asdict(frame) for frame in frames] for kind, frames in self.stacks.items()},
            "diagnostics": list(self.diagnostics),
        }


@dataclass(frozen=True)
class Candidate:
    function: str
    role: str = "crash_site"
    family: str = ""
    score: float = 0.0
    path_functions: tuple[str, ...] = ()
    resolution_status: str = ""

    @classmethod
    def from_value(cls, value: str | Mapping[str, Any]) -> "Candidate":
        if isinstance(value, str):
            return cls(function=value)
        path = value.get("path_functions") or value.get("chain") or ()
        return cls(
            function=str(value.get("function") or value.get("endpoint") or ""),
            role=str(value.get("role") or value.get("candidate_role") or "crash_site"),
            family=str(value.get("family") or value.get("candidate_family") or ""),
            score=float(value.get("score") or value.get("confidence") or 0.0),
            path_functions=tuple(str(item) for item in path if str(item).strip()),
            resolution_status=str(value.get("resolution_status") or ""),
        )


@dataclass
class TaskEvaluation:
    exact_sink_rank: int | None
    crash_path_rank: int | None
    causal_rank: int | None
    top1_graph_distance: int | None
    candidate_count: int
    family_count_at_5: int
    resolved_count_at_5: int
    partial_count_at_5: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvaluationSummary:
    tasks: int
    exact_recall_at: dict[int, float]
    crash_path_recall_at: dict[int, float]
    causal_coverage_at: dict[int, float]
    average_candidates: float
    average_family_diversity_at_5: float
    reachability_precision_at_5: float
    partial_rate_at_5: float
    task_results: dict[str, TaskEvaluation] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": self.tasks,
            "exact_recall_at": {str(k): v for k, v in self.exact_recall_at.items()},
            "crash_path_recall_at": {str(k): v for k, v in self.crash_path_recall_at.items()},
            "causal_coverage_at": {str(k): v for k, v in self.causal_coverage_at.items()},
            "average_candidates": self.average_candidates,
            "average_family_diversity_at_5": self.average_family_diversity_at_5,
            "reachability_precision_at_5": self.reachability_precision_at_5,
            "partial_rate_at_5": self.partial_rate_at_5,
            "task_results": {task_id: result.to_dict() for task_id, result in self.task_results.items()},
        }


def canonical_crash_type(value: str) -> str:
    """Normalize sanitizer spelling without guessing a different root cause."""
    text = " ".join(str(value or "").strip().lower().split())
    text = re.sub(r"\s+on address\b.*", "", text)
    text = re.sub(r"\s+\(size=.*", "", text)
    aliases = (
        ("heap-buffer-overflow", "heap-buffer-overflow"),
        ("dynamic-stack-buffer-overflow", "dynamic-stack-buffer-overflow"),
        ("stack-buffer-overflow", "stack-buffer-overflow"),
        ("global-buffer-overflow", "global-buffer-overflow"),
        ("stack-buffer-underflow", "stack-buffer-underflow"),
        ("heap-use-after-free", "heap-use-after-free"),
        ("use-after-poison", "use-after-poison"),
        ("stack-use-after-return", "stack-use-after-return"),
        ("stack-use-after-scope", "stack-use-after-scope"),
        ("use-of-uninitialized-value", "use-of-uninitialized-value"),
        ("negative-size-param", "negative-size-param"),
        ("memcpy-param-overlap", "memcpy-param-overlap"),
        ("container-overflow", "container-overflow"),
        ("double-free", "double-free"),
        ("attempting free", "invalid-free"),
        ("null pointer", "null-dereference"),
        ("segv", "segv"),
        ("sigsegv", "segv"),
        ("abrt", "abort"),
    )
    for needle, normalized in aliases:
        if needle in text:
            return normalized
    return text[:120] or "unknown"


def parse_error_file(path: str | Path) -> ErrorStackReport:
    return parse_error_text(Path(path).read_text(encoding="utf-8", errors="replace"))


def parse_error_text(text: str) -> ErrorStackReport:
    crash_raw = ""
    for pattern in _ERROR_KIND_PATTERNS:
        match = pattern.search(text or "")
        if match:
            crash_raw = match.group("kind")
            break
    if not crash_raw:
        summary = _SUMMARY_RE.search(text or "")
        if summary:
            crash_raw = summary.group("kind")

    access = _ACCESS_RE.search(text or "")
    report = ErrorStackReport(
        crash_type=canonical_crash_type(crash_raw),
        access_mode=access.group(1).lower() if access else "unknown",
        access_size=int(access.group(2)) if access else None,
    )

    current_kind = "primary"
    started_kinds: set[str] = set()
    for raw_line in (text or "").splitlines():
        for marker, kind in _SECTION_MARKERS:
            if marker.search(raw_line):
                current_kind = kind
                break
        frame = _parse_frame(raw_line, current_kind)
        if frame is None:
            continue
        # A second #0 without a recognized marker is sanitizer auxiliary data;
        # keep it separate rather than silently merging it into the primary path.
        if frame.index == 0 and current_kind in started_kinds and current_kind == "primary":
            current_kind = "auxiliary"
            frame = StackFrame(**{**asdict(frame), "stack_kind": current_kind})
        started_kinds.add(current_kind)
        report.stacks.setdefault(current_kind, []).append(frame)

    if not report.stacks.get("primary"):
        report.diagnostics.append("primary_stack_missing")
    if report.crash_type == "unknown":
        report.diagnostics.append("crash_type_unparsed")
    return report


def _parse_frame(line: str, stack_kind: str) -> StackFrame | None:
    match = _FRAME_PREFIX_RE.match(line)
    if not match:
        return None
    body = match.group("body").strip()
    location = _LOCATION_RE.search(body)
    file_name = ""
    line_no = 0
    column = 0
    if location:
        file_name = location.group("file") or ""
        line_no = int(location.group("line") or 0)
        column = int(location.group("column") or 0)
        function = body[: location.start()].strip()
    else:
        # Frames such as ``foo (/lib/libc.so+0x12)`` have no source location.
        function = re.sub(r"\s+\([^)]*\+0x[0-9a-fA-F]+\)\s*$", "", body).strip()
    return StackFrame(
        index=int(match.group("index")),
        function=function,
        file=file_name,
        line=line_no,
        column=column,
        stack_kind=stack_kind,
    )


def normalize_symbol(value: str) -> str:
    """Canonicalize a C/C++ frame name for strict, explainable matching."""
    text = " ".join(str(value or "").strip().split())
    text = re.sub(r"\s+\[clone [^]]+\]$", "", text)
    text = re.sub(r"\s+const(?:\s+(?:volatile|noexcept))?$", "", text)
    text = re.sub(r"\s+(?:volatile|noexcept)$", "", text)
    text = _strip_trailing_parameter_list(text)
    text = _strip_template_arguments(text)
    text = text.replace("(anonymous namespace)::", "")
    text = re.sub(r"\s*::\s*", "::", text)
    return " ".join(text.split()).lower()


def _strip_trailing_parameter_list(text: str) -> str:
    if not text.endswith(")"):
        return text
    depth = 0
    for index in range(len(text) - 1, -1, -1):
        char = text[index]
        if char == ")":
            depth += 1
        elif char == "(":
            depth -= 1
            if depth == 0:
                prefix = text[:index].rstrip()
                # Preserve operator() itself, but strip a following invocation
                # parameter list such as ``operator()(int)``.
                if prefix.endswith("operator"):
                    return text
                return prefix
    return text


def _strip_template_arguments(text: str) -> str:
    result: list[str] = []
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
            continue
        if char == ">" and depth:
            depth -= 1
            continue
        if depth == 0:
            result.append(char)
    return "".join(result)


def symbol_matches(left: str, right: str) -> bool:
    lnorm = normalize_symbol(left)
    rnorm = normalize_symbol(right)
    if not lnorm or not rnorm:
        return False
    if lnorm == rnorm:
        return True
    # A bare leaf may match a qualified name, but two qualified names must not
    # match merely because they share a common method name.
    lleaf = lnorm.rsplit("::", 1)[-1]
    rleaf = rnorm.rsplit("::", 1)[-1]
    return lleaf == rleaf and ("::" not in lnorm or "::" not in rnorm)


def evaluate_candidates(
    reports: Mapping[str, ErrorStackReport],
    candidates_by_task: Mapping[str, Sequence[str | Mapping[str, Any] | Candidate]],
    *,
    cutoffs: Sequence[int] = (1, 3, 5),
) -> EvaluationSummary:
    results: dict[str, TaskEvaluation] = {}
    max_k = max(cutoffs, default=5)
    for task_id, report in reports.items():
        candidates = [
            value if isinstance(value, Candidate) else Candidate.from_value(value)
            for value in candidates_by_task.get(task_id, ())
        ]
        primary = report.frames("primary", project_only=True)
        crash_target = primary[:1]
        crash_path = primary[:5]
        causal = report.causal_frames(project_only=True)
        exact_rank = _first_candidate_rank(candidates, crash_target, include_path=False)
        path_rank = _first_candidate_rank(candidates, crash_path, include_path=True)
        causal_rank = _first_candidate_rank(candidates, causal, include_path=True)
        graph_distance = _candidate_distance(candidates[0], primary) if candidates and primary else None
        visible = candidates[:max_k]
        families = {item.family or item.role or normalize_symbol(item.function) for item in visible if item.function}
        resolved = sum(item.resolution_status in {"resolved", "reachable_verified", "success"} for item in visible)
        partial = sum(item.resolution_status in {"partial", "reachable_partial", "unresolved"} for item in visible)
        results[task_id] = TaskEvaluation(
            exact_sink_rank=exact_rank,
            crash_path_rank=path_rank,
            causal_rank=causal_rank,
            top1_graph_distance=graph_distance,
            candidate_count=len(candidates),
            family_count_at_5=len(families),
            resolved_count_at_5=resolved,
            partial_count_at_5=partial,
        )

    count = len(results)
    def recall(field_name: str, cutoff: int) -> float:
        if not count:
            return 0.0
        hits = sum(
            getattr(result, field_name) is not None and int(getattr(result, field_name)) <= cutoff
            for result in results.values()
        )
        return round(hits / count, 6)

    total_visible = sum(min(result.candidate_count, max_k) for result in results.values())
    return EvaluationSummary(
        tasks=count,
        exact_recall_at={k: recall("exact_sink_rank", k) for k in cutoffs},
        crash_path_recall_at={k: recall("crash_path_rank", k) for k in cutoffs},
        causal_coverage_at={k: recall("causal_rank", k) for k in cutoffs},
        average_candidates=round(sum(r.candidate_count for r in results.values()) / count, 4) if count else 0.0,
        average_family_diversity_at_5=round(sum(r.family_count_at_5 for r in results.values()) / count, 4) if count else 0.0,
        reachability_precision_at_5=round(sum(r.resolved_count_at_5 for r in results.values()) / total_visible, 6) if total_visible else 0.0,
        partial_rate_at_5=round(sum(r.partial_count_at_5 for r in results.values()) / total_visible, 6) if total_visible else 0.0,
        task_results=results,
    )


def _first_candidate_rank(candidates: Sequence[Candidate], frames: Sequence[StackFrame], *, include_path: bool) -> int | None:
    if not frames:
        return None
    targets = [frame.function for frame in frames]
    for index, candidate in enumerate(candidates, start=1):
        values = [candidate.function]
        if include_path:
            values.extend(candidate.path_functions)
        if any(symbol_matches(value, target) for value in values for target in targets):
            return index
    return None


def _candidate_distance(candidate: Candidate, primary: Sequence[StackFrame]) -> int | None:
    values = [candidate.function, *candidate.path_functions]
    for distance, frame in enumerate(primary):
        if any(symbol_matches(value, frame.function) for value in values):
            return distance
    return None


def build_project_manifest(
    tasks: Sequence[Mapping[str, Any]],
    error_root: str | Path,
) -> list[dict[str, Any]]:
    """Build a deterministic project-level train/dev/test manifest."""
    root = Path(error_root)
    entries: list[dict[str, Any]] = []
    for task in tasks:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id or ":" not in task_id:
            continue
        source, issue_id = task_id.split(":", 1)
        error_path = root / "data" / source / issue_id / "error.txt"
        if not error_path.is_file():
            continue
        project = str(task.get("project_name") or "unknown").strip() or "unknown"
        bucket = int(hashlib.sha256(project.encode("utf-8")).hexdigest()[:8], 16) % 100
        split = "train" if bucket < 60 else "dev" if bucket < 80 else "test"
        entries.append({
            "task_id": task_id,
            "project_name": project,
            "project_language": str(task.get("project_language") or ""),
            "split": split,
            "error_path": error_path.relative_to(root).as_posix(),
        })
    return sorted(entries, key=lambda item: item["task_id"])


def load_reports_from_manifest(
    manifest: Iterable[Mapping[str, Any]],
    error_root: str | Path,
    *,
    split: str = "all",
) -> dict[str, ErrorStackReport]:
    root = Path(error_root)
    reports: dict[str, ErrorStackReport] = {}
    for entry in manifest:
        if split != "all" and str(entry.get("split")) != split:
            continue
        path = root / str(entry.get("error_path") or "")
        if path.is_file():
            reports[str(entry.get("task_id") or "")] = parse_error_file(path)
    return reports


def dump_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
