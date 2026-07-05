"""Domain pack selection."""

from __future__ import annotations

from typing import Any

from .archive import ArchivePack
from .font_sfnt import FontSfntPack
from .openthread_protocol import OpenThreadProtocolPack
from .pdf import PdfPack
from .tiff_exif import TiffExifPack
from .wireshark_packet import WiresharkPacketPack


_PACKS = [
    WiresharkPacketPack(),
    PdfPack(),
    TiffExifPack(),
    FontSfntPack(),
    OpenThreadProtocolPack(),
    ArchivePack(),
]


def select_domain_packs(state: Any, *, limit: int = 3) -> list[dict[str, Any]]:
    results = []
    for pack in _PACKS:
        score = pack.match(state)
        if score <= 0:
            continue
        result = pack.build(state)
        if result.match_score <= 0:
            result.match_score = score
        results.append(result.as_dict())
    results.sort(key=lambda item: item.get("match_score", 0), reverse=True)
    return results[:limit]
