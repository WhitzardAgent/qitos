from __future__ import annotations

from qitos.core.errors import ErrorCategory, classify_exception
from qitos.engine.recovery import RecoveryPolicy


def test_classify_exception_marks_stream_timeout_as_recoverable_model_error() -> None:
    info = classify_exception(RuntimeError("stream timeout"), "DECIDE", 7)

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True
    assert info.phase == "DECIDE"
    assert info.step_id == 7


def test_classify_exception_marks_timed_out_message_as_recoverable_model_error() -> None:
    info = classify_exception(RuntimeError("request timed out while streaming"), "PROPOSE", 3)

    assert info.category == ErrorCategory.MODEL
    assert info.recoverable is True


def test_recovery_policy_continues_on_stream_timeout() -> None:
    decision = RecoveryPolicy().handle(state=None, phase="DECIDE", step_id=11, exc=RuntimeError("stream timeout"))

    assert decision.handled is True
    assert decision.continue_run is True
    assert decision.stop_reason is None
