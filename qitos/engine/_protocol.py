"""Internal Engine reference type for runtime helper classes.

Runtime helpers are phase-specific slices of ``Engine.run()``. They access
private Engine state that is intentionally not a public protocol. Treating this
reference as ``Any`` keeps the type boundary honest while the public contracts
remain in ``qitos.core`` and the stable engine exports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    _EngineProtocol = Any
else:

    @runtime_checkable
    class _EngineProtocol(Protocol):
        """Runtime marker for Engine-like helper owners."""

        pass

__all__ = ["_EngineProtocol"]
