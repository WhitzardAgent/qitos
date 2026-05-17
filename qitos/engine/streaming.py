"""Stream handler protocol and adapter for Engine streaming.

Provides a structured lifecycle for streaming output instead of
bare ``Callable[[str], None]`` callbacks.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Protocol, runtime_checkable


@runtime_checkable
class StreamHandler(Protocol):
    """Protocol for structured streaming output.

    Engines and REPLs can implement this protocol to manage streaming
    lifecycle events (spinner start/stop, buffer management, etc.)
    instead of using bare callables.
    """

    def on_start(self) -> None:
        """Called when streaming begins (first delta is about to arrive)."""
        ...

    def on_delta(self, text: str) -> None:
        """Called for each text delta during streaming."""
        ...

    def on_end(self) -> None:
        """Called when streaming ends (last delta has been processed)."""
        ...


class StreamHandlerAdapter:
    """Adapts a bare ``Callable[[str], None]`` to the StreamHandler protocol.

    This provides backward compatibility so that existing code passing
    a simple callback still works.
    """

    def __init__(self, callback: Callable[[str], None]) -> None:
        self._callback = callback

    def on_start(self) -> None:
        """No-op for bare callback."""
        pass

    def on_delta(self, text: str) -> None:
        self._callback(text)

    def on_end(self) -> None:
        """No-op for bare callback."""
        pass


def to_stream_handler(callback: Any) -> Optional[StreamHandler]:
    """Normalize a callback to a StreamHandler.

    If the callback already implements StreamHandler, return it as-is.
    If it's a bare callable, wrap it in StreamHandlerAdapter.
    If None, return None.
    """
    if callback is None:
        return None
    if isinstance(callback, StreamHandler):
        return callback
    if callable(callback):
        return StreamHandlerAdapter(callback)
    return None
