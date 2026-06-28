"""State initialization mixin for CyberGymAgent."""

from __future__ import annotations

import os
from typing import Any, List

from ..context import CyberGymContextHistory
from ..state import CyberGymState
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
        state.poc_strategy = self._detect_poc_strategy(state)
        state.input_format = self._build_input_format_model(state)

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
