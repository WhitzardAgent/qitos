"""Weights & Biases trace processor for QitOS.

Streams run metrics to a W&B project so that agent trajectories,
token usage, critic scores, and tool invocations are visible in
the W&B dashboard.

Usage::

    from qitos.tracing import add_trace_processor
    from qitos.tracing.wandb_processor import WandbTraceProcessor

    processor = WandbTraceProcessor(project="my-qitos-runs")
    add_trace_processor(processor)

Requires the ``wandb`` package (``pip install qitos[wandb]``).
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


def _require_wandb():  # noqa: ANN202
    """Import wandb lazily and raise a helpful error if missing."""
    try:
        import wandb  # noqa: F401 — used for type checks below

        return wandb
    except ImportError as exc:
        raise ImportError(
            "wandb is required for WandbTraceProcessor. "
            "Install it with: pip install qitos[wandb]"
        ) from exc


class WandbTraceProcessor(TraceProcessor):
    """TraceProcessor that streams QitOS run data to W&B.

    Parameters
    ----------
    project:
        W&B project name (passed to ``wandb.init``).
    name:
        W&B run name.  Defaults to the QitOS trace name.
    config:
        Dictionary passed as ``config`` to ``wandb.init``.
    tags:
        List of tags for the W&B run.
    entity:
        W&B entity (user or team).
    auto_finish:
        Whether to call ``wandb.finish()`` when the trace ends.
        Defaults to ``True``.
    """

    def __init__(
        self,
        *,
        project: str = "qitos",
        name: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        entity: Optional[str] = None,
        auto_finish: bool = True,
    ) -> None:
        self._project = project
        self._name = name
        self._config = config or {}
        self._tags = tags or []
        self._entity = entity
        self._auto_finish = auto_finish
        self._run: Optional[Any] = None
        self._step_counter: int = 0
        self._total_tokens: int = 0
        self._total_steps: int = 0
        self._tool_calls: int = 0
        self._critic_scores: List[float] = []
        self._stop_reason: Optional[str] = None

    # -- helpers --------------------------------------------------------------

    def _log_span_metrics(self, span: Span) -> None:
        """Extract and log metrics from a completed span."""
        if self._run is None:
            return

        data = span.data

        if isinstance(data, GenerationSpanData):
            if data.token_usage:
                prompt_tokens = data.token_usage.get("prompt_tokens", 0)
                completion_tokens = data.token_usage.get("completion_tokens", 0)
                self._total_tokens += prompt_tokens + completion_tokens
                self._run.log(
                    {
                        "generation/prompt_tokens": prompt_tokens,
                        "generation/completion_tokens": completion_tokens,
                        "generation/total_tokens": prompt_tokens + completion_tokens,
                        "generation/model": data.model or "unknown",
                    },
                    step=self._step_counter,
                )
                self._step_counter += 1

        elif isinstance(data, StepSpanData):
            self._total_steps += 1
            self._run.log(
                {"step/number": data.step_number},
                step=self._step_counter,
            )
            self._step_counter += 1

        elif isinstance(data, CriticSpanData):
            if data.score is not None:
                self._critic_scores.append(data.score)
                self._run.log(
                    {
                        "critic/score": data.score,
                        "critic/name": data.critic_name or "unknown",
                    },
                    step=self._step_counter,
                )
                self._step_counter += 1

        elif isinstance(data, ToolSpanData):
            self._tool_calls += 1
            self._run.log(
                {"tool/name": data.tool_name},
                step=self._step_counter,
            )
            self._step_counter += 1

        elif isinstance(data, ActSpanData):
            if data.action_name:
                self._tool_calls += 1
                self._run.log(
                    {"action/name": data.action_name},
                    step=self._step_counter,
                )
                self._step_counter += 1

    # -- TraceProcessor interface ---------------------------------------------

    def on_trace_start(self, trace: Trace) -> None:
        wandb = _require_wandb()
        run_name = self._name or trace.name
        try:
            self._run = wandb.init(
                project=self._project,
                name=run_name,
                config=self._config,
                tags=self._tags,
                entity=self._entity,
                reinit=True,
            )
            # Log trace metadata
            if trace.metadata:
                wandb.config.update(trace.metadata)
        except Exception:
            logger.exception("Failed to initialize W&B run")

    def on_trace_end(self, trace: Trace) -> None:
        if self._run is None:
            return

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
            summary["stop_reason"] = stop_reason

        self._run.summary.update(summary)

        if self._auto_finish:
            try:
                self._run.finish()
            except Exception:
                logger.exception("Failed to finish W&B run")
            self._run = None

    def on_span_start(self, span: Span) -> None:
        # No action needed on span start for W&B
        pass

    def on_span_end(self, span: Span) -> None:
        self._log_span_metrics(span)

    def shutdown(self) -> None:
        if self._run is not None and self._auto_finish:
            try:
                self._run.finish()
            except Exception:
                logger.exception("Failed to finish W&B run on shutdown")
            self._run = None

    def force_flush(self) -> None:
        if self._run is not None:
            try:
                self._run.log({}, step=self._step_counter)
            except Exception:
                logger.exception("Failed to flush W&B run")
