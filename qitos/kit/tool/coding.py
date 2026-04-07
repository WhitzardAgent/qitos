"""Compatibility shim for the canonical coding bundle.

The real implementation now lives under `qitos.kit.tool.internal.coding_impl`
so the public `qitos.kit.tool` top level no longer acts as the
implementation center.
"""

from qitos.kit.tool.internal.coding_impl import CodingToolSet

__all__ = ["CodingToolSet"]
