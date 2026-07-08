"""Subprocess worker for tree-sitter analysis.

Runs analysis in an isolated subprocess so that tree-sitter C extension
crashes (SIGSEGV) don't kill the agent process.  The parent communicates
via stdin/stdout JSON.  If the child crashes (exit code 139), the parent
gets None back instead of dying.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def _run() -> None:
    """Entry point: read JSON from stdin, run analysis, write JSON to stdout."""
    payload = json.loads(sys.stdin.read())
    repo = payload["repository"]
    workspace_root = payload.get("workspace_root") or str(Path(repo).parent)
    candidate = payload["candidate"]
    mode = payload.get("mode", "automatic")

    from .service import AnalysisService
    from .models import SinkCandidateInput

    service = AnalysisService(repo, workspace_root=workspace_root)

    sci = SinkCandidateInput(
        candidate_id=candidate.get("candidate_id", "sink_runtime"),
        repository_id=candidate.get("repository_id", "repo_current"),
        file=candidate.get("file") or None,
        line=int(candidate.get("line") or 0) or 0,
        function=candidate.get("function") or None,
        callee=candidate.get("callee") or None,
        expression=candidate.get("expression") or None,
        category=candidate.get("category") or None,
        reason=candidate.get("reason") or "subprocess analysis",
        agent_confidence=float(candidate.get("agent_confidence") or 0.5),
        related_cve=candidate.get("related_cve") or None,
        metadata=dict(candidate.get("metadata") or {}),
    )

    result = service.analyze_sink_candidate(sci, mode=mode)
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        _run()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
