"""Tests for MlflowTraceProcessor with mocked mlflow API."""

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
    return Span(
        trace_id="trace-1",
        span_id=span_id,
        data=data,
        parent_span_id=None,
        processor=None,
    )


def _mock_mlflow_module() -> types.ModuleType:
    """Build a fake ``mlflow`` module with mock functions."""
    mock_mlflow = types.ModuleType("mlflow")

    mock_mlflow.set_tracking_uri = MagicMock()
    mock_mlflow.set_experiment = MagicMock()
    mock_mlflow.start_run = MagicMock(return_value=MagicMock())
    mock_mlflow.end_run = MagicMock()
    mock_mlflow.log_metrics = MagicMock()
    mock_mlflow.log_param = MagicMock()
    mock_mlflow.set_tag = MagicMock()

    return mock_mlflow


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMlflowTraceProcessorImport:
    def test_import_error_without_mlflow(self) -> None:
        from qitos.tracing.mlflow_processor import _require_mlflow

        with patch.dict("sys.modules", {"mlflow": None}):
            with pytest.raises(ImportError, match="pip install qitos\\[mlflow\\]"):
                _require_mlflow()


class TestMlflowTraceProcessorTraceLifecycle:
    def test_on_trace_start_calls_mlflow(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        mock_mlflow = _mock_mlflow_module()
        proc = MlflowTraceProcessor(
            experiment_name="test-exp",
            run_name="my-run",
            tracking_uri="http://localhost:5000",
            tags={"env": "test"},
        )

        with patch("qitos.tracing.mlflow_processor._require_mlflow", return_value=mock_mlflow):
            trace = Trace(trace_id="t1", name="run-1", metadata={"key": "val"})
            proc.on_trace_start(trace)

            mock_mlflow.set_tracking_uri.assert_called_once_with("http://localhost:5000")
            mock_mlflow.set_experiment.assert_called_once_with("test-exp")
            mock_mlflow.start_run.assert_called_once_with(
                run_name="my-run",
                tags={"env": "test"},
            )
            mock_mlflow.log_param.assert_called_once_with("key", "val")

    def test_on_trace_start_uses_trace_name_as_default(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        mock_mlflow = _mock_mlflow_module()
        proc = MlflowTraceProcessor(experiment_name="test-exp")

        with patch("qitos.tracing.mlflow_processor._require_mlflow", return_value=mock_mlflow):
            trace = Trace(trace_id="t1", name="auto-name")
            proc.on_trace_start(trace)

            mock_mlflow.start_run.assert_called_once_with(
                run_name="auto-name",
                tags={},
            )

    def test_on_trace_end_logs_summary_and_ends_run(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        mock_mlflow = _mock_mlflow_module()
        proc = MlflowTraceProcessor(experiment_name="test-exp")

        with patch("qitos.tracing.mlflow_processor._require_mlflow", return_value=mock_mlflow):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)

            # Simulate some data accumulation
            proc._total_tokens = 500
            proc._total_steps = 5
            proc._tool_calls = 3
            proc._critic_scores = [0.8, 0.9]

            proc.on_trace_end(trace)

            mock_mlflow.log_metrics.assert_called_once()
            metrics = mock_mlflow.log_metrics.call_args[0][0]
            assert metrics["total_tokens"] == 500
            assert metrics["total_steps"] == 5
            assert metrics["total_tool_calls"] == 3
            assert metrics["critic/avg_score"] == pytest.approx(0.85)
            mock_mlflow.end_run.assert_called_once()

    def test_on_trace_end_no_run(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        proc = MlflowTraceProcessor(experiment_name="test-exp")
        trace = Trace(trace_id="t1", name="r1")
        # Should not raise
        proc.on_trace_end(trace)

    def test_auto_end_run_false(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        mock_mlflow = _mock_mlflow_module()
        proc = MlflowTraceProcessor(experiment_name="test-exp", auto_end_run=False)

        with patch("qitos.tracing.mlflow_processor._require_mlflow", return_value=mock_mlflow):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)
            proc.on_trace_end(trace)

            mock_mlflow.end_run.assert_not_called()
            assert proc._active_run is not None

    def test_stop_reason_tagged(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        mock_mlflow = _mock_mlflow_module()
        proc = MlflowTraceProcessor(experiment_name="test-exp")

        with patch("qitos.tracing.mlflow_processor._require_mlflow", return_value=mock_mlflow):
            trace = Trace(trace_id="t1", name="r1", metadata={"stop_reason": "budget_steps"})
            proc.on_trace_start(trace)
            proc.on_trace_end(trace)

            mock_mlflow.set_tag.assert_any_call("stop_reason", "budget_steps")


class TestMlflowTraceProcessorSpanMetrics:
    def _make_processor(self) -> tuple:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        mock_mlflow = _mock_mlflow_module()
        proc = MlflowTraceProcessor(experiment_name="test-exp")

        # Patch _require_mlflow for the entire lifecycle
        self._mlflow_patcher = patch(
            "qitos.tracing.mlflow_processor._require_mlflow", return_value=mock_mlflow
        )
        self._mlflow_patcher.start()

        trace = Trace(trace_id="t1", name="r1")
        proc.on_trace_start(trace)

        return proc, mock_mlflow

    def teardown_method(self) -> None:
        if hasattr(self, "_mlflow_patcher"):
            self._mlflow_patcher.stop()

    def test_generation_span_logs_tokens(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(
            GenerationSpanData(
                model="gpt-4o",
                token_usage={"prompt_tokens": 100, "completion_tokens": 50},
            )
        )
        proc.on_span_end(span)

        mock_mlflow.log_metrics.assert_called_once()
        metrics = mock_mlflow.log_metrics.call_args[0][0]
        assert metrics["generation/prompt_tokens"] == 100
        assert metrics["generation/completion_tokens"] == 50
        assert metrics["generation/total_tokens"] == 150
        assert proc._total_tokens == 150

    def test_step_span_logs_step_number(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(StepSpanData(step_number=3))
        proc.on_span_end(span)

        metrics = mock_mlflow.log_metrics.call_args[0][0]
        assert metrics["step/number"] == 3
        assert proc._total_steps == 1

    def test_critic_span_logs_score(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(CriticSpanData(critic_name="verify", score=0.75))
        proc.on_span_end(span)

        metrics = mock_mlflow.log_metrics.call_args[0][0]
        assert metrics["critic/score"] == 0.75
        assert proc._critic_scores == [0.75]

    def test_tool_span_logs_tag(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(ToolSpanData(tool_name="search"))
        proc.on_span_end(span)

        mock_mlflow.set_tag.assert_called_with("tool.search.called", "true")
        assert proc._tool_calls == 1

    def test_act_span_logs_tag(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(ActSpanData(action_name="bash"))
        proc.on_span_end(span)

        mock_mlflow.set_tag.assert_called_with("action.bash.called", "true")
        assert proc._tool_calls == 1

    def test_generation_without_token_usage(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(GenerationSpanData(model="gpt-4o"))
        proc.on_span_end(span)

        mock_mlflow.log_metrics.assert_not_called()
        assert proc._total_tokens == 0

    def test_act_span_without_action_name(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(ActSpanData())
        proc.on_span_end(span)

        mock_mlflow.set_tag.assert_not_called()
        assert proc._tool_calls == 0

    def test_unknown_span_data_ignored(self) -> None:
        proc, mock_mlflow = self._make_processor()

        span = _make_span(AgentSpanData(name="agent-1"))
        proc.on_span_end(span)

        mock_mlflow.log_metrics.assert_not_called()


class TestMlflowTraceProcessorShutdown:
    def test_shutdown_ends_run(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        mock_mlflow = _mock_mlflow_module()
        proc = MlflowTraceProcessor(experiment_name="test-exp", auto_end_run=True)

        with patch("qitos.tracing.mlflow_processor._require_mlflow", return_value=mock_mlflow):
            trace = Trace(trace_id="t1", name="r1")
            proc.on_trace_start(trace)
            proc.shutdown()
            mock_mlflow.end_run.assert_called_once()
            assert proc._active_run is None

    def test_shutdown_no_run(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        proc = MlflowTraceProcessor(experiment_name="test-exp")
        # Should not raise (no active run means _require_mlflow is not called)
        proc.shutdown()

    def test_force_flush_no_op(self) -> None:
        from qitos.tracing.mlflow_processor import MlflowTraceProcessor

        proc = MlflowTraceProcessor(experiment_name="test-exp")
        # Should not raise
        proc.force_flush()
