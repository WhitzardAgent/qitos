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


class RenderStreamHook(RenderHook):
    """Emit normalized render events for terminal and frontend consumers."""

    def __init__(self, output_jsonl: Optional[str] = None):
        self.events: List[RenderEvent] = []
        self.output_jsonl = output_jsonl
        self._path = Path(output_jsonl) if output_jsonl else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    def on_run_start(self, task: str, state: Any, engine: "Engine") -> None:
        self._emit("lifecycle", "run_start", step_id=0, payload={"task": task, "max_steps": engine.budget.max_steps})

    def on_before_step(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit("lifecycle", "step_start", step_id=ctx.step_id, payload={"phase": ctx.phase.value})

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
            self._emit("action", "planned_actions", step_id=ctx.step_id, payload={"actions": payload["actions"]})

    def on_after_act(self, ctx: HookContext, engine: "Engine") -> None:
        if ctx.record is not None and ctx.record.tool_invocations:
            self._emit("action", "tool_invocations", step_id=ctx.step_id, payload={"tool_invocations": ctx.record.tool_invocations})
        if ctx.action_results:
            self._emit("observation", "action_results", step_id=ctx.step_id, payload={"action_results": ctx.action_results})

    def on_after_critic(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit("critic", "critic", step_id=ctx.step_id, payload=ctx.payload or {})

    def on_after_reduce(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit("state", "state_diff", step_id=ctx.step_id, payload=ctx.payload or {})

    def on_after_check_stop(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit(
            "lifecycle",
            "check_stop",
            step_id=ctx.step_id,
            payload={"result": (ctx.payload or {}).get("result"), "stop_reason": ctx.stop_reason},
        )

    def on_recover(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit("error", "recover", step_id=ctx.step_id, payload={"phase": ctx.phase.value, "error": str(ctx.error)})

    def on_after_step(self, ctx: HookContext, engine: "Engine") -> None:
        self._emit("lifecycle", "step_end", step_id=ctx.step_id, payload={"stop_reason": ctx.stop_reason})

    def on_run_end(self, result: "EngineResult", engine: "Engine") -> None:
        self._emit(
            "lifecycle",
            "done",
            step_id=max(0, result.step_count - 1),
            payload={"stop_reason": result.state.stop_reason, "final_result": result.state.final_result, "steps": result.step_count},
        )

    def on_event(self, event, state, record, engine) -> None:
        # Promote key model I/O events to first-class render nodes.
        if event.phase.value.lower() == "decide" and isinstance(event.payload, dict):
            stage = str(event.payload.get("stage", ""))
            if stage == "state_ready":
                observation = event.payload.get("observation")
                self._emit("observation", "state", step_id=event.step_id, payload={"observation": observation})
                if isinstance(observation, dict):
                    if "plan_steps" in observation:
                        self._emit(
                            "plan",
                            "plan",
                            step_id=event.step_id,
                            payload={"plan_steps": observation.get("plan_steps"), "plan_cursor": observation.get("plan_cursor")},
                        )
            elif stage == "model_input":
                self._emit(
                    "thinking",
                    "model_input",
                    step_id=event.step_id,
                    payload={
                        "prepared": event.payload.get("prepared"),
                        "history_message_count": event.payload.get("history_message_count"),
                        "messages": event.payload.get("messages"),
                    },
                )
            elif stage == "model_output":
                self._emit(
                    "thinking",
                    "model_output",
                    step_id=event.step_id,
                    payload={"raw_output": event.payload.get("raw_output")},
                )
        self._emit(
            "engine_event",
            event.phase.value.lower(),
            step_id=event.step_id,
            payload={"ok": event.ok, "payload": event.payload, "error": event.error},
        )

    def _emit(self, channel: str, node: str, step_id: int, payload: Optional[Dict[str, Any]] = None) -> None:
        evt = RenderEvent(channel=channel, node=node, step_id=step_id, payload=payload or {})
        self.events.append(evt)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(evt.to_dict(), ensure_ascii=False))
                f.write("\n")
        self.on_render_event(evt)

    def on_render_event(self, event: RenderEvent) -> None:
        """Override in subclasses for side effects (console/UI streaming)."""
        return None


class ClaudeStyleHook(RenderStreamHook):
    """Content-first terminal output focused on task, thought, action, observation, memory."""

    def __init__(
        self,
        output_jsonl: Optional[str] = None,
        max_preview_chars: int = 800,
        theme: str = "research",
    ):
        super().__init__(output_jsonl=output_jsonl)
        self.console = Console()
        self.max_preview_chars = max_preview_chars
        self._last_step: Optional[int] = None
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

    def on_render_event(self, event: RenderEvent) -> None:
        if event.node == "run_start":
            self._print_banner()
            self.console.print(Rule("[dim]RUN[/dim]", style="gray23"))
            return

        if event.node == "step_start":
            self._last_step = event.step_id
            self.console.print(Rule(f"STEP {event.step_id + 1}", style="gray23"))
            return

        if event.channel == "thinking":
            if event.node == "model_input":
                return
            if event.step_id in self._thought_steps:
                return
            thought = self._renderer.thought_text(event)
            if thought:
                self._rail("purple", "[purple]⦿[/purple] [italic slate_blue3]" + thought + "[/italic slate_blue3]")
                self._thought_steps.add(event.step_id)
            return

        if event.channel == "action":
            if event.step_id in self._action_steps:
                return
            action = self._renderer.action_summary(event)
            if action:
                status = action.get("status", "neutral")
                bg = "blue" if status != "error" else "red"
                badge = action.get("label", "ACTION")
                detail = action.get("detail", "")
                line = f"🚀 [bold white on {bg}] {badge} [/bold white on {bg}]"
                if detail:
                    line += f" [cyan]{detail}[/cyan]"
                self._rail("blue", line)
                self._action_steps.add(event.step_id)
            return

        if event.channel == "observation":
            if event.node in {"state", "observation"}:
                if event.step_id in self._state_steps:
                    return
                stats = self._renderer.state_summary(event)
                if stats:
                    fixed = self._render_state_row(stats)
                    self._rail("gray40", f"[dim]State[/dim] [dim]{fixed}[/dim]")
                self._state_steps.add(event.step_id)
                return
            if event.step_id in self._observation_steps:
                return
            obs = self._renderer.observation_summary(event)
            if obs:
                status = str(obs.get("status", "neutral"))
                color = "green" if status == "success" else ("red" if status == "error" else "blue")
                title = str(obs.get("title", "Observation"))
                if status == "error":
                    self._rail("red", f"[red][✘] Error: {title}[/red]")
                    self._observation_steps.add(event.step_id)
                    return
                self._rail(color, f"🔎 [bold {color}]Observation[/bold {color}] [bold italic]Title:[/bold italic] {title}")
                url = str(obs.get("url", "")).strip()
                if url:
                    self._rail(color, f"[dim]URL: {url}[/dim]")
                body = str(obs.get("body", "")).strip()
                if body:
                    self._rail(color, body if status != "error" else f"[red]{body}[/red]")
                table = obs.get("table")
                syntax = obs.get("syntax")
                if table is not None:
                    self.console.print(Text("┃", style=color), end=" ")
                    self.console.print(table)
                if isinstance(syntax, Syntax):
                    self.console.print(Text("┃", style=color), end=" ")
                    self.console.print(syntax)
                self._observation_steps.add(event.step_id)
            return

        if event.channel == "memory":
            if event.step_id in self._memory_steps:
                return
            mem = self._renderer.memory_summary(event)
            if mem:
                self._rail("gray50", f"[dim]memory[/dim] [dim]{mem}[/dim]")
                self._memory_steps.add(event.step_id)
            return

        if event.node == "step_end":
            self.console.print()
            return

        if event.node == "done":
            self.console.print(Rule("[bold]DONE[/bold]", style="gray23"))
            summary = self._renderer.done_summary(stop_reason=event.payload.get("stop_reason"), final_result=event.payload.get("final_result"))
            self._rail("green", f"[bold green]{summary}[/bold green]")
            return

    def _print_banner(self) -> None:
        self.console.print(Rule("[bold bright_cyan]QitOS: A Relaxable Agentic Framework for Reseachers [/bold bright_cyan]", style="bright_cyan"))
        self.console.print("[bright_cyan]   ██████╗ ██╗████████╗ ██████╗ ███████╗[/bright_cyan]")
        self.console.print("[cyan]  ██╔═══██╗██║╚══██╔══╝██╔═══██╗██╔════╝[/cyan]")
        self.console.print("[blue]  ██║   ██║██║   ██║   ██║   ██║███████╗[/blue]")
        self.console.print("[bright_blue]  ██║▄▄ ██║██║   ██║   ██║   ██║╚════██║[/bright_blue]")
        self.console.print("[blue]  ╚██████╔╝██║   ██║   ╚██████╔╝███████║[/blue]")
        self.console.print("[cyan]   ╚══▀▀═╝ ╚═╝   ╚═╝    ╚═════╝ ╚══════╝[/cyan]")
        self.console.print(f"[dim]minimalist stream runtime · theme={self.theme_name}[/dim]")
        self.console.print()

    def _rail(self, color: str, line: str) -> None:
        grp = Group(
            Padding(Text.from_markup(f"[{color}]┃[/{color}] {line}"), (0, 0, 0, 0)),
        )
        self.console.print(grp)

    def _render_state_row(self, stats: Dict[str, Any]) -> str:
        order = [
            ("scratchpad_tokens", "sp_toks"),
            ("scratchpad_items", "sp_items"),
            ("memory_records", "mem_recs"),
            ("workspace_files", "ws_files"),
        ]
        cells: List[str] = []
        for key, label in order:
            raw = stats.get(key, "-")
            value = "-" if raw is None else str(raw)
            cell = f"{label:<9} {value:>6}"
            cells.append(cell)
        return "  ".join(cells)

    def _print_agent_composition(self, engine: "Engine") -> None:
        self.console.print(Rule("[dim]AGENT COMPOSITION[/dim]", style="gray23"))
        memory_name = self._memory_name(engine)
        history_name = self._history_name(engine)
        model_name = self._model_name(engine)
        planning_name = self._planning_name(engine)
        tools = self._tool_list(engine)
        tools_desc = ", ".join(tools[:8]) if tools else "none"
        if len(tools) > 8:
            tools_desc += ", ..."
        rows = [
            ("memory", memory_name),
            ("history", history_name),
            ("base_model", model_name),
            ("planning", planning_name),
            ("tools", f"{tools_desc} ({len(tools)})"),
        ]
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
        if decision is not None and self.show_thought and getattr(decision, "rationale", None):
            RichRender.print_thought(str(decision.rationale), record.step_id)
        if decision is not None and self.show_action and getattr(decision, "actions", None):
            for action in decision.actions:
                obj = action if isinstance(action, Action) else Action.from_dict(action)
                self._tools_used.append(obj.name)
                RichRender.print_action(obj.name, obj.args, record.step_id)
        if self.show_observation and record.action_results:
            for obs in record.action_results:
                RichRender.print_observation(obs, record.step_id)

    def on_run_end(self, result: "EngineResult", engine: "Engine") -> None:
        if self.show_final_answer and result.state.final_result is not None:
            RichRender.print_final_answer(str(result.state.final_result), result.state.task)


class SimpleRichConsoleHook(RichConsoleHook):
    def __init__(self):
        super().__init__(show_step_header=False, show_thought=False, show_action=False, show_observation=False, show_final_answer=True)


class VerboseRichConsoleHook(RichConsoleHook):
    def __init__(self):
        super().__init__(show_step_header=True, show_thought=True, show_action=True, show_observation=True, show_final_answer=True)


__all__ = [
    "RenderHook",
    "RenderStreamHook",
    "ClaudeStyleHook",
    "RichConsoleHook",
    "SimpleRichConsoleHook",
    "VerboseRichConsoleHook",
]
