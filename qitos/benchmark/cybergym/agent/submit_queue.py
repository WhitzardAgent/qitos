from __future__ import annotations

from dataclasses import dataclass, field
from typing import Set, Tuple

from .family_runtime import CandidateRecord


@dataclass
class SubmitQueuePolicy:
    submitted_fingerprints: Set[str] = field(default_factory=set)
    queued_fingerprints: Set[str] = field(default_factory=set)
    cooled_family_ids: Set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.submitted_fingerprints = _normalize_non_empty(self.submitted_fingerprints)
        self.queued_fingerprints = _normalize_non_empty(self.queued_fingerprints)
        self.cooled_family_ids = _normalize_non_empty(self.cooled_family_ids)

    def accept(self, candidate: CandidateRecord) -> Tuple[bool, str]:
        family_id = str(getattr(candidate, "family_id", "") or "").strip()
        content_fingerprint = str(getattr(candidate, "content_fingerprint", "") or "").strip()
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
        self.queued_fingerprints.add(content_fingerprint)
        return True, "accepted"


def _normalize_non_empty(values: Set[str]) -> Set[str]:
    normalized: Set[str] = set()
    for item in values:
        value = str(item or "").strip()
        if value:
            normalized.add(value)
    return normalized
