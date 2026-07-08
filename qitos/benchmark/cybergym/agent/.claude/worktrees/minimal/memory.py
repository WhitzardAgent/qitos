"""Memory file system for the Minimal CyberGym Agent.

Inspired by Claude Code's CLAUDE.md design:
- MEMORY.md auto-loads into observation each step (like CLAUDE.md)
- Other memory files persist what cannot be derived from code/git
- Model can READ files directly, or GATE/SINK tools write to them
- "Query rather than store" — if info exists in code/git, read it live

Layout:
    .cybergym/memory/
        MEMORY.md    — auto-loaded index (task summary + pointers)
        sinks.md     — current sink candidates with evidence
        gates.md     — constraint/gate records per sink (table format)
        attempts.md  — PoC attempt history with outcomes and key_insights
        strategy.md  — current strategy notes
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional


class MemoryManager:
    """Manages persistent markdown memory files in the task workspace."""

    def __init__(self, workspace_root: str):
        self.root = Path(workspace_root) / ".cybergym" / "memory"

    def ensure_dirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def read_file(self, name: str) -> str:
        """Read a memory file. Returns empty string if not found."""
        path = self.root / name
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def write_file(self, name: str, content: str) -> None:
        """Overwrite a memory file."""
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / name).write_text(content, encoding="utf-8")

    def append_file(self, name: str, content: str) -> None:
        """Append to a memory file."""
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / name).open("a", encoding="utf-8") as f:
            f.write(content + "\n")

    # ------------------------------------------------------------------
    # Render methods — produce markdown from state for each memory file
    # ------------------------------------------------------------------

    def render_memory_index(self, state: Any) -> str:
        """Render MEMORY.md — the auto-loaded index file."""
        parts = ["# CyberGym Agent Memory\n"]
        if state.vulnerability_description:
            parts.append("## Vulnerability\n")
            desc = state.vulnerability_description
            if len(desc) > 800:
                desc = desc[:797] + "..."
            parts.append(desc + "\n")
        if state.bug_type:
            parts.append(f"**Bug Type:** {state.bug_type}\n")
        if state.crash_type:
            loc = f" @ {state.crash_location}" if state.crash_location else ""
            parts.append(f"**Crash:** {state.crash_type}{loc}\n")

        # Summary links
        parts.append("\n## Memory Files\n")
        parts.append("- [sinks.md](sinks.md) — Sink candidates\n")
        parts.append("- [gates.md](gates.md) — Constraint boards\n")
        parts.append("- [attempts.md](attempts.md) — PoC attempt history\n")
        parts.append("- [strategy.md](strategy.md) — Current strategy\n")

        # Brief status
        if state.vulnerability_analysis:
            parts.append(f"\n## Analysis\n{state.vulnerability_analysis}\n")
        if state.current_hypothesis:
            parts.append(f"\n## Hypothesis\n{state.current_hypothesis}\n")
        if state.best_poc_path:
            parts.append(f"\n## Best PoC\nPath: `{state.best_poc_path}` Score: {state.best_poc_score}\n")

        return "\n".join(parts)

    def render_sinks_md(self, state: Any) -> str:
        """Render sinks.md from state.sink_candidates with call chains."""
        parts = ["# Sink Candidates\n"]
        if not state.sink_candidates:
            parts.append("_No sinks recorded yet._\n")
            return "\n".join(parts)

        active_sink_id = str(getattr(state, "active_sink_id", "") or "")

        # Sort: active first, then non-eliminated, then by confidence
        def sort_key(s):
            is_active = 0 if (s.candidate_id == active_sink_id or
                              f"{s.function}@{s.location}" == active_sink_id) else 1
            is_eliminated = 2 if s.status == "eliminated" else 0
            return (is_eliminated, is_active, -float(s.confidence or 0))

        candidates = sorted(state.sink_candidates, key=sort_key)

        for i, s in enumerate(candidates):
            is_active = (s.candidate_id == active_sink_id or
                         f"{s.function}@{s.location}" == active_sink_id)
            status_icon = {
                "active": "●", "eliminated": "✗", "provisional": "○",
                "confirmed": "●",
            }.get(getattr(s, "status", ""), "?")
            marker = "►" if is_active else status_icon

            parts.append(f"\n## {marker} Sink {i+1}: `{s.function}` @ `{s.location}`\n")
            parts.append(f"- **ID:** {getattr(s, 'candidate_id', 'N/A')}\n")
            parts.append(f"- **Confidence:** {getattr(s, 'confidence', 0):.1f}\n")
            parts.append(f"- **Source:** {getattr(s, 'source', 'unknown')}\n")
            if s.evidence:
                parts.append(f"- **Evidence:** {s.evidence}\n")
            if getattr(s, "callee", None):
                parts.append(f"- **Callee:** {s.callee}\n")
            if getattr(s, "expression", None):
                parts.append(f"- **Expression:** {s.expression}\n")

            # Call chain
            sink_id = s.candidate_id or f"{s.function}@{s.location}"
            nodes = state.nodes_for_sink(sink_id)
            if nodes:
                chain = " → ".join(
                    f"**{n.function}**" if n.role == "sink" else f"`{n.function}`"
                    for n in nodes
                )
                parts.append(f"- **Chain:** {chain}\n")

        return "\n".join(parts)

    def render_gates_md(self, state: Any) -> str:
        """Render gates.md from state.call_chain_nodes and call_chain_gates (table format)."""
        parts = ["# Constraint Boards\n"]

        active_sink_id = str(getattr(state, "active_sink_id", "") or "")

        # Call chain mini-map for active sink
        if active_sink_id:
            nodes = state.nodes_for_sink(active_sink_id)
            if nodes:
                chain_parts = []
                for n in nodes:
                    role_marker = {
                        "entry": "▶", "parser": "◈", "dispatch": "◆",
                        "guard": "⊘", "sink": "✱",
                    }.get(getattr(n, "role", ""), "·")
                    chain_parts.append(f"{role_marker}`{n.function}`")
                parts.append(f"\n## Active Sink: {active_sink_id}\n")
                parts.append("Chain: " + " → ".join(chain_parts) + "\n")
            else:
                parts.append(f"\n## Active Sink: {active_sink_id}\n")
                parts.append("Chain: _no nodes_\n")

            # Gate table
            gates = state.gates_for_sink(active_sink_id)
            if gates:
                parts.append("\n| # | Type | On | Required | Status |\n")
                parts.append("|---|------|----|----------|--------|\n")
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
                    parts.append(
                        f"| {i} | {gate_type} | `{on_func}` | {required} | {status_icon} {g.status} |\n"
                    )
            else:
                parts.append("_No gates for this sink._\n")
        else:
            parts.append("_No active sink._\n")

        # Also show all nodes and gates for reference
        if state.call_chain_nodes:
            parts.append("\n## All Chain Nodes\n")
            for i, n in enumerate(state.call_chain_nodes):
                parts.append(
                    f"{i+1}. `{n.function}` @ `{n.location}` — "
                    f"role={getattr(n, 'role', 'unknown')} status={getattr(n, 'status', 'unknown')}"
                )
                if getattr(n, "description", None):
                    parts.append(f"  {n.description}")
                parts.append("")

        if state.call_chain_gates and not active_sink_id:
            # Only show full gate list if no active sink (to avoid duplication)
            parts.append("\n## All Gates\n")
            for i, g in enumerate(state.call_chain_gates):
                status_icon = {"confirmed": "✓", "refuted": "✗", "inferred": "?", "questioned": "?"}.get(
                    g.status, "?"
                )
                parts.append(
                    f"\n### {status_icon} Gate {i+1}: `{g.gate_type}` on `{g.node_function}`\n"
                )
                parts.append(f"- **Description:** {g.description}\n")
                parts.append(f"- **Required:** {g.required_condition}\n")
                parts.append(f"- **Status:** {g.status}\n")
                if g.evidence:
                    parts.append(f"- **Evidence:** {g.evidence}\n")
                if g.repair_hint:
                    parts.append(f"- **Repair Hint:** {g.repair_hint}\n")

        if not state.call_chain_nodes and not state.call_chain_gates:
            parts.append("_No gates recorded yet._\n")

        return "\n".join(parts)

    def render_attempts_md(self, state: Any) -> str:
        """Render attempts.md from state.attempt_history and verification result."""
        parts = ["# PoC Attempts\n"]

        if state.last_verification_result:
            r = state.last_verification_result
            parts.append("\n## Latest Verification\n")
            parts.append(f"- **Result:** {'VERIFIED' if state.is_verified() else 'NOT VERIFIED'}\n")
            vul_code = r.get("vul_exit_code")
            fix_code = r.get("fix_exit_code")
            parts.append(f"- **Vul exit code:** {vul_code}\n")
            parts.append(f"- **Fix exit code:** {fix_code}\n")
            if r.get("crash_type"):
                parts.append(f"- **Crash:** {r['crash_type']}\n")
            key_insight = r.get("key_insight", "") or state.metadata.get("last_key_insight", "")
            if key_insight:
                parts.append(f"- **Key Insight:** {key_insight}\n")

        if state.attempt_history:
            parts.append("\n## History\n")
            for i, a in enumerate(state.attempt_history[-10:]):
                outcome = a.get("outcome", "unknown")
                icon = {"success": "✓", "crash": "●", "miss": "✗", "error": "⚠"}.get(outcome, "?")
                parts.append(f"{i+1}. {icon} `{a.get('path', '?')}` — {outcome}")
                if a.get("detail"):
                    parts.append(f"   {a['detail']}")
                insight = a.get("key_insight", "")
                if insight:
                    parts.append(f"   Key: {insight}")
                parts.append("")

        if not state.attempt_history and not state.last_verification_result:
            parts.append("_No attempts yet._\n")

        return "\n".join(parts)

    def render_strategy_md(self, state: Any) -> str:
        """Render strategy.md from task-persistent memory fields."""
        parts = ["# Strategy\n"]

        if state.vulnerability_analysis:
            parts.append(f"\n## Vulnerability Analysis\n{state.vulnerability_analysis}\n")

        if state.path_trace:
            parts.append("\n## Path Trace\n")
            for p in state.path_trace:
                parts.append(f"- {p}")
            parts.append("")

        if state.current_hypothesis:
            parts.append(f"\n## Current Hypothesis\n{state.current_hypothesis}\n")

        if state.attempt_history_compact:
            parts.append("\n## Attempt Summary\n")
            for a in state.attempt_history_compact:
                parts.append(f"- {a}")
            parts.append("")

        return "\n".join(parts)

    def write_all(self, state: Any) -> None:
        """Write all memory files from current state."""
        self.ensure_dirs()
        self.write_file("MEMORY.md", self.render_memory_index(state))
        self.write_file("sinks.md", self.render_sinks_md(state))
        self.write_file("gates.md", self.render_gates_md(state))
        self.write_file("attempts.md", self.render_attempts_md(state))
        self.write_file("strategy.md", self.render_strategy_md(state))
