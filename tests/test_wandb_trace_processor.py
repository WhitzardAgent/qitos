"""Tests for WandbTraceProcessor with mocked wandb API."""

from __future__ import annotations

import types
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

from qitos.tracing.models import (
    ActSpanData,
    AgentSpanData,
    CriticSpanData,
    GenerationSpanData,
    Span,
    SpanType,
    StepSpanData,
    ToolSpanData,
    Trace,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_span(data, span_id: str = "span-1") -> Span:
    """Create a minimal Span with the given SpanData."""
    return Span(
        trace_id="trace-1",
        span_id=span_id,
        data=data,
        parent_span_id=None,
        processor=None,
    )


def _mock_wandb_module() -> types.ModuleType:
    """Build a fake ``wandb`` module with ``init`` returning a mock run."""
    mock_wandb = types.ModuleType("wandb")

    mock_run = MagicMock()
    mock_run.log = MagicMock()
    mock_run.finish = MagicMock()
    # summary is a MagicMock so .update() is tracked
    mock_run.summary = MagicMock()
    mock_run.summary._data: Dict[str, Any] = {}
    mock_run.summary.update = MagicMock(
        side_effect=lambda d: mock_run.summary._data.update(d)
    )

    mock_wandb.init = MagicMock(return_value=mock_run)
    mock_wandb.config = MagicMock()
    mock_wandb.config.update = MagicMock()
    return mock_wandb


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWandbTraceProcessorImport:
    """Verify import-time behavior when wandb is missing."""

    def test_import_error_without_wandb(self) -> None:
        from qitos.tracing.wandb_processor import _require_wandb

        with patch.dict("sys.modules", {"wandb": None}):
            with pytest.raises(ImportError, match="pip install qitos\\[wandb\\]"):
                _require_wandb()


class TestWandbTraceProcessorTraceLifecycle:
    """Test trace start/end lifecycle."""

    def test_on_trace_start_calls_wandb_init(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        proc = WandbTraceProcessor(project="test-proj", name="my-run", tags=["smoke"])

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="run-1", metadata={"key": "val"})
            proc.on_trace_start(trace)

            mock_wandb.init.assert_called_once_with(
                project="test-proj",
                name="my-run",
                config={},
                tags=["smoke"],
                entity=None,
                reinit=True,
            )
            mock_wandb.config.update.assert_called_once_with({"key": "val"})

    def test_on_trace_start_uses_trace_name_as_default(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        proc = WandbTraceProcessor(project="test-proj")

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="auto-name")
            proc.on_trace_start(trace)

            mock_wandb.init.assert_called_once_with(
                project="test-proj",
                name="auto-name",
                config={},
                tags=[],
                entity=None,
                reinit=True,
            )

    def test_on_trace_end_logs_summary_and_finishes(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        mock_run = mock_wandb.init.return_value
        proc = WandbTraceProcessor(project="test-proj")

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)

            # Simulate some data accumulation
            proc._total_tokens = 500
            proc._total_steps = 5
            proc._tool_calls = 3
            proc._critic_scores = [0.8, 0.9]

            proc.on_trace_end(trace)

            mock_run.summary.update.assert_called_once()
            summary = mock_run.summary.update.call_args[0][0]
            assert summary["total_tokens"] == 500
            assert summary["total_steps"] == 5
            assert summary["total_tool_calls"] == 3
            assert summary["critic/avg_score"] == pytest.approx(0.85)
            mock_run.finish.assert_called_once()

    def test_on_trace_end_no_run(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        proc = WandbTraceProcessor(project="test-proj")
        trace = Trace(trace_id="t1", name="r1")
        # Should not raise
        proc.on_trace_end(trace)

    def test_auto_finish_false(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        mock_run = mock_wandb.init.return_value
        proc = WandbTraceProcessor(project="test-proj", auto_finish=False)

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)
            proc.on_trace_end(trace)

            # Should NOT call finish because auto_finish=False
            mock_run.finish.assert_not_called()
            assert proc._run is not None


class TestWandbTraceProcessorSpanMetrics:
    """Test per-span metric extraction and logging."""

    def _make_processor(self) -> tuple:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        mock_run = mock_wandb.init.return_value
        proc = WandbTraceProcessor(project="test-proj")

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)

        return proc, mock_run

    def test_generation_span_logs_tokens(self) -> None:
        proc, mock_run = self._make_processor()

        span = _make_span(
            GenerationSpanData(
                model="gpt-4o",
                token_usage={"prompt_tokens": 100, "completion_tokens": 50},
            )
        )
        proc.on_span_end(span)

        mock_run.log.assert_called_once()
        call_kwargs = mock_run.log.call_args
        metrics = call_kwargs[0][0]
        assert metrics["generation/prompt_tokens"] == 100
        assert metrics["generation/completion_tokens"] == 50
        assert metrics["generation/total_tokens"] == 150
        assert metrics["generation/model"] == "gpt-4o"
        assert proc._total_tokens == 150

    def test_step_span_logs_step_number(self) -> None:
        proc, mock_run = self._make_processor()

        span = _make_span(StepSpanData(step_number=3))
        proc.on_span_end(span)

        metrics = mock_run.log.call_args[0][0]
        assert metrics["step/number"] == 3
        assert proc._total_steps == 1

    def test_critic_span_logs_score(self) -> None:
        proc, mock_run = self._make_processor()

        span = _make_span(CriticSpanData(critic_name="verify", score=0.75))
        proc.on_span_end(span)

        metrics = mock_run.log.call_args[0][0]
        assert metrics["critic/score"] == 0.75
        assert metrics["critic/name"] == "verify"
        assert proc._critic_scores == [0.75]

    def test_tool_span_logs_tool_name(self) -> None:
        proc, mock_run = self._make_processor()

        span = _make_span(ToolSpanData(tool_name="search"))
        proc.on_span_end(span)

        metrics = mock_run.log.call_args[0][0]
        assert metrics["tool/name"] == "search"
        assert proc._tool_calls == 1

    def test_act_span_logs_action_name(self) -> None:
        proc, mock_run = self._make_processor()

        span = _make_span(ActSpanData(action_name="bash"))
        proc.on_span_end(span)

        metrics = mock_run.log.call_args[0][0]
        assert metrics["action/name"] == "bash"
        assert proc._tool_calls == 1

    def test_generation_without_token_usage(self) -> None:
        proc, mock_run = self._make_processor()

        span = _make_span(GenerationSpanData(model="gpt-4o"))
        proc.on_span_end(span)

        # Should not log when token_usage is None
        mock_run.log.assert_not_called()
        assert proc._total_tokens == 0

    def test_act_span_without_action_name(self) -> None:
        proc, mock_run = self._make_processor()

        span = _make_span(ActSpanData())
        proc.on_span_end(span)

        # Should not log when action_name is None
        mock_run.log.assert_not_called()
        assert proc._tool_calls == 0

    def test_unknown_span_data_ignored(self) -> None:
        proc, mock_run = self._make_processor()

        # AgentSpanData is not one of the tracked types
        span = _make_span(AgentSpanData(name="agent-1"))
        proc.on_span_end(span)

        mock_run.log.assert_not_called()


class TestWandbTraceProcessorShutdown:
    """Test shutdown and force_flush."""

    def test_shutdown_finishes_run(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        mock_run = mock_wandb.init.return_value
        proc = WandbTraceProcessor(project="test-proj", auto_finish=True)

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)

        proc.shutdown()
        mock_run.finish.assert_called_once()
        assert proc._run is None

    def test_shutdown_no_run(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        proc = WandbTraceProcessor(project="test-proj")
        # Should not raise
        proc.shutdown()

    def test_force_flush(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        mock_run = mock_wandb.init.return_value
        proc = WandbTraceProcessor(project="test-proj")

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)

        proc.force_flush()
        # Should call log with empty dict to flush
        mock_run.log.assert_called_once()


class TestWandbTraceProcessorStopReason:
    """Test stop_reason in trace metadata."""

    def test_stop_reason_in_summary(self) -> None:
        from qitos.tracing.wandb_processor import WandbTraceProcessor

        mock_wandb = _mock_wandb_module()
        mock_run = mock_wandb.init.return_value
        proc = WandbTraceProcessor(project="test-proj")

        with patch("qitos.tracing.wandb_processor._require_wandb", return_value=mock_wandb):
            trace = Trace(trace_id="t1", name="r1", metadata={"stop_reason": "budget_steps"})
            proc.on_trace_start(trace)
            proc.on_trace_end(trace)

        summary = mock_run.summary.update.call_args[0][0]
        assert summary["stop_reason"] == "budget_steps"
