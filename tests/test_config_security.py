"""Tests for config security — API key masking and tracing redaction."""
from __future__ import annotations

import json

from qitos.config.loader import ModelConfig
from qitos.benchmark.common import write_benchmark_results
from qitos.core.spec import BenchmarkRunResult
from qitos.render import ClaudeStyleHook
from qitos.trace.events import TraceEvent
from qitos.trace.writer import TraceWriter
from qitos.tracing.config import _REDACTED_FIELDS, _REDACTED_MARKER, _redact_dict


def test_model_config_to_dict_masks_api_key():
    """ModelConfig.to_dict() masks non-empty api_key."""
    cfg = ModelConfig(api_key="sk-12345-secret")
    d = cfg.to_dict()
    assert d["api_key"] == "***REDACTED***"


def test_model_config_to_dict_empty_api_key():
    """ModelConfig.to_dict() returns empty string for empty api_key."""
    cfg = ModelConfig(api_key="")
    d = cfg.to_dict()
    assert d["api_key"] == ""


def test_model_config_preserves_other_fields():
    """Other fields are not affected by api_key masking."""
    cfg = ModelConfig(provider="anthropic", model="claude-3", api_key="sk-test")
    d = cfg.to_dict()
    assert d["provider"] == "anthropic"
    assert d["model"] == "claude-3"
    assert d["api_key"] == "***REDACTED***"


def test_redacted_fields_includes_sensitive_names():
    """_REDACTED_FIELDS includes common sensitive field names."""
    expected = {"api_key", "authorization", "token", "secret", "password",
                "access_token", "refresh_token", "private_key", "credentials"}
    assert expected.issubset(_REDACTED_FIELDS)


def test_redact_dict_masks_sensitive_fields():
    """_redact_dict replaces sensitive field values with the redaction marker."""
    data = {
        "tool_args": {"command": "ls"},
        "api_key": "sk-12345",
        "authorization": "Bearer abc",
        "safe_field": "visible",
    }
    result = _redact_dict(data)
    assert result["tool_args"] == _REDACTED_MARKER
    assert result["api_key"] == _REDACTED_MARKER
    assert result["authorization"] == _REDACTED_MARKER
    assert result["safe_field"] == "visible"


def test_redact_dict_handles_nested_dicts():
    """_redact_dict recursively redacts nested dicts."""
    data = {
        "outer": {
            "password": "secret123",
            "name": "test",
        }
    }
    result = _redact_dict(data)
    assert result["outer"]["password"] == _REDACTED_MARKER
    assert result["outer"]["name"] == "test"


def test_redact_dict_handles_lists_of_dicts():
    """_redact_dict recursively redacts dicts inside lists."""
    data = {
        "items": [
            {"token": "abc", "value": 1},
            {"token": "def", "value": 2},
        ]
    }
    result = _redact_dict(data)
    assert result["items"][0]["token"] == _REDACTED_MARKER
    assert result["items"][1]["token"] == _REDACTED_MARKER
    assert result["items"][0]["value"] == 1


def test_trace_writer_redacts_sensitive_manifest_and_events(tmp_path):
    """TraceWriter should not persist raw secrets from run metadata or events."""
    writer = TraceWriter(
        output_dir=str(tmp_path),
        run_id="redaction-demo",
        metadata={
            "run_spec": {
                "environment": {
                    "api_key": "sk-raw-secret",
                    "nested": {"token": "raw-token"},
                }
            }
        },
        strict_validate=False,
    )
    writer.write_event(
        TraceEvent(
            run_id="redaction-demo",
            step_id=0,
            phase="setup",
            ok=True,
            payload={"authorization": "Bearer raw-secret", "safe": "visible"},
            error=None,
            ts="2026-06-03T00:00:00+00:00",
        )
    )
    writer.finalize(status="failed", summary={})

    manifest_text = (tmp_path / "redaction-demo" / "manifest.json").read_text()
    event_text = (tmp_path / "redaction-demo" / "events.jsonl").read_text()
    manifest = json.loads(manifest_text)
    event = json.loads(event_text)

    assert "sk-raw-secret" not in manifest_text
    assert "raw-token" not in manifest_text
    assert "Bearer raw-secret" not in event_text
    assert (
        manifest["run_spec"]["environment"]["api_key"]
        == _REDACTED_MARKER
    )
    assert (
        manifest["run_spec"]["environment"]["nested"]["token"]
        == _REDACTED_MARKER
    )
    assert event["payload"]["authorization"] == _REDACTED_MARKER
    assert event["payload"]["safe"] == "visible"


def test_benchmark_result_writer_redacts_sensitive_metadata(tmp_path):
    """Benchmark jsonl output should not persist raw secrets in row metadata."""
    row = BenchmarkRunResult(
        task_id="task-1",
        benchmark="demo",
        split="test",
        prediction=None,
        success=False,
        stop_reason="failed",
        steps=1,
        latency_seconds=0.1,
        token_usage=0,
        cost=0.0,
        trace_run_dir=None,
        run_spec_ref=None,
        metadata={
            "execution": {
                "run_spec": {
                    "environment": {
                        "api_key": "sk-row-secret",
                        "nested": {"token": "row-token"},
                    }
                }
            }
        },
    )

    path = write_benchmark_results(tmp_path / "rows.jsonl", [row])
    text = path.read_text()
    payload = json.loads(text)

    assert "sk-row-secret" not in text
    assert "row-token" not in text
    assert (
        payload["metadata"]["execution"]["run_spec"]["environment"]["api_key"]
        == _REDACTED_MARKER
    )
    assert (
        payload["metadata"]["execution"]["run_spec"]["environment"]["nested"]["token"]
        == _REDACTED_MARKER
    )


def test_render_jsonl_redacts_sensitive_payload(tmp_path):
    """Terminal render event jsonl should not persist raw run metadata secrets."""
    path = tmp_path / "render_events.jsonl"
    hook = ClaudeStyleHook(output_jsonl=str(path))

    hook._emit(
        "engine_event",
        "init",
        step_id=0,
        payload={
            "run_meta": {
                "run_spec": {
                    "environment": {
                        "api_key": "sk-render-secret",
                        "nested": {"token": "render-token"},
                    }
                }
            }
        },
    )

    text = path.read_text()
    payload = json.loads(text)

    assert "sk-render-secret" not in text
    assert "render-token" not in text
    env = payload["payload"]["run_meta"]["run_spec"]["environment"]
    assert env["api_key"] == _REDACTED_MARKER
    assert env["nested"]["token"] == _REDACTED_MARKER
