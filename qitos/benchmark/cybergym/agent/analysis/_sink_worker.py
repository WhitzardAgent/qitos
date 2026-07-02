"""Isolated full-file sink detector worker."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def _run() -> None:
    payload = json.loads(sys.stdin.buffer.read())
    path = Path(payload["path"])
    source = path.read_bytes()

    from ..agent_impl.constraint_analysis import analyze_constraints
    from ..agent_impl.constraint_models import (
        AnalysisBudget,
        ExtractionRequest,
        SourceUnit,
        hint_from_description,
    )

    request = ExtractionRequest(
        source=SourceUnit(
            source,
            path=payload.get("relative_path", path.name),
            file_extension=path.suffix,
            completeness="full_file",
        ),
        sink_function=payload.get("sink_function", ""),
        sink_span=int(payload.get("line", 0) or 0) or None,
        vulnerability_hint=hint_from_description(payload.get("description", "")),
        budget=AnalysisBudget(max_milliseconds=int(payload.get("budget_ms", 900))),
    )
    result = analyze_constraints(request)
    output = {
        "sink_resolved": result.sink_resolved,
        "parse_has_error": result.parse_has_error,
        "candidates": [item.to_dict() for item in result.candidates],
        "diagnostics": [item.to_dict() for item in result.diagnostics],
        "stats": result.stats.to_dict(),
    }
    sys.stdout.buffer.write(json.dumps(output, ensure_ascii=False).encode())
    sys.stdout.buffer.write(b"\n")


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
