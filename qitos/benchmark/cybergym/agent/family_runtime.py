from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Literal, Sequence


FamilyState = Literal["new", "active", "cooldown", "retired", "revived"]
RuntimeStage = Literal["bootstrap", "exploration", "expansion", "recovery", "endgame"]


@dataclass
class FamilyRecord:
    family_id: str
    family_name: str
    parent_family_id: str
    state: FamilyState
    hypothesis: str
    generation_axes: List[str]
    candidate_count: int = 0
    submit_count: int = 0
    best_observed_signal: str = ""
    last_progress_step: int = 0
    cooldown_reason: str = ""
    retire_reason: str = ""
    revive_reason: str = ""


@dataclass
class CandidateRecord:
    candidate_id: str
    family_id: str
    file_path: str
    content_fingerprint: str
    mutation_summary: str
    expected_signal: str
    novelty_note: str
    base_seed: str
    generation_method: str
    ready_to_submit: bool
    priority: int = 0
    producer_agent: str = ""
    created_at: str = ""
    artifact_ref: str = ""
    hypothesis_ref: str = ""
    fingerprint_mode: str = ""
    artifact_sha256: str = ""


@dataclass
class FeedbackRecord:
    candidate_id: str
    family_id: str
    poc_id: str
    exit_code: int
    output: str
    poc_path: str = ""
    storage_path: str = ""
    assessment: str = ""
    suggested_action: str = ""


class FailureType(str, Enum):
    SUBMISSION_ERROR = "SUBMISSION_ERROR"
    NO_TRIGGER = "NO_TRIGGER"
    VUL_ONLY_TRIGGERED = "VUL_ONLY_TRIGGERED"
    REJECTED_AFTER_TRIGGER = "REJECTED_AFTER_TRIGGER"
    TIMEOUT = "TIMEOUT"
    OOM = "OOM"
    BOTH_SIDES_CRASH = "BOTH_SIDES_CRASH"
    UNKNOWN = "UNKNOWN"


@dataclass
class FailureRecord:
    candidate_id: str
    family_id: str
    failure_type: FailureType
    summary: str
    evidence_excerpt: str = ""
    related_poc_id: str = ""
    internal_only: bool = False


def hard_duplicate_candidate(candidate: CandidateRecord, submitted_fingerprints: Sequence[str]) -> bool:
    return candidate.content_fingerprint in set(submitted_fingerprints)


def enqueue_candidate(
    queue: List[CandidateRecord],
    candidate: CandidateRecord,
    submitted_fingerprints: Sequence[str],
) -> bool:
    if not candidate.ready_to_submit:
        return False
    if hard_duplicate_candidate(candidate, submitted_fingerprints):
        return False
    if any(item.content_fingerprint == candidate.content_fingerprint for item in queue):
        return False
    queue.append(candidate)
    queue.sort(key=lambda item: item.priority, reverse=True)
    return True


def retain_hot_feedback(history: Sequence[FeedbackRecord], max_items: int = 2) -> List[FeedbackRecord]:
    if max_items <= 0:
        return []
    return list(history[-max_items:])


def advance_stage(
    *,
    current_stage: str,
    current_step: int,
    max_steps: int,
    family_pool: Sequence[FamilyRecord],
    candidate_queue: Sequence[CandidateRecord],
    feedback_history: Sequence[FeedbackRecord],
) -> RuntimeStage:
    stage = current_stage if current_stage in {
        "bootstrap",
        "exploration",
        "expansion",
        "recovery",
        "endgame",
    } else "bootstrap"
    if max_steps > 0 and (max_steps - max(current_step, 0)) <= 3:
        return "endgame"
    if _repeated_no_progress_feedback(feedback_history):
        return "recovery"
    if stage == "bootstrap":
        if candidate_queue or any(family.candidate_count or family.submit_count for family in family_pool):
            return "exploration"
        return "bootstrap"
    if any(_is_progress_signal(family.best_observed_signal) for family in family_pool):
        return "expansion"
    if stage == "recovery" and candidate_queue:
        return "exploration"
    if stage == "endgame":
        return "endgame"
    return "exploration"


def apply_family_queue_discipline(
    family: FamilyRecord,
    feedback_history: Sequence[FeedbackRecord],
    *,
    current_step: int,
    repeated_no_progress_limit: int = 2,
) -> bool:
    if family.state in {"cooldown", "retired"}:
        return False

    relevant_feedback = [
        feedback
        for feedback in feedback_history
        if feedback.family_id == family.family_id
    ]
    if not relevant_feedback:
        return False

    latest_feedback = relevant_feedback[-1]
    if _is_progress_signal(latest_feedback.assessment):
        family.last_progress_step = max(current_step, 0)
        return False

    consecutive_no_progress = 0
    for feedback in reversed(relevant_feedback):
        if _is_no_progress_signal(feedback.assessment):
            consecutive_no_progress += 1
            continue
        break

    if consecutive_no_progress < repeated_no_progress_limit:
        return False

    family.state = "cooldown"
    family.cooldown_reason = "repeated_no_progress"
    return True


def _repeated_no_progress_feedback(history: Sequence[FeedbackRecord]) -> bool:
    if len(history) < 2:
        return False
    latest = history[-2:]
    return (
        latest[0].family_id == latest[1].family_id
        and all(_is_no_progress_signal(item.assessment) for item in latest)
    )


def _is_progress_signal(signal: str) -> bool:
    return signal in {
        "strong_progress",
        "execution_signal_only",
        "too_broad",
        "candidate_triggered",
        "success_observed",
    }


def _is_no_progress_signal(signal: str) -> bool:
    return signal in {
        "",
        "submitted",
        "submission_error",
        "no_trigger",
        "weak_progress",
        "sideways",
        "misleading_progress",
        "dead_end",
    }
