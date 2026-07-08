"""Subprocess worker for single-file tree-sitter indexing.

Runs ``index_file()`` in an isolated subprocess so that tree-sitter C extension
crashes (SIGSEGV / exit 139) don't kill the agent process.  The parent sends
JSON on stdin (root + path), the child writes JSON on stdout.

Usage::

    python -m cybergym_agent.analysis._index_worker < input.json > output.json
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def _run() -> None:
    payload = json.loads(sys.stdin.buffer.read())
    root = Path(payload["root"])
    path = Path(payload["path"])

    from .indexer import index_file
    from .models import stable_value

    digest, symbols, summaries, unresolved = index_file(root, path)

    result = {
        "digest": digest,
        "symbols": stable_value(symbols),
        "summaries": stable_value(summaries),
        "unresolved": unresolved,
    }
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode())
    sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
