#!/usr/bin/env python3
"""Wrap TIFF/EXIF bytes in a JPEG APP1 Exif segment."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Wrap TIFF bytes in JPEG APP1 Exif")
    parser.add_argument("--tiff", required=True, help="Input TIFF/EXIF payload path")
    parser.add_argument("--output", required=True, help="Output JPEG/EXIF path")
    parser.add_argument("--jpeg", default="", help="Optional JPEG seed to receive the APP1 segment")
    args = parser.parse_args()

    tiff_path = Path(args.tiff)
    if not tiff_path.is_file():
        print(json.dumps({
            "status": "error",
            "reason": "tiff_not_found",
            "tiff": str(tiff_path),
        }, sort_keys=True))
        return 1

    tiff = tiff_path.read_bytes()
    app1_payload = b"Exif\x00\x00" + tiff
    if len(app1_payload) + 2 > 0xFFFF:
        print(json.dumps({
            "status": "error",
            "reason": "app1_payload_too_large",
            "payload_size": len(app1_payload),
        }, sort_keys=True))
        return 1

    jpeg_seed = Path(args.jpeg).read_bytes() if args.jpeg else b"\xff\xd8\xff\xd9"
    if not jpeg_seed.startswith(b"\xff\xd8"):
        print(json.dumps({
            "status": "error",
            "reason": "jpeg_seed_missing_soi",
            "jpeg": args.jpeg,
        }, sort_keys=True))
        return 1

    app1 = b"\xff\xe1" + struct.pack(">H", len(app1_payload) + 2) + app1_payload
    output = jpeg_seed[:2] + app1 + jpeg_seed[2:]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(output)

    print(json.dumps({
        "status": "success",
        "format": "jpeg_exif",
        "tiff": str(tiff_path),
        "output": str(out),
        "tiff_size": len(tiff),
        "output_size": len(output),
        "sha256": hashlib.sha256(output).hexdigest(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
