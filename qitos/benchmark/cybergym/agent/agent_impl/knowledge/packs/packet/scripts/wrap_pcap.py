#!/usr/bin/env python3
"""Wrap a raw packet frame in a minimal pcap container."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Wrap raw frame bytes as pcap")
    parser.add_argument("--frame", required=True, help="Input raw frame path")
    parser.add_argument("--output", required=True, help="Output pcap path")
    parser.add_argument("--linktype", type=int, default=1, help="Pcap linktype, default 1 Ethernet")
    args = parser.parse_args()

    frame_path = Path(args.frame)
    if not frame_path.is_file():
        print(json.dumps({
            "status": "error",
            "reason": "frame_not_found",
            "frame": str(frame_path),
        }, sort_keys=True))
        return 1

    frame = frame_path.read_bytes()
    pcap = _wrap_pcap(frame, args.linktype)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pcap)

    print(json.dumps({
        "status": "success",
        "format": "pcap",
        "frame": str(frame_path),
        "output": str(out),
        "frame_size": len(frame),
        "pcap_size": len(pcap),
        "linktype": args.linktype,
        "sha256": hashlib.sha256(pcap).hexdigest(),
    }, sort_keys=True))
    return 0


def _wrap_pcap(frame: bytes, linktype: int = 1) -> bytes:
    global_header = struct.pack(
        "<IHHiiii",
        0xA1B2C3D4,
        2,
        4,
        0,
        0,
        65535,
        int(linktype),
    )
    record_header = struct.pack("<IIII", 0, 0, len(frame), len(frame))
    return global_header + record_header + frame


if __name__ == "__main__":
    raise SystemExit(main())
