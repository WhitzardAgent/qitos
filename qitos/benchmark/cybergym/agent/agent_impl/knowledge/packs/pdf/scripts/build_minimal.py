#!/usr/bin/env python3
"""Build a minimal PDF carrier and emit a JSON summary."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))

from cybergym_agent.toolbox.formats import pdf


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a minimal PDF carrier")
    parser.add_argument("--output", required=True, help="Output PDF path")
    args = parser.parse_args()

    data = pdf.minimal()
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    print(json.dumps({
        "status": "success",
        "format": "pdf",
        "output": str(out),
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
