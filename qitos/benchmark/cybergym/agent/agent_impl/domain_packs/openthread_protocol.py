from __future__ import annotations

from typing import Any

from .base import DomainPack, DomainPackResult, state_text


class OpenThreadProtocolPack(DomainPack):
    name = "openthread_protocol"

    def match(self, state: Any) -> float:
        text = state_text(state)
        return 0.84 if "openthread" in text or "opensc" in text or "apdu" in text or "tlv" in text or "ncp" in text else 0.0

    def build(self, state: Any) -> DomainPackResult:
        transcripts = list(getattr(state, "protocol_transcript_plans", []) or [])
        gaps = [] if any(len(t.get("steps", [])) >= 2 for t in transcripts) else ["needs_transcript_steps: protocol lifecycle requires ordered messages"]
        return DomainPackResult(
            pack=self.name,
            match_score=self.match(state),
            status="ready" if not gaps else "partial",
            recipe_patch={"carrier": {"format": "tlv", "seed_policy": "minimal_template_ok"}},
            rewrite_plan={"operations": [{"kind": "pad_to_length", "length": 32}], "invariants": ["preserve_tlv_order"]},
            open_gaps=gaps,
            sanity_expectations=[{"kind": "transcript", "expected": "ordered lifecycle", "description": "emit init/selector/payload in protocol order"}],
        )
