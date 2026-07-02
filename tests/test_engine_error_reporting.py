from __future__ import annotations

from qitos.engine.engine import Engine
from qitos.engine.states import RuntimePhase


def test_report_runtime_exception_writes_stderr_file_and_event(
    tmp_path, monkeypatch, capsys
) -> None:
    error_log = tmp_path / "qitos-errors.log"
    monkeypatch.setenv("QITOS_ERROR_LOG", str(error_log))

    engine = object.__new__(Engine)
    engine._last_runtime_error = None
    emitted = []
    engine._emit = lambda *args, **kwargs: emitted.append((args, kwargs))

    try:
        raise NameError("phase_local_steps is not defined")
    except NameError as exc:
        engine._report_runtime_exception(RuntimePhase.DECIDE, 1, exc)

    stderr = capsys.readouterr().err
    assert "[QitOS] runtime exception phase=DECIDE step=1" in stderr
    assert "NameError: phase_local_steps is not defined" in stderr

    persisted = error_log.read_text()
    assert "QitOS RUNTIME EXCEPTION phase=DECIDE step=1" in persisted
    assert "NameError: phase_local_steps is not defined" in persisted

    assert engine._last_runtime_error["error_type"] == "NameError"
    assert engine._last_runtime_error["phase"] == "DECIDE"
    assert emitted[0][0][:2] == (1, RuntimePhase.RECOVER)
    assert emitted[0][1]["ok"] is False
    assert emitted[0][1]["payload"]["traceback"].startswith("Traceback")


def test_report_runtime_exception_uses_trace_directory_fallback(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("QITOS_ERROR_LOG", raising=False)
    monkeypatch.delenv("QITOS_TRACE_DIR", raising=False)
    monkeypatch.setenv("CYBERGYM_TASK_TRACE_DIR", str(tmp_path))

    engine = object.__new__(Engine)
    engine._last_runtime_error = None
    engine._emit = lambda *args, **kwargs: None

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        engine._report_runtime_exception(RuntimePhase.ACT, 3, exc)

    assert "RuntimeError: boom" in (tmp_path / "step_error.log").read_text()
