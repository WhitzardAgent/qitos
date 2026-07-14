"""Render hooks built on top of the Engine hook system."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from rich.console import Console, Group
from rich.padding import Padding
from rich.rule import Rule
from rich.syntax import Syntax
from rich.text import Text

from qitos.tracing.config import _redact_dict

from ..core.action import Action
from ..engine.hooks import EngineHook, HookContext
from .cli_render import RichRender
from .content_renderer import ContentFirstRenderer
from .events import RenderEvent

if TYPE_CHECKING:
    from ..engine.engine import Engine, EngineResult


class RenderHook(EngineHook):
    """Alias for render-specific hook implementations."""


_CLAUDE_THEME_PRESETS: Dict[str, Dict[str, Any]] = {
    "research": {
        "spinner": "dots",
        "banner_style": "bright_cyan",
        "status_style": "bold cyan",
        "icons": {
            "plan": "◆",
            "thinking": "◈",
            "action": "▶",
            "observation": "◉",
            "memory": "▣",
            "critic": "✦",
            "state": "◍",
            "error": "✖",
            "lifecycle": "●",
        },
        "styles": {
            "plan": "cyan",
            "thinking": "magenta",
            "action": "yellow",
            "observation": "green",
            "memory": "bright_blue",
            "critic": "bright_magenta",
            "state": "blue",
            "error": "red",
            "lifecycle": "bright_black",
        },
    },
    "minimal": {
        "spinner": "line",
        "banner_style": "white",
        "status_style": "bold white",
        "icons": {
            "plan": "P",
            "thinking": "T",
            "action": "A",
            "observation": "O",
            "memory": "M",
            "critic": "C",
            "state": "S",
            "error": "E",
            "lifecycle": "L",
        },
        "styles": {
            "plan": "white",
            "thinking": "white",
            "action": "white",
            "observation": "white",
            "memory": "white",
            "critic": "white",
            "state": "white",
            "error": "red",
            "lifecycle": "bright_black",
        },
    },
    "neon": {
        "spinner": "bouncingBall",
        "banner_style": "bold bright_green",
        "status_style": "bold bright_green",
        "icons": {
            "plan": "⬢",
            "thinking": "⚡",
            "action": "➤",
            "observation": "◎",
            "memory": "⬡",
            "critic": "✶",
            "state": "◌",
            "error": "⨯",
            "lifecycle": "●",
        },
        "styles": {
            "plan": "bright_cyan",
            "thinking": "bright_magenta",
            "action": "bright_yellow",
            "observation": "bright_green",
            "memory": "bright_blue",
            "critic": "bright_magenta",
            "state": "bright_cyan",
            "error": "bright_red",
            "lifecycle": "bright_black",
        },
    },
}

_PHASE_COLORS: Dict[str, str] = {
    "ingestion": "bright_blue",
    "exploration": "bright_cyan",
    "investigation": "bright_yellow",
    "formulation": "bright_magenta",
    "verification": "bright_green",
}


class RenderStreamHook(RenderHook):
    """Emit normalized render events for terminal and frontend consumers."""

    def __init__(self, output_jsonl: Optional[str] = None, jsonl_flush_every: int = 10):
        self.events: List[RenderEvent] = []
        self.output_jsonl = output_jsonl
        self._path = Path(output_jsonl) if output_jsonl else None
        self._jsonl_buffer: List[str] = []
        self._jsonl_flush_every = jsonl_flush_every
        self._cybergym_memory_previous: Dict[str, Any] = {}
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def on_run_start(self, task: str, state: Any, engine: "Engine") -> None:
        self._emit(
            "lifecycle",
            "run_start",
            step_id=0,
            payload={"task": task, "max_steps": engine.budget.max_steps},
        )

    def on_before_step(self, ctx: HookContext, engine: "Engine") -> None:
        agent_id = getattr(ctx.record, "agent_id", None) if ctx.record else None
        agent_phase = getattr(ctx.state, "current_phase", None) or None
        self._emit(
            "lifecycle",
            "step_start",
            step_id=ctx.step_id,
            payload={"phase": ctx.phase.value, "agent_id": agent_id, "agent_phase": agent_phase},
        )

    def on_after_decide(self, ctx: HookContext, engine: "Engine") -> None:
        decision = ctx.decision
        if decision is None:
            return
        payload = {
            "mode": getattr(decision, "mode", None),
            "rationale": getattr(decision, "rationale", None),
            "actions": list(getattr(decision, "actions", []) or []),
            "final_answer": getattr(decision, "final_answer", None),
        }
        self._emit("thinking", "decision", step_id=ctx.step_id, payload=payload)
        if payload["actions"]:
            self._emit(
                "action",
                "planned_actions",
                step_id=ctx.step_id,
                payload={"actions": payload["actions"]},
            )

    def on_after_act(self, ctx: HookContext, engine: "Engine") -> None:
        if ctx.record is not None and ctx.record.tool_invocations:
            self._emit(
                "action",
                "tool_invocations",
                step_id=ctx.step_id,
                payload={"tool_invocations": ctx.record.tool_invocations},
            )
        if ctx.action_results:
            self._emit(
                "observation",
                "action_results",
                step_id=ctx.step_id,
                payload={"action_results": ctx.action_results},
            )

    def on_after_critic(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit("critic", "critic", step_id=ctx.step_id, payload=ctx.payload or {})

    def on_after_reduce(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit(
            "state", "state_diff", step_id=ctx.step_id, payload=ctx.payload or {}
        )

    def on_after_check_stop(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit(
            "lifecycle",
            "check_stop",
            step_id=ctx.step_id,
            payload={
                "result": (ctx.payload or {}).get("result"),
                "stop_reason": ctx.stop_reason,
            },
        )

    def on_recover(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit(
            "error",
            "recover",
            step_id=ctx.step_id,
            payload={"phase": ctx.phase.value, "error": str(ctx.error)},
        )

    def on_after_step(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit(
            "lifecycle",
            "step_end",
            step_id=ctx.step_id,
            payload={"stop_reason": ctx.stop_reason},
        )

    def on_run_end(self, result: "EngineResult", engine: "Engine") -> None:
        self._emit(
            "lifecycle",
            "done",
            step_id=max(0, result.step_count - 1),
            payload={
                "stop_reason": result.state.stop_reason,
                "final_result": result.state.final_result,
                "steps": result.step_count,
            },
        )
        # Flush remaining buffered render events
        self._flush_jsonl()

    def on_event(self, event, state, record, engine) -> None:
        # Promote multi-agent RuntimePhase events to first-class render nodes.
        phase_val = event.phase.value if hasattr(event.phase, "value") else str(event.phase)
        if phase_val in ("HANDOFF_START", "HANDOFF_END", "DELEGATE_START", "DELEGATE_END",
                         "FANOUT_START", "FANOUT_END"):
            channel = "handoff" if phase_val.startswith("HANDOFF") else "delegation"
            node = phase_val.lower()
            self._emit(channel, node, step_id=event.step_id, payload=dict(event.payload or {}))

        # Promote key model I/O events to first-class render nodes.
        # Fix 3A: use lightweight payloads instead of full duplicates.
        if event.phase.value.lower() == "decide" and isinstance(event.payload, dict):
            stage = str(event.payload.get("stage", ""))
            if stage == "state_ready":
                # Use observation_summary instead of full observation (Fix 1C already slimmed this)
                observation_summary = event.payload.get("observation_summary")
                observation = event.payload.get("observation")
                self._emit(
                    "observation",
                    "state",
                    step_id=event.step_id,
                    payload={"observation_summary": observation_summary} if observation_summary
                    else {"observation_type": event.payload.get("observation_type")},
                )
                if isinstance(observation, dict):
                    if "plan_steps" in observation:
                        self._emit(
                            "plan",
                            "plan",
                            step_id=event.step_id,
                            payload={
                                "plan_steps": observation.get("plan_steps"),
                                "plan_cursor": observation.get("plan_cursor"),
                            },
                        )
            elif stage == "model_input":
                # Use messages_summary instead of full messages list (Fix 1C)
                self._emit(
                    "thinking",
                    "model_input",
                    step_id=event.step_id,
                    payload={
                        "prepared": event.payload.get("prepared"),
                        "history_message_count": event.payload.get(
                            "history_message_count"
                        ),
                        "message_count": event.payload.get("message_count"),
                        "messages_summary": event.payload.get("messages_summary"),
                        "model_input_digest": event.payload.get("model_input_digest"),
                        "context": event.payload.get("context"),
                        "state_stats": event.payload.get("state_stats"),
                        "runtime_context_delivery": event.payload.get(
                            "runtime_context_delivery"
                        ),
                        "runtime_context_display": event.payload.get(
                            "runtime_context_display"
                        ),
                    },
                )
                if os.getenv("QITOS_TUI_SHOW_MEMORY", "1").strip().lower() not in {"0", "false", "no", "off"}:
                    snapshot = self._cybergym_memory_snapshot(state)
                    if snapshot:
                        self._emit(
                            "memory",
                            "cybergym_memory",
                            step_id=event.step_id,
                            payload={"snapshot": snapshot},
                        )
            elif stage == "model_output":
                self._emit(
                    "thinking",
                    "model_output",
                    step_id=event.step_id,
                    payload={
                        "raw_output": event.payload.get("raw_output"),
                        "reasoning_content": event.payload.get("reasoning_content"),
                        "model_response": event.payload.get("model_response"),
                        "context": event.payload.get("context"),
                    },
                )
            elif stage == "context_history":
                self._emit(
                    "lifecycle",
                    "context_history",
                    step_id=event.step_id,
                    payload={"context": event.payload.get("context")},
                )
            elif stage == "parser_result":
                self._emit(
                    "parser",
                    "parser_result",
                    step_id=event.step_id,
                    payload=dict(event.payload),
                )
            elif stage == "parser_diagnostics":
                self._emit(
                    "parser",
                    "parser_diagnostics",
                    step_id=event.step_id,
                    payload={"diagnostics": event.payload.get("diagnostics")},
                )
        # Lightweight engine event — only ok/error + stage, not full payload
        self._emit(
            "engine_event",
            event.phase.value.lower(),
            step_id=event.step_id,
            payload={
                "ok": event.ok,
                "error": event.error,
                "stage": event.payload.get("stage") if isinstance(event.payload, dict) else None,
            },
        )

    def _emit(
        self,
        channel: str,
        node: str,
        step_id: int,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        evt = RenderEvent(
            channel=channel, node=node, step_id=step_id, payload=payload or {}
        )
        self.events.append(evt)
        if self._path is not None:
            line = json.dumps(_redact_dict(evt.to_dict()), ensure_ascii=False) + "\n"
            self._jsonl_buffer.append(line)
            if len(self._jsonl_buffer) >= self._jsonl_flush_every:
                self._flush_jsonl()
            else:
                self._flush_jsonl()
        self.on_render_event(evt)

    def _cybergym_memory_snapshot(self, state: Any) -> Dict[str, Any]:
        workspace = str(getattr(state, "workspace_root", "") or "").strip()
        if not workspace:
            metadata = getattr(state, "metadata", None)
            if isinstance(metadata, dict):
                workspace = str(metadata.get("workspace_root") or "").strip()
        if not workspace:
            return {}
        metadata = getattr(state, "metadata", None)
        last_memory_action = dict(metadata.get("last_memory_action") or {}) if isinstance(metadata, dict) else {}
        root = Path(workspace).expanduser()
        memory_dir = root / ".cybergym"
        if not memory_dir.is_dir():
            return {}

        canonical: Dict[str, Any] = {}
        state_path = memory_dir / "state.json"
        if state_path.is_file():
            try:
                parsed = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
                if isinstance(parsed, dict):
                    canonical = parsed
            except Exception:
                canonical = {}

        memory_files = [
            "next_action.md",
            "task_brief.md",
            "harness_model.md",
            "corpus_model.md",
            "repo_model.md",
            "program_model.md",
            "hypothesis_pool.md",
            "construction_plan.md",
            "experiment_ledger.md",
            "gdb_observations.md",
            "exhausted_paths.md",
        ]
        template_markers = (
            "{{",
            "}}",
            "(Not yet initialized.)",
            "No hypothesis selected",
            "No active hypotheses",
            "Starting investigation",
            "{{SUMMARY}}",
        )
        statuses: Dict[str, str] = {}
        contents: Dict[str, str] = {}
        for name in memory_files:
            path = memory_dir / name
            if not path.exists():
                statuses[name] = "missing"
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                statuses[name] = "unknown"
                continue
            contents[name] = text
            stripped = text.strip()
            if not stripped:
                statuses[name] = "empty"
            elif name in {
                "program_model.md", "hypothesis_pool.md", "construction_plan.md",
                "next_action.md", "experiment_ledger.md", "gdb_observations.md",
                "exhausted_paths.md",
            } and canonical:
                statuses[name] = "generated"
            elif any(marker in stripped for marker in template_markers):
                statuses[name] = "template"
            else:
                statuses[name] = "ready"

        slots = canonical.get("slots") if isinstance(canonical.get("slots"), dict) else {}
        slot_sections: Dict[str, List[str]] = {}
        slot_revisions: Dict[str, Any] = {}
        slot_navigation: Dict[str, Dict[str, Any]] = {}
        for slot, record in slots.items():
            if not isinstance(record, dict):
                continue
            slot_revisions[str(slot)] = record.get("revision")
            section_records = [item for item in record.get("sections") or [] if isinstance(item, dict)]
            slot_sections[str(slot)] = [str(item.get("title") or "") for item in section_records if str(item.get("title") or "")]
            standard_titles = {str(title) for title in record.get("standard_sections") or []}
            standards = []
            for title in record.get("standard_sections") or []:
                item = next((value for value in section_records if value.get("title") == title), {})
                content = str(item.get("content") or "").strip()
                standards.append({"title": str(title), "status": "empty" if not content else str(item.get("owner") or "agent")})
            slot_navigation[str(slot)] = {
                "purpose": str(record.get("purpose") or ""),
                "phase": str(record.get("phase") or ""),
                "standards": standards,
                "custom_sections": [
                    str(item.get("title") or "") for item in section_records
                    if item.get("title") not in standard_titles and item.get("title") != "Slot Guide"
                ],
            }
        runtime = canonical.get("runtime") if isinstance(canonical.get("runtime"), dict) else {}
        required = str(runtime.get("required_action") or "")
        if not canonical:
            required = self._extract_markdown_section(contents.get("next_action.md", ""), "Required Action")
        ledger_recent = self._recent_table_rows(contents.get("experiment_ledger.md", ""), 3)
        gdb_recent = self._recent_markdown_headers(contents.get("gdb_observations.md", ""), 2)
        compact_state = {
            "statuses": statuses,
            "ledger_count": len(canonical.get("experiments") or []) if canonical else len(self._recent_table_rows(contents.get("experiment_ledger.md", ""), 100000)),
            "gdb_count": len(canonical.get("gdb_sessions") or []) if canonical else len(self._recent_markdown_headers(contents.get("gdb_observations.md", ""), 100000)),
            "revision": canonical.get("revision") if canonical else None,
            "ready_candidates": len([
                item for item in ((canonical.get("construction") or {}).get("ready_candidates") or [])
                if isinstance(item, dict) and item.get("status") == "ready"
            ]) if canonical else 0,
            "deadline_missed_at": (canonical.get("runtime") or {}).get("submission_deadline_missed_at") if canonical else None,
        }
        delta: List[str] = []
        previous = self._cybergym_memory_previous
        previous_statuses = previous.get("statuses") if isinstance(previous.get("statuses"), dict) else {}
        for key, value in statuses.items():
            old = previous_statuses.get(key)
            if old and old != value:
                delta.append(f"{key}:{old}->{value}")
        for key, label in (("ledger_count", "ledger"), ("gdb_count", "gdb")):
            old = previous.get(key)
            new = compact_state.get(key)
            if isinstance(old, int) and isinstance(new, int) and new > old:
                delta.append(f"{label}+{new - old}")
        previous_slots = previous.get("slot_revisions") if isinstance(previous.get("slot_revisions"), dict) else {}
        for slot, revision in slot_revisions.items():
            if previous_slots.get(slot) not in (None, revision):
                delta.append(f"{slot}@{revision}")
        compact_state["slot_revisions"] = slot_revisions
        self._cybergym_memory_previous = compact_state
        return {
            "phase": getattr(state, "current_phase", ""),
            "memory_dir": str(memory_dir),
            "file_statuses": statuses,
            "next_required": required,
            "slot_sections": slot_sections,
            "slot_navigation": slot_navigation,
            "ledger_recent": ledger_recent,
            "gdb_recent": gdb_recent,
            "delta": delta,
            "state_revision": compact_state["revision"],
            "ready_candidates": compact_state["ready_candidates"],
            "deadline_missed_at": compact_state["deadline_missed_at"],
            "last_memory_action": last_memory_action,
            "state_present": bool(canonical),
        }

    @staticmethod
    def _extract_markdown_section(text: str, heading: str) -> str:
        if not text:
            return ""
        pattern = rf"^##\s+{re.escape(heading)}\s*$"
        lines = text.splitlines()
        start = -1
        for idx, line in enumerate(lines):
            if re.match(pattern, line.strip(), flags=re.IGNORECASE):
                start = idx + 1
                break
        if start < 0:
            return ""
        collected: List[str] = []
        for line in lines[start:]:
            if line.startswith("## "):
                break
            stripped = line.strip()
            if stripped:
                collected.append(stripped)
            if len(collected) >= 3:
                break
        return " ".join(collected)

    @staticmethod
    def _recent_table_rows(text: str, limit: int) -> List[str]:
        rows: List[str] = []
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                continue
            if "POC Path" in stripped and "Result" in stripped:
                continue
            cells = [cell.strip() for cell in stripped.split("|") if cell.strip()]
            if len(cells) >= 5:
                # V27's ledger adds hypothesis/mutation/parent/GDB columns;
                # retain the actual Result column in the compact memory panel.
                result_index = 5 if len(cells) >= 9 else 4
                rows.append(f"{cells[0]} {cells[1]} {cells[result_index]}")
        return rows[-limit:]

    @staticmethod
    def _recent_markdown_headers(text: str, limit: int) -> List[str]:
        headers = [
            line.strip("# ").strip()
            for line in text.splitlines()
            if line.startswith("##") and line.strip("# ").strip()
        ]
        return headers[-limit:]

    def _flush_jsonl(self) -> None:
        """Flush buffered render events to disk."""
        if not self._jsonl_buffer or self._path is None:
            return
        with self._path.open("a", encoding="utf-8") as f:
            f.write("".join(self._jsonl_buffer))
        self._jsonl_buffer.clear()

    def flush(self) -> None:
        """Public flush for external callers (e.g., step boundary)."""
        self._flush_jsonl()

    def on_render_event(self, event: RenderEvent) -> None:
        """Override in subclasses for side effects (console/UI streaming)."""
        return None


class _TeeConsole:
    """Proxy that forwards every print/rule/log call to two Rich Consoles."""

    def __init__(self, primary: Console, secondary: Console):
        object.__setattr__(self, "_primary", primary)
        object.__setattr__(self, "_secondary", secondary)

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._primary, name)
        if callable(attr):
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    self._secondary.__getattribute__(name)(*args, **kwargs)
                except Exception:
                    pass
                return attr(*args, **kwargs)
            return wrapper
        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._primary, name, value)

    @property
    def is_terminal(self) -> bool:
        return self._primary.is_terminal


class ClaudeStyleHook(RenderStreamHook):
    """Content-first terminal output focused on task, thought, action, observation, memory."""

    def __init__(
        self,
        output_jsonl: Optional[str] = None,
        max_preview_chars: int = 50000,
        theme: str = "research",
        log_file: Optional[str] = None,
    ):
        super().__init__(output_jsonl=output_jsonl)
        env_max = os.getenv("QITOS_TUI_MAX_TOOL_CHARS", "").strip()
        if env_max:
            try:
                max_preview_chars = int(env_max)
            except ValueError:
                pass
        self.max_preview_chars = max_preview_chars
        self.detail_mode = os.getenv("QITOS_TUI_DETAIL", "normal").strip().lower() or "normal"
        self.show_context_digest = os.getenv("QITOS_TUI_SHOW_CONTEXT", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.show_memory_panel = os.getenv("QITOS_TUI_SHOW_MEMORY", "1").strip().lower() not in {"0", "false", "no", "off"}
        # Per-task TUI log file: Rich Console writes the same rendered output
        # to both the terminal and a plain-text log file, preserving the
        # STEP / finish / tool_calls / ctx_used format for offline analysis.
        self._log_file = None
        self._log_console = None
        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_file = open(log_path, "w", encoding="utf-8")
            self._log_console = Console(file=self._log_file, width=200, no_color=True, legacy_windows=False)
            self.console = _TeeConsole(Console(), self._log_console)
        else:
            self.console = Console()
        self._last_step: Optional[int] = None
        self._last_agent_id: Optional[str] = None
        self._status: Any = None
        chosen = _CLAUDE_THEME_PRESETS.get(theme, _CLAUDE_THEME_PRESETS["research"])
        self.theme_name = theme if theme in _CLAUDE_THEME_PRESETS else "research"
        self._spinner: str = "arc"
        self._renderer = ContentFirstRenderer(max_preview_chars=max_preview_chars)
        self._thought_steps: set[int] = set()
        self._action_steps: set[int] = set()
        self._observation_steps: set[int] = set()
        self._state_steps: set[int] = set()
        self._memory_steps: set[int] = set()
        self._parser_steps: set[tuple[int, str]] = set()
        self._pending_state_stats: Dict[int, Dict[str, Any]] = {}
        self._rendered_action_indices: set[tuple[int, int]] = set()
        self._rendered_observation_indices: set[tuple[int, int]] = set()

    @staticmethod
    def _phase_badge(phase: str) -> str:
        color = _PHASE_COLORS.get(phase, "gray")
        return f"[bold white on {color}] {phase.upper()} [/bold white on {color}]"

    def _should_render_parser_diagnostic(self, diag: Dict[str, Any]) -> bool:
        severity = str(diag.get("severity") or "error").lower()
        if severity == "error":
            return True
        if diag.get("salvage_applied"):
            return False
        code = str(diag.get("code") or "").strip().lower()
        if code.startswith("salvaged_"):
            return False
        return True

    def on_run_start(self, task: str, state: Any, engine: "Engine") -> None:
        super().on_run_start(task, state, engine)
        self._print_agent_composition(engine)
        self._start_status("[dim]Agent is warming up...[/dim]")

    def on_before_decide(self, ctx: HookContext, engine: "Engine") -> None:
        self._update_status("[dim]Agent is brainstorming...[/dim]")

    def on_before_act(self, ctx: HookContext, engine: "Engine") -> None:
        self._update_status("[dim]Agent is executing actions...[/dim]")

    def on_before_critic(self, ctx: HookContext, engine: "Engine") -> None:
        self._update_status("[dim]Agent is self-critiquing...[/dim]")

    def on_before_reduce(self, ctx: HookContext, engine: "Engine") -> None:
        self._update_status("[dim]Agent is updating state...[/dim]")

    def on_before_check_stop(self, ctx: HookContext, engine: "Engine") -> None:
        self._update_status("[dim]Agent is evaluating stop criteria...[/dim]")

    def on_run_end(self, result: "EngineResult", engine: "Engine") -> None:
        self._stop_status()
        super().on_run_end(result, engine)
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None
            self._log_console = None

    def on_render_event(self, event: RenderEvent) -> None:
        if event.node == "run_start":
            self._print_banner()
            self.console.print(Rule("[dim]RUN[/dim]", style="gray23"))
            task = (event.payload or {}).get("task", "")
            if task:
                preview = (task[:500] + "...") if len(task) > 500 else task
                self._rail("cyan", f"[bold cyan]TASK[/bold cyan] {preview}")
            return

        if event.node == "step_start":
            self._last_step = event.step_id
            agent_id = (event.payload or {}).get("agent_id")
            agent_phase = (event.payload or {}).get("agent_phase")
            label = f"STEP {event.step_id + 1}"
            if agent_id:
                label += f" ── agent: {agent_id}"
                if self._last_agent_id is not None and self._last_agent_id != agent_id:
                    self._rail(
                        "yellow",
                        f"⚡ Agent switched: [bold]{self._last_agent_id}[/bold] → [bold]{agent_id}[/bold]",
                    )
                self._last_agent_id = agent_id
            self.console.print(Rule(label, style="gray23"))
            # Phase badge — colored pill below the STEP separator
            if agent_phase:
                self._rail(
                    _PHASE_COLORS.get(agent_phase, "blue"),
                    self._phase_badge(agent_phase),
                )
            return

        if event.channel == "thinking":
            if event.node == "model_input":
                if event.step_id not in self._state_steps:
                    stats = dict(self._pending_state_stats.pop(event.step_id, {}))
                    model_stats = self._renderer.state_summary(event) or {}
                    stats.update(model_stats)
                    if stats:
                        fixed = self._render_state_row(stats)
                        self._rail("gray40", f"[dim]State[/dim] [dim]{fixed}[/dim]")
                    if self.show_context_digest:
                        digest = (event.payload or {}).get("model_input_digest")
                        if isinstance(digest, dict):
                            for line in self._render_context_digest(digest):
                                self._rail("gray50", f"[dim]{line}[/dim]")
                    # Render Constraint Board — same text the LLM sees
                    constraint_board = stats.get("constraint_board")
                    if isinstance(constraint_board, str) and constraint_board.strip():
                        self._rail("cyan", "[bold cyan]── Constraint Board ──[/bold cyan]")
                        for line in constraint_board.strip().splitlines():
                            stripped = line.strip()
                            if not stripped:
                                self.console.print("")
                                continue
                            # Color-code by semantics (matching LLM-facing format)
                            if stripped.startswith("- FIRST BLOCKER"):
                                self._rail("bold yellow", f"[bold yellow]{stripped}[/bold yellow]")
                            elif "[refuted]" in stripped or "Refuted Gates" in stripped:
                                self._rail("red", f"[red]{stripped}[/red]")
                            elif "repair:" in stripped:
                                self._rail("bright_red", f"[bright_red]{stripped}[/bright_red]")
                            elif "[confirmed]" in stripped or "Confirmed Gates" in stripped:
                                self._rail("green", f"[green]{stripped}[/green]")
                            elif "[inferred" in stripped or "[unknown" in stripped or "Open Gates" in stripped:
                                self._rail("yellow", f"[yellow]{stripped}[/yellow]")
                            elif stripped.startswith("- [") and "] entry" in stripped:
                                self._rail("bright_cyan", f"[bright_cyan]{stripped}[/bright_cyan]")
                            elif stripped.startswith("- [") and "] sink" in stripped:
                                self._rail("bright_magenta", f"[bright_magenta]{stripped}[/bright_magenta]")
                            elif stripped.startswith("- [") and "] parser" in stripped:
                                self._rail("cyan", f"[cyan]{stripped}[/cyan]")
                            elif stripped.startswith("- [") and "] guard" in stripped:
                                self._rail("bright_yellow", f"[bright_yellow]{stripped}[/bright_yellow]")
                            elif stripped.startswith("- [") and "] dispatch" in stripped:
                                self._rail("blue", f"[blue]{stripped}[/blue]")
                            elif "Chain Gates:" in stripped:
                                self._rail("bold cyan", f"[bold cyan]{stripped}[/bold cyan]")
                            else:
                                self._rail("gray70", f"[dim]{stripped}[/dim]")
                    # Render Task Memory — same text the LLM sees
                    task_memory = stats.get("task_memory")
                    if isinstance(task_memory, str) and task_memory.strip():
                        self._rail("magenta", "[bold magenta]── Task Memory ──[/bold magenta]")
                        for line in task_memory.strip().splitlines():
                            stripped = line.strip()
                            if not stripped:
                                continue
                            if stripped.startswith("- Analysis:"):
                                self._rail("blue", f"[blue]{stripped}[/blue]")
                            elif stripped.startswith("- Hypothesis:"):
                                self._rail("magenta", f"[bold magenta]{stripped}[/bold magenta]")
                            elif stripped.startswith("- Path:"):
                                self._rail("cyan", f"[cyan]{stripped}[/cyan]")
                            elif stripped.startswith("- Attempt:"):
                                self._rail("gray70", f"[dim]{stripped}[/dim]")
                            else:
                                self._rail("gray70", stripped)
                    # Render Sink Candidates — same text the LLM sees
                    sink_candidates = stats.get("sink_candidates")
                    if isinstance(sink_candidates, str) and sink_candidates.strip():
                        self._rail("magenta", "[bold magenta]── Sink Candidates ──[/bold magenta]")
                        for line in sink_candidates.strip().splitlines():
                            stripped = line.strip()
                            if not stripped:
                                continue
                            if "CHECKPOINT BLOCKED" in stripped:
                                self._rail("bold red", f"[bold red]{stripped}[/bold red]")
                            elif stripped.startswith("No sink candidates") or "REQUIRED" in stripped:
                                self._rail("bright_yellow", f"[bright_yellow]{stripped}[/bright_yellow]")
                            elif stripped.startswith("- `") and "high conf" in stripped:
                                self._rail("bright_magenta", f"[bright_magenta]{stripped}[/bright_magenta]")
                            elif stripped.startswith("- `") and "medium conf" in stripped:
                                self._rail("magenta", f"[magenta]{stripped}[/magenta]")
                            elif stripped.startswith("- `"):
                                self._rail("gray70", f"[dim]{stripped}[/dim]")
                            elif stripped.startswith("Sink Candidates"):
                                self._rail("bold magenta", f"[bold magenta]{stripped}[/bold magenta]")
                            else:
                                self._rail("gray70", stripped)
                    # Render Objective — same text the LLM sees
                    objective = stats.get("objective")
                    if isinstance(objective, str) and objective.strip():
                        self._rail("green", f"[green]── Objective ──[/green] {objective.strip()}")
                    # Render Task Context — vulnerability, bug type, input format
                    task_ctx = stats.get("task_context")
                    if isinstance(task_ctx, str) and task_ctx.strip():
                        self._rail("cyan", "[bold cyan]── Task Context ──[/bold cyan]")
                        for line in task_ctx.strip().splitlines():
                            stripped = line.strip()
                            if not stripped:
                                continue
                            if stripped.startswith("Vulnerability:"):
                                self._rail("bright_cyan", f"[bright_cyan]{stripped}[/bright_cyan]")
                            elif stripped.startswith("Bug Type:"):
                                self._rail("yellow", f"[yellow]{stripped}[/yellow]")
                            elif stripped.startswith("Strategy:"):
                                self._rail("green", f"[green]{stripped}[/green]")
                            elif stripped.startswith("Input Format:"):
                                self._rail("blue", f"[blue]{stripped}[/blue]")
                            elif "CHECKPOINT" in stripped:
                                self._rail("bold red", f"[bold red]{stripped}[/bold red]")
                            else:
                                self._rail("gray70", stripped)
                    # Render Allowed Tools — checkpoint state
                    allowed_tools = stats.get("allowed_tools")
                    if isinstance(allowed_tools, str) and allowed_tools.strip():
                        self._rail("gray50", "[dim]── Allowed Tools ──[/dim]")
                        for line in allowed_tools.strip().splitlines():
                            stripped = line.strip()
                            if not stripped:
                                continue
                            if "CHECKPOINT" in stripped or "BLOCKED" in stripped:
                                self._rail("bold red", f"[bold red]{stripped}[/bold red]")
                            elif stripped.startswith("- `record_sink"):
                                self._rail("bright_magenta", f"[bright_magenta]{stripped}[/bright_magenta]")
                            else:
                                self._rail("gray50", f"[dim]{stripped}[/dim]")
                    # Render Suggested Sinks — auto-discovered, unconfirmed
                    suggested_sinks = stats.get("suggested_sinks")
                    if isinstance(suggested_sinks, str) and suggested_sinks.strip():
                        self._rail("bright_blue", "[bold bright_blue]── Suggested Sinks ──[/bold bright_blue]")
                        for line in suggested_sinks.strip().splitlines():
                            stripped = line.strip()
                            if not stripped:
                                continue
                            self._rail("bright_blue", f"[bright_blue]{stripped}[/bright_blue]")
                    runtime_display = (event.payload or {}).get(
                        "runtime_context_display"
                    )
                    if isinstance(runtime_display, dict):
                        runtime_text = str(runtime_display.get("content") or "").strip()
                        if runtime_text:
                            from rich.markup import escape

                            tool_call_id = runtime_display.get("tool_call_id") or "unknown"
                            self._rail(
                                "bright_green",
                                "[bold bright_green]── Runtime Context "
                                f"(folded into tool {escape(str(tool_call_id))}) ──[/bold bright_green]",
                            )
                            for line in runtime_text.splitlines():
                                self._rail("gray70", f"[dim]{escape(line)}[/dim]")
                    self._state_steps.add(event.step_id)
                return
            if event.step_id in self._thought_steps:
                return
            thought = self._renderer.thought_text(event)
            if thought:
                self._rail(
                    "purple",
                    "[purple]⦿[/purple] [italic slate_blue3]"
                    + thought
                    + "[/italic slate_blue3]",
                )
                response_summary = self._renderer.model_response_summary(event)
                if response_summary:
                    self._rail("gray50", f"[dim]{response_summary}[/dim]")
                self._thought_steps.add(event.step_id)
            return

        if event.node == "context_history":
            compact = self._renderer.compact_summary(event)
            if compact:
                self._update_status("[dim]Agent is compacting context...[/dim]")
                self._rail(
                    compact.get("color", "gray50"),
                    compact.get("text", "Context update"),
                )
            return

        if event.channel == "parser":
            if event.node == "parser_result":
                payload = event.payload or {}
                if (
                    payload.get("has_diagnostics")
                    and str(payload.get("severity") or "").lower() == "error"
                ):
                    self._update_status(
                        "[dim]Agent is repairing output contract...[/dim]"
                    )
                return
            if event.node == "parser_diagnostics":
                key = (event.step_id, event.node)
                if key in self._parser_steps:
                    return
                diag = self._renderer.parser_diagnostic_summary(event)
                if diag:
                    if not self._should_render_parser_diagnostic(diag):
                        self._parser_steps.add(key)
                        return
                    color = str(diag.get("color") or "red")
                    severity = str(diag.get("severity") or "error")
                    badge = "PARSER ERROR" if severity == "error" else "PARSER WARNING"
                    line = f"[bold white on {color}] {badge} [/bold white on {color}]"
                    suffix = " · ".join(
                        part for part in (diag.get("parser"), diag.get("code")) if part
                    )
                    if suffix:
                        line += f" [dim]{suffix}[/dim]"
                    self._rail(color, line)
                    self._rail(color, str(diag.get("summary") or ""))
                    if diag.get("details"):
                        self._rail(color, f"[dim]{diag.get('details')}[/dim]")
                    if diag.get("protocol"):
                        self._rail(
                            color, f"[dim]Protocol:[/dim] {diag.get('protocol')}"
                        )
                    if diag.get("selected_parser"):
                        parser_line = (
                            f"[dim]Selected parser:[/dim] {diag.get('selected_parser')}"
                        )
                        if diag.get("fallback_used"):
                            parser_line += " [dim](fallback)[/dim]"
                        self._rail(color, parser_line)
                    if diag.get("extraction_mode"):
                        self._rail(
                            color,
                            f"[dim]Extraction:[/dim] {diag.get('extraction_mode')}",
                        )
                    if diag.get("expected_shape"):
                        self._rail(
                            color, f"[dim]Expected:[/dim] {diag.get('expected_shape')}"
                        )
                    if diag.get("repair_instruction"):
                        self._rail(
                            color,
                            f"[bold]Repair:[/bold] {diag.get('repair_instruction')}",
                        )
                    if diag.get("raw_output_preview"):
                        self._rail(
                            color,
                            f"[dim]Raw preview:[/dim] {diag.get('raw_output_preview')}",
                        )
                    if diag.get("salvage_summary"):
                        self._rail(
                            color, f"[dim]Salvage:[/dim] {diag.get('salvage_summary')}"
                        )
                self._parser_steps.add(key)
                return

        if event.channel == "action":
            if event.node == "tool_invocations" and event.step_id in self._action_steps:
                # Planned actions already contain the canonical model request.
                # Results, including failures, are rendered as observations;
                # rendering invocation metadata again only duplicates actions.
                return
            event_key = (event.step_id, id(event))
            if event_key in self._rendered_action_indices:
                return
            action = self._renderer.action_summary(event)
            if action:
                action_count = action.get("action_count", 1)
                sub_actions = action.get("actions")
                status = action.get("status", "neutral")
                bg = "blue" if status != "error" else "red"

                # Actions can be scheduled serially even when a model emitted
                # several calls. Do not infer parallelism from their count.
                if action_count > 1 and sub_actions:
                    self._rail(
                        "bright_blue",
                        f"🚀 [bold white on bright_blue] {action_count} ACTIONS [/bold white on bright_blue]",
                    )
                    for i, sub in enumerate(sub_actions):
                        idx_key = (event.step_id, i)
                        if idx_key in self._rendered_action_indices:
                            continue
                        self._render_full_action(sub, bg=bg, prefix="  ")
                        self._rendered_action_indices.add(idx_key)
                else:
                    self._render_full_action(action, bg=bg)

                self._action_steps.add(event.step_id)
                self._rendered_action_indices.add(event_key)
            return

        if event.channel == "observation":
            if event.node in {"state", "observation"}:
                stats = self._renderer.state_summary(event)
                if stats:
                    self._pending_state_stats[event.step_id] = dict(stats)
                return
            event_key = (event.step_id, id(event))
            if event_key in self._rendered_observation_indices:
                return
            obs = self._renderer.observation_summary(event)
            if obs:
                obs_count = obs.get("observation_count", 0)
                all_obs = obs.get("all_observations")

                # Multi-observation: show all results with index labels
                if obs_count > 1 and all_obs:
                    self._rail(
                        "bright_green",
                        f"🔎 [bold white on bright_green] {obs_count} RESULTS [/bold white on bright_green]",
                    )
                    for i, sub_obs in enumerate(all_obs):
                        idx_key = (event.step_id, i)
                        if idx_key in self._rendered_observation_indices:
                            continue
                        self._render_single_observation(sub_obs, index=i + 1)
                        self._rendered_observation_indices.add(idx_key)
                else:
                    self._render_single_observation(obs)

                self._observation_steps.add(event.step_id)
                self._rendered_observation_indices.add(event_key)
            return

        if event.channel == "memory":
            if not self.show_memory_panel:
                return
            if event.step_id in self._memory_steps:
                return
            mem = self._renderer.memory_summary(event)
            if mem:
                label = "Memory" if event.node == "cybergym_memory" else "memory"
                self._rail("gray50", f"[dim]{label}[/dim] [dim]{mem}[/dim]")
                self._memory_steps.add(event.step_id)
            return

        if event.channel == "handoff":
            payload = event.payload or {}
            if event.node == "handoff_start":
                from_agent = payload.get("from", "?")
                to_agent = payload.get("to", "?")
                self._rail(
                    "yellow",
                    f"[bold yellow]⇄ HANDOFF[/bold yellow] [dim]{from_agent}[/dim] → [bold]{to_agent}[/bold]",
                )
            elif event.node == "handoff_end":
                self._rail(
                    "yellow",
                    f"[dim]⇄ Handoff complete[/dim]",
                )
            return

        if event.channel == "delegation":
            payload = event.payload or {}
            if event.node.startswith("delegate"):
                agent_name = payload.get("agent_name", payload.get("agent", "?"))
                task = payload.get("task", "")
                task_preview = (task[:80] + "...") if len(task) > 80 else task
                if event.node == "delegate_start":
                    self._rail(
                        "blue",
                        f"[bold blue]↗ DELEGATE[/bold blue] → [bold]{agent_name}[/bold]"
                        + (f" [dim]{task_preview}[/dim]" if task_preview else ""),
                    )
                elif event.node == "delegate_end":
                    status = payload.get("status", "done")
                    color = "green" if status == "done" else "red"
                    self._rail(
                        color,
                        f"[dim]↗ Delegate result:[/dim] [bold]{agent_name}[/bold] [dim]({status})[/dim]",
                    )
            elif event.node.startswith("fanout"):
                task_count = payload.get("task_count", payload.get("num_tasks", 0))
                if event.node == "fanout_start":
                    self._rail(
                        "bright_magenta",
                        f"[bold bright_magenta]⊛ FANOUT[/bold bright_magenta] [dim]{task_count} task(s) dispatched[/dim]",
                    )
                elif event.node == "fanout_end":
                    succeeded = payload.get("succeeded", 0)
                    failed = payload.get("failed", 0)
                    self._rail(
                        "bright_magenta",
                        f"[dim]⊛ FanOut complete:[/dim] [green]{succeeded} succeeded[/green], [red]{failed} failed[/red]",
                    )
            return

        if event.node == "step_end":
            self.console.print()
            return

        if event.node == "done":
            self.console.print(Rule("[bold]DONE[/bold]", style="gray23"))
            summary = self._renderer.done_summary(
                stop_reason=event.payload.get("stop_reason"),
                final_result=event.payload.get("final_result"),
            )
            self._rail("green", f"[bold green]{summary}[/bold green]")
            return

    def _print_banner(self) -> None:
        self.console.print(
            Rule(
                "[bold bright_cyan]QitOS: A Relaxable Agentic Framework for Reseachers [/bold bright_cyan]",
                style="bright_cyan",
            )
        )
        self.console.print(
            "[bright_cyan]   ██████╗ ██╗████████╗ ██████╗ ███████╗[/bright_cyan]"
        )
        self.console.print("[cyan]  ██╔═══██╗██║╚══██╔══╝██╔═══██╗██╔════╝[/cyan]")
        self.console.print("[blue]  ██║   ██║██║   ██║   ██║   ██║███████╗[/blue]")
        self.console.print(
            "[bright_blue]  ██║▄▄ ██║██║   ██║   ██║   ██║╚════██║[/bright_blue]"
        )
        self.console.print("[blue]  ╚██████╔╝██║   ██║   ╚██████╔╝███████║[/blue]")
        self.console.print("[cyan]   ╚══▀▀═╝ ╚═╝   ╚═╝    ╚═════╝ ╚══════╝[/cyan]")
        self.console.print(
            f"[dim]minimalist stream runtime · theme={self.theme_name}[/dim]"
        )
        self.console.print()

    def _rail(self, color: str, line: str) -> None:
        grp = Group(
            Padding(Text.from_markup(f"[{color}]┃[/{color}] {line}"), (0, 0, 0, 0)),
        )
        self.console.print(grp)

    def _render_single_observation(self, obs: Dict[str, Any], index: int | None = None) -> None:
        """Render one observation block. *index* is 1-based for multi-observation display."""
        status = str(obs.get("status", "neutral"))
        color = (
            "green"
            if status == "success"
            else ("red" if status == "error" else "blue")
        )
        title = str(obs.get("title", "Observation"))
        prefix = f"  └[{index}]" if index is not None else "🔎"

        if status == "error":
            self._rail("red", f"{prefix} [red][✘] Error: {title}[/red]")
            # A failed tool call is still an observation.  In particular,
            # CyberGym tools return model-facing recovery Cards (for example
            # an invalid cursor retry or a missing PoC filename) in ``body``.
            # Returning immediately used to hide that actionable Card in the
            # TUI while the provider history retained it, creating two
            # incompatible views of the same result.
            body = str(obs.get("body", "")).strip()
            if body:
                self._rail("red", f"[red]{body}[/red]")
            url = str(obs.get("url", "")).strip()
            if url:
                self._rail("red", f"[dim]URL: {url}[/dim]")
            return

        self._rail(
            color,
            f"{prefix} [bold {color}]Observation[/bold {color}] [bold italic]Title:[/bold italic] {title}",
        )
        url = str(obs.get("url", "")).strip()
        if url:
            self._rail(color, f"[dim]URL: {url}[/dim]")
        body = str(obs.get("body", "")).strip()
        if body:
            self._rail(
                color, body if status != "error" else f"[red]{body}[/red]"
            )
        table = obs.get("table")
        syntax = obs.get("syntax")
        if table is not None:
            self.console.print(Text("┃", style=color), end=" ")
            self.console.print(table)
        if isinstance(syntax, Syntax):
            self.console.print(Text("┃", style=color), end=" ")
            self.console.print(syntax)
        secondary = obs.get("secondary")
        if isinstance(secondary, dict):
            secondary_title = str(
                secondary.get("title", "Tool Observation")
            ).strip() or "Tool Observation"
            secondary_body = str(secondary.get("body", "")).strip()
            secondary_url = str(secondary.get("url", "")).strip()
            secondary_table = secondary.get("table")
            secondary_syntax = secondary.get("syntax")
            self._rail(
                "blue",
                "📎 [bold blue]Tool Observation[/bold blue] "
                f"[bold italic]Title:[/bold italic] {secondary_title}",
            )
            if secondary_url:
                self._rail("blue", f"[dim]URL: {secondary_url}[/dim]")
            if secondary_body:
                self._rail("blue", secondary_body)
            if secondary_table is not None:
                self.console.print(Text("┃", style="blue"), end=" ")
                self.console.print(secondary_table)
            if isinstance(secondary_syntax, Syntax):
                self.console.print(Text("┃", style="blue"), end=" ")
                self.console.print(secondary_syntax)

    def _render_state_row(self, stats: Dict[str, Any]) -> str:
        order = [
            ("input_tokens_total", "ctx_used"),
            ("occupancy_ratio", "ctx_pct"),
            ("system_prompt_tokens", "sys_toks"),
            ("prepared_tokens", "anchor_toks"),
            ("history_tokens", "hist_toks"),
            ("counting_mode", "count_mode"),
            ("planned_prompt_tokens", "plan_toks"),
            ("provider_prompt_tokens", "provider_toks"),
            ("meter_source", "meter"),
        ]
        cells: List[str] = []
        for key, label in order:
            raw = stats.get(key, "-")
            if key == "input_tokens_total":
                budget = stats.get("available_input_budget")
                value = "-" if raw is None else str(raw)
                if budget is not None:
                    value += f"/{budget}"
            elif key == "occupancy_ratio" and isinstance(raw, (int, float)):
                value = f"{raw * 100:5.1f}%"
            else:
                value = "-" if raw is None else str(raw)
            cell = f"{label:<9} {value:>6}"
            cells.append(cell)
        return "  ".join(cells)

    def _render_full_action(self, action: Dict[str, Any], *, bg: str, prefix: str = "") -> None:
        """Render exact action arguments without applying display truncation."""
        label = str(action.get("label") or "ACTION")
        action_id = str(action.get("action_id") or "")
        suffix = f" [dim]id={action_id}[/dim]" if action_id else ""
        self._rail("blue", f"{prefix}🚀 [bold white on {bg}] Action {label} [/bold white on {bg}]{suffix}")
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        if not args:
            self._rail("blue", f"{prefix}   [dim](no arguments)[/dim]")
            return
        for key, value in args.items():
            if isinstance(value, (dict, list)):
                rendered = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
            else:
                rendered = str(value)
            rendered_lines = rendered.splitlines() or [""]
            self._rail("blue", f"{prefix}   [cyan]{key}[/cyan]={rendered_lines[0]}")
            for line in rendered_lines[1:]:
                self._rail("blue", f"{prefix}     {line}")

    def _render_context_digest(self, digest: Dict[str, Any]) -> List[str]:
        role_counts = digest.get("role_counts") if isinstance(digest.get("role_counts"), dict) else {}
        roles = ", ".join(f"{key}={value}" for key, value in sorted(role_counts.items()))
        sections = digest.get("sections") if isinstance(digest.get("sections"), dict) else {}
        active_sections = [key for key, value in sections.items() if value]
        line = (
            "Context digest "
            f"messages={digest.get('message_count', '-')} "
            f"roles=[{roles or '-'}] "
            f"tool_calls={digest.get('tool_call_count', 0)} "
            f"hash={digest.get('messages_hash', '-')}"
        )
        if active_sections:
            line += " sections=" + ",".join(active_sections)
        out = [line]
        sidecar = str(digest.get("sidecar_path") or "").strip()
        if sidecar:
            out.append("Context sidecar " + self._truncate_middle(sidecar, 150))
        if self.detail_mode == "debug":
            recent = digest.get("recent_history")
            if isinstance(recent, list) and recent:
                parts = []
                for item in recent[-6:]:
                    if not isinstance(item, dict):
                        continue
                    label = str(item.get("role") or "?")
                    if item.get("name"):
                        label += f":{item.get('name')}"
                    elif item.get("tool_names"):
                        label += ":" + ",".join(str(x) for x in item.get("tool_names") or [])
                    label += f"({item.get('content_len', 0)})"
                    parts.append(label)
                if parts:
                    out.append("Context recent " + " -> ".join(parts))
        return out

    def _print_agent_composition(self, engine: "Engine") -> None:
        self.console.print(Rule("[dim]AGENT COMPOSITION[/dim]", style="gray23"))
        memory_name = self._memory_name(engine)
        history_name = self._history_name(engine)
        model_name = self._model_name(engine)
        protocol_name = self._protocol_name(engine)
        prompt_name = self._prompt_name(engine)
        planning_name = self._planning_name(engine)
        tools = self._tool_list(engine)
        tools_desc = ", ".join(tools[:8]) if tools else "none"
        if len(tools) > 8:
            tools_desc += ", ..."
        rows = [
            ("memory", memory_name),
            ("history", history_name),
            ("base_model", model_name),
            ("protocol", protocol_name),
            ("prompt", prompt_name),
            ("context", self._context_row(engine)),
            ("planning", planning_name),
            ("tools", f"{tools_desc} ({len(tools)})"),
        ]
        # Multi-agent info
        agent_registry = getattr(engine, "agent_registry", None)
        if agent_registry is not None and hasattr(agent_registry, "list_available"):
            available = list(agent_registry.list_available())
            if available:
                agent_names = ", ".join(s.name for s in available)
                rows.append(("agents", agent_names))
                # Determine mode from tool registry
                tool_names = set(tools)
                mode_parts = []
                if any("delegate" in t.lower() for t in tool_names):
                    mode_parts.append("delegate")
                if any("fanout" in t.lower() for t in tool_names):
                    mode_parts.append("fanout")
                has_handoff = any("handoff" in t.lower() for t in tool_names)
                if has_handoff or len(available) > 1:
                    mode_parts.append("handoff")
                mode = "multi-agent (" + "+".join(mode_parts) + ")" if mode_parts else "multi-agent"
                rows.append(("mode", mode))
        for key, value in rows:
            self._rail("gray50", self._composition_row(key, value))
        self.console.print()

    def _memory_name(self, engine: "Engine") -> str:
        mem = getattr(engine.agent, "memory", None)
        if mem is None:
            return "none"
        return mem.__class__.__name__

    def _history_name(self, engine: "Engine") -> str:
        hist = getattr(engine.agent, "history", None)
        if hist is not None:
            return hist.__class__.__name__
        runtime_hist = getattr(engine, "_runtime_history", None)
        if runtime_hist is not None:
            return runtime_hist.__class__.__name__
        policy = getattr(engine, "history_policy", None)
        policy_name = policy.__class__.__name__ if policy is not None else "none"
        return f"EngineRuntimeHistory ({policy_name})"

    def _model_name(self, engine: "Engine") -> str:
        llm = getattr(getattr(engine, "agent", None), "llm", None)
        if llm is None:
            return "none"
        for key in ("model_name", "model", "name"):
            value = getattr(llm, key, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return llm.__class__.__name__

    def _planning_name(self, engine: "Engine") -> str:
        search = getattr(engine, "search", None)
        if search is not None:
            return search.__class__.__name__
        selector = getattr(engine, "branch_selector", None)
        if selector is not None:
            return selector.__class__.__name__
        planner = getattr(getattr(engine, "agent", None), "planner", None)
        if planner is not None:
            return planner.__class__.__name__
        return "none"

    def _protocol_name(self, engine: "Engine") -> str:
        protocol = engine.resolve_protocol() if hasattr(engine, "resolve_protocol") else None
        if protocol is None:
            return "none"
        fallbacks = list(getattr(protocol, "fallback_protocols", ()) or [])
        if not fallbacks:
            return str(getattr(protocol, "id", protocol))
        return f"{getattr(protocol, 'id', protocol)} -> {', '.join(str(x) for x in fallbacks)}"

    def _tool_list(self, engine: "Engine") -> List[str]:
        registry = getattr(engine, "tool_registry", None)
        if registry is None:
            return []
        names: List[str] = []
        try:
            listed = registry.list_tools() if hasattr(registry, "list_tools") else []
            if isinstance(listed, list):
                names = [str(x) for x in listed]
        except Exception:
            names = []
        return sorted(names)

    def _context_row(self, engine: "Engine") -> str:
        runtime = getattr(engine, "_context_runtime", None)
        llm = getattr(getattr(engine, "agent", None), "llm", None)
        info = (
            runtime.run_meta(llm)
            if runtime is not None and callable(getattr(runtime, "run_meta", None))
            else {}
        )
        window = info.get("context_window") or "-"
        counting = info.get("counting_mode") or "disabled"
        reserve = info.get("reserve_tokens")
        reserve_text = f"reserve={reserve}" if reserve is not None else "reserve=-"
        compact = (
            "auto"
            if getattr(getattr(engine, "context_config", None), "enabled", False)
            else "off"
        )
        return f"{window} window · {counting} counting · {reserve_text} · compact={compact}"

    def _prompt_name(self, engine: "Engine") -> str:
        meta = dict(getattr(engine, "_last_prompt_metadata", {}) or {})
        builder = str(meta.get("prompt_builder") or "default")
        delivery = str(meta.get("tool_schema_delivery") or "prompt_injection")
        sections = meta.get("sections_used") or []
        section_text = ",".join(str(item) for item in sections[:4]) if sections else "-"
        return f"{builder} · delivery={delivery} · sections={section_text}"

    def _composition_row(self, key: str, value: str) -> str:
        key_w = 12
        val_w = 92
        k = f"{key:<{key_w}}"
        v = self._truncate_plain(str(value), val_w)
        return f"[dim]{k}[/dim] [white]{v}[/white]"

    def _truncate_plain(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[: max(8, limit - 3)] + "..."

    def _truncate_middle(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        keep = max(8, (limit - 5) // 2)
        return f"{text[:keep]}...{text[-keep:]}"

    def _start_status(self, text: str) -> None:
        if self._status is None:
            self._status = self.console.status(
                text,
                spinner=self._spinner,
            )
            self._status.start()
        else:
            self._status.update(text)

    def _update_status(self, text: str) -> None:
        if self._status is None:
            self._start_status(text)
            return
        self._status.update(text)

    def _stop_status(self) -> None:
        if self._status is None:
            return
        self._status.stop()
        self._status = None


class RichConsoleHook(RenderHook):
    """Legacy rich hook kept for compatibility."""

    def __init__(
        self,
        show_step_header: bool = True,
        show_thought: bool = True,
        show_action: bool = True,
        show_observation: bool = True,
        show_final_answer: bool = True,
    ):
        self.show_step_header = show_step_header
        self.show_thought = show_thought
        self.show_action = show_action
        self.show_observation = show_observation
        self.show_final_answer = show_final_answer
        self._tools_used: list[str] = []

    def on_step_end(self, record, state, engine) -> None:
        decision = record.decision
        if (
            decision is not None
            and self.show_thought
            and getattr(decision, "rationale", None)
        ):
            RichRender.print_thought(str(decision.rationale), record.step_id)
        if (
            decision is not None
            and self.show_action
            and getattr(decision, "actions", None)
        ):
            for action in decision.actions:
                obj = action if isinstance(action, Action) else Action.from_dict(action)
                self._tools_used.append(obj.name)
                RichRender.print_action(obj.name, obj.args, record.step_id)
        if self.show_observation and record.action_results:
            for obs in record.action_results:
                RichRender.print_observation(obs, record.step_id)

    def on_run_end(self, result: "EngineResult", engine: "Engine") -> None:
        if self.show_final_answer and result.state.final_result is not None:
            RichRender.print_final_answer(
                str(result.state.final_result), result.state.task
            )


class SimpleRichConsoleHook(RichConsoleHook):
    def __init__(self):
        super().__init__(
            show_step_header=False,
            show_thought=False,
            show_action=False,
            show_observation=False,
            show_final_answer=True,
        )


class VerboseRichConsoleHook(RichConsoleHook):
    def __init__(self):
        super().__init__(
            show_step_header=True,
            show_thought=True,
            show_action=True,
            show_observation=True,
            show_final_answer=True,
        )


__all__ = [
    "RenderHook",
    "RenderStreamHook",
    "ClaudeStyleHook",
    "RichConsoleHook",
    "SimpleRichConsoleHook",
    "VerboseRichConsoleHook",
]
