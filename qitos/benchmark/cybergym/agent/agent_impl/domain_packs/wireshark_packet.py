from __future__ import annotations

from typing import Any

from .base import DomainPack, DomainPackResult, state_text


class WiresharkPacketPack(DomainPack):
    name = "wireshark_packet"

    def match(self, state: Any) -> float:
        text = state_text(state)
        if "wireshark" in text or "dissector" in text or "packet" in text or "tvb" in text:
            return 0.86
        return 0.0

    def build(self, state: Any) -> DomainPackResult:
        protocols = list(getattr(state, "harness_protocols", []) or [])
        gaps = [] if protocols else ["needs_harness_protocol: packet dissector stack/selector not extracted"]
        return DomainPackResult(
            pack=self.name,
            match_score=self.match(state),
            status="ready" if not gaps else "partial",
            recipe_patch={"carrier": {"format": "packet", "seed_policy": "minimal_template_ok"}},
            rewrite_plan={"operations": [{"kind": "pad_to_length", "length": 64}], "invariants": ["preserve_packet_dispatch"]},
            open_gaps=gaps,
            sanity_expectations=[{"kind": "harness_protocol", "expected": "packet dispatch", "description": "input must satisfy dissector stack/selector"}],
        )
