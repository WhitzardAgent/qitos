"""Candidate family management mixin."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

if TYPE_CHECKING:
    from ...state import CyberGymState

from ...family_runtime import (
    CandidateRecord,
    FamilyRecord,
    FeedbackRecord,
    advance_stage,
    apply_family_queue_discipline,
    enqueue_candidate,
)
from ...submit_queue import SubmitQueuePolicy
from ...evidence_selector import select_family_evidence


class CandidateFamilyMixin:
    """Candidate family management — family pool, candidate queue, fingerprinting."""

    @staticmethod
    def _find_family(state: CyberGymState, family_id: str) -> FamilyRecord | None:
        for family in state.family_pool:
            if family.family_id == family_id:
                return family
        return None

    @staticmethod
    def _family_snapshot(family: FamilyRecord) -> Dict[str, Any]:
        return {
            "family_id": family.family_id,
            "family_name": family.family_name,
            "parent_family_id": family.parent_family_id,
            "state": family.state,
            "hypothesis": family.hypothesis,
            "generation_axes": list(family.generation_axes),
            "candidate_count": family.candidate_count,
            "submit_count": family.submit_count,
            "best_observed_signal": family.best_observed_signal,
        }

    @staticmethod
    def _previous_family_feedback_raw(
        state: CyberGymState,
        family_id: str,
        latest_poc_id: str,
    ) -> str:
        for feedback in reversed(state.feedback_history):
            if feedback.family_id != family_id or feedback.poc_id == latest_poc_id:
                continue
            return feedback.output
        return ""

    @staticmethod
    def _latest_family_feedback(state: CyberGymState, family_id: str) -> FeedbackRecord | None:
        for feedback in reversed(state.feedback_history):
            if feedback.family_id == family_id:
                return feedback
        return None

    @staticmethod
    def _family_mutation_hints(state: CyberGymState, family_id: str) -> List[str]:
        hints = state.metadata.get("family_mutation_hints", {}).get(family_id, [])
        return [str(item) for item in hints if isinstance(item, str)]

    @staticmethod
    def _candidate_fingerprint(raw_candidate: Dict[str, Any]) -> str:
        fingerprint_payload = {
            "family_id": raw_candidate.get("family_id", ""),
            "mutation_summary": raw_candidate.get("mutation_summary", ""),
            "expected_signal": raw_candidate.get("expected_signal", ""),
            "base_seed": raw_candidate.get("base_seed", ""),
            "generation_method": raw_candidate.get("generation_method", ""),
        }
        encoded = json.dumps(fingerprint_payload, sort_keys=True).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _apply_insight_judgement(
        self,
        state: CyberGymState,
        family: FamilyRecord,
        latest_feedback: FeedbackRecord,
        judgement: Dict[str, Any],
    ) -> None:
        latest_feedback.assessment = judgement["assessment"]
        latest_feedback.suggested_action = judgement["suggested_action"]
        for feedback in reversed(state.feedback_history):
            if feedback.poc_id == latest_feedback.poc_id and feedback.family_id == latest_feedback.family_id:
                feedback.assessment = judgement["assessment"]
                feedback.suggested_action = judgement["suggested_action"]
                break
        state.metadata.setdefault("family_mutation_hints", {})[family.family_id] = judgement["mutation_hints"]
        if judgement["suggested_action"] == "branch_family":
            branched_family = self._branch_family(state, family, judgement)
            state.metadata.setdefault("family_mutation_hints", {})[branched_family.family_id] = judgement["mutation_hints"]
            family.state = "cooldown"
            family.cooldown_reason = str(judgement.get("reason") or "branch_family")
            return
        self._update_family_from_insight(family, judgement)
        if judgement["suggested_action"] == "stop_task":
            state.set_stop("success", final_result=latest_feedback.poc_path or latest_feedback.poc_id or "verified")

    @staticmethod
    def _apply_family_queue_discipline(state: CyberGymState) -> bool:
        current_step = int(getattr(state, "current_step", 0) or 0)
        negative_evidence = (state.metadata or {}).get("negative_evidence", []) if isinstance(state.metadata, dict) else []
        poc_recipe = (state.metadata or {}).get("poc_recipe", {}) if isinstance(state.metadata, dict) else {}
        cooled_any = False
        for family in state.family_pool:
            cooled_any = apply_family_queue_discipline(
                family,
                state.feedback_history,
                current_step=current_step,
                negative_evidence=negative_evidence,
                poc_recipe=poc_recipe,
            ) or cooled_any
        return cooled_any

    @staticmethod
    def _update_runtime_stage(state: CyberGymState) -> None:
        state.runtime_stage = advance_stage(
            current_stage=state.runtime_stage,
            current_step=int(getattr(state, "current_step", 0) or 0),
            max_steps=int(getattr(state, "max_steps", 0) or 0),
            family_pool=state.family_pool,
            candidate_queue=[*state.ready_pocs, *state.candidate_queue],
            feedback_history=state.feedback_history,
        )

    @staticmethod
    def _update_family_from_insight(
        family: FamilyRecord,
        judgement: Dict[str, Any],
    ) -> None:
        action = str(judgement.get("suggested_action") or "")
        reason = str(judgement.get("reason") or "")
        revision = str(judgement.get("hypothesis_revision") or "").strip()
        if revision:
            family.hypothesis = revision
        if action in {"expand_family", "keep_family_active", "branch_family"}:
            previous_state = family.state
            family.state = "revived" if previous_state in {"cooldown", "retired"} else "active"
            if family.state == "revived":
                family.revive_reason = reason
            return
        if action == "cooldown_family":
            family.state = "cooldown"
            family.cooldown_reason = reason
            return
        if action == "retire_family":
            family.state = "retired"
            family.retire_reason = reason

    @staticmethod
    def _latest_feedback_action(state: CyberGymState) -> str:
        if not state.hot_feedback_window:
            return ""
        return str(state.hot_feedback_window[-1].suggested_action or "")

    @staticmethod
    def _select_candidate_family(state: CyberGymState) -> FamilyRecord | None:
        stage = str(state.runtime_stage or "bootstrap")
        negative_evidence = list(
            (state.metadata or {}).get("negative_evidence", [])
            if isinstance(state.metadata, dict) else []
        )
        # Pre-compute family blocking from negative evidence
        blocked_family_ids: set[str] = set()
        for ev in negative_evidence:
            if ev.get("ttl", 0) <= 0:
                continue
            fid = str(ev.get("family_id", ""))
            if not fid:
                continue
            kind = ev.get("kind", "")
            if kind in ("path_reached_no_trigger", "no_crash_unknown"):
                # Count same-family no-trigger evidences
                same_count = sum(
                    1 for e2 in negative_evidence
                    if e2.get("family_id") == fid
                    and e2.get("kind") in ("path_reached_no_trigger", "no_crash_unknown")
                    and e2.get("ttl", 0) > 0
                )
                if same_count >= 3:
                    blocked_family_ids.add(fid)

        if stage in {"bootstrap", "exploration"}:
            desired_states = ("new", "active", "revived")
        elif stage == "recovery":
            desired_states = ("revived", "cooldown", "active", "new")
        elif stage == "endgame":
            desired_states = ("active", "revived", "new")
        else:
            desired_states = ("active", "revived", "new")

        for desired_state in desired_states:
            for family in state.family_pool:
                if family.state == desired_state and family.family_id not in blocked_family_ids:
                    return family
            # If all families in this state are blocked, try anyway (don't hard-block)
            if blocked_family_ids:
                for family in state.family_pool:
                    if family.state == desired_state:
                        return family
        return None

    @staticmethod
    def _candidate_budget_for_stage(runtime_stage: str) -> int:
        if runtime_stage == "expansion":
            return 2
        return 1

    @staticmethod
    def _prune_retired_family_candidates(state: CyberGymState) -> None:
        retained: List[CandidateRecord] = []
        for candidate in state.candidate_queue:
            family = CandidateFamilyMixin._find_family(state, candidate.family_id)
            if family is not None and family.state == "retired":
                continue
            retained.append(candidate)
        state.candidate_queue = retained

    @staticmethod
    def _direct_candidate_family_id() -> str:
        return "direct-main"

    @staticmethod
    def _candidate_record_from_path(
        path: str,
        *,
        family_id: str,
        ready_to_submit: bool = True,
        workspace_root: str = "",
    ) -> CandidateRecord:
        normalized_path = str(path or "").strip()
        fingerprint = CandidateFamilyMixin._file_fingerprint(normalized_path, workspace_root=workspace_root)
        candidate_id = "direct:" + hashlib.sha1(normalized_path.encode("utf-8")).hexdigest()[:12]
        return CandidateRecord(
            candidate_id=candidate_id,
            family_id=family_id,
            file_path=normalized_path,
            content_fingerprint=fingerprint,
            mutation_summary="direct_candidate",
            expected_signal="submit_for_feedback",
            novelty_note="direct_tool_output",
            base_seed="",
            generation_method="direct_tool_output",
            ready_to_submit=ready_to_submit,
            priority=0,
            producer_agent="main_agent",
            fingerprint_mode="artifact",
            artifact_sha256=fingerprint,
        )

    # Maximum number of PoCs that can be registered in a single call.
    # Prevents the ready_pocs queue from being flooded with dozens of
    # candidates at once (e.g., a BASH command generating 43 variants).
    _MAX_DIRECT_CANDIDATES_PER_STEP = 5

    def _register_direct_candidates(
        self,
        state: CyberGymState,
        paths: List[str],
    ) -> None:
        ordered_paths: List[str] = []
        seen: set[str] = set()
        for raw in paths:
            cleaned = self._normalize_ready_poc_path(state, str(raw or ""))
            if not cleaned:
                continue
            if cleaned in seen:
                continue
            seen.add(cleaned)
            ordered_paths.append(cleaned)
        if not ordered_paths:
            return

        # Cap: only register the first N candidates per call to avoid
        # flooding the queue with too many variants at once.
        max_per_step = CandidateFamilyMixin._MAX_DIRECT_CANDIDATES_PER_STEP
        existing_ready = len(state.ready_pocs)
        remaining_capacity = max(0, max_per_step - existing_ready)
        if remaining_capacity <= 0:
            return
        ordered_paths = ordered_paths[:remaining_capacity]

        family_id = self._direct_candidate_family_id()
        submitted_fingerprints = list(state.submitted_fingerprints or state.metadata.get("submitted_candidate_fingerprints", []) or [])
        ready_fingerprints = {item.content_fingerprint for item in state.ready_pocs}
        for path in ordered_paths:
            candidate = self._candidate_record_from_path(
                path,
                family_id=family_id,
                ready_to_submit=True,
                workspace_root=state.workspace_root,
            )
            if candidate.content_fingerprint in ready_fingerprints:
                continue
            if enqueue_candidate(
                state.ready_pocs,
                candidate,
                submitted_fingerprints=submitted_fingerprints,
            ):
                ready_fingerprints.add(candidate.content_fingerprint)
        if state.ready_pocs:
            state.candidate_required = False

    def _register_pocs_from_output_dir(self, state: CyberGymState) -> None:
        poc_dir = self._poc_output_dir_path(state)
        if not poc_dir.exists() or not poc_dir.is_dir():
            return
        paths: List[str] = []
        for file_path in sorted(poc_dir.iterdir(), key=lambda item: item.name):
            if not file_path.is_file():
                continue
            if file_path.stat().st_size <= 0:
                continue
            display = self._display_path(str(file_path), state=state)
            if self._normalize_ready_poc_path(state, display):
                paths.append(display)
        if paths:
            self._register_direct_candidates(state, paths)

    @staticmethod
    def _drain_candidate_queue_to_ready_pocs(state: CyberGymState) -> None:
        submitted_fingerprints = {
            str(item or "").strip()
            for item in (state.submitted_fingerprints or state.metadata.get("submitted_candidate_fingerprints", []) or [])
            if str(item or "").strip()
        }
        ready_fingerprints = {
            str(getattr(item, "content_fingerprint", "") or "").strip()
            for item in state.ready_pocs
            if str(getattr(item, "content_fingerprint", "") or "").strip()
        }
        cooled_family_ids = {
            str(getattr(family, "family_id", "") or "").strip()
            for family in state.family_pool
            if getattr(family, "state", "") == "cooldown"
            and str(getattr(family, "family_id", "") or "").strip()
        }
        negative_evidence = list(
            (state.metadata or {}).get("negative_evidence", [])
            if isinstance(state.metadata, dict) else []
        )
        policy = SubmitQueuePolicy(
            submitted_fingerprints=submitted_fingerprints,
            queued_fingerprints=set(ready_fingerprints),
            cooled_family_ids=cooled_family_ids,
            negative_evidence=negative_evidence,
        )
        retained: List[CandidateRecord] = []
        for candidate in list(state.candidate_queue or []):
            accepted, reason = policy.accept(candidate)
            if not accepted:
                if reason in {"not_ready", "family_cooldown"}:
                    retained.append(candidate)
                continue
            state.ready_pocs.append(candidate)
        state.ready_pocs.sort(key=lambda item: item.priority, reverse=True)
        state.candidate_queue = retained
        if state.ready_pocs:
            state.candidate_required = False

    @staticmethod
    def _next_branch_family_id(state: CyberGymState, parent_family_id: str) -> str:
        index = 1
        existing_ids = {family.family_id for family in state.family_pool}
        while True:
            candidate_id = f"{parent_family_id}.branch{index}"
            if candidate_id not in existing_ids:
                return candidate_id
            index += 1

    def _branch_family(
        self,
        state: CyberGymState,
        family: FamilyRecord,
        judgement: Dict[str, Any],
    ) -> FamilyRecord:
        branched = FamilyRecord(
            family_id=self._next_branch_family_id(state, family.family_id),
            family_name=family.family_name,
            parent_family_id=family.family_id,
            state="new",
            hypothesis=str(judgement.get("hypothesis_revision") or family.hypothesis),
            generation_axes=list(family.generation_axes),
        )
        state.family_pool.append(branched)
        return branched

    @staticmethod
    def _file_content_fingerprint(path: str) -> str:
        try:
            candidate_path = Path(path)
        except (TypeError, ValueError):
            return ""
        if not candidate_path.is_file():
            return ""
        return "sha256:" + hashlib.sha256(candidate_path.read_bytes()).hexdigest()

    @staticmethod
    def _path_or_file_fingerprint(path: str) -> str:
        file_fingerprint = CandidateFamilyMixin._file_content_fingerprint(path)
        if file_fingerprint:
            return file_fingerprint
        normalized = str(path or "").strip()
        if not normalized:
            return ""
        return "path:" + normalized

    @staticmethod
    def _file_fingerprint(path: str, workspace_root: str = "") -> str:
        if workspace_root and path and not Path(str(path)).is_absolute():
            candidate = Path(workspace_root) / str(path)
            file_fingerprint = CandidateFamilyMixin._file_content_fingerprint(str(candidate))
            if file_fingerprint:
                return file_fingerprint
        return CandidateFamilyMixin._path_or_file_fingerprint(path)

    @staticmethod
    def _candidate_fingerprint_for_path(state: CyberGymState, path: str) -> str:
        raw = str(path or "").strip()
        if not raw:
            return ""
        candidates = [Path(raw)]
        if not candidates[0].is_absolute() and state.workspace_root:
            candidates.insert(0, Path(state.workspace_root) / raw)
        for candidate_path in candidates:
            fingerprint = CandidateFamilyMixin._file_content_fingerprint(str(candidate_path))
            if fingerprint:
                return fingerprint
        return CandidateFamilyMixin._path_or_file_fingerprint(raw)


# ---------------------------------------------------------------------------
# _select_family_evidence_safe  (was @staticmethod on agent)
# ---------------------------------------------------------------------------

def select_family_evidence_safe(
    family_name: str,
    evidence_index: Dict[str, Any],
) -> Dict[str, object]:
    try:
        return select_family_evidence(family_name, evidence_index or {})
    except ValueError as exc:
        if "unknown family_name" not in str(exc):
            raise

    paths: List[str] = []
    seen: set[str] = set()
    source = evidence_index if isinstance(evidence_index, dict) else {}
    for key in ("parser_paths", "entrypoints", "seed_paths", "field_paths"):
        values = source.get(key)
        if not isinstance(values, list):
            continue
        for item in values:
            if isinstance(item, dict):
                item = item.get("path")
            path = "" if item is None else str(item).strip()
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)
            if len(paths) >= 4:
                return {"family_name": family_name, "paths": paths}
    return {"family_name": family_name, "paths": paths}
