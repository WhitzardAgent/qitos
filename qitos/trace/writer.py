"""Trace writer for event-sourced QitOS runs."""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .events import TraceEvent, TraceStep
from .schema import TraceSchemaValidator


class TraceWriter:
    def __init__(
        self,
        output_dir: str,
        run_id: str,
        schema_version: str = "v1",
        metadata: Optional[Dict[str, Any]] = None,
        strict_validate: bool = True,
    ):
        self.output_dir = output_dir
        self.run_id = run_id
        self.schema_version = schema_version
        self.metadata = metadata or {}
        self.strict_validate = strict_validate
        self.run_dir = os.path.join(output_dir, run_id)
        self.events_path = os.path.join(self.run_dir, "events.jsonl")
        self.steps_path = os.path.join(self.run_dir, "steps.jsonl")
        self.manifest_path = os.path.join(self.run_dir, "manifest.json")
        self._event_count = 0
        self._step_count = 0

        os.makedirs(self.run_dir, exist_ok=True)
        self._write_manifest(status="running")

    def write_event(self, event: TraceEvent) -> None:
        self._append_jsonl(self.events_path, event.to_dict())
        self._event_count += 1

    def write_step(self, step: TraceStep) -> None:
        self._append_jsonl(self.steps_path, step.to_dict())
        self._step_count += 1

    def finalize(self, status: str, summary: Optional[Dict[str, Any]] = None) -> None:
        self._write_manifest(status=status, summary=summary or {})
        if self.strict_validate and status != "running":
            self._validate_artifacts()

    def _append_jsonl(self, path: str, payload: Dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.write("\n")

    def _write_manifest(
        self, status: str, summary: Optional[Dict[str, Any]] = None
    ) -> None:
        token_usage, latency_seconds, cost = self._summary_totals(summary or {})
        merged_summary: Dict[str, Any] = {
            "stop_reason": None,
            "final_result": None,
            "steps": self._step_count,
            "failure_report": {},
        }
        merged_summary.update(summary or {})
        payload = {
            "run_id": self.run_id,
            "schema_version": self.schema_version,
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "event_count": self._event_count,
            "step_count": self._step_count,
            "summary": merged_summary,
            "model_id": self.metadata.get("model_id", "unknown"),
            "prompt_hash": self.metadata.get("prompt_hash", "unknown"),
            "tool_versions": self.metadata.get("tool_versions", {}),
            "seed": self.metadata.get("seed", None),
            "run_config_hash": self.metadata.get("run_config_hash", "unknown"),
            "git_sha": self.metadata.get("git_sha"),
            "package_version": self.metadata.get("package_version"),
            "benchmark_name": self.metadata.get("benchmark_name"),
            "benchmark_split": self.metadata.get("benchmark_split"),
            "model_family": self.metadata.get("model_family"),
            "prompt_protocol": self.metadata.get("prompt_protocol"),
            "parser_name": self.metadata.get("parser_name"),
            "tool_manifest": self.metadata.get("tool_manifest", []),
            "run_spec": self.metadata.get("run_spec"),
            "experiment_spec": self.metadata.get("experiment_spec"),
            "official_run": bool(self.metadata.get("official_run", False)),
            "replay_mode": self.metadata.get("replay_mode"),
            "replay_note": self.metadata.get("replay_note"),
            "token_usage": token_usage,
            "latency_seconds": latency_seconds,
            "cost": cost,
        }
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _summary_totals(self, summary: Dict[str, Any]) -> tuple[int, float, float]:
        task_result = summary.get("task_result")
        metrics = {}
        if isinstance(task_result, dict):
            maybe_metrics = task_result.get("metrics")
            if isinstance(maybe_metrics, dict):
                metrics = maybe_metrics
        token_usage = summary.get("token_usage", metrics.get("token_usage", 0))
        latency_seconds = summary.get(
            "latency_seconds", metrics.get("elapsed_seconds", 0.0)
        )
        cost = summary.get("cost", metrics.get("cost", 0.0))
        return int(token_usage or 0), float(latency_seconds or 0.0), float(cost or 0.0)

    def _validate_artifacts(self) -> None:
        validator = TraceSchemaValidator()
        with open(self.manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        events = []
        if os.path.exists(self.events_path):
            with open(self.events_path, "r", encoding="utf-8") as f:
                events = [
                    json.loads(line) for line in f.read().splitlines() if line.strip()
                ]
        steps = []
        if os.path.exists(self.steps_path):
            with open(self.steps_path, "r", encoding="utf-8") as f:
                steps = [
                    json.loads(line) for line in f.read().splitlines() if line.strip()
                ]

        validator.validate_manifest(manifest)
        validator.validate_events(events)
        validator.validate_steps(steps)


def runtime_event_to_trace(run_id: str, event: Any) -> TraceEvent:
    phase = getattr(event, "phase", None)
    if phase is not None and hasattr(phase, "value"):
        phase = phase.value
    ts = str(getattr(event, "ts", "")).strip() or datetime.now(timezone.utc).isoformat()
    return TraceEvent(
        run_id=run_id,
        step_id=int(getattr(event, "step_id", 0)),
        phase=str(phase),
        ok=bool(getattr(event, "ok", True)),
        payload=_normalize(getattr(event, "payload", {}) or {}),
        error=getattr(event, "error", None),
        ts=ts,
    )


def runtime_step_to_trace(step: Any) -> TraceStep:
    decision = getattr(step, "decision", None)
    decision_payload: Any
    if decision is not None and hasattr(decision, "__dict__"):
        decision_payload = (
            asdict(decision)
            if hasattr(decision, "__dataclass_fields__")
            else dict(decision.__dict__)
        )
    else:
        decision_payload = decision

    return TraceStep(
        step_id=int(getattr(step, "step_id", 0)),
        observation=_normalize(getattr(step, "observation", None)),
        decision=_normalize(decision_payload),
        model_response=_normalize(dict(getattr(step, "model_response", {}) or {})),
        actions=_normalize(list(getattr(step, "actions", []) or [])),
        action_results=_normalize(list(getattr(step, "action_results", []) or [])),
        tool_invocations=_normalize(list(getattr(step, "tool_invocations", []) or [])),
        critic_outputs=_normalize(list(getattr(step, "critic_outputs", []) or [])),
        state_diff=_normalize(dict(getattr(step, "state_diff", {}) or {})),
        context=_normalize(dict(getattr(step, "context", {}) or {})),
        prompt_metadata=_normalize(dict(getattr(step, "prompt_metadata", {}) or {})),
        protocol_id=getattr(step, "protocol_id", None),
        parser_selected=getattr(step, "parser_selected", None),
        parser_fallback_used=bool(getattr(step, "parser_fallback_used", False)),
        parser_attempts=_normalize(list(getattr(step, "parser_attempts", []) or [])),
        parser_diagnostics=_normalize(
            dict(getattr(step, "parser_diagnostics", {}) or {})
        ),
        parser_contract=getattr(step, "parser_contract", None),
        parser_salvage_applied=bool(getattr(step, "parser_salvage_applied", False)),
        decision_source=getattr(step, "decision_source", None),
        native_tool_call_used=bool(getattr(step, "native_tool_call_used", False)),
        native_tool_call_fallback_reason=getattr(
            step, "native_tool_call_fallback_reason", None
        ),
        visual_assets=_normalize(list(getattr(step, "visual_assets", []) or [])),
        observation_modalities=_normalize(
            list(getattr(step, "observation_modalities", []) or [])
        ),
        visual_asset_count=int(getattr(step, "visual_asset_count", 0) or 0),
        has_screenshot=bool(getattr(step, "has_screenshot", False)),
        has_dom=bool(getattr(step, "has_dom", False)),
        has_accessibility_tree=bool(
            getattr(step, "has_accessibility_tree", False)
        ),
        model_input_modalities=_normalize(
            list(getattr(step, "model_input_modalities", []) or [])
        ),
        model_input_visual_count=int(
            getattr(step, "model_input_visual_count", 0) or 0
        ),
    )


def _normalize(value: Any) -> Any:
    if value is not None and dataclasses.is_dataclass(value):
        return {k: _normalize(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return repr(value)
