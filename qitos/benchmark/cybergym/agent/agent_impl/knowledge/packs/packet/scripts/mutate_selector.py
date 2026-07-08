#!/usr/bin/env python3
"""Mutate packet selector fields while preserving basic carrier reachability."""

from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Mutate packet selector fields")
    parser.add_argument("--seed", required=True, help="Input raw frame or pcap path")
    parser.add_argument("--output", required=True, help="Output path")
    parser.add_argument("--eth-type", type=lambda value: int(value, 0), default=None)
    parser.add_argument("--udp-dport", type=int, default=None)
    parser.add_argument("--udp-sport", type=int, default=None)
    parser.add_argument("--tcp-dport", type=int, default=None)
    parser.add_argument("--tcp-sport", type=int, default=None)
    args = parser.parse_args()

    seed_path = Path(args.seed)
    if not seed_path.is_file():
        print(json.dumps({
            "status": "error",
            "reason": "seed_not_found",
            "seed": str(seed_path),
        }, sort_keys=True))
        return 1

    data = seed_path.read_bytes()
    packet = _split_pcap(data)
    frame = bytearray(packet["frame"])
    applied: list[str] = []
    issues: list[str] = []

    if args.eth_type is not None:
        if len(frame) >= 14:
            struct.pack_into(">H", frame, 12, args.eth_type)
            applied.append("eth_type")
        else:
            issues.append("frame_too_short_for_eth_type")

    port_updates = {
        "udp_sport": args.udp_sport,
        "udp_dport": args.udp_dport,
        "tcp_sport": args.tcp_sport,
        "tcp_dport": args.tcp_dport,
    }
    if any(value is not None for value in port_updates.values()):
        applied.extend(_mutate_transport_ports(frame, port_updates, issues))

    if not applied:
        print(json.dumps({
            "status": "error",
            "reason": "no_selector_mutation_applied",
            "issues": issues,
        }, sort_keys=True))
        return 1

    output_bytes = _join_pcap(packet, bytes(frame))
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(output_bytes)

    print(json.dumps({
        "status": "success",
        "seed": str(seed_path),
        "output": str(out),
        "pcap_wrapped": packet["is_pcap"],
        "applied_operations": applied,
        "issues": issues,
    }, sort_keys=True))
    return 0


def _split_pcap(data: bytes) -> dict[str, object]:
    if len(data) >= 40 and data[:4] in {b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4"}:
        endian = "<" if data[:4] == b"\xd4\xc3\xb2\xa1" else ">"
        incl_len = struct.unpack(f"{endian}I", data[32:36])[0]
        frame_start = 40
        frame_end = min(frame_start + incl_len, len(data))
        return {
            "is_pcap": True,
            "endian": endian,
            "prefix": data[:24],
            "record_header": data[24:40],
            "suffix": data[frame_end:],
            "frame": data[frame_start:frame_end],
        }
    return {"is_pcap": False, "frame": data}


def _join_pcap(packet: dict[str, object], frame: bytes) -> bytes:
    if not packet.get("is_pcap"):
        return frame
    endian = str(packet["endian"])
    old_record = bytes(packet["record_header"])
    ts_sec, ts_usec, _old_incl, _old_orig = struct.unpack(f"{endian}IIII", old_record)
    record = struct.pack(f"{endian}IIII", ts_sec, ts_usec, len(frame), len(frame))
    return bytes(packet["prefix"]) + record + frame + bytes(packet["suffix"])


def _mutate_transport_ports(
    frame: bytearray,
    port_updates: dict[str, int | None],
    issues: list[str],
) -> list[str]:
    applied: list[str] = []
    ip_offset = 14 if len(frame) >= 14 and frame[12:14] == b"\x08\x00" else 0
    if len(frame) < ip_offset + 20:
        issues.append("frame_too_short_for_ipv4")
        return applied

    version_ihl = frame[ip_offset]
    if version_ihl >> 4 != 4:
        issues.append("not_ipv4")
        return applied

    ihl = (version_ihl & 0x0F) * 4
    proto = frame[ip_offset + 9]
    transport_offset = ip_offset + ihl
    if len(frame) < transport_offset + 4:
        issues.append("frame_too_short_for_transport_ports")
        return applied

    if proto == 17:
        applied.extend(_set_port(frame, transport_offset, "udp_sport", port_updates.get("udp_sport")))
        applied.extend(_set_port(frame, transport_offset + 2, "udp_dport", port_updates.get("udp_dport")))
        if len(frame) >= transport_offset + 8:
            struct.pack_into(">H", frame, transport_offset + 6, 0)
            applied.append("udp_checksum_zeroed")
    elif proto == 6:
        applied.extend(_set_port(frame, transport_offset, "tcp_sport", port_updates.get("tcp_sport")))
        applied.extend(_set_port(frame, transport_offset + 2, "tcp_dport", port_updates.get("tcp_dport")))
        if len(frame) >= transport_offset + 18:
            struct.pack_into(">H", frame, transport_offset + 16, 0)
            applied.append("tcp_checksum_zeroed")
    else:
        issues.append(f"unsupported_ip_proto_{proto}")

    _repair_ipv4_header_checksum(frame, ip_offset, ihl)
    return applied


def _set_port(frame: bytearray, offset: int, op_id: str, value: int | None) -> list[str]:
    if value is None:
        return []
    struct.pack_into(">H", frame, offset, int(value) & 0xFFFF)
    return [op_id]


def _repair_ipv4_header_checksum(frame: bytearray, ip_offset: int, ihl: int) -> None:
    if len(frame) < ip_offset + ihl:
        return
    struct.pack_into(">H", frame, ip_offset + 10, 0)
    checksum = _internet_checksum(bytes(frame[ip_offset:ip_offset + ihl]))
    struct.pack_into(">H", frame, ip_offset + 10, checksum)


def _internet_checksum(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for idx in range(0, len(data), 2):
        total += (data[idx] << 8) + data[idx + 1]
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


if __name__ == "__main__":
    raise SystemExit(main())
