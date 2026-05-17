"""Terminal markdown rendering using Rich.

Renders markdown-formatted text to ANSI-formatted terminal output,
with syntax highlighting for code blocks via Pygments.
"""

from __future__ import annotations

import io
import sys
from typing import Optional

from rich.markdown import Markdown
from rich.console import Console
from rich.theme import Theme


# Theme matching Claude Code's style
_CLAUDE_THEME = Theme({
    "markdown.heading": "bold",
    "markdown.code": "cyan",
    "markdown.codeblock": "default",
    "markdown.item.bullet": "default",
    "markdown.item.number": "default",
    "markdown.link": "underline blue",
    "markdown.bold": "bold",
    "markdown.italic": "italic",
})


def _detect_width() -> int:
    try:
        import os
        return os.get_terminal_size().columns
    except OSError:
        return 80


def render_markdown(text: str, indent: str = "  ") -> None:
    """Render markdown text to the terminal with ANSI formatting.

    Args:
        text: Markdown-formatted text.
        indent: Prefix for each line (default: 2 spaces).
    """
    if not text or not text.strip():
        return

    md = Markdown(text)

    # Capture rendered output
    buf = io.StringIO()
    console = Console(
        file=buf,
        theme=_CLAUDE_THEME,
        force_terminal=True,
        no_color=False,
        width=_detect_width(),
    )
    console.print(md, end="")

    rendered = buf.getvalue()
    if not rendered:
        # Fallback: print raw text
        for line in text.split("\n"):
            print(f"{indent}{line}")
        return

    # Apply indent prefix to each line
    for line in rendered.split("\n"):
        if line.strip():
            print(f"{indent}{line}")
        else:
            print()


def render_text_or_markdown(text: str, indent: str = "  ") -> None:
    """Render text, auto-detecting if it contains markdown.

    If the text has markdown features (headings, code blocks, lists, bold),
    renders as markdown. Otherwise prints as plain text.
    """
    if not text or not text.strip():
        return

    # Detect markdown features
    has_markdown = any([
        "#" in text and any(line.startswith("#") for line in text.split("\n")),
        "```" in text,
        "**" in text,
        "* " in text or "- " in text,
        "`" in text and not text.startswith("<"),
    ])

    if has_markdown:
        render_markdown(text, indent=indent)
    else:
        for line in text.split("\n"):
            print(f"{indent}{line}")
