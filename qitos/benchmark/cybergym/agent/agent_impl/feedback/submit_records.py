"""Submit record methods — recording/persisting submit output.

Extracted from FeedbackMixin to reduce mixin.py size.
All functions take an ``agent`` parameter for methods that need
access to mixin helpers (_display_path, _candidate_fingerprint_for_path, etc.).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from ...state import CyberGymState

from ...family_runtime import (
    FeedbackRecord,
    FailureRecord,
    FailureType,
    retain_hot_feedback,
)
from ...context import PROJECT_ARTIFACT_ROOT
from ..core.constants import REPEATED_FAILURE_REFLECTION_THRESHOLD


def submitted_candidate_context(
    agent: Any,
    state: CyberGymState,
    metadata: Dict[str, Any] | None,
) -> Dict[str, Any]:
    metadata = metadata or {}
    action_args = agent._metadata_action_args(metadata)

    submitted_path = str(
        metadata.get("poc_path")
        or action_args.get("poc_path")
        or ""
    )
    submitted_fingerprint = str(
        metadata.get("content_fingerprint")
        or agent._candidate_fingerprint_for_path(state, submitted_path)
        or ""
    )

    candidate_id = str(metadata.get("candidate_id") or "")
    family_id = str(metadata.get("family_id") or "")
    matched_ready_index: Optional[int] = None

    for index, candidate in enumerate(state.ready_pocs):
        if (
            (candidate_id and candidate_id == candidate.candidate_id)
            or agent._candidate_paths_match(state, submitted_path, candidate.file_path)
            or (
                submitted_fingerprint
                and submitted_fingerprint == candidate.content_fingerprint
            )
        ):
            candidate_id = candidate_id or candidate.candidate_id
            family_id = family_id or candidate.family_id
            submitted_path = submitted_path or candidate.file_path
            submitted_fingerprint = submitted_fingerprint or candidate.content_fingerprint
            matched_ready_index = index
            break

    if not candidate_id and submitted_path:
        candidate_id = "direct:" + hashlib.sha1(submitted_path.encode("utf-8")).hexdigest()[:12]
    if not family_id:
        family_id = agent._direct_candidate_family_id()

    return {
        "poc_path": submitted_path,
        "candidate_id": candidate_id,
        "family_id": family_id,
        "content_fingerprint": submitted_fingerprint,
        "matched_ready_index": matched_ready_index,
    }


def append_feedback_record(
    agent: Any,
    state: CyberGymState,
    output: Dict[str, Any],
    metadata: Dict[str, Any] | None,
    submit_context: Dict[str, Any] | None = None,
) -> None:
    metadata = metadata or {}
    submit_context = submit_context or submitted_candidate_context(agent, state, metadata)
    candidate_id = str(submit_context.get("candidate_id") or "")
    family_id = str(submit_context.get("family_id") or "")
    content_fingerprint = str(submit_context.get("content_fingerprint") or "")
    submitted_path = str(submit_context.get("poc_path") or "")
    poc_id = str(output.get("poc_id") or "")
    raw_output = feedback_output_text(output)
    storage_path = persist_submit_output(agent, state, poc_id, raw_output, poc_path=submitted_path)
    # Archive a versioned snapshot of the submitted PoC
    if submitted_path and state.workspace_root:
        archive_poc_version(agent, state, submitted_path)
    exit_code = feedback_exit_code(output)
    verdict = agent._verification_outcome_label(output)
    suggested_action = agent._verdict_to_action(verdict, output)

    state.feedback_history.append(
        FeedbackRecord(
            candidate_id=candidate_id,
            family_id=family_id,
            poc_id=poc_id,
            poc_path=submitted_path,
            exit_code=exit_code,
            output=raw_output,
            storage_path=storage_path,
            assessment=verdict,
            suggested_action=suggested_action,
        )
    )
    state.hot_feedback_window = retain_hot_feedback(state.feedback_history, max_items=3)
    failure_record = agent._derive_failure_record(output, submit_context)
    if failure_record is not None:
        state.failure_history.append(failure_record)
    if candidate_id and poc_id:
        state.submitted_candidate_index[candidate_id] = poc_id
    state.last_submitted_poc_path = str(
        submitted_path
        or agent._metadata_action_args(metadata).get("poc_path")
        or ""
    )
    state.last_submitted_poc_hash = str(content_fingerprint or "")
    if content_fingerprint:
        submitted = state.metadata.setdefault("submitted_candidate_fingerprints", [])
        if content_fingerprint not in submitted:
            submitted.append(content_fingerprint)
        if content_fingerprint not in state.submitted_fingerprints:
            state.submitted_fingerprints.append(content_fingerprint)
    update_family_feedback_state(state, family_id, verdict)
    ready_index = submit_context.get("matched_ready_index")
    if isinstance(ready_index, int) and 0 <= ready_index < len(state.ready_pocs):
        state.ready_pocs.pop(ready_index)

        # Batch drain: on MISS, remove all remaining same-family PoCs
        # to prevent the 22->21->20... one-at-a-time drain loop.
        vul_exit = output.get("vul_exit_code")
        is_miss = (vul_exit is None or vul_exit == 0) and not output.get("accepted")
        if is_miss and family_id:
            before = len(state.ready_pocs)
            state.ready_pocs = [
                poc for poc in state.ready_pocs
                if str(getattr(poc, "family_id", "") or "") != family_id
            ]
            removed = before - len(state.ready_pocs)
            if removed > 0:
                notes = state.metadata.setdefault("_recent_notes", [])
                notes.append(
                    f"batch_drain: removed {removed} same-family PoCs after MISS"
                )
                state.metadata["_recent_notes"] = notes[-6:]


def persist_submit_output(
    agent: Any,
    state: CyberGymState,
    poc_id: str,
    raw_output: str,
    *,
    poc_path: str = "",
) -> str:
    if not poc_id:
        return ""
    workspace_root = str(state.workspace_root or getattr(agent, "workspace_root", "") or "").strip()
    if not workspace_root:
        return ""
    project_root = Path(workspace_root) / PROJECT_ARTIFACT_ROOT
    feedback_dir = project_root / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    path = feedback_dir / f"{poc_id}.txt"
    if poc_path:
        content = f"poc_path: {agent._display_path(poc_path, state=state)}\n\n{raw_output}"
    else:
        content = raw_output
    path.write_text(content, encoding="utf-8")
    display_path = agent._display_path(str(path), state=state)
    append_project_artifact_index(
        agent=agent,
        state=state,
        kind="feedback",
        path=display_path,
        step_id=int(getattr(agent, "_runtime_step_id", getattr(state, "current_step", 0)) or 0),
        original_chars=len(content),
    )
    return display_path


def archive_poc_version(
    agent: Any,
    state: CyberGymState,
    poc_path: str,
) -> str:
    """Copy submitted PoC to a versioned archive directory.

    Archives preserve the original file suffix (.pcap, .png, .b2frame,
    etc.) and are stored under ``.cybergym/poc_archive/`` so that
    historical PoC files survive being overwritten by subsequent writes.
    """
    import shutil

    workspace = Path(state.workspace_root)
    source = workspace / poc_path if not Path(poc_path).is_absolute() else Path(poc_path)
    if not source.exists():
        return ""

    archive_dir = workspace / ".cybergym" / "poc_archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    # Version based on poc_attempts count (+1 because poc_attempts
    # hasn't been incremented yet when append_feedback_record runs).
    version = state.poc_attempts + 1
    # Preserve original suffix (could be .pcap, .png, .b2frame, etc.)
    suffix = source.suffix
    archived_name = f"poc_v{version}{suffix}"
    dest = archive_dir / archived_name

    try:
        shutil.copy2(str(source), str(dest))
        return str(dest.relative_to(workspace))
    except (OSError, ValueError):
        return ""


def append_project_artifact_index(
    *,
    agent: Any,
    state: CyberGymState,
    kind: str,
    path: str,
    step_id: int,
    original_chars: int,
) -> None:
    workspace_root = str(state.workspace_root or getattr(agent, "workspace_root", "") or "").strip()
    if not workspace_root:
        return
    try:
        project_root = Path(workspace_root) / PROJECT_ARTIFACT_ROOT
        project_root.mkdir(parents=True, exist_ok=True)
        index_path = project_root / "INDEX.md"
        if not index_path.exists():
            index_path.write_text(
                "# Externalized Context Index\n\n"
                "Paths below are relative to the task workspace.\n",
                encoding="utf-8",
            )
        line = (
            f"- kind={kind} step={int(step_id)} "
            f"path={path} chars={int(original_chars)}\n"
        )
        if line.rstrip("\n") in index_path.read_text(encoding="utf-8").splitlines():
            return
        with index_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:
        return


def feedback_output_text(output: Dict[str, Any]) -> str:
    return str(
        output.get("raw_output")
        or output.get("output")
        or output.get("error")
        or ""
    )


def feedback_exit_code(output: Dict[str, Any]) -> int:
    exit_code = output.get("exit_code")
    if exit_code is None:
        exit_code = output.get("vul_exit_code")
    if exit_code is None:
        return -1
    return int(exit_code)


def signal_rank(signal: str) -> int:
    order = {
        "submission_error": 0,
        "submitted": 1,
        "no_trigger": 2,
        "no_crash_unknown": 2,
        "execution_signal_only": 3,
        "too_broad": 4,
        "candidate_rejected": 4,
        "candidate_triggered": 5,
    }
    return order.get(str(signal or ""), -1)


def update_family_feedback_state(
    state: CyberGymState,
    family_id: str,
    verdict: str,
) -> None:
    if not family_id:
        return
    for family in state.family_pool:
        if family.family_id != family_id:
            continue
        family.submit_count += 1
        if signal_rank(verdict) >= signal_rank(family.best_observed_signal):
            family.best_observed_signal = verdict
        if family.state == "new":
            family.state = "active"
        break


def extract_verification_hints(result: Any) -> List[str]:
    if not isinstance(result, dict):
        return []

    text_parts = [
        str(result.get("raw_output") or ""),
        str(result.get("vul_stderr") or ""),
    ]
    hints: List[str] = []
    seen = set()
    for text in text_parts:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            lower = line.lower()
            if (
                lower.startswith("info: seed:")
                or lower.startswith("info: loaded ")
                or lower.startswith("running:")
                or lower.startswith("executed ")
                or lower.startswith("***")
                or "fuzzing was not performed" in lower
            ):
                continue
            if (
                lower.startswith("warning:")
                or lower.startswith("error:")
                or "addresssanitizer" in lower
                or "undefinedbehavior" in lower
                or "runtime error:" in lower
                or "segmentation fault" in lower
                or "assertion" in lower
            ):
                if line not in seen:
                    seen.add(line)
                    hints.append(line)
    return hints[:4]


def update_failure_counters(
    agent: Any,
    state: CyberGymState,
    result: Dict[str, Any],
) -> None:
    if state.is_verified():
        state.repeated_failure_signature = ""
        state.repeated_failure_count = 0
        state.metadata.pop("needs_reflection_nudge", None)
        return

    hints = extract_verification_hints(result)
    signature = json.dumps(
        {
            "vul_exit_code": result.get("vul_exit_code"),
            "verification_status": result.get("verification_status"),
            "hints": hints[:3],
        },
        sort_keys=True,
    )

    if signature == state.repeated_failure_signature:
        state.repeated_failure_count += 1
    else:
        state.repeated_failure_signature = signature
        state.repeated_failure_count = 1
    if (
        state.repeated_failure_count >= REPEATED_FAILURE_REFLECTION_THRESHOLD
        and not agent._failure_reflection_acknowledged(state)
        and not agent._failure_reflection_on_cooldown(state)
    ):
        state.metadata["needs_reflection_nudge"] = True
    if state.repeated_failure_count >= 3:
        agent._maybe_set_loop_reminder(state, f"repeated-failure:{signature}")


def record_verification_attempt(
    agent: Any,
    state: CyberGymState,
    result: Dict[str, Any],
    *,
    poc_path: str = "",
) -> None:
    hints = extract_verification_hints(result)
    score = 0
    vul = result.get("vul_exit_code")
    if result.get("accepted") is True:
        score = 2
    elif vul is not None and vul != 0:
        score = 1
    state.verification_history.append(
        {
            "poc_path": poc_path,
            "score": score,
            "vul_exit_code": vul,
            "verification_status": result.get("verification_status"),
            "hints": hints[:3],
        }
    )
    state.verification_history = state.verification_history[-8:]


def update_best_poc_for_path(
    state: CyberGymState,
    score: int,
    poc_path: str,
) -> None:
    if score > state.best_poc_score and poc_path:
        state.best_poc_score = score
        state.best_poc_path = poc_path
