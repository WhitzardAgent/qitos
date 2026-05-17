"""Built-in slash commands for the REPL.

Provides a command registry that can be extended with custom commands.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from .formatter import BOLD, DIM, GREEN, RED, RESET, YELLOW
from .markdown import render_text_or_markdown


@dataclass
class CommandContext:
    """Context passed to slash command handlers."""

    repl: Any  # AgentREPL instance — avoids circular import by using Any
    args: str  # Everything after the command name


# Command handler type
CommandHandler = Callable[[CommandContext], None]


@dataclass
class Command:
    """A slash command definition."""

    name: str
    description: str
    handler: CommandHandler
    aliases: List[str] = None

    def __post_init__(self):
        if self.aliases is None:
            self.aliases = []


class CommandRegistry:
    """Registry of slash commands with tab-completion support."""

    def __init__(self) -> None:
        self._commands: Dict[str, Command] = {}

    def register(self, command: Command) -> None:
        """Register a command and its aliases."""
        self._commands[command.name] = command
        for alias in (command.aliases or []):
            self._commands[alias] = command

    def get(self, name: str) -> Optional[Command]:
        return self._commands.get(name)

    def names(self) -> List[str]:
        """All command names and aliases (for tab completion)."""
        return sorted(self._commands.keys())

    def primary_names(self) -> List[str]:
        """Only primary command names (for help display)."""
        seen: set = set()
        result: List[str] = []
        for cmd in self._commands.values():
            if cmd.name not in seen:
                seen.add(cmd.name)
                result.append(cmd.name)
        return sorted(result)

    def handle(self, repl: Any, text: str) -> Optional[str]:
        """Handle a slash command. Returns 'exit' to quit, True if handled."""
        parts = text.strip().split()
        if not parts:
            return True
        cmd_name = parts[0].lower()
        # Strip leading /
        if cmd_name.startswith("/"):
            cmd_name = cmd_name[1:]
        rest = " ".join(parts[1:]) if len(parts) > 1 else ""

        command = self.get(cmd_name)
        if command is None:
            print(f"Unknown command: /{cmd_name}")
            return True

        ctx = CommandContext(repl=repl, args=rest)
        command.handler(ctx)
        # Special: /exit returns "exit"
        if cmd_name in ("exit", "quit"):
            return "exit"
        return True


# ---------------------------------------------------------------------------
# Built-in command handlers
# ---------------------------------------------------------------------------

def _cmd_help(ctx: CommandContext) -> None:
    repl = ctx.repl
    registry = repl._command_registry
    print(f"\n{BOLD}Commands:{RESET}")
    for name in registry.primary_names():
        cmd = registry.get(name)
        if cmd:
            print(f"  /{cmd.name:<14s} {cmd.description}")
    print()


def _cmd_exit(ctx: CommandContext) -> None:
    print("Goodbye!")


def _cmd_clear(ctx: CommandContext) -> None:
    ctx.repl._reset_session()
    print("Session cleared.\n")


def _cmd_compact(ctx: CommandContext) -> None:
    engine = ctx.repl._engine
    if engine is None:
        print("No active session to compact.")
        return
    history = engine._history()
    if not hasattr(history, "compact"):
        print(f"{DIM}[Compaction not available for current history backend]{RESET}")
        return
    try:
        result = history.compact()
        if result:
            print(f"{GREEN}Context compacted.{RESET}")
        else:
            print(f"{DIM}[No compaction needed]{RESET}")
    except Exception as exc:
        print(f"{RED}Compaction failed: {exc}{RESET}")


def _cmd_cost(ctx: CommandContext) -> None:
    tokens = ctx.repl._total_tokens
    inp = tokens["input"]
    out = tokens["output"]
    total = inp + out
    print(f"Token usage: {inp:,} input + {out:,} output = {total:,} total")


def _cmd_undo(ctx: CommandContext) -> None:
    engine = ctx.repl._engine
    if engine is None:
        print("No active session.")
        return
    history = engine._history()
    removed = 0
    for _ in range(2):
        if hasattr(history, "_items") and history._items:
            history._items.pop()
            removed += 1
    if removed:
        print(f"{DIM}[Removed {removed} messages from history]{RESET}")
    else:
        print(f"{DIM}[Nothing to undo]{RESET}")


def _cmd_model(ctx: CommandContext) -> None:
    repl = ctx.repl
    name = ctx.args.strip()

    if not name:
        model_name = ""
        llm = getattr(repl.agent, "llm", None)
        if llm is not None:
            model_name = getattr(llm, "model", "") or getattr(llm, "model_name", "")
        print(f"Current model: {model_name or 'unknown'}")
        return

    llm = getattr(repl.agent, "llm", None)
    if llm is None:
        print(f"{RED}No LLM configured.{RESET}")
        return

    old_model = getattr(llm, "model", "")
    try:
        llm.model = name
        # Update parser if available
        from qitos.models.profile_registry import infer_default_protocol
        from qitos.protocols import parser_from_protocol
        new_protocol = infer_default_protocol(name)
        new_parser = parser_from_protocol(new_protocol) if new_protocol else None
        if new_parser:
            repl.agent.model_parser = new_parser
        if repl._engine is not None:
            repl._engine._resolved_protocol = None
            repl._engine.resolve_protocol()
        print(f"Switched model: {old_model} → {name}")
    except Exception as exc:
        print(f"{RED}Failed to switch model: {exc}{RESET}")


def _cmd_diff(ctx: CommandContext) -> None:
    workspace = ctx.repl.workspace
    try:
        result = subprocess.run(
            ["git", "diff"],
            capture_output=True, text=True,
            cwd=workspace, timeout=10,
        )
        diff = result.stdout.strip()
        if diff:
            render_text_or_markdown(f"```diff\n{diff}\n```")
        else:
            print(f"{DIM}No uncommitted changes.{RESET}")
    except FileNotFoundError:
        print(f"{RED}git not found.{RESET}")
    except subprocess.TimeoutExpired:
        print(f"{RED}git diff timed out.{RESET}")


def _cmd_status(ctx: CommandContext) -> None:
    repl = ctx.repl
    print(repl._status_line())


def _cmd_commit(ctx: CommandContext) -> None:
    """Guided git commit."""
    workspace = ctx.repl.workspace
    try:
        # Show current changes
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True,
            cwd=workspace, timeout=10,
        )
        if not status.stdout.strip():
            print(f"{DIM}No changes to commit.{RESET}")
            return

        diff_stat = subprocess.run(
            ["git", "diff", "--stat"],
            capture_output=True, text=True,
            cwd=workspace, timeout=10,
        )
        log = subprocess.run(
            ["git", "log", "--oneline", "-5"],
            capture_output=True, text=True,
            cwd=workspace, timeout=10,
        )

        print(f"{BOLD}Changes:{RESET}")
        print(status.stdout.strip())
        if diff_stat.stdout.strip():
            print(f"\n{BOLD}Diff stats:{RESET}")
            print(diff_stat.stdout.strip())
        if log.stdout.strip():
            print(f"\n{BOLD}Recent commits:{RESET}")
            print(log.stdout.strip())

        # Get commit message
        msg = ctx.args.strip()
        if not msg:
            try:
                msg = input(f"\n{BOLD}Commit message:{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"{DIM}[Cancelled]{RESET}")
                return
        if not msg:
            print(f"{DIM}[No message provided, cancelled]{RESET}")
            return

        # Stage and commit
        subprocess.run(
            ["git", "add", "-A"],
            cwd=workspace, timeout=10,
        )
        result = subprocess.run(
            ["git", "commit", "-m", msg],
            capture_output=True, text=True,
            cwd=workspace, timeout=30,
        )
        if result.returncode == 0:
            print(f"{GREEN}Committed: {msg}{RESET}")
        else:
            print(f"{RED}Commit failed: {result.stderr.strip()}{RESET}")
    except FileNotFoundError:
        print(f"{RED}git not found.{RESET}")
    except subprocess.TimeoutExpired:
        print(f"{RED}git command timed out.{RESET}")


def _cmd_pr(ctx: CommandContext) -> None:
    """Guided PR creation using gh."""
    workspace = ctx.repl.workspace
    try:
        # Check gh is available
        check = subprocess.run(
            ["gh", "--version"],
            capture_output=True, timeout=5,
        )
        if check.returncode != 0:
            print(f"{RED}gh CLI not found. Install it from https://cli.github.com{RESET}")
            return

        # Show current branch
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
            cwd=workspace, timeout=5,
        )
        current_branch = branch.stdout.strip()
        print(f"{BOLD}Current branch:{RESET} {current_branch}")

        # Check for uncommitted changes
        status = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True, text=True,
            cwd=workspace, timeout=5,
        )
        if status.stdout.strip():
            print(f"{YELLOW}Uncommitted changes detected. Commit first with /commit.{RESET}")
            return

        # Get title and body
        parts = ctx.args.strip().split("|", 1)
        title = parts[0].strip() if parts else ""
        body = parts[1].strip() if len(parts) > 1 else ""

        if not title:
            try:
                title = input(f"{BOLD}PR title:{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                print(f"{DIM}[Cancelled]{RESET}")
                return
        if not title:
            print(f"{DIM}[No title provided, cancelled]{RESET}")
            return

        if not body:
            try:
                body = input(f"{BOLD}PR body (optional):{RESET} ").strip()
            except (EOFError, KeyboardInterrupt):
                body = ""

        # Push if needed and create PR
        cmd = ["gh", "pr", "create", "--title", title]
        if body:
            cmd.extend(["--body", body])
        else:
            cmd.extend(["--body", ""])

        result = subprocess.run(
            cmd,
            capture_output=True, text=True,
            cwd=workspace, timeout=60,
        )
        if result.returncode == 0:
            print(f"{GREEN}PR created: {result.stdout.strip()}{RESET}")
        else:
            print(f"{RED}PR creation failed: {result.stderr.strip()}{RESET}")
    except FileNotFoundError:
        print(f"{RED}gh not found.{RESET}")
    except subprocess.TimeoutExpired:
        print(f"{RED}Command timed out.{RESET}")


def _cmd_memory(ctx: CommandContext) -> None:
    """Show memory entries."""
    import os
    memory_dir = os.path.join(os.path.expanduser("~"), ".qitos", "memory")
    memory_index = os.path.join(memory_dir, "MEMORY.md")

    if not os.path.isfile(memory_index):
        print(f"{DIM}No memory index found at {memory_dir}{RESET}")
        return

    try:
        with open(memory_index, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if content:
            print(f"{BOLD}Memory entries:{RESET}")
            for line in content.split("\n"):
                if line.strip():
                    print(f"  {line}")
        else:
            print(f"{DIM}Memory index is empty.{RESET}")
    except OSError as exc:
        print(f"{RED}Error reading memory: {exc}{RESET}")


def _cmd_permissions(ctx: CommandContext) -> None:
    """Show current permission configuration."""
    repl = ctx.repl
    agent = repl.agent
    perm_mode = getattr(agent, "permission_mode", "default")
    pipeline = getattr(agent, "permission_pipeline", None)

    print(f"{BOLD}Permission mode:{RESET} {perm_mode}")

    if pipeline is not None:
        mode = getattr(pipeline, "mode", None)
        if mode is not None:
            print(f"{BOLD}Pipeline mode:{RESET} {mode}")
        rbw = getattr(pipeline, "_rbw_enforcer", None)
        if rbw is not None:
            cache_size = len(getattr(rbw, "_cache", {}))
            print(f"{BOLD}Read-before-write:{RESET} enabled ({cache_size} files tracked)")
        auto = getattr(pipeline, "_auto_classifier", None)
        if auto is not None:
            print(f"{BOLD}Auto-classifier:{RESET} enabled")

    # Show protected paths
    try:
        from qitos.kit.permission.rules import PROTECTED_PATHS
        print(f"\n{BOLD}Protected paths ({len(PROTECTED_PATHS)}):{RESET}")
        for p in list(PROTECTED_PATHS)[:10]:
            print(f"  {DIM}{p}{RESET}")
        if len(PROTECTED_PATHS) > 10:
            print(f"  {DIM}... and {len(PROTECTED_PATHS) - 10} more{RESET}")
    except ImportError:
        pass


def build_default_registry() -> CommandRegistry:
    """Build the default command registry with built-in commands."""
    registry = CommandRegistry()
    registry.register(Command("help", "Show this help", _cmd_help, aliases=["h"]))
    registry.register(Command("exit", "Exit the session", _cmd_exit, aliases=["quit", "q"]))
    registry.register(Command("clear", "Clear session history and reset", _cmd_clear))
    registry.register(Command("compact", "Compact conversation history to save context", _cmd_compact))
    registry.register(Command("cost", "Show token usage statistics", _cmd_cost))
    registry.register(Command("undo", "Remove last turn from history", _cmd_undo))
    registry.register(Command("model", "Show or switch the active model", _cmd_model))
    registry.register(Command("diff", "Show uncommitted git changes", _cmd_diff))
    registry.register(Command("status", "Show session status", _cmd_status))
    registry.register(Command("commit", "Guided git commit", _cmd_commit))
    registry.register(Command("pr", "Guided PR creation with gh", _cmd_pr))
    registry.register(Command("memory", "Show memory entries", _cmd_memory))
    registry.register(Command("permissions", "Show permission configuration", _cmd_permissions))
    return registry
