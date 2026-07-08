"""Structured 5-section observation rendering for the Minimal CyberGym Agent.

Sections: Vulnerability, Sink Candidates, Constraint Boards, Experiments, Task Memory.
Designed for maximum readability, information density, and iteration support.
Constraint Boards and Experiments are scoped to the active sink.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, NamedTuple, Optional

from ..core.metadata_keys import RUNTIME_EVIDENCE

if TYPE_CHECKING:
    from qitos.core.observation import Observation
    from ...state import CyberGymState


class ObservationResult(NamedTuple):
    """Structured result from _render_observation()."""
    text: str
    sections: Dict[str, str]


class ObservationMixin:
    """5-section observation rendering for the Minimal CyberGym Agent."""

    def _render_observation(
        self,
        state: CyberGymState,
        *,
        is_initial: bool = False,
    ) -> ObservationResult:
        """Render observation text as a 5-section Runtime Context."""
        sections: Dict[str, str] = {}
        sections["vulnerability"] = self._render_vulnerability_section(state)
        sections["sinks"] = self._render_sinks_section(state)
        sections["constraints"] = self._render_constraints_section(state)
        sections["experiments"] = self._render_experiments_section(state)
        sections["memory"] = self._render_task_memory_section(state)
        text = "\n\n".join(v for v in sections.values() if v.strip())
        return ObservationResult(text=text, sections=sections)

    # ------------------------------------------------------------------
    # Section 1: Vulnerability
    # ------------------------------------------------------------------

    @staticmethod
    def _render_vulnerability_section(state: CyberGymState) -> str:
        parts = ["## Vulnerability"]
        desc = (state.vulnerability_description or "").strip()
        if desc:
            if len(desc) > 1200:
                desc = desc[:1197] + "..."
            parts.append(desc)
        meta = []
        if state.bug_type:
            meta.append(f"**Bug Type:** {state.bug_type}")
        if state.cve_id:
            meta.append(f"**CVE:** {state.cve_id}")
        if state.crash_type:
            loc = f" @ {state.crash_location}" if state.crash_location else ""
            meta.append(f"**Crash:** {state.crash_type}{loc}")
        # Input format hint
        fmt = getattr(state, "input_format", None)
        if fmt:
            fmt_name = getattr(fmt, "format_name", "") or getattr(fmt, "carrier_format", "")
            if fmt_name:
                meta.append(f"**Input Format:** {fmt_name}")
            magic = getattr(fmt, "magic_bytes", "")
            if magic:
                meta.append(f"**Magic:** `{magic}`")
        if meta:
            parts.append(" | ".join(meta))
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Section 2: Sink Candidates
    # ------------------------------------------------------------------

    def _render_sinks_section(self, state: CyberGymState) -> str:
        parts = ["## Sink Candidates"]
        candidates = [
            s for s in state.sink_candidates
            if s.status != "eliminated"
        ]
        if not candidates:
            parts.append("_No sinks recorded yet._")
            return "\n".join(parts)

        # Determine active sink ID
        active_sink_id = self._active_sink_id(state)

        # Sort: active first, then by confidence descending
        def sort_key(s):
            is_active = 0 if (s.candidate_id == active_sink_id or
                              f"{s.function}@{s.location}" == active_sink_id) else 1
            return (is_active, -float(s.confidence or 0))

        candidates.sort(key=sort_key)

        for s in candidates:
            is_active = (s.candidate_id == active_sink_id or
                         f"{s.function}@{s.location}" == active_sink_id)
            marker = "►" if is_active else {
                "active": "●", "confirmed": "●", "provisional": "○",
            }.get(s.status, "?")
            sid = s.candidate_id or f"s_{candidates.index(s)}"
            line = f"- {marker} **[{sid}]** `{s.function}` @ `{s.location}`"
            detail_parts = []
            if s.confidence:
                detail_parts.append(f"conf={s.confidence:.1f}")
            if s.source:
                detail_parts.append(f"src={s.source}")
            if s.evidence:
                ev = s.evidence
                if len(ev) > 100:
                    ev = ev[:97] + "..."
                detail_parts.append(ev)
            if detail_parts:
                line += " — " + " | ".join(detail_parts)
            parts.append(line)
            # Call chain for this sink
            nodes = state.nodes_for_sink(s.candidate_id or f"{s.function}@{s.location}")
            if nodes:
                chain = " → ".join(
                    f"**{n.function}**" if n.role == "sink" else f"`{n.function}`"
                    for n in nodes
                )
                parts.append(f"  Chain: {chain}")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Section 3: Constraint Boards (table format, active sink)
    # ------------------------------------------------------------------

    def _render_constraints_section(self, state: CyberGymState) -> str:
        parts = ["## Constraint Boards"]
        active_sink_id = self._active_sink_id(state)

        if not active_sink_id:
            parts.append("_No active sink._")
            return "\n".join(parts)

        # Mini call-chain map
        nodes = state.nodes_for_sink(active_sink_id)
        if nodes:
            chain_parts = []
            for n in nodes:
                role_marker = {
                    "entry": "▶", "parser": "◈", "dispatch": "◆",
                    "guard": "⊘", "sink": "✱",
                }.get(getattr(n, "role", ""), "·")
                chain_parts.append(f"{role_marker}`{n.function}`")
            parts.append(" → ".join(chain_parts))
        else:
            parts.append(f"Chain: _no nodes for {active_sink_id}_")

        # Gate table
        gates = state.gates_for_sink(active_sink_id)
        if gates:
            parts.append("")
            parts.append("| # | Type | On | Required | Status |")
            parts.append("|---|------|----|----------|--------|")
            for i, g in enumerate(gates, 1):
                status_icon = {
                    "confirmed": "✓", "refuted": "✗", "inferred": "?",
                    "questioned": "?",
                }.get(g.status, "?")
                gate_type = g.gate_type or "unknown"
                on_func = g.node_function or "—"
                required = g.required_condition or "—"
                if len(required) > 50:
                    required = required[:47] + "..."
                status_str = f"{status_icon} {g.status}"
                parts.append(f"| {i} | {gate_type} | `{on_func}` | {required} | {status_str} |")
        else:
            parts.append("_No gates recorded for this sink._")

        # Open gates summary
        open_gates = [g for g in gates if g.status in ("inferred", "unknown", "questioned")]
        if open_gates:
            parts.append(f"\n**{len(open_gates)} open gate(s)** — confirm or refute with GATE confirm")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Section 4: Experiments (active sink)
    # ------------------------------------------------------------------

    def _render_experiments_section(self, state: CyberGymState) -> str:
        parts = ["## Experiments"]

        # Latest verification result
        vr = state.last_verification_result
        if vr and isinstance(vr, dict):
            vul_code = vr.get("vul_exit_code")
            fix_code = vr.get("fix_exit_code")
            poc_path = vr.get("poc_path", "?")
            key_insight = vr.get("key_insight", "") or state.metadata.get("last_key_insight", "")
            status = "✓ VERIFIED" if state.is_verified() else (
                f"● vul_exit={vul_code}" if vul_code and vul_code != 0 else
                f"✗ vul_exit={vul_code}" if vul_code is not None else "⚠ error"
            )
            line = f"- **Latest:** `{poc_path}` — {status}"
            if fix_code is not None:
                line += f" fix_exit={fix_code}"
            if key_insight:
                line += f"\n  Key: _{key_insight}_"
            parts.append(line)
            # Crash info
            if state.crash_type:
                loc = f" @ {state.crash_location}" if state.crash_location else ""
                parts.append(f"  Crash: {state.crash_type}{loc}")

        # Attempt history
        attempts = list(state.attempt_history or [])
        if attempts:
            # Show last 8 attempts
            shown = attempts[-8:]
            if len(attempts) > 8:
                parts.append(f"\n_Prior attempts: {len(attempts) - 8} more_\n")
            for i, a in enumerate(shown):
                outcome = a.get("outcome", "unknown")
                icon = {"success": "✓", "crash": "●", "miss": "✗", "error": "⚠"}.get(outcome, "?")
                path = a.get("path", "?")
                path_short = path.rsplit("/", 1)[-1] if "/" in path else path
                detail = a.get("detail", "")
                line = f"- {icon} `{path_short}` — {outcome}"
                if detail and len(detail) < 80:
                    line += f" ({detail})"
                insight = a.get("key_insight", "")
                if insight:
                    line += f"\n  Key: _{insight}_"
                parts.append(line)

        # Runtime evidence (GDB debug outputs)
        evidence_list = state.metadata.get(RUNTIME_EVIDENCE)
        if isinstance(evidence_list, list) and evidence_list:
            parts.append("\n**Debug Evidence:**")
            for ev in evidence_list[-4:]:
                if not isinstance(ev, dict):
                    continue
                ev_id = ev.get("evidence_id", "?")
                poc = ev.get("poc_path", "")
                rc = ev.get("returncode", -1)
                snippet = ""
                if ev.get("output"):
                    snippet = str(ev["output"])[:150]
                elif ev.get("output_snippet"):
                    snippet = str(ev["output_snippet"])[:150]
                poc_short = poc.rsplit("/", 1)[-1] if poc else "?"
                line = f"- [{ev_id}] `{poc_short}` rc={rc}"
                if snippet:
                    snippet = snippet.replace("\n", " ").strip()
                    if len(snippet) > 120:
                        snippet = snippet[:117] + "..."
                    line += f": {snippet}"
                parts.append(line)

        if not vr and not attempts:
            parts.append("_No experiments yet._")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Section 5: Task Memory
    # ------------------------------------------------------------------

    @staticmethod
    def _render_task_memory_section(state: CyberGymState) -> str:
        parts = ["## Task Memory"]
        has_content = False

        if state.vulnerability_analysis:
            has_content = True
            va = state.vulnerability_analysis
            if len(va) > 400:
                va = va[:397] + "..."
            parts.append(f"**Analysis:** {va}")

        if state.current_hypothesis:
            has_content = True
            hyp = state.current_hypothesis
            if len(hyp) > 300:
                hyp = hyp[:297] + "..."
            parts.append(f"**Hypothesis:** {hyp}")

        if state.path_trace:
            has_content = True
            parts.append("**Path Trace:**")
            for p in state.path_trace[-8:]:
                parts.append(f"- {p}")

        if state.attempt_history_compact:
            has_content = True
            parts.append("**Attempt Summary:**")
            for a in state.attempt_history_compact[-6:]:
                parts.append(f"- {a}")

        if not has_content:
            parts.append("_No persistent memory yet._")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _active_sink_id(state: CyberGymState) -> str:
        """Return the active sink ID from state."""
        # Try explicit active_sink_id first
        aid = getattr(state, "active_sink_id", "")
        if aid:
            return str(aid)
        # Fall back to primary sink
        primary = getattr(state, "_primary_sink_id", None)
        if callable(primary):
            return str(primary()) or ""
        return ""
