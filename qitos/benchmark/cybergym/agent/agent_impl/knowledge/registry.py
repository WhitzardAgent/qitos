"""Knowledge pack registry — select, register, and query packs.

The registry is a singleton that holds all registered KnowledgePack instances.
Packs are registered at module import time via register().  At runtime,
select_packs() uses EvidenceView to find the best-matching packs.

Detection is evidence-based, not keyword-based:
- project_name match → candidate (weak, score ≤ 0.5)
- corpus magic match → confirmed (strong, score ≥ 0.7)
- harness API/protocol match → confirmed (strong, score ≥ 0.8)
- source-backed format hint → confirmed (authoritative, score ≥ 0.9)
"""

from __future__ import annotations

import logging
from typing import Any

from .backend_registry import get_backend_registry
from .evidence import EvidenceView
from .models import DetectionResult, PackDescriptor

logger = logging.getLogger(__name__)


class KnowledgeRegistry:
    """Registry of executable knowledge packs."""

    def __init__(self) -> None:
        self._packs: dict[str, Any] = {}  # pack_id -> KnowledgePack instance

    def register(self, pack: Any) -> None:
        """Register a knowledge pack.

        Skips if required backends are not available — the pack will not
        appear in select_packs() results.
        """
        descriptor: PackDescriptor = pack.descriptor
        backend = get_backend_registry()
        missing = backend.check_pack_requirements(descriptor.required_backends)
        if missing:
            logger.info(
                "Pack %s skipped: missing backends %s",
                descriptor.pack_id, ", ".join(missing),
            )
            return
        self._packs[descriptor.pack_id] = pack
        logger.debug("Registered pack: %s", descriptor.pack_id)

    def register_unavailable(self, pack: Any) -> None:
        """Register a pack even if backends are missing.

        The pack will appear in list_packs() but detect() will return
        insufficient with missing_evidence listing the missing backends.
        """
        descriptor: PackDescriptor = pack.descriptor
        self._packs[descriptor.pack_id] = pack
        logger.debug("Registered pack (unavailable): %s", descriptor.pack_id)

    def select_packs(
        self,
        evidence: EvidenceView,
        limit: int = 3,
    ) -> list[tuple[Any, DetectionResult]]:
        """Select the best-matching packs for the current task.

        Returns up to `limit` (pack, detection_result) pairs sorted by
        detection score descending.
        """
        results: list[tuple[Any, DetectionResult]] = []
        for pack in self._packs.values():
            try:
                result = pack.detect(evidence)
            except Exception as e:
                logger.warning("Pack %s detect() failed: %s", pack.descriptor.pack_id, e)
                continue
            if result.decision in ("confirmed", "candidate"):
                results.append((pack, result))

        # Sort by score descending, then by decision strength
        _decision_order = {"confirmed": 0, "candidate": 1}
        results.sort(key=lambda x: (_decision_order.get(x[1].decision, 99), -x[1].score))
        return results[:limit]

    def get_pack(self, pack_id: str) -> Any | None:
        """Get a pack by ID."""
        return self._packs.get(pack_id)

    def list_packs(self) -> list[PackDescriptor]:
        """List all registered pack descriptors."""
        return [pack.descriptor for pack in self._packs.values()]

    def is_empty(self) -> bool:
        """Check if no packs are registered."""
        return len(self._packs) == 0


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_registry: KnowledgeRegistry | None = None


def get_knowledge_registry() -> KnowledgeRegistry:
    """Get or create the global KnowledgeRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = KnowledgeRegistry()
        _auto_register_packs()
    return _registry


def _auto_register_packs() -> None:
    """Attempt to register all available packs.

    Packs whose required backends are missing will be skipped silently.
    New packs are added here as they are implemented.
    """
    # Phase 1 packs — registered when their backends are available
    _try_register("agent_impl.knowledge.packs.pdf", "PdfKnowledgePack")
    _try_register("agent_impl.knowledge.packs.sfnt", "SfntKnowledgePack")
    _try_register("agent_impl.knowledge.packs.packet", "PacketKnowledgePack")

    # Phase 4 packs — registered when implemented
    _try_register("agent_impl.knowledge.packs.elf", "ElfKnowledgePack")
    _try_register("agent_impl.knowledge.packs.image", "ImageKnowledgePack")
    _try_register("agent_impl.knowledge.packs.codec", "CodecKnowledgePack")
    _try_register("agent_impl.knowledge.packs.structured_text", "StructuredTextKnowledgePack")
    _try_register("agent_impl.knowledge.packs.crypto", "CryptoKnowledgePack")
    _try_register("agent_impl.knowledge.packs.archive", "ArchiveKnowledgePack")
    _try_register("agent_impl.knowledge.packs.cad", "CadKnowledgePack")
    _try_register("agent_impl.knowledge.packs.audio", "AudioKnowledgePack")


def _try_register(module_path: str, class_name: str) -> None:
    """Try to import and register a pack; silently skip on failure.

    If the pack can be imported but its backends are missing, registers
    it as unavailable so that detect() can still produce candidate decisions
    based on keywords/project name.
    """
    try:
        import importlib
        module = importlib.import_module(module_path)
        pack_class = getattr(module, class_name)
        pack = pack_class()
        registry = get_knowledge_registry_raw()
        # Try normal registration first (checks backends)
        registry.register(pack)
    except (ImportError, AttributeError, Exception) as e:
        # Pack not implemented yet — this is expected
        logger.debug("Pack %s.%s not available: %s", module_path, class_name, e)
        return

    # If register() skipped due to missing backends, try register_unavailable
    if registry.get_pack(pack.descriptor.pack_id) is None:
        try:
            registry.register_unavailable(pack)
        except Exception:
            pass


def get_knowledge_registry_raw() -> KnowledgeRegistry:
    """Get the registry without triggering auto-registration.
    Used internally during auto-registration to avoid recursion.
    """
    global _registry
    if _registry is None:
        _registry = KnowledgeRegistry()
    return _registry
