"""AgentREPL — interactive REPL for any QitOS AgentModule.

Provides a full-featured interactive REPL with streaming output,
permission confirmation, markdown rendering, slash commands, and
multi-turn conversation. Works with any ``AgentModule`` out of the box.

Usage::

    from qitos.kit.repl import AgentREPL
    from my_agent import MyAgent

    repl = AgentREPL(agent=MyAgent(llm=llm), workspace=".")
    repl.run()
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

from .commands import CommandRegistry, build_default_registry
from .formatter import (
    BOLD, DIM, GREEN, RED, RESET, YELLOW,
    DisplayConfig,
    action_name, action_args, model_text_from_record,
    clean_model_text, clean_stream_text,
    tool_detail, format_tool_result, format_duration, print_separator,
)
from .markdown import render_text_or_markdown
from .spinner import Spinner
from qitos.engine.streaming import StreamHandler


# ---------------------------------------------------------------------------
# REPLStreamHandler — StreamHandler implementation for the REPL
# ---------------------------------------------------------------------------

class REPLStreamHandler:
    """StreamHandler that manages streaming output in the REPL.

    Handles spinner start/stop, text buffer accumulation, protocol tag
    stripping, and incremental display of clean text.
    """

    def __init__(self, repl: Any) -> None:
        self._repl = repl
        self._active: bool = False
        self._buffer: str = ""
        self._displayed: int = 0
        self._newline: bool = False

    @property
    def is_active(self) -> bool:
        return self._active

    def on_start(self) -> None:
        """Called when streaming begins. Stop spinner."""
        if not self._active and self._repl._spinner is not None:
            self._repl._spinner.stop()
            self._repl._spinner = None
        self._active = True

    def on_delta(self, text: str) -> None:
        """Called for each text delta. Accumulate and display clean text."""
        if not text:
            return
        if not self._active:
            self.on_start()
        self._buffer += text
        clean = clean_stream_text(self._buffer)
        if len(clean) > self._displayed:
            new_text = clean[self._displayed:]
            self._displayed = len(clean)
            if not self._newline:
                sys.stdout.write("  ")
                self._newline = True
            sys.stdout.write(new_text)
            sys.stdout.flush()

    def on_end(self) -> None:
        """Called when streaming ends. Finalize output and reset state."""
        if self._active:
            sys.stdout.write("\n\n")
            sys.stdout.flush()
        self._active = False
        self._buffer = ""
        self._displayed = 0
        self._newline = False


# ---------------------------------------------------------------------------
# AgentREPL
# ---------------------------------------------------------------------------

class AgentREPL:
    """Interactive REPL for any QitOS AgentModule.

    Features:
    - Streaming token-by-token output
    - Permission confirmation pipeline
    - Markdown rendering with syntax highlighting
    - Built-in slash commands (extensible)
    - Multi-turn conversation with shared Engine + history
    - Spinner animation during model calls
    - Token usage tracking
    - Status bar with model/step/token/mode info
    - Readline support for arrow-key history and tab completion

    Args:
        agent: Any QitOS ``AgentModule`` instance.
        workspace: Root directory for file operations.
        max_steps: Maximum agent steps per turn.
        display: Display configuration (tool names, markers, colors).
        command_registry: Custom command registry (defaults to built-in commands).
        permission_pipeline: Optional permission pipeline for tool approval.
            If the agent has a ``permission_pipeline`` attribute, it's used automatically.
        stream_callback: Optional override for the streaming text callback.
            If None, defaults to printing tokens to stdout.
    """

    def __init__(
        self,
        agent: Any,
        workspace: str = ".",
        max_steps: int = 50,
        display: Optional[DisplayConfig] = None,
        command_registry: Optional[CommandRegistry] = None,
        permission_pipeline: Any = None,
        stream_callback: Optional[Callable[[str], None]] = None,
    ):
        self.agent = agent
        self.workspace = workspace
        self.max_steps = max_steps
        self.display = display or DisplayConfig()
        self._command_registry = command_registry or build_default_registry()
        self._permission_pipeline = permission_pipeline
        self._stream_callback_override = stream_callback

        # Internal state
        self._engine: Any = None
        self._state: Any = None
        self._started_at: float = 0.0
        self._stream_handler: Optional[REPLStreamHandler] = None
        self._total_tokens: Dict[str, int] = {"input": 0, "output": 0}
        self._spinner: Optional[Spinner] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _status_line(self) -> str:
        """Build a compact status bar line."""
        model_name = ""
        if self._engine is not None:
            llm = getattr(self.agent, "llm", None)
            model_name = getattr(llm, "model", "") if llm else ""
        step = self._state.current_step if self._state else 0
        total_tokens = self._total_tokens["input"] + self._total_tokens["output"]
        perm_mode = getattr(self.agent, "permission_mode", "default")
        if total_tokens >= 1000:
            tokens_str = f"{total_tokens // 1000}K"
        else:
            tokens_str = str(total_tokens)
        return f"{DIM}[{model_name} | steps: {step}/{self.max_steps} | tokens: {tokens_str} | mode: {perm_mode}]{RESET}"

    def run(self) -> None:
        """Launch the interactive REPL loop."""
        # Enable readline for arrow-key history and tab completion
        try:
            import readline
            readline.set_completer_delims(" \t\n;")
            cmd_names = [f"/{n}" for n in self._command_registry.names()]
            def completer(text, state):
                matches = [c for c in cmd_names if c.startswith(text)]
                if state < len(matches):
                    return matches[state]
                return None
            readline.set_completer(completer)
            readline.parse_and_bind("tab: complete")
        except ImportError:
            pass

        while True:
            try:
                if self._engine is not None:
                    print(self._status_line())
                task = input(f"{BOLD}❯{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not task:
                continue

            if task.startswith("/"):
                result = self._command_registry.handle(self, task)
                if result == "exit":
                    break
                elif result:
                    continue

            try:
                self._run_turn(task)
            except KeyboardInterrupt:
                print(f"\n{DIM}[Interrupted]{RESET}\n")
            except Exception as exc:
                print(f"\n{RED}Error: {exc}{RESET}\n")

    def run_headless(self, task: str) -> None:
        """Run a single task. Prints output with no interactivity."""
        try:
            self._run_turn(task)
        except Exception as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)

    # ------------------------------------------------------------------
    # Engine lifecycle
    # ------------------------------------------------------------------

    def _ensure_engine(self, task: str) -> None:
        """Create and configure the Engine for a session."""
        from qitos.engine.engine import Engine
        from qitos.engine.states import ContextConfig, RuntimeBudget

        # Resolve permission pipeline and RBW enforcer explicitly
        pipeline = self._get_permission_pipeline()
        rbw_enforcer = getattr(self.agent, "_rbw_enforcer", None)

        # Create StreamHandler for structured streaming lifecycle
        if self._stream_callback_override:
            from qitos.engine.streaming import to_stream_handler
            handler = to_stream_handler(self._stream_callback_override)
        else:
            handler = REPLStreamHandler(self)
        self._stream_handler = handler

        engine = Engine(
            agent=self.agent,
            budget=RuntimeBudget(max_steps=self.max_steps),
            permission_pipeline=pipeline,
            read_before_write_enforcer=rbw_enforcer,
            permission_interaction_callback=self._permission_interaction_handler,
            context_config=ContextConfig(
                tool_result_max_chars=50000,
                tool_result_per_message_max_chars=200000,
                reactive_compact=True,
                loop_max_repeats=3,
            ),
        )
        state, observation = engine.init_session(task)

        # Enable streaming via handler
        engine.stream_callback = handler

        self._engine = engine
        self._state = state
        self._started_at = time.monotonic()
        # Store observation for first turn
        self._initial_observation = observation

    def _reset_session(self) -> None:
        """Reset the session state."""
        self._engine = None
        self._state = None
        self._total_tokens = {"input": 0, "output": 0}

    # ------------------------------------------------------------------
    # Turn execution
    # ------------------------------------------------------------------

    def _run_turn(self, task: str) -> None:
        """Execute a full user turn (may span multiple agent steps)."""
        from qitos.engine.states import StepRecord

        first_turn = self._engine is None

        if first_turn:
            self._ensure_engine(task)
            observation = self._initial_observation
        else:
            self._state, observation = self._engine.submit_turn(self._state, task)

        repair_count = 0
        max_repairs = 3

        turn_start = time.monotonic()

        while True:
            step_id = self._state.current_step

            if self._engine.budget_exhausted(self._state):
                print(f"{DIM}[Max steps reached]{RESET}")
                break

            # Reset streaming handler for this step
            if self._stream_handler is not None:
                self._stream_handler._active = False
                self._stream_handler._buffer = ""
                self._stream_handler._displayed = 0
                self._stream_handler._newline = False

            # --- DECIDE ---
            self._spinner = Spinner("Thinking")
            self._spinner.start()
            step_result = self._engine.step(self._state, observation)

            # Handle recovered steps — engine already recovered, just continue
            if step_result.recovered:
                if self._spinner:
                    self._spinner.stop()
                    self._spinner = None
                print(f"{DIM}[Recovered from error, continuing...]{RESET}")
                if self._state.current_step >= self.max_steps - 1:
                    break
                self._state.advance_step()
                observation = self._engine.rebuild_observation(self._state)
                continue

            if step_result.error is not None:
                if self._spinner:
                    self._spinner.stop()
                    self._spinner = None
                print(f"{RED}Error: {step_result.error}{RESET}")
                break

            decision = step_result.decision
            record = step_result.record

            text_was_streamed = self._stream_handler.is_active if self._stream_handler else False

            # Stop spinner (may already be stopped by streaming handler)
            if self._spinner:
                self._spinner.stop()
                self._spinner = None

            # Track token usage
            model_response = getattr(record, "model_response", None)
            if isinstance(model_response, dict):
                usage = model_response.get("usage") or {}
                self._total_tokens["input"] += int(usage.get("prompt_tokens") or 0)
                self._total_tokens["output"] += int(usage.get("completion_tokens") or 0)

            # Print model text (only if not streamed)
            raw_text = model_text_from_record(record)
            clean_text = clean_model_text(raw_text)
            if clean_text and not text_was_streamed:
                render_text_or_markdown(clean_text)
                print()

            # --- FINAL ---
            if decision.mode == "final":
                fa = clean_model_text(str(decision.final_answer or ""))
                if fa and fa != clean_text:
                    render_text_or_markdown(fa)
                break

            # --- WAIT (parser repair) ---
            if decision.mode == "wait":
                repair_count += 1
                if repair_count > max_repairs:
                    if clean_text:
                        break
                    print(f"{DIM}[Parser could not interpret model output]{RESET}")
                    break
                if clean_text and len(clean_text) > 20 and not any(
                    tag in clean_text.lower()
                    for tag in ("<tool_use", "<invoke", "<minimax", "action:")
                ):
                    break
                if self._state.current_step >= self.max_steps - 1:
                    break
                self._state.advance_step()
                observation = self._engine.rebuild_observation(self._state)
                continue

            # --- HANDOFF ---
            if decision.mode == "handoff":
                print(f"{DIM}[Handoff not supported in REPL mode]{RESET}")
                break

            # --- ACT ---
            # Note: Permissions are now handled by the Engine's ActionExecutor
            # via permission_interaction_callback. No pre-filtering needed.
            actions = decision.actions or []
            if decision.mode == "act" and actions:
                # Print tool calls
                marker = self.display.output_marker
                for act in actions:
                    name = action_name(act)
                    args = action_args(act)
                    display = self.display.tool_display_name(name)
                    detail = tool_detail(self.display, name, args)

                    if detail:
                        print(f"{marker} {BOLD}{display}{RESET}({detail})")
                    else:
                        print(f"{marker} {BOLD}{display}{RESET}")

            if not actions:
                break

            # Print tool results (from step_result.action_results,
            # which were already executed by engine.step())
            invocations = record.tool_invocations or []
            results = step_result.action_results or []
            rprefix = self.display.result_prefix
            for inv, result in zip(invocations, results):
                tool_name = inv.get("tool_name", "") if isinstance(inv, dict) else str(inv)
                formatted = format_tool_result(self.display, tool_name, result)
                if formatted:
                    for line in formatted.split("\n"):
                        print(f"  {rprefix} {line}")

            observation = step_result.observation
            if step_result.stop:
                break

            if self._state.current_step >= self.max_steps - 1:
                print(f"{DIM}[Max steps reached]{RESET}")
                break
            self._state.advance_step()

        # Print timing
        elapsed = time.monotonic() - turn_start
        churn = self.display.churn_marker
        print(f"\n{DIM}{churn} Churned for {format_duration(elapsed)}{RESET}")
        print_separator()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_permission_pipeline(self) -> Any:
        """Get the permission pipeline, checking agent attribute first."""
        if self._permission_pipeline is not None:
            return self._permission_pipeline
        return getattr(self.agent, "permission_pipeline", None)

    def _permission_interaction_handler(
        self, tool_name: str, args: Dict[str, Any], permission: Any
    ) -> str:
        """Handle permission "ask" decisions interactively.

        Called by the ActionExecutor when a permission decision is "ask".
        Returns "allow" or "deny".
        """
        display = self.display.tool_display_name(tool_name)
        detail = tool_detail(self.display, tool_name, args)
        if detail:
            print(f"  {YELLOW}? {display}({detail}){RESET}")
        else:
            print(f"  {YELLOW}? {display}{RESET}")
        if hasattr(permission, "message") and permission.message:
            print(f"    {DIM}{permission.message}{RESET}")
        if self._confirm_tool():
            return "allow"
        else:
            print(f"  {DIM}[Denied: {display}]{RESET}")
            return "deny"

    @staticmethod
    def _confirm_tool() -> bool:
        """Prompt user to confirm a tool call."""
        try:
            choice = input(f"  {DIM}[y/n]:{RESET} ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return choice in ("y", "yes", "")
