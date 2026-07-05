from __future__ import annotations

from typing import Any

from .base import DomainPack, result_for_format, state_text


class TiffExifPack(DomainPack):
    name = "tiff_exif"

    def match(self, state: Any) -> float:
        text = state_text(state)
        return 0.88 if "tiff" in text or "exif" in text or "graphicsmagick" in text or "imagemagick" in text else 0.0

    def build(self, state: Any):
        return result_for_format(
            pack=self.name,
            score=self.match(state),
            fmt="tiff",
            operations=[{"kind": "set_bytes_ascii", "offset": 0, "text": "II"}],
            sanity=[{"kind": "magic", "expected": "II*\\x00", "description": "keep TIFF endian/magic header"}],
        )
