"""Output formatting for the REPL.

Provides tool display name mapping, tool detail formatting,
tool result formatting, duration formatting, and text cleaning.
All customizable via ``DisplayConfig``.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# ANSI escape codes
# ---------------------------------------------------------------------------

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Display configuration
# ---------------------------------------------------------------------------

# Default tool display name mapping
DEFAULT_TOOL_DISPLAY = {
    "Glob": "Glob",
    "glob_v2": "Glob",
    "glob_files": "Glob",
    "Grep": "Grep",
    "grep_v2": "Grep",
    "grep_files": "Grep",
    "Read": "Read",
    "read_file": "Read",
    "file_read_v2": "Read",
    "read_file_range": "Read",
    "view": "Read",
    "Edit": "Edit",
    "file_edit_v2": "Edit",
    "str_replace": "Edit",
    "replace_lines": "Edit",
    "Write": "Write",
    "write_file": "Write",
    "file_write_v2": "Write",
    "Bash": "Bash",
    "bash_v2": "Bash",
    "run_command": "Bash",
    "WebFetch": "WebFetch",
    "web_fetch_v2": "WebFetch",
    "web_fetch": "WebFetch",
    "AskUserQuestion": "Ask",
    "ask_user_choice": "Ask",
    "list_tree": "LsTree",
    "list_files": "LsFiles",
    "create": "Write",
    "search": "Grep",
    "insert": "Edit",
    "append_file": "Edit",
    "make_directory": "Bash",
}


@dataclass
class DisplayConfig:
    """Configuration for REPL output formatting."""

    # Tool name mapping: internal name -> display name
    tool_display: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_TOOL_DISPLAY))

    # Unicode markers
    output_marker: str = "\u2b32"      # ⏲ (close to ⏺)
    result_prefix: str = "\u23ff"       # ⏿ (close to ⎿)
    churn_marker: str = "\u2733"        # ✳ (close to ✻)

    # Whether to use ANSI colors
    use_color: bool = True

    def tool_display_name(self, name: str) -> str:
        return self.tool_display.get(name, name)


# ---------------------------------------------------------------------------
# Tool detail formatting
# ---------------------------------------------------------------------------

def tool_detail(config: DisplayConfig, name: str, args: dict) -> str:
    """Short human-readable detail for a tool call."""
    n = config.tool_display_name(name)
    if n in ("Read",):
        path = args.get("path", args.get("file_path", ""))
        return str(path) if path else ""
    if n in ("Edit",):
        path = args.get("path", args.get("file_path", ""))
        return str(path) if path else ""
    if n in ("Write",):
        path = args.get("path", args.get("file_path", ""))
        return str(path) if path else ""
    if n in ("Glob",):
        return args.get("pattern", "")
    if n in ("Grep",):
        parts = []
        if args.get("pattern"):
            parts.append(args["pattern"])
        if args.get("path"):
            parts.append(args["path"])
        return " in ".join(parts)
    if n in ("Bash",):
        cmd = args.get("command", "")
        first_line = cmd.split("\n")[0].strip()
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        return first_line
    if n in ("WebFetch",):
        return args.get("url", "")
    if n in ("Ask",):
        return args.get("question", "")[:60]
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 40:
            v_str = v_str[:37] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts[:3])


# ---------------------------------------------------------------------------
# Tool result formatting
# ---------------------------------------------------------------------------

def format_tool_result(config: DisplayConfig, tool_name: str, result: Any) -> str:
    """Format a tool result for display. Concise, tool-specific."""
    from qitos.core.tool_result import ToolResult

    tr = ToolResult.from_value(result)
    if tr.status != "success":
        return f"{RED}Error: {tr.error or 'unknown'}{RESET}"

    output = tr.output
    name = config.tool_display_name(tool_name)

    if isinstance(output, dict):
        status = output.get("status", "")

        if name == "Read":
            content = output.get("content", "")
            lines = content.count("\n") + 1 if content else 0
            total_lines = output.get("total_lines", lines)
            path = output.get("path", "")
            if total_lines and total_lines != lines:
                return f"{DIM}Read lines {output.get('offset', 1)}-{output.get('offset', 0) + lines} of {total_lines} from {path}{RESET}"
            return f"{DIM}Read {lines} lines from {path}{RESET}"

        if name == "Glob":
            count = output.get("match_count", output.get("num_files", 0))
            truncated = output.get("truncated", False)
            suffix = "+" if truncated else ""
            return f"{DIM}Found {count}{suffix} files{RESET}"

        if name == "Grep":
            count = output.get("match_count", 0)
            return f"{DIM}Found {count} matches{RESET}"

        if name == "Edit":
            # Show diff if old_content is available
            old = output.get("old_content", "")
            new = output.get("new_content", "")
            path = output.get("path", "")
            if old and new:
                import difflib
                old_lines = old.splitlines(keepends=True)
                new_lines = new.splitlines(keepends=True)
                diff = difflib.unified_diff(
                    old_lines, new_lines,
                    fromfile=f"a/{path}", tofile=f"b/{path}",
                    n=3,
                )
                diff_text = "".join(diff).strip()
                if diff_text:
                    # Color the diff lines
                    colored = []
                    for line in diff_text.split("\n"):
                        if line.startswith("+") and not line.startswith("+++"):
                            colored.append(f"{GREEN}{line}{RESET}")
                        elif line.startswith("-") and not line.startswith("---"):
                            colored.append(f"{RED}{line}{RESET}")
                        elif line.startswith("@@"):
                            colored.append(f"{DIM}{line}{RESET}")
                        else:
                            colored.append(line)
                    return "\n".join(colored)
            return f"{GREEN}Edited {path}{RESET}"

        if name == "Write":
            path = output.get("path", "")
            return f"{GREEN}Wrote to {path}{RESET}"

        if name == "Bash":
            stdout = str(output.get("stdout", output.get("output", "")))
            stderr = str(output.get("stderr", ""))
            exit_code = output.get("exit_code", 0)
            lines = (stdout or stderr).strip().split("\n")
            if len(lines) > 20:
                shown = lines[-20:]
                header = f"{DIM}... ({len(lines) - 20} more lines){RESET}\n"
            else:
                shown = lines
                header = ""
            body = "\n".join(shown)
            if len(body) > 500:
                body = body[:497] + "..."
            result_str = header + body if header else body
            if exit_code and int(exit_code) != 0:
                return f"{RED}{result_str}{RESET}"
            return result_str

        if status == "success":
            path = output.get("path", "")
            if path:
                return f"{GREEN}Success: {path}{RESET}"
            return f"{GREEN}Success{RESET}"

        text = str(output)
        if len(text) > 300:
            text = text[:297] + "..."
        return text

    text = str(output or "")
    if len(text) > 300:
        text = text[:297] + "..."
    return text


# ---------------------------------------------------------------------------
# Duration / separator
# ---------------------------------------------------------------------------

def format_duration(seconds: float) -> str:
    """Format elapsed time like 'Xm Xs' or 'Xs'."""
    if seconds < 1:
        return f"{seconds:.0f}s"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    if mins > 0:
        return f"{mins}m {secs}s"
    return f"{secs}s"


def print_separator() -> None:
    """Print a horizontal separator line."""
    try:
        cols = os.get_terminal_size().columns
    except OSError:
        cols = 80
    print(DIM + "─" * cols + RESET)


# ---------------------------------------------------------------------------
# Text cleaning — strip protocol markup from model output
# ---------------------------------------------------------------------------

def strip_json_decisions(text: str) -> str:
    """Strip JSON decision blocks like {"thought":...,"action":...} from text."""
    result = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            j = i
            while j < len(text):
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if depth == 0:
                candidate = text[i:j + 1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict) and "thought" in parsed and (
                        "action" in parsed or "final_answer" in parsed
                    ):
                        i = j + 1
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass
        result.append(text[i])
        i += 1
    return "".join(result)


def clean_model_text(text: str) -> str:
    """Strip internal protocol markup from model output."""
    if not text:
        return ""
    text = re.sub(
        r"<(?:tool_use|tool_call|tool)>.*?</(?:tool_use|tool_call|tool)>",
        "", text, flags=re.DOTALL
    )
    text = re.sub(r"<final_answer>.*?</final_answer>", "", text, flags=re.DOTALL)
    text = re.sub(r"<minimax:tool_call>.*?</minimax:tool_call>", "", text, flags=re.DOTALL)
    text = re.sub(r"<minimax:response>.*?</minimax:response>", "", text, flags=re.DOTALL)
    text = re.sub(r"<decision\b[^>]*>.*?</decision>", "", text, flags=re.DOTALL)
    text = strip_json_decisions(text)
    text = re.sub(r"^Thought:\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Action:\s*\w+\(.*?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Final Answer:\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_stream_text(text: str) -> str:
    """Clean streaming text by stripping protocol tags (partial + complete).

    Unlike ``clean_model_text``, this also handles partial/incomplete
    XML tags that are still being streamed token-by-token.
    """
    text = re.sub(
        r"<(?:tool_use|tool_call|tool)>.*?</(?:tool_use|tool_call|tool)>",
        "", text, flags=re.DOTALL
    )
    text = re.sub(r"<final_answer>.*?</final_answer>", "", text, flags=re.DOTALL)
    text = re.sub(r"<minimax:tool_call>.*?</minimax:tool_call>", "", text, flags=re.DOTALL)
    text = re.sub(r"<minimax:response>.*?</minimax:response>", "", text, flags=re.DOTALL)
    text = re.sub(r"<decision\b[^>]*>.*?</decision>", "", text, flags=re.DOTALL)
    text = re.sub(r"<thinking>.*?</thinking>", "", text, flags=re.DOTALL)
    # Partial/incomplete tags (still being streamed)
    text = re.sub(
        r"<(?:tool_use|tool_call|tool|minimax:|final_answer|decision|thinking)[^>]*$", "",
        text
    )
    text = strip_json_decisions(text)
    text = re.sub(r"^Thought:\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Action:\s*\w+\(.*?\)\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"^Final Answer:\s*", "", text, flags=re.MULTILINE)
    return text


# ---------------------------------------------------------------------------
# Action helpers
# ---------------------------------------------------------------------------

def action_name(action: Any) -> str:
    """Extract the name from an Action object or dict."""
    if isinstance(action, dict):
        return str(action.get("name", ""))
    return str(getattr(action, "name", ""))


def action_args(action: Any) -> dict:
    """Extract args from an Action object or dict."""
    if isinstance(action, dict):
        return dict(action.get("args", {}) or {})
    args = getattr(action, "args", {})
    return dict(args) if isinstance(args, dict) else {}


def model_text_from_record(record: Any) -> str:
    """Extract model response text from a StepRecord."""
    model_response = getattr(record, "model_response", None)
    if isinstance(model_response, dict):
        return str(model_response.get("text", "") or "").strip()
    return ""
