from __future__ import annotations

from typing import Any

from .base import DomainPack, result_for_format, state_text


class PdfPack(DomainPack):
    name = "pdf"

    def match(self, state: Any) -> float:
        text = state_text(state)
        return 0.9 if "pdf" in text or "poppler" in text or "mupdf" in text or "xref" in text else 0.0

    def build(self, state: Any):
        return result_for_format(
            pack=self.name,
            score=self.match(state),
            fmt="pdf",
            operations=[{"kind": "set_bytes_ascii", "offset": 0, "text": "%PDF-1.4\n"}],
            sanity=[{"kind": "magic", "expected": "%PDF", "description": "keep PDF magic/xref structure parseable"}],
        )
