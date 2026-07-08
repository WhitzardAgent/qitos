"""Backend capability registry — probe optional libraries at import time.

Each optional dependency is probed once.  Packs declare required_backends
in their PackDescriptor; the registry checks availability before the pack
is used.  Missing backend → typed degradation, never crash.

All probes are try/except ImportError with version extraction.
No runtime downloads, no dynamic code execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackendStatus:
    """Status of a single optional backend library."""

    name: str                  # e.g. "pikepdf"
    available: bool
    version: str | None = None
    capabilities: frozenset[str] = frozenset()
    error: str | None = None   # ImportError message if unavailable


def _probe_pikepdf() -> BackendStatus:
    try:
        import pikepdf
        return BackendStatus(
            name="pikepdf",
            available=True,
            version=getattr(pikepdf, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate", "repair"}),
        )
    except ImportError as e:
        return BackendStatus(name="pikepdf", available=False, error=str(e))


def _probe_fonttools() -> BackendStatus:
    try:
        import fontTools
        return BackendStatus(
            name="fontTools",
            available=True,
            version=getattr(fontTools, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate", "repair"}),
        )
    except ImportError as e:
        return BackendStatus(name="fontTools", available=False, error=str(e))


def _probe_scapy() -> BackendStatus:
    try:
        import scapy
        return BackendStatus(
            name="scapy",
            available=True,
            version=getattr(scapy, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate"}),
        )
    except ImportError as e:
        return BackendStatus(name="scapy", available=False, error=str(e))


def _probe_tifffile() -> BackendStatus:
    try:
        import tifffile
        return BackendStatus(
            name="tifffile",
            available=True,
            version=getattr(tifffile, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate"}),
        )
    except ImportError as e:
        return BackendStatus(name="tifffile", available=False, error=str(e))


def _probe_lief() -> BackendStatus:
    try:
        import lief
        return BackendStatus(
            name="lief",
            available=True,
            version=getattr(lief, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate"}),
        )
    except ImportError as e:
        return BackendStatus(name="lief", available=False, error=str(e))


def _probe_construct() -> BackendStatus:
    try:
        import construct
        return BackendStatus(
            name="construct",
            available=True,
            version=getattr(construct, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate"}),
        )
    except ImportError as e:
        return BackendStatus(name="construct", available=False, error=str(e))


def _probe_kaitaistruct() -> BackendStatus:
    try:
        import kaitaistruct
        return BackendStatus(
            name="kaitaistruct",
            available=True,
            version=getattr(kaitaistruct, "__version__", None),
            capabilities=frozenset({"parse"}),
        )
    except ImportError as e:
        return BackendStatus(name="kaitaistruct", available=False, error=str(e))


def _probe_lxml() -> BackendStatus:
    try:
        import lxml
        return BackendStatus(
            name="lxml",
            available=True,
            version=getattr(lxml, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate"}),
        )
    except ImportError as e:
        return BackendStatus(name="lxml", available=False, error=str(e))


def _probe_cryptography() -> BackendStatus:
    try:
        import cryptography
        return BackendStatus(
            name="cryptography",
            available=True,
            version=getattr(cryptography, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate"}),
        )
    except ImportError as e:
        return BackendStatus(name="cryptography", available=False, error=str(e))


def _probe_pillow() -> BackendStatus:
    try:
        import PIL
        return BackendStatus(
            name="Pillow",
            available=True,
            version=getattr(PIL, "__version__", None),
            capabilities=frozenset({"parse", "build", "validate"}),
        )
    except ImportError as e:
        return BackendStatus(name="Pillow", available=False, error=str(e))


def _probe_pyelftools() -> BackendStatus:
    try:
        import elftools
        return BackendStatus(
            name="pyelftools",
            available=True,
            version=getattr(elftools, "__version__", None),
            capabilities=frozenset({"parse"}),
        )
    except ImportError as e:
        return BackendStatus(name="pyelftools", available=False, error=str(e))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_ALL_PROBES = {
    "pikepdf": _probe_pikepdf,
    "fontTools": _probe_fonttools,
    "scapy": _probe_scapy,
    "tifffile": _probe_tifffile,
    "lief": _probe_lief,
    "construct": _probe_construct,
    "kaitaistruct": _probe_kaitaistruct,
    "lxml": _probe_lxml,
    "cryptography": _probe_cryptography,
    "Pillow": _probe_pillow,
    "pyelftools": _probe_pyelftools,
}


class BackendRegistry:
    """Registry of optional backend library statuses.

    Probes are lazy — only run when first queried.  Results are cached.
    """

    def __init__(self) -> None:
        self._backends: dict[str, BackendStatus] = {}
        self._probed: set[str] = set()

    def probe(self, name: str) -> BackendStatus:
        """Get the status of a backend, probing if necessary."""
        if name not in self._probed:
            probe_fn = _ALL_PROBES.get(name)
            if probe_fn is not None:
                try:
                    self._backends[name] = probe_fn()
                except Exception as e:
                    self._backends[name] = BackendStatus(
                        name=name, available=False, error=f"probe failed: {e}",
                    )
            else:
                self._backends[name] = BackendStatus(
                    name=name, available=False, error=f"unknown backend: {name}",
                )
            self._probed.add(name)
        return self._backends.get(name, BackendStatus(
            name=name, available=False, error="not probed",
        ))

    def is_available(self, name: str) -> bool:
        """Check if a backend is available."""
        return self.probe(name).available

    def get_available_backends(self) -> dict[str, BackendStatus]:
        """Get status of all known backends."""
        for name in _ALL_PROBES:
            self.probe(name)
        return dict(self._backends)

    def check_pack_requirements(self, required_backends: tuple[str, ...]) -> tuple[str, ...]:
        """Return tuple of missing backend names for a pack's requirements."""
        missing: list[str] = []
        for name in required_backends:
            if not self.is_available(name):
                missing.append(name)
        return tuple(missing)

    def snapshot(self) -> dict[str, Any]:
        """Serializable snapshot of all backend statuses."""
        self.get_available_backends()
        return {
            name: {
                "available": bs.available,
                "version": bs.version,
                "error": bs.error,
            }
            for name, bs in self._backends.items()
        }


# Global singleton
_registry: BackendRegistry | None = None


def get_backend_registry() -> BackendRegistry:
    """Get the global BackendRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = BackendRegistry()
    return _registry
