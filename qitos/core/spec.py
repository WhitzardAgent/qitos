"""Stable run and experiment specifications for reproducible QitOS runs."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Dict, Mapping, Optional


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _read_package_version() -> str:
    try:
        init_path = Path(__file__).resolve().parents[1] / "__init__.py"
        content = init_path.read_text(encoding="utf-8")
        match = re.search(r'^__version__ = [\'"]([^\'"]+)[\'"]', content, re.M)
        if match:
            return str(match.group(1))
    except Exception:
        pass
    return "unknown"


def _current_git_sha() -> Optional[str]:
    env_sha = str(os.getenv("GITHUB_SHA", "")).strip()
    if env_sha:
        return env_sha[:12]
    try:
        root = Path(__file__).resolve().parents[2]
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(root),
            stderr=subprocess.DEVNULL,
            text=True,
        )
        sha = str(out).strip()
        return sha or None
    except Exception:
        return None


def _infer_model_family(model_name: Optional[str]) -> Optional[str]:
    if model_name is None:
        return None
    value = str(model_name).strip()
    if not value:
        return None
    if "/" in value:
        return value.split("/", 1)[0]
    parts = [x for x in re.split(r"[-_.:]", value) if x]
    return parts[0] if parts else value


@dataclass
class RunSpec:
    """Stable description of one official or reproducible QitOS run."""

    model_family: Optional[str] = None
    model_name: Optional[str] = None
    prompt_protocol: Optional[str] = None
    parser_name: Optional[str] = None
    toolset_name: Optional[str] = None
    tool_manifest: list[dict[str, Any]] = field(default_factory=list)
    environment: Dict[str, Any] = field(default_factory=dict)
    seed: Optional[int] = None
    stop_criteria: list[str] = field(default_factory=list)
    git_sha: Optional[str] = field(default_factory=_current_git_sha)
    package_version: str = field(default_factory=_read_package_version)
    trace_schema_version: str = "v1"
    benchmark_name: Optional[str] = None
    benchmark_split: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_value(cls, value: "RunSpec | Mapping[str, Any] | None") -> "RunSpec":
        if isinstance(value, cls):
            return cls(**value.to_dict())
        if isinstance(value, Mapping):
            return cls(**dict(value))
        return cls()

    def fingerprint(self) -> str:
        raw = json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def is_official_run(self) -> bool:
        return all(
            [
                bool(self.model_name),
                bool(self.prompt_protocol),
                bool(self.parser_name),
                bool(self.trace_schema_version),
                bool(self.package_version),
            ]
        )

    @classmethod
    def infer(
        cls,
        *,
        model_name: Optional[str] = None,
        prompt_protocol: Optional[str] = None,
        parser_name: Optional[str] = None,
        toolset_name: Optional[str] = None,
        tool_manifest: Optional[list[dict[str, Any]]] = None,
        environment: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
        stop_criteria: Optional[list[str]] = None,
        trace_schema_version: str = "v1",
        benchmark_name: Optional[str] = None,
        benchmark_split: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "RunSpec":
        return cls(
            model_family=_infer_model_family(model_name),
            model_name=model_name,
            prompt_protocol=prompt_protocol,
            parser_name=parser_name,
            toolset_name=toolset_name,
            tool_manifest=list(tool_manifest or []),
            environment=dict(environment or {}),
            seed=seed,
            stop_criteria=list(stop_criteria or []),
            trace_schema_version=str(trace_schema_version or "v1"),
            benchmark_name=benchmark_name,
            benchmark_split=benchmark_split,
            metadata=dict(metadata or {}),
        )


@dataclass
class ExperimentSpec:
    """Specification for a batch of benchmark or research runs."""

    name: Optional[str] = None
    benchmark_name: Optional[str] = None
    benchmark_split: Optional[str] = None
    judge_config: Dict[str, Any] = field(default_factory=dict)
    benchmark_metadata: Dict[str, Any] = field(default_factory=dict)
    run_defaults: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_value(
        cls, value: "ExperimentSpec | Mapping[str, Any] | None"
    ) -> Optional["ExperimentSpec"]:
        if value is None:
            return None
        if isinstance(value, cls):
            return cls(**value.to_dict())
        if isinstance(value, Mapping):
            return cls(**dict(value))
        return None


@dataclass
class BenchmarkRunResult:
    """Normalized high-level benchmark output row."""

    task_id: str
    benchmark: str
    split: str
    prediction: Any
    success: bool
    stop_reason: Optional[str]
    steps: int
    latency_seconds: float
    token_usage: int
    cost: float
    trace_run_dir: Optional[str]
    run_spec_ref: Optional[str]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(asdict(self))

    @classmethod
    def from_value(
        cls, value: "BenchmarkRunResult | Mapping[str, Any]"
    ) -> "BenchmarkRunResult":
        if isinstance(value, cls):
            return cls(**value.to_dict())
        if isinstance(value, Mapping):
            return cls(**dict(value))
        raise TypeError("BenchmarkRunResult.from_value expects a mapping or instance")
