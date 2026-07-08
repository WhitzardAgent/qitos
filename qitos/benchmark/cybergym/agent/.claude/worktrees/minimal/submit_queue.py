from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set, Tuple

from .family_runtime import CandidateRecord


@dataclass
class SubmitQueuePolicy:
    submitted_fingerprints: Set[str] = field(default_factory=set)
    queued_fingerprints: Set[str] = field(default_factory=set)
    cooled_family_ids: Set[str] = field(default_factory=set)
    negative_evidence: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.submitted_fingerprints = _normalize_non_empty(self.submitted_fingerprints)
        self.queued_fingerprints = _normalize_non_empty(self.queued_fingerprints)
        self.cooled_family_ids = _normalize_non_empty(self.cooled_family_ids)

    def accept(self, candidate: CandidateRecord) -> Tuple[bool, str]:
        family_id = str(getattr(candidate, "family_id", "") or "").strip()
        content_fingerprint = str(getattr(candidate, "content_fingerprint", "") or "").strip()
        mutation_summary = str(getattr(candidate, "mutation_summary", "") or "").strip()
        if not getattr(candidate, "ready_to_submit", False):
            return False, "not_ready"
        if not content_fingerprint:
            return False, "missing_content_fingerprint"
        if content_fingerprint in self.submitted_fingerprints:
            return False, "duplicate_submitted_fingerprint"
        if family_id in self.cooled_family_ids:
            return False, "family_cooldown"
        if content_fingerprint in self.queued_fingerprints:
            return False, "duplicate_queued_fingerprint"
        # Negative evidence blocking: same family + same mutation axis + same
        # no-trigger evidence ≥3 → block to prevent blind repetition.
        if family_id and self.negative_evidence:
            _no_trigger_kinds = {"path_reached_no_trigger", "no_crash_unknown"}
            same_family_no_trigger = [
                ev for ev in self.negative_evidence
                if ev.get("family_id") == family_id
                and ev.get("kind") in _no_trigger_kinds
                and ev.get("ttl", 0) > 0
            ]
            # Check if mutation_summary matches a previously failed axis
            if mutation_summary and same_family_no_trigger:
                matching_axis = [
                    ev for ev in same_family_no_trigger
                    if mutation_summary in str(ev.get("summary", ""))
                ]
                if len(matching_axis) >= 3:
                    return False, "blocked_by_negative_evidence"
            # Block if too many same-family no-trigger evidences regardless of axis
            if len(same_family_no_trigger) >= 5:
                return False, "blocked_by_negative_evidence"
        self.queued_fingerprints.add(content_fingerprint)
        return True, "accepted"


def _normalize_non_empty(values: Set[str]) -> Set[str]:
    normalized: Set[str] = set()
    for item in values:
        value = str(item or "").strip()
        if value:
            normalized.add(value)
    return normalized
