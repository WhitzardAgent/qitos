"""Canonical task schema for QitOS agentic workloads."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field as dc_field
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from .env import EnvSpec


@dataclass
class TaskValidationIssue:
    code: str
    message: str
    field: str
    details: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class TaskResourceBinding:
    kind: str
    source: str
    target: Optional[str] = None
    exists: bool = False
    required: bool = True
    metadata: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class TaskCriterionResult:
    criterion: str
    passed: bool
    evidence: str = ""


@dataclass
class TaskResult:
    task_id: str
    success: bool
    stop_reason: Optional[str]
    final_result: Any
    criteria: List[TaskCriterionResult] = dc_field(default_factory=list)
    artifacts: List[TaskResourceBinding] = dc_field(default_factory=list)
    metrics: Dict[str, Any] = dc_field(default_factory=dict)
    metadata: Dict[str, Any] = dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TaskResource:
    """One resource entry required by a task."""

    kind: str  # file | dir | url | artifact | image
    path: Optional[str] = None
    uri: Optional[str] = None
    mount_to: Optional[str] = None
    required: bool = True
    description: str = ""
    metadata: Dict[str, Any] = dc_field(default_factory=dict)


@dataclass
class TaskBudget:
    """Task-level budget contract."""

    max_steps: Optional[int] = None
    max_runtime_seconds: Optional[float] = None
    max_tokens: Optional[int] = None


@dataclass
class Task:
    """Task package with objective, resources, and environment requirements."""

    id: str
    objective: str
    inputs: Dict[str, Any] = dc_field(default_factory=dict)
    resources: List[TaskResource] = dc_field(default_factory=list)
    env_spec: Optional[EnvSpec] = None
    constraints: Dict[str, Any] = dc_field(default_factory=dict)
    success_criteria: List[str] = dc_field(default_factory=list)
    budget: TaskBudget = dc_field(default_factory=TaskBudget)
    metadata: Dict[str, Any] = dc_field(default_factory=dict)

    def resolve_resources(
        self, workspace: Optional[str] = None
    ) -> List[TaskResourceBinding]:
        root = Path(workspace).resolve() if workspace else None
        out: List[TaskResourceBinding] = []
        for item in self.resources:
            source = item.path or item.uri or ""
            target = item.mount_to
            exists = False
            if root is not None and item.path:
                exists = (root / item.path).exists()
                if target is None:
                    target = item.path
            out.append(
                TaskResourceBinding(
                    kind=item.kind,
                    source=source,
                    target=target,
                    exists=exists,
                    required=item.required,
                    metadata=dict(item.metadata),
                )
            )
        return out

    def validate(self) -> None:
        issues = self.validate_structured()
        if issues:
            first = issues[0]
            raise ValueError(f"{first.code}: {first.message}")

    def validate_structured(
        self, workspace: Optional[str] = None
    ) -> List[TaskValidationIssue]:
        issues: List[TaskValidationIssue] = []
        if not self.id or not isinstance(self.id, str):
            issues.append(
                TaskValidationIssue(
                    code="TASK_ID_INVALID",
                    message="Task.id must be a non-empty string",
                    field="id",
                )
            )
        if not self.objective or not isinstance(self.objective, str):
            issues.append(
                TaskValidationIssue(
                    code="TASK_OBJECTIVE_INVALID",
                    message="Task.objective must be a non-empty string",
                    field="objective",
                )
            )

        if self.budget.max_steps is not None and int(self.budget.max_steps) <= 0:
            issues.append(
                TaskValidationIssue(
                    code="TASK_BUDGET_STEPS_INVALID",
                    message="Task budget max_steps must be > 0",
                    field="budget.max_steps",
                    details={"value": self.budget.max_steps},
                )
            )
        if (
            self.budget.max_runtime_seconds is not None
            and float(self.budget.max_runtime_seconds) <= 0
        ):
            issues.append(
                TaskValidationIssue(
                    code="TASK_BUDGET_RUNTIME_INVALID",
                    message="Task budget max_runtime_seconds must be > 0",
                    field="budget.max_runtime_seconds",
                    details={"value": self.budget.max_runtime_seconds},
                )
            )
        if self.budget.max_tokens is not None and int(self.budget.max_tokens) <= 0:
            issues.append(
                TaskValidationIssue(
                    code="TASK_BUDGET_TOKENS_INVALID",
                    message="Task budget max_tokens must be > 0",
                    field="budget.max_tokens",
                    details={"value": self.budget.max_tokens},
                )
            )

        if self.env_spec is not None:
            if (
                not isinstance(self.env_spec.type, str)
                or not self.env_spec.type.strip()
            ):
                issues.append(
                    TaskValidationIssue(
                        code="TASK_ENV_SPEC_INVALID",
                        message="env_spec.type must be a non-empty string",
                        field="env_spec.type",
                    )
                )

        root = Path(workspace).resolve() if workspace else None
        for idx, item in enumerate(self.resources):
            if item.kind not in {"file", "dir", "url", "artifact", "image"}:
                issues.append(
                    TaskValidationIssue(
                        code="TASK_RESOURCE_KIND_INVALID",
                        message=f"Unsupported TaskResource.kind: {item.kind}",
                        field=f"resources[{idx}].kind",
                        details={"kind": item.kind},
                    )
                )
            if not item.path and not item.uri:
                issues.append(
                    TaskValidationIssue(
                        code="TASK_RESOURCE_LOCATOR_MISSING",
                        message="TaskResource requires path or uri",
                        field=f"resources[{idx}]",
                    )
                )
            if item.mount_to is not None and (
                not isinstance(item.mount_to, str) or not item.mount_to.strip()
            ):
                issues.append(
                    TaskValidationIssue(
                        code="TASK_RESOURCE_MOUNT_INVALID",
                        message="TaskResource.mount_to must be a non-empty string when provided",
                        field=f"resources[{idx}].mount_to",
                    )
                )
            if root is None or not item.path:
                continue
            candidate = (root / item.path).resolve()
            if item.required and not candidate.exists():
                issues.append(
                    TaskValidationIssue(
                        code="TASK_RESOURCE_MISSING",
                        message=f"Required resource does not exist: {item.path}",
                        field=f"resources[{idx}].path",
                        details={"path": item.path},
                    )
                )
                continue
            if candidate.exists():
                if not _is_writable(candidate):
                    issues.append(
                        TaskValidationIssue(
                            code="TASK_RESOURCE_NOT_WRITABLE",
                            message=f"Resource is not writable: {item.path}",
                            field=f"resources[{idx}].path",
                            details={"path": item.path},
                        )
                    )
            else:
                parent = candidate.parent
                if not _is_writable(parent):
                    issues.append(
                        TaskValidationIssue(
                            code="TASK_RESOURCE_PARENT_NOT_WRITABLE",
                            message=f"Parent directory is not writable for: {item.path}",
                            field=f"resources[{idx}].path",
                            details={"path": item.path, "parent": str(parent)},
                        )
                    )
        return issues

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if self.env_spec is not None:
            payload["env_spec"] = asdict(self.env_spec)
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "Task":
        resources_raw = payload.get("resources", [])
        resources: List[TaskResource] = []
        if isinstance(resources_raw, list):
            for item in resources_raw:
                if isinstance(item, TaskResource):
                    resources.append(item)
                elif isinstance(item, dict):
                    resources.append(TaskResource(**item))

        budget_raw = payload.get("budget", {})
        if isinstance(budget_raw, TaskBudget):
            budget = budget_raw
        elif isinstance(budget_raw, dict):
            budget = TaskBudget(**budget_raw)
        else:
            budget = TaskBudget()

        env_raw = payload.get("env_spec")
        if isinstance(env_raw, EnvSpec):
            env_spec = env_raw
        elif isinstance(env_raw, dict):
            env_spec = EnvSpec(**env_raw)
        else:
            env_spec = None

        obj = cls(
            id=str(payload.get("id", "")),
            objective=str(payload.get("objective", "")),
            inputs=(
                payload.get("inputs", {})
                if isinstance(payload.get("inputs", {}), dict)
                else {}
            ),
            resources=resources,
            env_spec=env_spec,
            constraints=(
                payload.get("constraints", {})
                if isinstance(payload.get("constraints", {}), dict)
                else {}
            ),
            success_criteria=[
                str(x)
                for x in payload.get("success_criteria", [])
                if isinstance(payload.get("success_criteria", []), list)
            ],
            budget=budget,
            metadata=(
                payload.get("metadata", {})
                if isinstance(payload.get("metadata", {}), dict)
                else {}
            ),
        )
        obj.validate()
        return obj


def _is_writable(path: Path) -> bool:
    try:
        if not path.exists():
            return False
        return os.access(path, os.W_OK)
    except Exception:
        return False


__all__ = [
    "Task",
    "TaskResource",
    "TaskBudget",
    "TaskValidationIssue",
    "TaskResourceBinding",
    "TaskCriterionResult",
    "TaskResult",
]
