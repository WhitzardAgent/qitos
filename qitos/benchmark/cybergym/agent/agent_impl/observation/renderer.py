"""Observation building mixin — prompt construction, state rendering, memory sections."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional

if TYPE_CHECKING:
    from qitos.core.observation import Observation
    from ...state import CyberGymState

from ..core.constants import (
    POC_OUTPUT_DIR,
    CANDIDATE_REQUIRED_REMINDER_TEXT,
)
from ...analysis.ir_renderer import IRRenderer
from .memory import MemoryMixin
from .sections import SectionMixin
from .validation import ValidationMixin


class ObservationResult(NamedTuple):
    """Structured result from _render_observation()."""
    text: str
    sections: Dict[str, str]



class ObservationMixin(MemoryMixin, SectionMixin):
    """Observation building — prompts, state blocks, memory, tool lines."""

    @staticmethod
    def _summarize_tool_observation(short_name: str, output: Any) -> str:
        name = str(short_name or "tool")
        if isinstance(output, dict):
            status = str(output.get("status") or "").strip()
            if name == "submit_poc":
                verdict = output.get("accepted")
                vul_exit = output.get("vul_exit_code")
                if verdict is True:
                    return f"- submit_poc: result=accepted vul_exit={vul_exit}"
                if output.get("status") == "error":
                    return f"- submit_poc: result=submission_error"
                return f"- submit_poc: result=submitted vul_exit={vul_exit}"
            if name == "bash":
                rc = output.get("returncode")
                command = str(output.get("command") or "")
                return f"- bash: rc={rc} {command}".rstrip()
            if name in ("find_symbols", "callsite_search"):
                query = str(output.get("query") or output.get("symbol") or "")
                count = output.get("result_count") or output.get("callsite_count") or 0
                results = output.get("results", [])
                preview_lines = []
                for r in results:
                    kind = str(r.get("kind", ""))
                    path_r = str(r.get("path", ""))
                    ln = r.get("line_number", "")
                    sig = str(r.get("signature") or r.get("preview", ""))
                    preview_lines.append(f"{kind}:{path_r}:{ln} {sig}")
                preview = " | ".join(preview_lines)
                return f"- {name}: query={query} count={count} top=[{preview}]"
            if name == "read":
                path_r = str(output.get("path") or "")
                offset_r = output.get("offset", 0) or 0
                total = output.get("total_lines", 0)
                has_more = output.get("has_more") or output.get("truncated")
                content = str(output.get("content") or "")
                line_count = content.count("\n") + 1 if content else 0
                range_str = f"L{offset_r+1}-{offset_r+line_count}"
                if total:
                    range_str += f"/{total}"
                more_str = " [TRUNCATED]" if has_more else ""
                return f"- read: {path_r} {range_str}{more_str}"
            if name == "grep":
                pattern = str(output.get("pattern") or "")
                mode = str(output.get("mode") or "")
                count = output.get("match_count") or output.get("file_count") or 0
                if mode == "files_with_matches":
                    filenames = output.get("filenames", [])
                    files_preview = ", ".join(filenames)
                    return f"- grep: pattern={pattern} mode=files count={count} files=[{files_preview}]"
                else:
                    return f"- grep: pattern={pattern} mode={mode} count={count}"
            if name in ("glob", "corpus_inspect", "file_info"):
                count = output.get("result_count") or output.get("file_count") or 0
                if name == "file_info":
                    path_f = str(output.get("path") or "")
                    detail = f" type={output.get('file_type', '')}"
                    return f"- file_info: {path_f}{detail}"
                return f"- {name}: count={count}"
            path = str(output.get("path") or "").strip()
            if status and path:
                return f"- {name}: {status} {path}"
            if status:
                return f"- {name}: {status}"
            if path:
                return f"- {name}: {path}"
        if output:
            return f"- {name}: {str(output)}"
        return ""

    def _task_spec_summary_lines(self, state: CyberGymState) -> List[str]:
        lines: List[str] = []
        if state.expected_signal and state.expected_signal != "unknown":
            lines.append(f"- Expected Signal: `{state.expected_signal}`")
        if state.input_vector_hints:
            lines.append(f"- Input Hints: {', '.join(state.input_vector_hints)}")
        if state.likely_entrypoints:
            lines.append(f"- Likely Entrypoints: {', '.join(state.likely_entrypoints)}")
        if state.task_spec_confidence and state.task_spec_confidence < 0.5:
            lines.append(f"- Task-Spec Confidence: {state.task_spec_confidence:.2f}")
        return lines

    def _render_task_context_sections(self, state: CyberGymState, *, include_repo_details: bool = False) -> List[str]:
        """Render shared Task Context + Patch Diff + Task Spec + Memory sections.

        Used by both _build_initial_brief and _build_observation_packet.
        """
        sections: List[str] = []
        context_lines: List[str] = []
        current_phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")
        if state.vulnerability_description:
            # P20: render FULL description — it is the single most important
            # Level-1 signal (no patch.diff available).  A 260-char cap was
            # dropping trigger conditions and root-cause details.
            desc_text = state.vulnerability_description.replace("\n", " ")
            context_lines.append(
                f"- Vulnerability: {desc_text}"
            )
        if state.bug_type:
            context_lines.append(f"- Bug Type: `{state.bug_type}`")
        crash_type_prior = str((state.metadata or {}).get("crash_type_prior", "") or getattr(state, "crash_type", "") or "")
        if crash_type_prior:
            context_lines.append(f"- Crash Type: `{crash_type_prior}`")
        if state.poc_strategy:
            context_lines.append(f"- Strategy: `{state.poc_strategy}`")
        if hasattr(state, "input_format") and state.input_format and state.input_format.format_type:
            fmt = state.input_format
            fmt_line = f"- Input Format: `{fmt.format_type}`"
            if fmt.entry_point:
                status = "confirmed" if fmt.confirmed else "inferred"
                fmt_line += f" | Entry: `{fmt.entry_point}` ({status})"
            if fmt.input_path:
                fmt_line += f" | Input via: `{fmt.input_path}`"
            if fmt.magic_bytes:
                fmt_line += f" | Magic: `{fmt.magic_bytes}`"
            context_lines.append(fmt_line)
        if include_repo_details:
            if state.repo_dir:
                context_lines.append(f"- Source Root: `{self._display_path(state.repo_dir, state=state)}`")
            if state.corpus_files:
                context_lines.append(f"- Corpus: {', '.join(state.corpus_files)}")
        if state.harness_entry_confirmed or state.metadata.get("harness_entry_confirmed"):
            context_lines.append("- Harness entry: **confirmed** (LLVMFuzzerTestOneInput found in source)")
        # Input path: show first callee from call graph
        reachable_cands = getattr(state, "reachable_function_candidates", None) or []
        if reachable_cands and current_phase in ("ingestion", "exploration") and not state.confirmed_sink_candidates():
            first_callee = reachable_cands[0].get("function", "") if reachable_cands else ""
            first_depth = reachable_cands[0].get("depth", 0) if reachable_cands else 0
            if first_callee and first_depth == 1:
                context_lines.append(f"- First callee: `{first_callee}` (direct input consumer)")
        # Affected component from description analysis
        affected = str(getattr(state, "affected_component", "") or "").strip()
        if affected:
            context_lines.append(f"- Affected component: `{affected}`")
        if context_lines:
            sections.extend(["## Task Context", *context_lines])
        # Vague description guidance in exploration phase
        if (state.task_spec_confidence < 0.4
                and getattr(state, "current_phase", "") == "exploration"):
            vague_lines = [
                "- Description is vague — use broad grep searches with keywords "
                "from the description to locate the vulnerable code before reading deeply."
            ]
            if getattr(state, "search_anchors", None):
                anchors = ", ".join(f"`{s}`" for s in state.search_anchors[:6])
                vague_lines.append(f"  Search targets: {anchors}")
            if getattr(state, "sink_candidates", None):
                visible = [c for c in state.sink_candidates
                           if c.status != "eliminated"]
                top = [c.function for c in sorted(visible, key=lambda x: -x.confidence)[:3]]
                vague_lines.append(f"  Top sink candidates: {', '.join(f'`{f}`' for f in top)}")
            sections.extend(["## Vague Description Guidance", *vague_lines])
        harness_lines = self._harness_resolution_lines(state)
        if harness_lines:
            sections.extend(["## Harness Resolution", *harness_lines])
        # Crash Type Assessment — prompt LLM to infer crash type and form mental model
        current_phase = str(getattr(state, "current_phase", "") or "")
        if current_phase == "ingestion" and not (state.metadata or {}).get("crash_type_prior") and not getattr(state, "crash_type", ""):
            sections.append(
                "## Vulnerability Analysis (Ingestion Phase)\n"
                "**You MUST classify the crash type before proceeding.**\n\n"
                "1. **Crash type** (MANDATORY): What ASAN crash type is this? "
                "(Heap-buffer-overflow, Heap-use-after-free, Heap-double-free, "
                "Stack-buffer-overflow, Global-buffer-overflow, "
                "Use-of-uninitialized-value, Index-out-of-bounds, SEGV, or UNKNOWN)\n"
                "   **Infer the crash type now and remember it for later analysis.** "
                "This feeds into crash-type-aware navigation scoring.\n\n"
                "2. **Expected dangerous operations**: Based on the crash type, what operations "
                "should you look for in the code?\n"
                "   - For UAF: free/delete/realloc → then access without null-check\n"
                "   - For buffer overflow: memcpy/memmove/strcpy/read → with missing length check\n"
                "   - For double-free: free/delete called twice on same pointer\n"
                "   - For uninit: variable used before being set in a conditional branch\n\n"
                "3. **Key information from description**: Extract function names, file names, "
                "module names, parameter names, trigger conditions. Use grep to search for these "
                "in the codebase — description names may differ from code names "
                "(e.g., 'USER NAME' in description → `user_name` in code).\n\n"
                "4. **Input path**: How does the fuzz driver consume input? "
                "Read the harness entry function to determine: "
                "direct data/size passing? temp file? structured split? magic header check?\n\n"
                "5. **Sink hypothesis**: Based on the above, which function is most likely the crash site? "
                "Call `sink(function, location?, evidence?, confidence?)` with your best hypothesis.\n\n"
                "This analysis guides your exploration. You can revise your hypothesis later "
                "based on code reading and ASAN feedback."
            )
        # Sink Candidates — always shown, even when empty
        sink_candidates = [c for c in (getattr(state, "sink_candidates", None) or [])
                           if c.status != "eliminated"]
        auto_sources = {"static_navigation", "graph_auto_deepen"}
        if sink_candidates:
            sink_lines = [f"- Sink Candidates ({len(sink_candidates)}):"]
            for c in sorted(sink_candidates, key=lambda x: -x.confidence)[:5]:
                from ...analysis.vuln_patterns import is_entry_point_function
                is_entry = is_entry_point_function(c.function)
                conf_label = "entry" if is_entry else "high" if c.confidence >= 0.7 else "medium" if c.confidence >= 0.4 else "low"
                status = f" [{c.status}]" if c.status != "candidate" else ""
                # Entry-point visual distinction
                if is_entry:
                    status += " [ENTRY — NOT CRASH SITE]"
                # Auto-discovered tag — makes these visually distinct from model-confirmed
                auto_prefix = "[AUTO] " if c.source in auto_sources else ""
                # Graph metadata enrichment tags
                meta = c.metadata or {}
                tags = []
                if meta.get("graph_validated"):
                    tags.append("graph-validated")
                if meta.get("reachable_from_entry"):
                    tags.append("reachable")
                if meta.get("description_anchor_stale"):
                    tags.append("STALE")
                risk_count = int(meta.get("risk_signal_count", 0) or 0)
                if risk_count > 0:
                    tags.append(f"{risk_count} risk{'s' if risk_count != 1 else ''}")
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                if bool(meta.get("requires_review")):
                    label = "STATIC LEAD" if c.source in auto_sources else "WEAK PRIOR"
                    status += f" [{label}—REQUIRES MODEL CONFIRMATION]"
                sink_lines.append(f"  {auto_prefix}`{c.function}` ({conf_label} conf){status}{tag_str} — {c.evidence}")
            sections.extend(["## Sink Candidates", *sink_lines])
            # Depth nudge when all sinks are entry-point functions
            from ...analysis.vuln_patterns import is_entry_point_function, CRASH_TYPE_SINK_HINTS
            all_entry = all(is_entry_point_function(c.function) for c in sink_candidates)
            if all_entry and sink_candidates:
                ct_prior = str((state.metadata or {}).get("crash_type_prior", "") or getattr(state, "crash_type", "") or "")
                hints = CRASH_TYPE_SINK_HINTS.get(ct_prior, {})
                hint_text = hints.get("hint", "Trace the call chain deeper from the entry point.")
                kw_list = list(hints.get("keywords", {}).keys())[:4]
                kw_hint = f" Look for functions with: {', '.join(kw_list)}." if kw_list else ""
                sections.append(
                    "⚠ DEPTH NUDGE: You recorded entry-point functions only. "
                    "These are NOT the crash sinks — the actual crash is typically 3-8 calls deeper. "
                    + hint_text + kw_hint +
                    " Use callsite_search or read to trace deeper."
                )
        else:
            checkpoint_active = getattr(state, "pending_sink_checkpoint", False)
            if checkpoint_active:
                sections.extend([
                    "## Sink Candidates",
                    "- **SINK HYPOTHESIS NEEDED** — You haven't recorded a sink candidate yet. "
                    "Call `sink(function, location?, evidence?, confidence?)` "
                    "to record your best hypothesis. You may proceed without one, but a recorded sink helps focus.",
                    "- If you have identified a vulnerable function in your reasoning, record it. "
                    "You can also proceed with write/bash/submit if you have a working hypothesis to test.",
                ])
            else:
                sections.extend([
                    "## Sink Candidates",
                    "- None recorded yet. Call `sink(function, location?, evidence?, confidence?)` "
                    "when you identify a vulnerable function. This is REQUIRED before leaving exploration.",
                ])
        # Suggested Sinks — auto-discovered candidates not yet confirmed by model
        model_confirmed = {c.function.lower() for c in (getattr(state, "sink_candidates", []) or [])
                           if c.source == "model_candidate" and c.status != "eliminated"}
        unconfirmed_auto = [c for c in (getattr(state, "sink_candidates", []) or [])
                            if c.source in auto_sources
                            and c.status != "eliminated"
                            and c.function.lower() not in model_confirmed
                            and c.confidence >= 0.5]
        if unconfirmed_auto:
            suggest_lines = ["- Suggested sinks (auto-discovered via static analysis, not yet confirmed):"]
            for c in sorted(unconfirmed_auto, key=lambda x: -x.confidence)[:3]:
                meta = c.metadata or {}
                role = meta.get("role", "")
                risk_signals = meta.get("risk_signals") or [{}]
                risk_desc = risk_signals[0].get("reason", "") if risk_signals else ""
                detail = f" ({role})" if role else ""
                if risk_desc:
                    detail += f" — {risk_desc}"
                suggest_lines.append(
                    f"  `{c.function}`{detail} — "
                    f"Call `sink(\"{c.function}\", evidence)` to confirm."
                )
            sections.extend(["## Suggested Sinks", *suggest_lines])
        # Sink Localization Strategy — show whenever no confirmed sinks
        current_step = getattr(state, "current_step", 0) or 0
        if not state.confirmed_sink_candidates() and current_phase in ("exploration", "investigation"):
            from ...analysis.vuln_patterns import CRASH_TYPE_SINK_HINTS, CRASH_TYPE_MENTAL_MODEL
            ct_prior = str((state.metadata or {}).get("crash_type_prior", "") or getattr(state, "crash_type", "") or "")
            hints = CRASH_TYPE_SINK_HINTS.get(ct_prior, {})
            model = CRASH_TYPE_MENTAL_MODEL.get(ct_prior, {})
            kw_list = list(hints.get("keywords", {}).keys())[:6]
            kw_text = ", ".join(kw_list) if kw_list else "read, parse, decode, get, free, check"
            hint_text = hints.get("hint", "")
            mental_model = model.get("mental_model", "")
            expected_ops = model.get("expected_ops", "")
            search_tip = model.get("search_tip", "")
            fwd_kw = model.get("forward_keywords", kw_text)
            bwd_kw = model.get("backward_keywords", "size, length, bounds, check, limit")
            strategy_lines = [
                "## Sink Localization Strategy",
                f"Crash type: `{ct_prior or 'UNKNOWN'}`.",
            ]
            if mental_model:
                strategy_lines.append(f"**Mental model**: {mental_model}")
            if expected_ops:
                strategy_lines.append(f"**Expected dangerous operations**: {expected_ops}")
            if search_tip:
                strategy_lines.append(f"**Search tip**: {search_tip}")
            strategy_lines.extend([
                f"1. **Forward trace**: From the harness entry, trace the input-consuming call chain. "
                f"Focus on functions named: {fwd_kw}. The actual crash is typically 3-8 calls deeper than the entry.",
                f"2. **Backward trace**: Search for dangerous operations matching the crash type, "
                f"then verify each is on the input path. Look for: {bwd_kw}.",
            ])
            if hint_text:
                strategy_lines.append(f"   Hint: {hint_text}")
            # Show current best lead depth if available
            leads = getattr(state, "sink_search_leads", []) or []
            if leads:
                top_lead = leads[0]
                lead_func = str(top_lead.get("function") or "")
                lead_depth = int(top_lead.get("evidence", {}).get("call_depth", 0) or 0) if isinstance(top_lead.get("evidence"), dict) else 0
                if lead_func:
                    strategy_lines.append(
                        f"   Current top lead: `{lead_func}` (depth {lead_depth}) — "
                        f"{'follow its callees deeper' if lead_depth < 3 else 'verify this is on the crash path'}."
                    )
            sections.extend(strategy_lines)
        # Likely Crash Functions — top results from reachable_functions_from_entry
        reachable_candidates = getattr(state, "reachable_function_candidates", None) or []
        if reachable_candidates and current_phase in ("exploration", "investigation"):
            # Show top 5 candidates not already recorded as sinks
            recorded_funcs = {c.function.lower() for c in (getattr(state, "sink_candidates", []) or [])
                              if c.status != "eliminated"}
            fresh = [c for c in reachable_candidates
                     if c.get("function", "").lower() not in recorded_funcs][:5]
            if fresh:
                crash_lines = [
                    "## Likely Crash Functions (from reachability analysis)",
                    "These functions are reachable from the harness entry, ranked by crash-type relevance. "
                    "Prioritize investigating these before recording your sink candidate.",
                ]
                for c in fresh:
                    depth = c.get("depth", "?")
                    score = c.get("score", 0)
                    why = c.get("why", "")
                    risk_desc = ""
                    risks = c.get("risk_signals") or []
                    if risks:
                        risk_desc = f" | {risks[0].get('kind', '')}: {risks[0].get('expression', '')}"
                    crash_lines.append(
                        f"  `{c.get('function', '?')}` (depth {depth}, score {score:.2f}) — {why}{risk_desc}"
                    )
                sections.extend(crash_lines)
        patch_diff = (state.patch_diff or str(state.metadata.get("patch_diff", "") or "")).strip()
        if patch_diff:
            sections.extend(["## Patch Diff", patch_diff])
        task_spec_lines = self._task_spec_summary_lines(state)
        if task_spec_lines:
            sections.extend(["## Task Spec", *task_spec_lines])
        return sections

    @staticmethod
    def _harness_resolution_lines(state: CyberGymState) -> List[str]:
        candidates = list(getattr(state, "harness_candidates", []) or [])
        resolution = getattr(state, "harness_resolution", None)
        if not candidates and not getattr(state, "submit_harness_targets", None):
            return []
        status = str(getattr(resolution, "status", "unresolved") or "unresolved")
        selected_id = str(getattr(resolution, "selected_candidate_id", "") or "")
        selected = next((item for item in candidates if item.candidate_id == selected_id), None)
        lines = [f"- Status: **{status}**"]
        if selected:
            binary = str(getattr(resolution, "selected_binary", "") or "")
            label = f"`{binary}` → " if binary else ""
            lines.append(
                f"- Selected Harness: {label}`{selected.source_path}:{selected.line}` "
                f"(`{selected.entry_function}`)"
            )
            if selected.reachable_symbols:
                lines.append("- Vulnerability Reachability: " + ", ".join(
                    f"`{item}`" for item in selected.reachable_symbols
                ))
        alternatives = [item for item in candidates if item.candidate_id != selected_id]
        if alternatives:
            rendered = []
            for item in alternatives[:6]:
                names = "/".join(item.binary_names[:2])
                suffix = f" ({names})" if names else ""
                selected_binary = str(getattr(resolution, "selected_binary", "") or "")
                if selected_binary and selected_binary not in item.binary_names:
                    why = "submit-target mismatch"
                elif any("unresolved" in fact for fact in item.evidence):
                    why = "reachability unresolved"
                elif status == "reachability_verified" and not item.reachable_symbols:
                    why = "no source-backed vulnerability path found"
                else:
                    why = item.status
                rendered.append(f"`{item.source_path}:{item.line}`{suffix} [{why}]")
            lines.append("- Alternatives: " + "; ".join(rendered))
        reasons = list(getattr(resolution, "reasons", []) or [])
        conflicts = list(getattr(resolution, "conflicts", []) or [])
        if reasons:
            lines.append("- Evidence: " + "; ".join(reasons[:4]))
        if conflicts:
            lines.append("- Conflicts: " + "; ".join(conflicts[:4]))
        next_action = str(getattr(resolution, "next_action", "") or "")
        if next_action:
            lines.append(f"- Next verification action: {next_action}")
        return lines

    # ------------------------------------------------------------------
    # Section renderers are now in SectionMixin (sections.py)
    # ------------------------------------------------------------------

    def _render_observation(self, state: CyberGymState, *, is_initial: bool = False) -> "ObservationResult":
        """Build the new 6-section observation brief with delta rendering.

        This replaces the old 12-section flat structure from
        _build_initial_brief() and _build_observation_packet().
        The sections are:
        1. Mission — task identity
        2. Current Assessment — confirmed/likely/unknown/rejected
        3. Vulnerability Path — call chain diagram
        4. Required Conditions — PoC-relevant constraints
        5. Experiments — PoC attempts
        6. Next Action — blocking gap + recommendation

        Delta rendering: on subsequent steps, unchanged sections are
        compressed to a single-line marker. Full brief is regenerated at
        step 0, phase transitions, and after compaction.

        Returns an ObservationResult with both the combined text and
        individual sections for TUI metadata storage.
        """
        import hashlib as _hashlib

        current_step = int(getattr(state, "current_step", 0) or 0)
        current_phase = str(getattr(state, "current_phase", "ingestion") or "ingestion")

        # Determine if full brief should be generated (no delta)
        prev_meta = getattr(state, "metadata", {}) or {}
        prev_step = int(prev_meta.get("_obs_last_step", -1) or -1)
        prev_phase = str(prev_meta.get("_obs_last_phase", "") or "")

        # Semantic event triggers — force full refresh on significant state changes
        prev_events = dict(prev_meta.get("_obs_last_events") or {})
        from ..core.runtime_context_contract import get_context_revisions
        current_revisions = get_context_revisions(state)
        previous_revisions = dict(prev_meta.get("_obs_last_revisions") or {})
        cur_n_sinks = len(state.confirmed_sink_candidates())
        cur_n_attempts = int(getattr(state, "poc_attempts", 0) or 0)
        cur_sink_ckpt = bool(getattr(state, "pending_sink_checkpoint", False))
        cur_refl = bool(getattr(state, "pending_reflection", False))
        semantic_event = (
            cur_n_sinks != prev_events.get("n_confirmed_sinks", -1)
            or cur_n_attempts != prev_events.get("n_poc_attempts", -1)
            or (cur_sink_ckpt and not prev_events.get("sink_checkpoint", False))
            or (cur_refl and not prev_events.get("pending_reflection", False))
            or current_revisions != previous_revisions
        )
        force_full = (
            is_initial
            or current_step == 0
            or prev_step < 0  # first call ever
            or current_phase != prev_phase  # phase transition
            or current_step - prev_step > 10  # periodic full refresh
            or semantic_event
        )

        # Build all section content (5-section model)
        vulnerability = self._render_vulnerability(state)
        sink_candidates = self._render_sink_candidates(state)
        constraint_board = self._render_constraint_board(state)
        experiments = self._render_experiments(state)
        task_memory = self._render_task_memory(state)

        # Named sections for delta comparison and TUI storage
        current_sections = {
            "vulnerability": vulnerability,
            "sink_candidates": sink_candidates,
            "constraint_board": constraint_board,
            "experiments": experiments,
            "task_memory": task_memory,
        }

        # Store current sections for next step's delta comparison
        # IMPORTANT: deep-copy previous hashes BEFORE writing, since
        # prev_meta IS state.metadata (mutable dict reference).
        prev_hashes = dict(prev_meta.get("_obs_last_sections") or {})

        prev_meta["_obs_last_step"] = current_step
        prev_meta["_obs_last_phase"] = current_phase
        prev_meta["_obs_last_events"] = {
            "n_confirmed_sinks": cur_n_sinks,
            "n_poc_attempts": cur_n_attempts,
            "sink_checkpoint": cur_sink_ckpt,
            "pending_reflection": cur_refl,
        }
        prev_meta["_obs_last_revisions"] = dict(current_revisions)
        prev_meta["_obs_last_sections"] = {
            k: _hashlib.sha256(v.encode()).hexdigest()[:12] if v else ""
            for k, v in current_sections.items()
        }

        # Context contract: observation has exactly these five top-level
        # sections.  Hashes above remain available for future compact delta
        # rendering, but delta markers/Foundation/Allowed Tools are not emitted
        # as separate model-facing sections.
        combined = "\n\n".join(content for content in current_sections.values() if content)

        return ObservationResult(
            text=combined,
            sections=dict(current_sections),
        )

    def _build_initial_brief(self, state: CyberGymState) -> str:
        return self._render_observation(state, is_initial=True).text

    def _build_observation_packet(
        self,
        state: CyberGymState,
    ) -> str:
        return self._render_observation(state, is_initial=False).text

    def _one_shot_reminder_lines(self, state: CyberGymState) -> List[str]:
        from ..core.constants import NO_CANDIDATE_READ_ACTION_LIMIT

        lines: List[str] = []
        reminder = str(getattr(state, "pending_reminder", "") or "").strip()
        if reminder:
            state.metadata["_one_shot_reminder_rendered"] = True
            lines.extend(
                f"- {line.strip()}"
                for line in reminder.splitlines()
                if line.strip()
            )
        # Soft budget reminder — nudges toward PoC construction without
        # blocking reads (replaces the old FORCE_SUBMIT_HARD block).
        if (
            state.current_phase in ("formulation", "verification")
            and not self._ready_poc_paths(state)
            and state.phase_read_actions >= NO_CANDIDATE_READ_ACTION_LIMIT
        ):
            lines.append(
                "BUDGET NOTE: You've done many reads without producing a PoC. "
                "Consider whether you now have enough understanding to construct "
                "a candidate. If a specific read is still needed to unblock PoC "
                "construction, proceed with it — but don't read speculatively."
            )

        # Submission budget: warn when many submissions with no trigger
        if (state.current_phase in ("verification", "formulation")
            and state.phase_submissions >= 10
            and state.best_poc_score == 0
            and not state.pending_reminder):
            lines.append(
                "SUBMISSION BUDGET NOTE: You've submitted many candidates in this phase "
                "without triggering the vulnerability. Before submitting another variant, "
                "read the vulnerable code path to understand why your inputs don't reach "
                "the sink. Consider whether the trigger requires a different input format "
                "or a different code path entirely."
            )

        # Corpus reminder: suggest seed mutation instead of from-scratch crafting
        if (getattr(state, 'corpus_files', None)
            and state.poc_attempts >= 2
            and state.best_poc_score == 0
            and not state.discriminant_failed
            and not state.pending_reminder):
            lines.append(
                "CORPUS NOTE: Seed files are available. Use `CorpusInspect` to find a seed, "
                "then mutate it with Python/HexView instead of crafting an input from scratch. "
                "Seeds already satisfy format requirements that handcrafted inputs usually miss."
            )

        return lines

    def _current_objective(self, state: CyberGymState) -> str:
        if getattr(state, "pending_reflection", False):
            return "Consider reflecting on the failure pattern before continuing. Decide whether to branch to a new PoC family."
        ready_paths = self._candidate_ready_submit_paths(state, include_active=True)
        if ready_paths:
            if self._candidate_ready_file_missing(state):
                missing = self._missing_ready_poc_paths(state)
                return f"Regenerate missing ready PoC file(s): {', '.join(missing[:3])}."
            if len(ready_paths) <= 1:
                return f"Submit the complete ready PoC list now: `{ready_paths[0]}`."
            return f"Submit the complete ready PoC list now ({len(ready_paths)} paths)."
        if state.last_verification_result and not state.is_verified():
            if state.best_poc_score == 1:
                return ("PARTIAL HIT achieved — vul crashed but precision unverified. "
                        "Refine the PoC for precision: reduce overflow to minimal bytes, "
                        "target exact offset from source code, study the patch diff. "
                        f"Create a refined PoC under `{POC_OUTPUT_DIR}/` and submit it.")
            return f"Analyze the latest feedback, then construct a new PoC under `{POC_OUTPUT_DIR}/` and submit it."
        if getattr(state, "pending_chain_checkpoint", False):
            return ("Record at least one chain node (record_chain_node) describing "
                    "a function in the vulnerability path, then continue investigation.")
        if getattr(state, "pending_gates_checkpoint", False):
            return ("Record at least one gate (record_gate) describing a condition "
                    "the PoC must satisfy, then continue investigation.")
        if state.candidate_required or self._read_budget_exhausted(state):
            return "Prioritize forming a concrete PoC, while using targeted evidence checks when they directly improve candidate construction."
        if state.current_phase == "ingestion":
            return "Read README.md first, inspect local task files and repo structure, then identify likely source files."
        if state.current_phase == "investigation":
            return "Narrow to one concrete vulnerable path and extract the trigger condition."
        if state.current_phase == "exploration":
            active_sinks = state.confirmed_sink_candidates()
            conf = float(getattr(state, "task_spec_confidence", 0.5) or 0.5)
            if not active_sinks and conf >= 0.6:
                return ("Description is specific — quickly locate the named function, "
                        "trace to its leaf callee, and call `sink`.")
            if not active_sinks:
                return ("Explore the repo to identify the vulnerable sink function, "
                        "then call `sink`. Use broad grep searches "
                        "to compensate for the vague description.")
            return "Narrow to one concrete vulnerable path and extract the trigger condition."
        if state.current_phase == "verification":
            return f"Create a candidate PoC under `{POC_OUTPUT_DIR}/` immediately, then submit it."
        return f"Produce the first candidate PoC file under `{POC_OUTPUT_DIR}/`, then submit it for feedback."

    # ------------------------------------------------------------------
    # State rendering
    # ------------------------------------------------------------------

    def _allowed_tool_lines(self, state: CyberGymState) -> List[str]:
        from ...tool_names import (
            EVIDENCE_TOOLS, READ_ONLY_TOOLS,
            SUBMIT_POC as SUBMIT_POC_TOOL,
            RECORD_CHAIN_NODE as RECORD_CHAIN_NODE_TOOL,
            RECORD_GATE as RECORD_GATE_TOOL,
        )

        if getattr(state, "pending_sink_checkpoint", False):
            conf = float(getattr(state, "task_spec_confidence", 0.5) or 0.5)
            nudge_lines = [
                "- `sink(function, location?, evidence?, confidence?)` — "
                "STRONGLY RECOMMENDED before proceeding.",
            ]
            # For descriptions that name specific functions, hint at them
            desc_sinks = [c for c in (getattr(state, "sink_candidates", []) or [])
                          if c.source in ("description", "description_symbol")
                          and c.status != "eliminated"]
            if desc_sinks and conf >= 0.4:
                names = ", ".join(
                    f"`{c.function}`" for c in sorted(desc_sinks, key=lambda x: -x.confidence)[:3]
                )
                nudge_lines.append(
                    f"- Description names {names} — read its source, then call "
                    f"`sink` for the function (or its leaf callee) where "
                    f"the actual crash occurs."
                )
            elif conf >= 0.6:
                nudge_lines.append(
                    "- The description is specific — you likely already know the target function. "
                    "read it briefly, then record your sink candidate."
                )
            nudge_lines.extend([
                "- `read` / `grep` / `find_symbols` / `callsite_search` — "
                "only if needed to identify the sink function.",
                "- `WRITE` / `BASH` — allowed if you have a hypothesis to test.",
                "- `submit_poc` — allowed if you have a PoC file ready.",
            ])
            return nudge_lines
        if getattr(state, "pending_chain_checkpoint", False):
            return [
                "- `record_chain_node(function, location, role, description, status?)` — "
                "record one function in the entry-to-sink chain NOW.",
                "- `record_gate(node_function, gate_type, description, required_condition, status?)` — "
                "record one path constraint the PoC must satisfy.",
                "- `read` / `grep` / `find_symbols` / `callsite_search` — "
                "only if needed to identify the next chain node.",
                "- Do not call `submit_poc`, `WRITE`, `BASH`, or edit tools until the checkpoint is satisfied.",
            ]
        if getattr(state, "pending_gates_checkpoint", False):
            return [
                "- `record_gate(node_function, gate_type, description, required_condition, status?)` — "
                "record one path constraint the PoC must satisfy NOW.",
                "- `record_chain_node(function, location, role, description, status?)` — "
                "record another chain node if needed.",
                "- `READ` / `GREP` / `FindSymbols` / `CallsiteSearch` — "
                "only if needed to identify the next gate.",
                "- Do not call `submit_poc`, `WRITE`, `BASH`, or edit tools until the checkpoint is satisfied.",
            ]
        ready_paths = self._candidate_ready_submit_paths(state, include_active=True)
        if ready_paths:
            if self._candidate_ready_file_missing(state):
                missing = self._missing_ready_poc_paths(state)
                return [
                    f"- `{self.BASH_TOOL}(command)`; create or regenerate missing ready PoC file(s): {', '.join(missing[:3])}.",
                    f"- `{self.WRITE_TOOL}(path, content)`",
                    "- `submit_poc(poc_path)` after the candidate file exists.",
                    "- Consider reflecting on the failure pattern if explicitly flagged by Current State.",
                ]
            chunks = self._render_candidate_path_chunks(ready_paths)
            lines = [
                f"- `submit_poc(poc_path)` only; call it once for every path in the complete ready PoC list.",
            ]
            if chunks:
                lines.append(
                    f"- Complete ready PoC list to submit in this same response "
                    f"({len(ready_paths)} total): {chunks[0]}."
                )
                for chunk in chunks[1:]:
                    lines.append(f"- Continue complete ready PoC list: {chunk}.")
            lines.append("- Do not stop after submitting only one path; submit every listed path.")
            lines.append("- Do not call `read`, `grep`, `bash`, or edit tools before the complete list is submitted.")
            return lines
        if self._should_filter_to_candidate_tools(state):
            names = self._candidate_construction_tool_names(state)
            lines: List[str] = []
            if self.READ_TOOL in names:
                lines.append(f"- `{self.READ_TOOL}(path, offset?, limit?)`; only for a concrete blocking question.")
            if self.GREP_TOOL in names:
                lines.append(f"- `{self.GREP_TOOL}(pattern, path?, glob?, output_mode?)`; only for a concrete blocking search.")
            if self.GLOB_TOOL in names:
                lines.append(f"- `{self.GLOB_TOOL}(pattern, path?)`; only for a concrete blocking file lookup.")
            evidence_names = [
                name
                for name in EVIDENCE_TOOLS
                if name in names
            ]
            if evidence_names:
                lines.append("- `" + "` / `".join(evidence_names) + "`; only for one concrete blocking evidence check.")
            if self.BASH_TOOL in names:
                lines.append(f"- `{self.BASH_TOOL}(command)`; search/generate only when it directly unblocks the candidate.")
            if self.WRITE_TOOL in names:
                lines.append(f"- `{self.WRITE_TOOL}(path, content)`")
            if SUBMIT_POC_TOOL in names:
                lines.append("- `submit_poc(poc_path)`")
            tracking = [
                name
                for name in (
                    RECORD_CHAIN_NODE_TOOL, RECORD_GATE_TOOL,
                )
                if name in names
            ]
            if tracking:
                lines.append("- `" + "` / `".join(tracking) + "`")
            return lines
        lines = [
            f"- `{self.READ_TOOL}(path, offset?, limit?)` or `read(match_id=..., radius=...)` to jump to any search hit",
            f"- `{self.GREP_TOOL}(pattern, path?, glob?, output_mode?, head_limit?, offset?)` → results include match_id for read jumps",
            f"- `{self.GLOB_TOOL}(pattern, path?)` → narrow files before grep/find_symbols",
            f"- `{self.REPO_MAP_TOOL}(path?)` / `{self.FIND_SYMBOLS_TOOL}(query, kind?, path?)` / `{self.CALLSITE_SEARCH_TOOL}(symbol, path?)` — repo_map maps layout; find_symbols finds definitions+signatures; callsite_search traces callers",
            f"- `{self.CORPUS_INSPECT_TOOL}(path?)` / `{self.FILE_INFO_TOOL}(path)` / `{self.HEX_VIEW_TOOL}(path, offset?, length?)` / `{self.STRUCT_PROBE_TOOL}(path, offset?, formats?, endian?)` — inspect seeds before constructing candidates",
            f"- `{self.BASH_TOOL}(command)` — write candidates with Python; use toolbox for format-specific mutation",
            f"- `{self.WRITE_TOOL}(path, content)` — text candidates only; prefer bash for binary",
            "- `submit_poc(poc_path)`; submit every distinct ready PoC in one step when multiple PoCs are ready.",
            "- `record_chain_node` / `record_gate` / `sink`",
            "- `record_chain_node(function, location, role, description, status)` — record each function in the entry-to-sink chain",
            "- `record_gate(node_function, gate_type, description, required_condition, status)` — record each constraint the PoC must satisfy",
            "- `sink(function, location?, evidence?, confidence?)` — propose a sink candidate after reading code",
            "- Parallel read-only calls are allowed; keep batches to at most `4` tools.",
        ]
        return lines

    # ------------------------------------------------------------------
    # Step tracing
    # ------------------------------------------------------------------

    def _step_trace_root(self, state: CyberGymState) -> Path:
        trace_root = str(state.metadata.get("trace_run_dir") or "").strip()
        if trace_root:
            return Path(trace_root) / "agent_steps"
        workspace_root = str(state.workspace_root or getattr(self, "workspace_root", "") or "").strip()
        return Path(workspace_root or ".") / ".cybergym" / "agent_steps"

    def _step_trace_dir(self, state: CyberGymState) -> Path:
        step_id = int(getattr(self, "_runtime_step_id", getattr(state, "current_step", 0)) or 0)
        return self._step_trace_root(state) / f"step-{step_id:04d}"

    def _write_step_sidecar(
        self,
        state: CyberGymState,
        name: str,
        content: str,
        *,
        context_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            step_dir = self._step_trace_dir(state)
            step_dir.mkdir(parents=True, exist_ok=True)
            path = step_dir / name
            path.write_text(str(content or ""), encoding="utf-8")
            if context_payload is not None:
                context_path = step_dir / "context.json"
                context_path.write_text(
                    json.dumps(context_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                summary_path = self._step_trace_root(state) / "trace_summary.jsonl"
                summary_path.parent.mkdir(parents=True, exist_ok=True)
                with summary_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(context_payload, ensure_ascii=False))
                    handle.write("\n")
        except Exception:
            return

    def _step_context_payload(
        self,
        state: CyberGymState,
        *,
        observation: Optional[Observation] = None,
    ) -> Dict[str, Any]:
        step_id = int(getattr(self, "_runtime_step_id", getattr(state, "current_step", 0)) or 0)
        payload = {
            "step_id": step_id,
            "state": self._state_line(state).replace("STATE ", ""),
            "phase": state.current_phase,
            "allowed_tools": self._allowed_tool_names_for_trace(state),
            "ready_pocs": self._ready_poc_paths(state),
            "candidate_ready": bool(self._ready_poc_paths(state)),
            "candidate_required": bool(state.candidate_required),
            "read_budget_remaining": max(
                0,
                self._candidate_targeted_read_limit(state) - self._candidate_reads_used(state),
            ),
            "blocking_question_required": False,
            "objective": self._current_objective(state),
            "durable_project_memory": dict(state.durable_project_memory or {}),
            "durable_code_facts": list(state.durable_code_facts or []),
            "durable_feedback_facts": list(state.durable_feedback_facts or []),
            "harness_signals": [
                getattr(item, "__dict__", item)
                for item in list(getattr(state, "harness_signals", []) or [])
            ],
            "path_constraints": [
                getattr(item, "__dict__", item)
                for item in list(getattr(state, "path_constraints", []) or [])
            ],
            "aci_metrics": dict(state.metadata.get("aci_metrics", {}) or {}),
        }
        if isinstance(observation, dict):
            payload["observation_step_id"] = observation.get("step_id")
        return payload

    def _allowed_tool_names_for_trace(self, state: CyberGymState) -> List[str]:
        return sorted(self._layered_tool_schema_names(state))
