"""Prompt-rendering mixin — system prompt assembly."""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...state import CyberGymState

from ...context import PROJECT_ARTIFACT_ROOT
from ..core.constants import POC_OUTPUT_DIR
from .phase import phase_local_steps
from .prompt_resources import prompt_resource, render_prompt_resource

_BUG_TYPE_RESOURCES: dict[str, str] = {
    "buffer_overflow": "buffer_overflow.md", "use_after_free": "use_after_free.md",
    "integer_overflow": "integer_overflow.md", "null_pointer_dereference": "null_pointer_dereference.md",
    "format_string": "format_string.md", "race_condition": "race_condition.md",
    "uninitialized_value": "uninitialized_value.md",
}
_STRATEGY_SUPPLEMENTS: dict[tuple[str, str], str] = {
    ("binary_python", "buffer_overflow"): "bug_guidance/supplement_binary_buffer_overflow.md",
    ("corpus_mutate", "buffer_overflow"): "bug_guidance/supplement_corpus_buffer_overflow.md",
    ("hex", "buffer_overflow"): "bug_guidance/supplement_hex_memory.md",
    ("hex", "integer_overflow"): "bug_guidance/supplement_hex_memory.md",
    ("binary_python", "use_after_free"): "bug_guidance/supplement_binary_uaf.md",
    ("hex", "use_after_free"): "bug_guidance/supplement_binary_uaf.md",
}
_PROCEDURE_MEMORY_MAP: dict[str, str] = {
    "buffer_overflow": "bounds_overflow_recipe.md", "use_after_free": "lifetime_uaf_recipe.md",
    "integer_overflow": "integer_size_recipe.md", "null_pointer_dereference": "segv_dispatch_recipe.md",
    "uninitialized_value": "uninitialized_value_recipe.md",
    "heap-buffer-overflow": "bounds_overflow_recipe.md", "stack-buffer-overflow": "bounds_overflow_recipe.md",
    "heap-use-after-free": "lifetime_uaf_recipe.md", "heap-double-free": "lifetime_uaf_recipe.md",
    "use-of-uninitialized-value": "uninitialized_value_recipe.md", "segv": "segv_dispatch_recipe.md",
}


class PromptsMixin:
    """Prompt rendering and bug-type classification."""

    def _bug_type_guidance(self, bug_type: str, poc_strategy: str = "") -> str:
        name = _BUG_TYPE_RESOURCES.get(bug_type)
        result = prompt_resource(f"bug_guidance/{name}") if name else ""
        supplement = _STRATEGY_SUPPLEMENTS.get((poc_strategy, bug_type))
        if supplement:
            result += prompt_resource(supplement)
        if poc_strategy in ("binary_python", "corpus_mutate"):
            result += prompt_resource("bug_guidance/supplement_toolbox.md")
        return result

    def _procedure_memory_guidance(self, state: CyberGymState) -> str:
        recipe_key = state.bug_type or ""
        if not recipe_key and state.crash_type:
            recipe_key = state.crash_type
        if not recipe_key:
            da = state.description_analysis
            if hasattr(da, "vuln_type") and da.vuln_type:
                recipe_key = da.vuln_type
        parts: list[str] = []
        recipe_file = _PROCEDURE_MEMORY_MAP.get(recipe_key)
        if recipe_file:
            content = prompt_resource(f"procedure_memory/{recipe_file}")
            if content:
                parts.append(f"\n## PoC Construction Procedure ({recipe_key})")
                parts.append(content)
        if state.poc_strategy in ("corpus_mutate", "binary_python"):
            carrier = prompt_resource("procedure_memory/format_carrier_recipes.md")
            if carrier:
                parts.append("\n## Format Carrier Guidance")
                parts.append("\n".join(carrier.split("\n")[:60]))
        actions = prompt_resource("procedure_memory/candidate_action_templates.md")
        if actions:
            parts.append("\n## PoC Action Templates")
            parts.append("\n".join(actions.split("\n")[:50]))
        return "\n".join(parts)

    def _format_guidance_prompt(self, state: CyberGymState) -> str:
        """Render format-specific guidance from the active pack mode."""
        return ""  # Pack knowledge disabled

        if mode == "unconfirmed" or not pack_id:
            return ""

        if mode == "candidate":
            score = pack_mode.get("detection_score", 0.0)
            missing = pack_mode.get("missing_evidence", ())
            return (
                f"\n## Format Hint (candidate, score={score:.2f})\n"
                f"The input may be **{pack_id}** format. Verify using corpus inspection or hex view,\n"
                f"then use `confirm_format` to activate format-specific tools.\n"
                f"Missing evidence: {', '.join(missing[:3]) or 'none'}\n"
            )

        # confirmed: load pack-local SKILL.md summary first, then legacy guidance
        skill_summary = self._pack_skill_summary(pack_id)
        try:
            resource = prompt_resource(f"format_guidance/{pack_id}.md")
        except (FileNotFoundError, TypeError):
            resource = ""
        if skill_summary:
            parts = [f"\n## Pack Skill: {pack_id}", skill_summary]
            if resource:
                parts.append(f"\n## Format Guidance: {pack_id}\n{resource}")
            return "\n".join(parts)
        if resource:
            return f"\n## Format Guidance: {pack_id}\n" + resource

        # Generic confirmed guidance from carrier contract
        metadata = getattr(state, "metadata", {}) or {}
        contract = metadata.get("carrier_contract")
        protected = ""
        derived = ""
        if isinstance(contract, dict):
            protected = ", ".join(list(contract.get("protected_fields", []))[:5])
            derived = ", ".join(list(contract.get("derived_fields", []))[:5])
        lines = [f"\n## Format: {pack_id} (confirmed)"]
        if protected:
            lines.append(f"- Protected fields (do not overwrite): {protected}")
        if derived:
            lines.append(f"- Derived fields (auto-recomputed): {derived}")
        lines.append("- Pack-driven build handles checksums, lengths, and cross-references")
        return "\n".join(lines)

    @staticmethod
    def _pack_skill_summary(pack_id: str, max_lines: int = 80) -> str:
        """Return a compact active-pack SKILL.md summary for prompt guidance."""
        safe_id = "".join(ch for ch in str(pack_id or "") if ch.isalnum() or ch in {"_", "-"})
        if not safe_id:
            return ""
        try:
            text = (
                resources.files("cybergym_agent.agent_impl.knowledge.packs")
                .joinpath(safe_id, "SKILL.md")
                .read_text(encoding="utf-8")
            )
        except (FileNotFoundError, ModuleNotFoundError, TypeError, ValueError):
            return ""

        lines = text.splitlines()
        if lines and lines[0].strip() == "---":
            for idx, line in enumerate(lines[1:], start=1):
                if line.strip() == "---":
                    lines = lines[idx + 1:]
                    break

        kept: list[str] = []
        for line in lines:
            kept.append(line)
            if len(kept) >= max_lines:
                break
        return "\n".join(kept).strip()

    def base_persona_prompt(self, state: CyberGymState) -> str:
        return render_prompt_resource("system/base_persona.md", project_root=PROJECT_ARTIFACT_ROOT.as_posix())

    def task_policy_prompt(self, state: CyberGymState) -> str:
        parts = []
        if state.bug_type:
            parts.append(f"\n## Bug Type: {state.bug_type}")
            parts.append(self._bug_type_guidance(state.bug_type, state.poc_strategy))
        if state.cve_id:
            parts.append(f"\n## CVE ID: {state.cve_id}")
        # Format-specific guidance from pack mode
        format_guidance = self._format_guidance_prompt(state)
        if format_guidance:
            parts.append(format_guidance)
        proc = self._procedure_memory_guidance(state)
        if proc:
            parts.append(proc)
        return "\n".join(parts)

    def runtime_context_protocol_prompt(self, state: CyberGymState) -> str:
        return prompt_resource("system/runtime_context_protocol.md")

    def extra_instructions_prompt(self, state: CyberGymState) -> str:
        return prompt_resource("system/execution_policy.md")

    def tool_usage_hint_prompt(self, state: CyberGymState) -> str:
        return render_prompt_resource("system/tool_usage.md", POC_OUTPUT_DIR=POC_OUTPUT_DIR, delegate_hint="")

    def _multi_action_guidance_prompt(self, state: CyberGymState) -> str:
        return prompt_resource("system/multi_action.md")

    def _phase_operating_guidance(self, state: CyberGymState) -> str:
        if self._should_reinvestigate(state):
            # Auto-activate reinvestigation transition + unblock reads
            state.reinvestigate_requested = True
            state.candidate_required = False
            return render_prompt_resource("phase/reinvestigate.md", poc_attempts=int(state.poc_attempts or 0))
        mode = self._derive_control_mode(state)
        if mode == "chain_checkpoint_pending":
            return prompt_resource("mode/chain_checkpoint_pending.md")
        if mode == "gates_checkpoint_pending":
            return prompt_resource("mode/gates_checkpoint_pending.md")
        if mode == "post_submit_miss":
            hint = self._feedback_action_guidance(state)
            return prompt_resource("phase/post_submit_miss.md").replace("{{hint_line}}\n", f"- {hint}\n" if hint else "")
        if mode == "candidate_ready":
            return prompt_resource("phase/candidate_ready.md")
        if mode == "attempt_record_pending":
            return prompt_resource("phase/attempt_record_pending.md")
        if mode == "reflection_pending":
            return prompt_resource("phase/reflection_pending.md")
        dispatchers = {
            "ingestion": lambda: prompt_resource("phase/ingestion.md"),
            "exploration": lambda: self._exploration_guidance(state),
            "investigation": lambda: self._investigation_guidance(state),
            "formulation": lambda: self._formulation_guidance(state),
            "verification": lambda: self._verification_guidance(state),
        }
        handler = dispatchers.get(state.current_phase)
        return handler() if handler else ""

    def _exploration_guidance(self, state: CyberGymState) -> str:
        text = prompt_resource("phase/exploration.md")
        if not state.confirmed_sink_candidates() and phase_local_steps(state) >= 2:
            text += "\n\n" + prompt_resource("mode/exploration_mandatory_sink.md")
        return text

    def _investigation_guidance(self, state: CyberGymState) -> str:
        text = prompt_resource("phase/investigation.md")
        open_gates = state.open_gates() if hasattr(state, "open_gates") else []
        if open_gates:
            first = open_gates[0]
            text += (f"\n- OPEN GATE: {first.description} (status={first.status}). "
                     "Confirm or refute this gate by reading the relevant source code before proceeding.")
        return text + "\n" + prompt_resource("mode/investigation_tools.md")

    def _formulation_guidance(self, state: CyberGymState) -> str:
        return render_prompt_resource("phase/formulation.md", constraint_lines=self._build_formulation_constraints(state))

    def _build_formulation_constraints(self, state: CyberGymState) -> str:
        has_constraint = bool(state.durable_code_facts) or bool(
            any(f.startswith(("crash_type:", "crash_location:", "failed_gate:"))
                for f in (state.durable_feedback_facts or [])))
        confirmed_gates = state.confirmed_gates() if hasattr(state, "confirmed_gates") else []
        has_chain = bool(confirmed_gates)
        parts: list[str] = []
        if not has_constraint and not has_chain and not state.candidate_required:
            parts.append(prompt_resource("mode/formulation_no_constraint.md"))
        elif has_chain and not has_constraint:
            parts.append(render_prompt_resource("mode/formulation_chain_constraint.md", confirmed_gate_count=len(confirmed_gates)))
        nodes = list(getattr(state, "call_chain_nodes", []) or [])
        all_gates = list(getattr(state, "call_chain_gates", []) or [])
        if nodes:
            uncovered = [n for n in nodes
                         if not any(g.status == "confirmed" for g in all_gates if g.node_order == n.order)]
            if uncovered and not state.candidate_required:
                names = [n.function for n in uncovered[:3]]
                parts.append(render_prompt_resource("mode/formulation_uncovered_nodes.md", uncovered_names=", ".join(names)))
            parts.append(render_prompt_resource("mode/formulation_chain_constraint.md", confirmed_gate_count=len(confirmed_gates)))
        if confirmed_gates:
            parts.append(prompt_resource("mode/formulation_pre_construction.md"))
            suggestions = list(getattr(state, "suggested_constraints", []) or [])
            if any(s.get("role") == "trigger" for s in suggestions):
                parts.append(prompt_resource("mode/formulation_trigger_gates.md"))
        return "\n".join(parts)

    def _verification_guidance(self, state: CyberGymState) -> str:
        gate_lines = self._build_verification_gate_lines(state)
        return render_prompt_resource("phase/verification.md", gate_lines=("\n".join(gate_lines) + "\n" if gate_lines else ""))

    def _build_verification_gate_lines(self, state: CyberGymState) -> list[str]:
        lines: list[str] = []
        if state.discriminant_failed:
            lines.append(prompt_resource("mode/verification_discriminant_fail.md").strip())
        elif state.best_poc_score == 1 and not state.is_verified():
            lines.append(prompt_resource("mode/verification_partial_hit.md").strip())
        if state.last_verification_result and not state.is_verified():
            gate = self._classify_failed_gate(dict(state.last_verification_result))
            if gate:
                lines.append(f"- Failed gate: `{gate}`")
                hint = self._failed_gate_repair_hint(gate)
                if hint:
                    lines.append(f"- {hint}")
                action_hint = self._feedback_action_guidance(state)
                if action_hint:
                    lines.append(f"- {action_hint}")
            open_gates = state.open_gates() if hasattr(state, "open_gates") else []
            if open_gates and open_gates[0].status == "inferred":
                first = open_gates[0]
                lines.append(render_prompt_resource("mode/verification_gate_repair.md", gate_description=first.description).strip())
                if first.evidence:
                    lines.append(render_prompt_resource("mode/verification_gate_evidence.md", gate_evidence=first.evidence).strip())
            refuted = state.refuted_gates() if hasattr(state, "refuted_gates") else []
            for rg in refuted[-3:]:
                if rg.repair_hint:
                    lines.append(f"- [refuted] {rg.description} — repair: {rg.repair_hint}")
        return lines
