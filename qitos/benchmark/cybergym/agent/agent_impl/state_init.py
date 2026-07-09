"""State initialization mixin for CyberGymAgent."""

from __future__ import annotations

import os
import re
from typing import Any, List

from ..context import CyberGymContextHistory
from ..state import CyberGymState, SinkCandidate
from ..task_spec import build_task_spec
from .constants import POC_OUTPUT_DIR, SEED_CORPUS_ENABLED


class StateInitMixin:
    """CyberGym state initialization."""

    def init_state(self, task: str, **kwargs: Any) -> CyberGymState:
        """Create the initial CyberGymState from the task input."""
        state = CyberGymState(
            task=task,
            max_steps=self.max_steps,
            workspace_root=self.workspace_root,
        )

        state.vulnerability_description = kwargs.get(
            "description", kwargs.get("vulnerability_description", "")
        )
        state.task_id = kwargs.get("task_id", "")
        state.agent_id = kwargs.get("agent_id", "")
        state.checksum = kwargs.get("checksum", "")
        state.server_url = kwargs.get("server_url", self.server_url)
        state.metadata["trace_run_dir"] = kwargs.get("trace_run_dir", "")

        if not state.vulnerability_description:
            state.vulnerability_description = task

        state.error_txt = kwargs.get("error_txt", "")
        state.metadata["error_txt"] = state.error_txt
        state.difficulty = kwargs.get("difficulty", "level1")
        state.metadata["difficulty"] = state.difficulty
        # Level 1: patch_diff is not available — do not store it
        if state.difficulty == "level1":
            state.patch_diff = ""
            state.metadata["patch_diff"] = ""
        else:
            state.patch_diff = kwargs.get("patch_diff", "")
            state.metadata["patch_diff"] = state.patch_diff
        state.metadata["poc_output_dir"] = POC_OUTPUT_DIR
        self._ensure_poc_output_dir(state)

        state.cve_id = self._extract_cve_id(state.vulnerability_description)
        state.bug_type = self._classify_bug_type(state.vulnerability_description)
        # P40: mark that bug type classification was attempted (even if result
        # is empty), so the ingestion phase transition can distinguish "we
        # analysed the description" from "description exists".
        state.metadata["_bug_type_classified"] = True
        state.affected_component = self._extract_affected_component(
            state.vulnerability_description
        )

        repo_dir = kwargs.get("source_root") or kwargs.get("repo_dir", "")
        if repo_dir and os.path.isdir(repo_dir):
            state.repo_dir = repo_dir
            state.repo_index = self._build_repo_index(repo_dir)
            if kwargs.get("repo_dir") and kwargs.get("repo_dir") != repo_dir:
                state.repo_archive_root = kwargs.get("repo_dir", "")
                state.metadata["repo_archive_root"] = state.repo_archive_root
        else:
            repo_dir = os.path.join(self.workspace_root, "repo-vul")
            if os.path.isdir(repo_dir):
                state.repo_dir = repo_dir
                state.repo_index = self._build_repo_index(repo_dir)

        task_root = kwargs.get("task_root") or self.task_root
        state.metadata["task_root"] = task_root
        state.harness_info = self._parse_harness_info(task_root)
        state.submit_harness_targets = self._extract_submit_harness_targets(state.harness_info)

        spec = build_task_spec(
            state.vulnerability_description,
            error_txt=state.error_txt or str(state.metadata.get("error_txt") or ""),
            patch_diff=state.patch_diff or str(state.metadata.get("patch_diff") or ""),
            harness_info=state.harness_info or "",
        )
        state.vulnerability_class = str(spec.get("vulnerability_class") or "")
        state.expected_signal = str(spec.get("expected_signal") or "")
        state.input_vector_hints = list(spec.get("input_vector_hints") or [])
        state.likely_entrypoints = list(spec.get("likely_entrypoints") or [])
        state.likely_fuzz_targets = list(spec.get("likely_fuzz_targets") or [])
        state.source_files_mentioned = list(spec.get("source_files_mentioned") or [])
        state.symbols_mentioned = list(spec.get("symbols_mentioned") or [])
        state.task_spec_confidence = float(spec.get("task_spec_confidence") or 0.0)
        state.search_anchors = list(spec.get("search_anchors") or [])

        if state.repo_dir and os.path.isdir(state.repo_dir):
            state.corpus_files = self._discover_corpus_files(state.repo_dir)
        if SEED_CORPUS_ENABLED:
            try:
                seeds = self._prepare_seed_corpus(task_root, state.repo_dir)
            except Exception:
                seeds = []
            repo_samples: List[str] = []
            try:
                abs_samples = self._discover_repo_seed_samples(state.repo_dir or "")
                base = task_root or self.workspace_root
                for ap in abs_samples:
                    try:
                        repo_samples.append(os.path.relpath(ap, base))
                    except Exception:
                        continue
            except Exception:
                repo_samples = []
            merged = seeds + [r for r in repo_samples if r not in set(seeds)]
            if merged:
                rest = [c for c in state.corpus_files if c not in set(merged)]
                state.corpus_files = merged + rest
                state.metadata["seed_corpus_count"] = len(seeds)
                state.metadata["repo_sample_count"] = len(repo_samples)

        self._ensure_family_bootstrap(state)

        # Discover all targets and relate concrete source harnesses to the task.
        if state.repo_dir and os.path.isdir(state.repo_dir):
            try:
                from pathlib import Path
                from .repo_index import build_repo_index

                target_records = self._discover_fuzzer_targets(state.repo_dir)
                state.metadata["fuzzer_targets"] = target_records
                structural_index = build_repo_index(Path(state.repo_dir))
                state.metadata["repo_index_v2"] = structural_index
                state.harness_candidates = self._build_harness_candidates(
                    structural_index,
                    target_records,
                    state.submit_harness_targets,
                )
                self._resolve_harness_candidates(state, structural_index)
                state.likely_fuzz_targets = sorted({
                    name
                    for candidate in state.harness_candidates
                    for name in candidate.binary_names
                }, key=str.lower)[:12]
                selected_binary = state.harness_resolution.selected_binary
                if selected_binary and state.harness_resolution.selected_candidate_id:
                    state.metadata["fuzzer_target"] = selected_binary
            except Exception as exc:
                state.harness_candidates = []
                state.metadata["harness_resolution_error"] = str(exc)[:300]
                self._resolve_harness_candidates(state, {})

        state.poc_strategy = self._detect_poc_strategy(state)
        state.input_format = self._build_input_format_model(state)
        _generate_sink_candidates(state)

        # Build the immutable structural program graph before the first model
        # turn.  Failure is represented as PARTIAL_INDEX and never prevents the
        # agent from starting.
        try:
            self._bootstrap_analysis_index(state)
            if state.metadata.get("_pending_sink_analysis"):
                self._run_pending_sink_analysis(state)
        except Exception as exc:
            state.analysis_index_status = "PARTIAL_INDEX"
            state.analysis_index_coverage = {"reason": f"bootstrap_error:{type(exc).__name__}"}

        if self.memory and state.bug_type:
            relevant = self.memory.retrieve(query={"text": state.bug_type})
            if relevant:
                state.metadata["relevant_memories"] = [
                    str(r.content)[:500] for r in relevant[:3]
                ]

        state.current_phase = self._phase_engine.current_phase(state)

        if isinstance(self.history, CyberGymContextHistory):
            self.history.set_state(state)

        return state


def _generate_sink_candidates(state: CyberGymState) -> None:
    """Generate initial sink candidates from description + harness info."""
    desc = (state.vulnerability_description or "").strip()
    if not desc:
        return

    seen: set[str] = set()

    # 1. Explicit function names: func_name() pattern
    for m in re.finditer(r'([a-zA-Z_]\w+)\s*\(\)', desc):
        func = m.group(1)
        if func not in seen and not func[0].isupper():  # skip type names
            state.sink_candidates.append(SinkCandidate(
                function=func, confidence=0.8,
                status="provisional", source="description", evidence=f"Named in description: {func}()",
                metadata={"requires_review": True, "description_derived": True},
            ))
            seen.add(func)

    # 2. From harness_candidates' reachable_symbols that match description keywords
    desc_lower = desc.lower()
    for candidate in list(state.harness_candidates or [])[:5]:
        for sym in (candidate.reachable_symbols or []):
            if sym not in seen and sym.lower() in desc_lower:
                state.sink_candidates.append(SinkCandidate(
                    function=sym, confidence=0.5,
                    status="provisional", source="harness_chain",
                    metadata={"requires_review": True, "description_derived": True},
                    evidence="In harness call chain + mentioned in description"
                ))
                seen.add(sym)

    if state.sink_candidates:
        import hashlib
        for candidate in state.sink_candidates:
            if not candidate.reason:
                candidate.reason = candidate.evidence
            if not candidate.candidate_id:
                material = f"{candidate.repository_id}|{candidate.location}|{candidate.function}"
                candidate.candidate_id = "sink_" + hashlib.blake2s(material.encode(), digest_size=6).hexdigest()
        # Description-derived names are weak priors.  They remain provisional
        # until the model inspects code and explicitly records a candidate.
        state.active_sink_id = ""
