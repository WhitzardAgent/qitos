"""StreamTransformer protocol — base class for all stream transformers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..events import EngineEvent


@dataclass
class TransformerOutput:
    """Output from a StreamTransformer.

    Wraps the transformed value with metadata about which transformer
    produced it and what type it is.
    """

    type: str = "custom"
    value: Any = None
    step_id: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class StreamTransformer(ABC):
    """Base class for stream transformers.

    Subclasses override ``transform()`` to convert EngineEvents into
    application-specific output. The AsyncEngine calls each transformer
    in order for every event.

    A transformer can:
    - Return a TransformerOutput to emit a value
    - Return None to suppress the event
    - Accumulate state internally across multiple events
    """

    # Unique identifier for this transformer type
    output_type: str = "custom"

    @abstractmethod
    def transform(self, event: EngineEvent) -> Optional[TransformerOutput]:
        """Transform an EngineEvent into output.

        Parameters
        ----------
        event : EngineEvent
            The event from the engine.

        Returns
        -------
        TransformerOutput or None
            The transformed output, or None to suppress.
        """

    async def atransform(self, event: EngineEvent) -> Optional[TransformerOutput]:
        """Async version of transform().

        By default delegates to the synchronous transform(). Subclasses
        that need native async I/O (e.g., WebSocket streaming, async DB
        writes) should override this method.

        Parameters
        ----------
        event : EngineEvent
            The event from the engine.

        Returns
        -------
        TransformerOutput or None
            The transformed output, or None to suppress.
        """
        return self.transform(event)

    def on_run_start(self) -> None:
        """Called when the engine run starts."""

    def on_run_end(self) -> None:
        """Called when the engine run ends."""


class TransformerChain:
    """Runs a list of StreamTransformers in order."""

    def __init__(self, transformers: Optional[List[StreamTransformer]] = None):
        self._transformers = list(transformers or [])

    @property
    def transformers(self) -> List[StreamTransformer]:
        return list(self._transformers)

    def add(self, transformer: StreamTransformer) -> None:
        self._transformers.append(transformer)

    def process(self, event: EngineEvent) -> List[TransformerOutput]:
        """Run all transformers on an event and collect outputs."""
        outputs: List[TransformerOutput] = []
        for t in self._transformers:
            result = t.transform(event)
            if result is not None:
                outputs.append(result)
        return outputs

    async def aprocess(self, event: EngineEvent) -> List[TransformerOutput]:
        """Run all transformers on an event asynchronously and collect outputs."""
        outputs: List[TransformerOutput] = []
        for t in self._transformers:
            result = await t.atransform(event)
            if result is not None:
                outputs.append(result)
        return outputs

    def on_run_start(self) -> None:
        for t in self._transformers:
            t.on_run_start()

    def on_run_end(self) -> None:
        for t in self._transformers:
            t.on_run_end()


__all__ = ["StreamTransformer", "TransformerOutput", "TransformerChain"]
