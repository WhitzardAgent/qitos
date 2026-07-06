"""Packet parser — uses Scapy to dissect packets and extract layer fields.

Extracts: protocol layers, field names/values, selector fields, checksums.
Produces a field_map with named structural nodes for mutation targeting.
Also supports TLV (Type-Length-Value) protocol structures.
"""

from __future__ import annotations

import logging
from typing import Any

from ...models import FieldInfo, ParseResult

logger = logging.getLogger(__name__)


def parse_packet(artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
    """Parse a packet/TLV artifact using Scapy.

    Returns a ParseResult with field_map keyed by structured names like:
      - packet.eth.dst
      - packet.ip.src
      - packet.udp.dport
      - packet.udp.payload
    """
    try:
        from scapy.all import Ether, IP, Raw, conf
        conf.verb = 0  # Suppress Scapy warnings
    except ImportError:
        return ParseResult(status="backend_unavailable")

    if not artifact or len(artifact) < 4:
        return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

    field_map: dict[str, FieldInfo] = {}
    warnings: list[str] = []
    evidence_ids: list[str] = []
    carrier_family = "packet"

    try:
        # Try Ethernet frame first
        pkt = Ether(artifact)

        # Extract layers
        layers: list[tuple[str, Any]] = []
        current = pkt
        offset = 0

        while current is not None:
            layer_name = current.__class__.__name__
            layers.append((layer_name, current))

            # Extract fields from this layer
            for fname in current.fields_desc:
                field_name = fname.name
                try:
                    value = getattr(current, field_name, None)
                    if value is None:
                        continue
                    full_name = f"packet.{layer_name.lower()}.{field_name}"

                    # Estimate offset (approximate — Scapy doesn't expose byte offsets directly)
                    field_width = _estimate_field_width(fname)

                    field_map[full_name] = FieldInfo(
                        name=full_name,
                        offset=offset,  # approximate
                        width=field_width,
                        value=value if not hasattr(value, '__bytes__') else bytes(value).hex(),
                        node_id=f"layer_{layer_name.lower()}",
                        derived="chksum" in field_name.lower() or "len" in field_name.lower(),
                    )
                    evidence_ids.append(full_name)
                except Exception:
                    continue

            # Move to next layer
            offset += len(current)
            current = current.payload if current.payload and current.payload.__class__.__name__ != "Raw" else None

        # Check for raw payload
        raw = pkt[Raw] if Raw in pkt else None
        if raw:
            payload_bytes = bytes(raw)
            field_map["packet.raw_payload"] = FieldInfo(
                name="packet.raw_payload",
                offset=offset,
                width=len(payload_bytes),
                value=payload_bytes[:64].hex() + ("..." if len(payload_bytes) > 64 else ""),
                node_id="payload",
            )
            evidence_ids.append("packet.raw_payload")

        # Determine carrier family from layers
        layer_names = [l[0] for l in layers]
        if "TCP" in layer_names or "UDP" in layer_names:
            carrier_family = "ip_packet"
        elif "Dot3" in layer_names:
            carrier_family = "ethernet"
        else:
            carrier_family = "packet"

    except Exception as e:
        # Scapy couldn't parse — try TLV interpretation
        try:
            return _parse_tlv(artifact)
        except Exception:
            return ParseResult(
                status="failed",
                carrier_family="packet",
                parse_warnings=(f"scapy_parse_failed: {e}",),
            )

    return ParseResult(
        status="success",
        carrier_family=carrier_family,
        version="",
        structural_summary={
            "layer_count": len(layers),
            "layer_names": [l[0] for l in layers],
            "has_payload": "packet.raw_payload" in field_map,
        },
        field_map=field_map,
        node_count=len(set(f.node_id for f in field_map.values())),
        parse_warnings=tuple(warnings),
        evidence_ids=tuple(evidence_ids),
    )


def _parse_tlv(artifact: bytes) -> ParseResult:
    """Parse a generic TLV (Type-Length-Value) structure."""
    field_map: dict[str, FieldInfo] = {}
    evidence_ids: list[str] = []
    warnings: list[str] = []

    offset = 0
    idx = 0
    while offset + 4 <= len(artifact) and idx < 50:
        # Assume 1-byte type, 1-byte length, then value
        tlv_type = artifact[offset]
        if offset + 2 <= len(artifact):
            tlv_length = artifact[offset + 1]
            tlv_value = artifact[offset + 2:offset + 2 + tlv_length] if offset + 2 + tlv_length <= len(artifact) else b""

            field_map[f"tlv.{idx}.type"] = FieldInfo(
                name=f"tlv.{idx}.type",
                offset=offset,
                width=1,
                value=tlv_type,
                node_id=f"tlv_{idx}",
            )
            field_map[f"tlv.{idx}.length"] = FieldInfo(
                name=f"tlv.{idx}.length",
                offset=offset + 1,
                width=1,
                value=tlv_length,
                node_id=f"tlv_{idx}",
                derived=True,
            )
            if tlv_value:
                field_map[f"tlv.{idx}.value"] = FieldInfo(
                    name=f"tlv.{idx}.value",
                    offset=offset + 2,
                    width=tlv_length,
                    value=tlv_value[:32].hex(),
                    node_id=f"tlv_{idx}",
                )
            evidence_ids.append(f"tlv.{idx}")
            offset += 2 + tlv_length
            idx += 1
        else:
            break

    return ParseResult(
        status="partial" if not field_map else "success",
        carrier_family="tlv",
        structural_summary={"tlv_count": idx, "format": "generic_tlv"},
        field_map=field_map,
        node_count=idx,
        parse_warnings=tuple(warnings),
        evidence_ids=tuple(evidence_ids),
    )


def _estimate_field_width(field_desc: Any) -> int:
    """Estimate byte width of a Scapy field descriptor."""
    try:
        fmt = getattr(field_desc, "fmt", "B")
        width_map = {"B": 1, "H": 2, "I": 4, "Q": 8, "b": 1, "h": 2, "i": 4, "q": 8}
        return width_map.get(fmt, 0)
    except Exception:
        return 0
