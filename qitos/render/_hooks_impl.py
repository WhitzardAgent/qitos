"""Render hooks built on top of the Engine hook system."""

from __future__ import annotations

import json
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

    def __init__(self, output_jsonl: Optional[str] = None):
        self.events: List[RenderEvent] = []
        self.output_jsonl = output_jsonl
        self._path = Path(output_jsonl) if output_jsonl else None
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

    def on_event(self, event, state, record, engine) -> None:
        # Promote multi-agent RuntimePhase events to first-class render nodes.
        phase_val = event.phase.value if hasattr(event.phase, "value") else str(event.phase)
        if phase_val in ("HANDOFF_START", "HANDOFF_END", "DELEGATE_START", "DELEGATE_END",
                         "FANOUT_START", "FANOUT_END"):
            channel = "handoff" if phase_val.startswith("HANDOFF") else "delegation"
            node = phase_val.lower()
            self._emit(channel, node, step_id=event.step_id, payload=dict(event.payload or {}))

        # Promote key model I/O events to first-class render nodes.
        if event.phase.value.lower() == "decide" and isinstance(event.payload, dict):
            stage = str(event.payload.get("stage", ""))
            if stage == "state_ready":
                observation = event.payload.get("observation")
                self._emit(
                    "observation",
                    "state",
                    step_id=event.step_id,
                    payload={"observation": observation},
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
                self._emit(
                    "thinking",
                    "model_input",
                    step_id=event.step_id,
                    payload={
                        "prepared": event.payload.get("prepared"),
                        "history_message_count": event.payload.get(
                            "history_message_count"
                        ),
                        "messages": event.payload.get("messages"),
                        "context": event.payload.get("context"),
                        "state_stats": event.payload.get("state_stats"),
                    },
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
        self._emit(
            "engine_event",
            event.phase.value.lower(),
            step_id=event.step_id,
            payload={"ok": event.ok, "payload": event.payload, "error": event.error},
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
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(_redact_dict(evt.to_dict()), ensure_ascii=False))
                f.write("\n")
        self.on_render_event(evt)

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
        self.max_preview_chars = max_preview_chars
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
            event_key = (event.step_id, id(event))
            if event_key in self._rendered_action_indices:
                return
            action = self._renderer.action_summary(event)
            if action:
                action_count = action.get("action_count", 1)
                sub_actions = action.get("actions")
                status = action.get("status", "neutral")
                bg = "blue" if status != "error" else "red"

                # Multi-action: show parallel summary banner then individual actions
                if action_count > 1 and sub_actions:
                    self._rail(
                        "bright_blue",
                        f"🚀 [bold white on bright_blue] {action_count} ACTIONS IN PARALLEL [/bold white on bright_blue]",
                    )
                    for i, sub in enumerate(sub_actions):
                        sub_label = sub.get("label", "?")
                        sub_detail = sub.get("detail", "")
                        idx_key = (event.step_id, i)
                        if idx_key in self._rendered_action_indices:
                            continue
                        line = f"  ┌ [bold white on {bg}] {sub_label} [/bold white on {bg}]"
                        if sub_detail:
                            line += f" [cyan]{sub_detail}[/cyan]"
                        self._rail("blue", line)
                        self._rendered_action_indices.add(idx_key)
                else:
                    badge = action.get("label", "ACTION")
                    detail = action.get("detail", "")
                    line = f"🚀 [bold white on {bg}] {badge} [/bold white on {bg}]"
                    if detail:
                        line += f" [cyan]{detail}[/cyan]"
                    self._rail("blue", line)

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
            if event.step_id in self._memory_steps:
                return
            mem = self._renderer.memory_summary(event)
            if mem:
                self._rail("gray50", f"[dim]memory[/dim] [dim]{mem}[/dim]")
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
            ("history_tokens", "hist_toks"),
            ("output_tokens", "out_toks"),
            ("scratchpad_tokens", "sp_toks"),
            ("scratchpad_items", "sp_items"),
            ("memory_records", "mem_recs"),
            ("workspace_files", "ws_files"),
        ]
        cells: List[str] = []
        for key, label in order:
            raw = stats.get(key, "-")
            if key == "occupancy_ratio" and isinstance(raw, (int, float)):
                value = f"{raw * 100:5.1f}%"
            else:
                value = "-" if raw is None else str(raw)
            cell = f"{label:<9} {value:>6}"
            cells.append(cell)
        return "  ".join(cells)

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
