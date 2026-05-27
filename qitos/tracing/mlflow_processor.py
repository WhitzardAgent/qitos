"""MLflow trace processor for QitOS.

Streams run metrics to an MLflow tracking server so that agent
trajectories, token usage, critic scores, and tool invocations
are visible in the MLflow UI.

Usage::

    from qitos.tracing import add_trace_processor
    from qitos.tracing.mlflow_processor import MlflowTraceProcessor

    processor = MlflowTraceProcessor(experiment_name="qitos-runs")
    add_trace_processor(processor)

Requires the ``mlflow`` package (``pip install qitos[mlflow]``).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .models import (
    ActSpanData,
    AgentSpanData,
    CriticSpanData,
    GenerationSpanData,
    Span,
    SpanData,
    SpanType,
    StepSpanData,
    ToolSpanData,
    Trace,
)
from .processor import TraceProcessor

logger = logging.getLogger(__name__)


def _require_mlflow():  # noqa: ANN202
    """Import mlflow lazily and raise a helpful error if missing."""
    try:
        import mlflow  # noqa: F401 — used for type checks below

        return mlflow
    except ImportError as exc:
        raise ImportError(
            "mlflow is required for MlflowTraceProcessor. "
            "Install it with: pip install qitos[mlflow]"
        ) from exc


class MlflowTraceProcessor(TraceProcessor):
    """TraceProcessor that streams QitOS run data to MLflow.

    Parameters
    ----------
    experiment_name:
        MLflow experiment name (passed to ``mlflow.set_experiment``).
    run_name:
        MLflow run name.  Defaults to the QitOS trace name.
    tracking_uri:
        MLflow tracking server URI.  If ``None``, uses the default
        (local ``mlruns/`` directory or ``MLFLOW_TRACKING_URI`` env var).
    tags:
        Dictionary of tags for the MLflow run.
    auto_end_run:
        Whether to call ``mlflow.end_run()`` when the trace ends.
        Defaults to ``True``.
    """

    def __init__(
        self,
        *,
        experiment_name: str = "qitos",
        run_name: Optional[str] = None,
        tracking_uri: Optional[str] = None,
        tags: Optional[Dict[str, str]] = None,
        auto_end_run: bool = True,
    ) -> None:
        self._experiment_name = experiment_name
        self._run_name = run_name
        self._tracking_uri = tracking_uri
        self._tags = tags or {}
        self._auto_end_run = auto_end_run
        self._active_run: Optional[Any] = None
        self._step_counter: int = 0
        self._total_tokens: int = 0
        self._total_steps: int = 0
        self._tool_calls: int = 0
        self._critic_scores: List[float] = []

    # -- helpers --------------------------------------------------------------

    def _log_span_metrics(self, span: Span) -> None:
        """Extract and log metrics from a completed span."""
        if self._active_run is None:
            return

        mlflow = _require_mlflow()
        data = span.data

        if isinstance(data, GenerationSpanData):
            if data.token_usage:
                prompt_tokens = data.token_usage.get("prompt_tokens", 0)
                completion_tokens = data.token_usage.get("completion_tokens", 0)
                self._total_tokens += prompt_tokens + completion_tokens
                mlflow.log_metrics(
                    {
                        "generation/prompt_tokens": prompt_tokens,
                        "generation/completion_tokens": completion_tokens,
                        "generation/total_tokens": prompt_tokens + completion_tokens,
                    },
                    step=self._step_counter,
                )
                if data.model:
                    mlflow.log_param(
                        "generation_model", data.model
                    )
                self._step_counter += 1

        elif isinstance(data, StepSpanData):
            self._total_steps += 1
            mlflow.log_metrics(
                {"step/number": data.step_number},
                step=self._step_counter,
            )
            self._step_counter += 1

        elif isinstance(data, CriticSpanData):
            if data.score is not None:
                self._critic_scores.append(data.score)
                mlflow.log_metrics(
                    {"critic/score": data.score},
                    step=self._step_counter,
                )
                if data.critic_name:
                    mlflow.set_tag("critic.last_name", data.critic_name)
                self._step_counter += 1

        elif isinstance(data, ToolSpanData):
            self._tool_calls += 1
            mlflow.set_tag(f"tool.{data.tool_name}.called", "true")
            self._step_counter += 1

        elif isinstance(data, ActSpanData):
            if data.action_name:
                self._tool_calls += 1
                mlflow.set_tag(f"action.{data.action_name}.called", "true")
                self._step_counter += 1

    # -- TraceProcessor interface ---------------------------------------------

    def on_trace_start(self, trace: Trace) -> None:
        mlflow = _require_mlflow()
        try:
            if self._tracking_uri:
                mlflow.set_tracking_uri(self._tracking_uri)
            mlflow.set_experiment(self._experiment_name)
            run_name = self._run_name or trace.name
            self._active_run = mlflow.start_run(
                run_name=run_name,
                tags=self._tags,
            )
            # Log trace metadata as params
            if trace.metadata:
                for key, value in trace.metadata.items():
                    try:
                        mlflow.log_param(key, value)
                    except Exception:
                        pass  # Skip non-serializable params
        except Exception:
            logger.exception("Failed to initialize MLflow run")

    def on_trace_end(self, trace: Trace) -> None:
        if self._active_run is None:
            return

        mlflow = _require_mlflow()

        # Log final summary metrics
        summary: Dict[str, Any] = {
            "total_tokens": self._total_tokens,
            "total_steps": self._total_steps,
            "total_tool_calls": self._tool_calls,
        }
        if self._critic_scores:
            summary["critic/avg_score"] = sum(self._critic_scores) / len(
                self._critic_scores
            )
            summary["critic/min_score"] = min(self._critic_scores)
            summary["critic/max_score"] = max(self._critic_scores)

        # Try to extract stop_reason from trace metadata
        stop_reason = trace.metadata.get("stop_reason")
        if stop_reason:
            mlflow.set_tag("stop_reason", stop_reason)

        mlflow.log_metrics(summary)

        if self._auto_end_run:
            try:
                mlflow.end_run()
            except Exception:
                logger.exception("Failed to end MLflow run")
            self._active_run = None

    def on_span_start(self, span: Span) -> None:
        pass

    def on_span_end(self, span: Span) -> None:
        self._log_span_metrics(span)

    def shutdown(self) -> None:
        if self._active_run is not None and self._auto_end_run:
            mlflow = _require_mlflow()
            try:
                mlflow.end_run()
            except Exception:
                logger.exception("Failed to end MLflow run on shutdown")
            self._active_run = None

    def force_flush(self) -> None:
        # MLflow auto-flushes; no explicit action needed
        pass
