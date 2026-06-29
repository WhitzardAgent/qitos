"""
CLI Rendering Components using Rich

Provides production-grade console visual experience for QitOS Agent execution.

Features:
- Step-by-step execution visualization
- Color-coded rendering for thoughts, actions, observations
- Final answer highlighting
- Error handling display
"""

import json
from typing import Any, Dict, List, Optional
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.box import DOUBLE, ROUNDED
from rich.color import Color
from rich.theme import Theme

console = Console()

DEFAULT_THEME = Theme(
    {
        "thought": "cyan",
        "action": "yellow",
        "observation": "green",
        "final_answer": "gold1",
        "error": "red",
        "step": "magenta",
    }
)


class RichRender:
    """
    Unified Rich rendering component for QitOS CLI.

    Provides static methods for consistent visual output throughout
    Agent execution lifecycle.
    """

    @staticmethod
    def print_step_header(step: int, total_steps: Optional[int] = None) -> None:
        """
        Render a prominent Panel showing current step.

        Args:
            step: Current step number (0-indexed internally, 1-indexed for display)
            total_steps: Optional total steps for progress display
        """
        step_display = step + 1
        if total_steps is not None:
            title = f"[bold]Step {step_display}/{total_steps}[/bold]"
        else:
            title = f"[bold]Step {step_display}[/bold]"

        rule = Rule(title, style="magenta", align="center")
        console.print(rule)

    @staticmethod
    def print_llm_input(
        messages: List[Dict[str, Any]], step: Optional[int] = None
    ) -> None:
        """
        Render the complete input messages sent to LLM after gather.

        Args:
            messages: List of message dicts with 'role' and 'content' keys
            step: Optional step number for header
        """
        if not messages:
            return

        from rich.console import Group

        # Build header
        if step is not None:
            header = Text(f"📤 LLM Input (Step {step + 1})", style="bold blue")
        else:
            header = Text("📤 LLM Input", style="bold blue")

        # Build message panels
        message_panels = []
        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            # Style based on role
            if role == "system":
                role_style = "dim"
                panel_style = "dim"
                icon = "⚙️"
            elif role == "user":
                role_style = "green"
                panel_style = "green"
                icon = "👤"
            elif role == "assistant":
                role_style = "cyan"
                panel_style = "cyan"
                icon = "🤖"
            else:
                role_style = "white"
                panel_style = "white"
                icon = "📝"

            # Truncate content if too long (high limit for full debug visibility)
            content_str = str(content)
            if len(content_str) > 200000:
                content_str = content_str[:200000] + "\n... [truncated]"

            # Format content with syntax highlighting if it's JSON
            if isinstance(content, dict):
                try:
                    content_json = json.dumps(content, ensure_ascii=False, indent=2)
                    content_text = Syntax(
                        content_json, "json", theme="monokai", word_wrap=True
                    )
                except:
                    content_text = Text(content_str, style=role_style)
            else:
                content_text = Text(content_str, style=role_style)

            msg_panel = Panel(
                content_text,
                title=f"{icon} {role.upper()}",
                box=ROUNDED,
                style=panel_style,
                expand=True,
            )
            message_panels.append(msg_panel)

        # Combine all panels
        content = Group(header, *message_panels)

        panel = Panel(
            content,
            title="[blue]Messages sent to LLM[/blue]",
            subtitle=f"[dim]Total: {len(messages)} messages[/dim]",
            box=DOUBLE,
            style="blue",
            expand=True,
        )
        console.print(panel)
        console.print()

    @staticmethod
    def print_thought(text: str, step: Optional[int] = None) -> None:
        """
        Render LLM reasoning process in cyan.

        Args:
            text: Thought content from LLM
            step: Optional step number for header
        """
        if not text:
            return

        content = Text(text, style="cyan")

        if step is not None:
            header = Text(f"🤔 Thought (Step {step + 1}):\n", style="bold cyan")
        else:
            header = Text("🤔 Thought:\n", style="bold cyan")

        panel = Panel(
            header + content,
            title="[cyan]Reasoning[/cyan]",
            subtitle="[dim]Thinking process[/dim]",
            box=ROUNDED,
            style="cyan",
            expand=True,
        )
        console.print(panel)

    @staticmethod
    def print_action(
        tool_name: str, args: Dict[str, Any], step: Optional[int] = None
    ) -> None:
        """
        Render tool call in yellow.

        Args:
            tool_name: Name of the tool being called
            args: Arguments passed to the tool
            step: Optional step number
        """
        if not tool_name:
            return

        # Build header
        if step is not None:
            header = Text(f"⚡ Action (Step {step + 1}): ", style="bold yellow")
        else:
            header = Text("⚡ Action: ", style="bold yellow")

        header.append(Text(f"{tool_name}", style="bold yellow"))

        # Build content with args
        if args:
            try:
                args_json = json.dumps(args, ensure_ascii=False, indent=2)
                args_syntax = Syntax(args_json, "json", theme="monokai", word_wrap=True)

                # Use Group to combine Text and Syntax
                from rich.console import Group

                content = Group(
                    header, Text("\n📋 Arguments:", style="yellow"), args_syntax
                )
            except (TypeError, ValueError):
                header.append(Text(f"\n📋 Args: {args}", style="yellow"))
                content = header
        else:
            content = header

        panel = Panel(
            content,
            title="[yellow]Tool Call[/yellow]",
            subtitle="[dim]Executing action[/dim]",
            box=ROUNDED,
            style="yellow",
            expand=True,
        )
        console.print(panel)

    @staticmethod
    def print_action_result(
        tool_name: str,
        success: bool = True,
        result: Optional[Any] = None,
        error: Optional[str] = None,
    ) -> None:
        """
        Render tool execution result.

        Args:
            tool_name: Name of the tool
            success: Whether the tool executed successfully
            result: Tool execution result
            error: Error message if failed
        """
        if success:
            status = Text("✓ ", style="green bold")
            status_text = "Success"
        else:
            status = Text("✗ ", style="red bold")
            status_text = "Error"

        content = Text.assemble(
            status,
            Text(f"{tool_name}: {status_text}\n", style="bold yellow"),
        )

        if success and result is not None:
            try:
                result_json = json.dumps(result, ensure_ascii=False, indent=2)
                result_text = Syntax(
                    result_json, "json", theme="monokai", word_wrap=True
                )
                content.append(Text("\n📊 Result:\n", style="green"))
                content.append(result_text)
            except (TypeError, ValueError):
                content.append(Text(f"\n📊 Result: {result}", style="green"))
        elif error:
            error_text = Text(f"\n❌ Error: {error}", style="red")
            content.append(error_text)

        panel = Panel(
            content,
            title="[green]Observation[/green]",
            subtitle="[dim]Tool execution result[/dim]",
            box=ROUNDED,
            style="green" if success else "red",
            expand=True,
        )
        console.print(panel)

    @staticmethod
    def print_observation(content: Any, step: Optional[int] = None) -> None:
        """
        Render tool execution result (Observation) in green.

        Args:
            content: Observation content from tool execution
            step: Optional step number
        """
        if content is None:
            return

        content_text = Text(str(content), style="green")

        if step is not None:
            header = Text(f"👁️ Observation (Step {step + 1}):\n", style="bold green")
        else:
            header = Text("👁️ Observation:\n", style="bold green")

        if isinstance(content, dict):
            try:
                content_json = json.dumps(content, ensure_ascii=False, indent=2)
                content_text = Syntax(
                    content_json, "json", theme="monokai", word_wrap=True
                )
            except (TypeError, ValueError):
                pass

        panel = Panel(
            header + content_text,
            title="[green]Observation[/green]",
            subtitle="[dim]Tool execution result[/dim]",
            box=ROUNDED,
            style="green",
            expand=True,
        )
        console.print(panel)

    @staticmethod
    def print_final_answer(answer: str, task: Optional[str] = None) -> None:
        """
        Render final answer in bold gold1 with double border.

        Args:
            answer: Final answer content
            task: Original task for context
        """
        if not answer:
            return

        title = "🎯 Final Answer"

        content = Text(answer, style="bold gold1")

        if task:
            task_text = Text(f"Task: {task}\n\n", style="dim")
            content = task_text + content

        panel = Panel(
            content,
            title=f"[bold gold1]{title}[/bold gold1]",
            subtitle="[dim]Execution complete[/dim]",
            box=DOUBLE,
            style="gold1",
            expand=True,
        )
        console.print(panel)
        console.print()

    @staticmethod
    def print_error(msg: str, exception: Optional[Exception] = None) -> None:
        """
        Render error or exception in red.

        Args:
            msg: Error message
            exception: Optional exception object
        """
        content = Text(f"❌ {msg}", style="red bold")

        if exception:
            import traceback

            error_details = Text(f"\n\n{traceback.format_exc()}", style="red")
            content = Text.assemble(content, error_details)

        panel = Panel(
            content,
            title="[red]Error[/red]",
            subtitle="[dim]Something went wrong[/dim]",
            box=ROUNDED,
            style="red",
            expand=True,
        )
        console.print(panel)

    @staticmethod
    def print_info(msg: str) -> None:
        """
        Render informational message.

        Args:
            msg: Info message
        """
        console.print(f"ℹ️  {msg}", style="blue")

    @staticmethod
    def print_separator(style: str = "dim") -> None:
        """Print a separator line."""
        console.print(Rule(style=style))

    @staticmethod
    def print_execution_summary(
        steps: int, tools_used: List[str], duration: float
    ) -> None:
        """
        Render execution summary.

        Args:
            steps: Total steps executed
            tools_used: List of tools used
            duration: Execution duration in seconds
        """
        table = Table(title="📊 Execution Summary", box=ROUNDED)

        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="magenta")

        table.add_row("Total Steps", str(steps))
        table.add_row("Tools Used", ", ".join(tools_used) if tools_used else "None")
        table.add_row("Duration", f"{duration:.2f}s")

        console.print(table)

    @staticmethod
    def print_welcome(agent_name: str, system_prompt: str = None) -> None:
        """
        Render welcome message.

        Args:
            agent_name: Name of the agent
            system_prompt: System prompt or None
        """
        title = Text(f"🧘 {agent_name}", style="bold magenta")
        panel = Panel(
            title,
            title="[bold]QitOS Agent[/bold]",
            subtitle="[dim]Interactive Mode[/dim]",
            box=DOUBLE,
            style="magenta",
            expand=True,
        )
        console.print(panel)

        if system_prompt:
            prompt_preview = Text(
                (
                    system_prompt[:200] + "..."
                    if len(system_prompt) > 200
                    else system_prompt
                ),
                style="dim",
            )
            console.print("\n[bold]System Prompt:[/bold]")
            console.print(PromptPreviewPanel(prompt_preview))

        console.print("\n[bold]Commands:[/bold]")
        console.print("  • Enter your task when prompted")
        console.print("  • Type [bold]quit[/bold] or [bold]exit[/bold] to exit")
        console.print("  • Type [bold]help[/bold] for more options\n")

    @staticmethod
    def print_prompt(prompt_text: str = "Your task") -> str:
        """
        Render interactive prompt and get user input.

        Args:
            prompt_text: Prompt text to display

        Returns:
            User input string
        """
        return console.input(f"[bold cyan]{prompt_text}:[/bold cyan] ")

    @staticmethod
    def clear() -> None:
        """Clear the console."""
        console.clear()


class PromptPreviewPanel(Panel):
    """Custom panel for displaying system prompt preview."""

    def __init__(self, content, **kwargs):
        super().__init__(
            content,
            title="[dim]System[/dim]",
            subtitle=None,
            box=ROUNDED,
            style="dim",
            **kwargs,
        )


def print_step_header(step: int, total_steps: Optional[int] = None) -> None:
    """Convenience function for RichRender.print_step_header"""
    RichRender.print_step_header(step, total_steps)


def print_llm_input(messages: List[Dict[str, Any]], step: Optional[int] = None) -> None:
    """Convenience function for RichRender.print_llm_input"""
    RichRender.print_llm_input(messages, step)


def print_thought(text: str, step: Optional[int] = None) -> None:
    """Convenience function for RichRender.print_thought"""
    RichRender.print_thought(text, step)


def print_action(
    tool_name: str, args: Dict[str, Any], step: Optional[int] = None
) -> None:
    """Convenience function for RichRender.print_action"""
    RichRender.print_action(tool_name, args, step)


def print_observation(content: Any, step: Optional[int] = None) -> None:
    """Convenience function for RichRender.print_observation"""
    RichRender.print_observation(content, step)


def print_final_answer(answer: str, task: Optional[str] = None) -> None:
    """Convenience function for RichRender.print_final_answer"""
    RichRender.print_final_answer(answer, task)


def print_error(msg: str, exception: Optional[Exception] = None) -> None:
    """Convenience function for RichRender.print_error"""
    RichRender.print_error(msg, exception)
