"""SFNT/Font parser — uses fontTools.ttLib to extract structural fields.

Extracts: table directory, table offsets/checksums, CFF/gvar/head/cmap tables.
Produces a field_map with named structural nodes for mutation targeting.
"""

from __future__ import annotations

import logging
from typing import Any

from ...models import FieldInfo, ParseResult

logger = logging.getLogger(__name__)


def parse_sfnt(artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
    """Parse an SFNT/Font artifact using fontTools.ttLib.

    Returns a ParseResult with field_map keyed by structured names like:
      - sfnt.header.sfVersion
      - sfnt.header.numTables
      - sfnt.table.<tag>.offset
      - sfnt.table.<tag>.length
      - sfnt.table.<tag>.checksum
    """
    try:
        from fontTools.ttLib import TTFont
        from fontTools.ttLib.sfnt import SFNTReader
    except ImportError:
        return ParseResult(status="backend_unavailable")

    if not artifact or len(artifact) < 12:
        return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

    field_map: dict[str, FieldInfo] = {}
    warnings: list[str] = []
    evidence_ids: list[str] = []

    # Parse SFNT header directly from raw bytes
    try:
        sfnt_version = artifact[:4]
        num_tables = int.from_bytes(artifact[4:6], "big")

        field_map["sfnt.header.sfVersion"] = FieldInfo(
            name="sfnt.header.sfVersion",
            offset=0,
            width=4,
            value=sfnt_version.hex(),
            node_id="header",
            protected=True,
        )
        field_map["sfnt.header.numTables"] = FieldInfo(
            name="sfnt.header.numTables",
            offset=4,
            width=2,
            value=num_tables,
            node_id="header",
        )
        evidence_ids.append("sfnt.header")
    except Exception as e:
        warnings.append(f"header_parse_failed: {e}")
        return ParseResult(
            status="failed",
            carrier_family="sfnt",
            parse_warnings=tuple(warnings),
        )

    # Parse table directory entries
    offset = 12
    for i in range(min(num_tables, 40)):  # limit to 40 tables
        if offset + 16 > len(artifact):
            warnings.append(f"table_directory_truncated_at_{i}")
            break

        tag = artifact[offset:offset + 4].decode("ascii", errors="replace").strip()
        checksum = int.from_bytes(artifact[offset + 4:offset + 8], "big")
        table_offset = int.from_bytes(artifact[offset + 8:offset + 12], "big")
        table_length = int.from_bytes(artifact[offset + 12:offset + 16], "big")

        tag_lower = tag.lower()

        field_map[f"sfnt.table.{tag_lower}.offset"] = FieldInfo(
            name=f"sfnt.table.{tag_lower}.offset",
            offset=offset + 8,
            width=4,
            value=table_offset,
            node_id=f"table_{tag_lower}",
            derived=True,
        )
        field_map[f"sfnt.table.{tag_lower}.length"] = FieldInfo(
            name=f"sfnt.table.{tag_lower}.length",
            offset=offset + 12,
            width=4,
            value=table_length,
            node_id=f"table_{tag_lower}",
            derived=True,
        )
        field_map[f"sfnt.table.{tag_lower}.checksum"] = FieldInfo(
            name=f"sfnt.table.{tag_lower}.checksum",
            offset=offset + 4,
            width=4,
            value=checksum,
            node_id=f"table_{tag_lower}",
            derived=True,
        )

        evidence_ids.append(f"sfnt.table.{tag_lower}")
        offset += 16

    # Determine carrier family
    carrier_family = "sfnt"
    version_str = ""
    if sfnt_version == b"\x00\x01\x00\x00":
        carrier_family = "truetype"
        version_str = "1.0"
    elif sfnt_version == b"OTTO":
        carrier_family = "opentype"
        version_str = "OTTO"
    elif sfnt_version == b"true":
        carrier_family = "truetype"
        version_str = "true"
    elif sfnt_version == b"ttcf":
        carrier_family = "ttc"
        version_str = "ttcf"

    # Try full TTFont parse for deeper structure
    try:
        from io import BytesIO
        font = TTFont(BytesIO(artifact))
        # Extract key table info
        if "head" in font:
            head = font["head"]
            field_map["sfnt.head.checkSumAdjustment"] = FieldInfo(
                name="sfnt.head.checkSumAdjustment",
                offset=0,
                width=0,
                value=getattr(head, "checkSumAdjustment", 0),
                node_id="table_head",
                derived=True,
                protected=True,
            )
            field_map["sfnt.head.flags"] = FieldInfo(
                name="sfnt.head.flags",
                offset=0,
                width=0,
                value=getattr(head, "flags", 0),
                node_id="table_head",
            )

        if "maxp" in font:
            maxp = font["maxp"]
            field_map["sfnt.maxp.numGlyphs"] = FieldInfo(
                name="sfnt.maxp.numGlyphs",
                offset=0,
                width=0,
                value=getattr(maxp, "numGlyphs", 0),
                node_id="table_maxp",
            )

        if "cmap" in font:
            cmap = font["cmap"]
            num_subtables = len(cmap.tables)
            field_map["sfnt.cmap.numSubtables"] = FieldInfo(
                name="sfnt.cmap.numSubtables",
                offset=0,
                width=0,
                value=num_subtables,
                node_id="table_cmap",
            )

        if "CFF " in font:
            field_map["sfnt.cff.present"] = FieldInfo(
                name="sfnt.cff.present",
                offset=0,
                width=0,
                value=True,
                node_id="table_cff",
            )

        if "gvar" in font:
            field_map["sfnt.gvar.present"] = FieldInfo(
                name="sfnt.gvar.present",
                offset=0,
                width=0,
                value=True,
                node_id="table_gvar",
            )

        if "glyf" in font:
            glyf = font["glyf"]
            field_map["sfnt.glyf.numGlyphs"] = FieldInfo(
                name="sfnt.glyf.numGlyphs",
                offset=0,
                width=0,
                value=len(glyf),
                node_id="table_glyf",
            )

        font.close()
    except Exception as e:
        warnings.append(f"ttfont_parse_partial: {e}")

    return ParseResult(
        status="success" if not warnings or all("partial" not in w for w in warnings) else "partial",
        carrier_family=carrier_family,
        version=version_str,
        structural_summary={
            "num_tables": num_tables,
            "carrier_family": carrier_family,
            "table_tags": sorted(set(
                k.split(".")[2] for k in field_map if k.startswith("sfnt.table.")
            )),
        },
        field_map=field_map,
        node_count=len(set(f.node_id for f in field_map.values())),
        parse_warnings=tuple(warnings),
        evidence_ids=tuple(evidence_ids),
    )
