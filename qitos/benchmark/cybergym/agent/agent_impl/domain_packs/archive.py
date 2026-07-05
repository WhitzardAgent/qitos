from __future__ import annotations

from typing import Any

from .base import DomainPack, result_for_format, state_text


class ArchivePack(DomainPack):
    name = "archive"

    def match(self, state: Any) -> float:
        text = state_text(state)
        return 0.8 if "archive" in text or "zip" in text or "rar" in text or "lha" in text or "compression" in text else 0.0

    def build(self, state: Any):
        return result_for_format(
            pack=self.name,
            score=self.match(state),
            fmt="zip",
            operations=[{"kind": "pad_to_length", "length": 96}],
            sanity=[{"kind": "checksum", "expected": "crc32", "description": "archive member length/checksum must stay coherent"}],
        )
