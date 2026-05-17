"""Coding context builders for QitOS agents.

Provides reusable context sections that any coding agent would need:
- Environment info (cwd, platform, git branch)
- Git status (dirty files)
- Project instructions (.qitos/instructions.md, CLAUDE.md)
- Memory system instructions (~/.qitos/memory/)
- Session-specific guidance (tool usage hints)
"""

from __future__ import annotations

import os
import platform
import subprocess
from typing import Optional


def build_env_section(workspace_root: str) -> str:
    """Build the environment info section for the system prompt.

    :param workspace_root: Absolute path to the workspace directory.
    """
    cwd = os.path.abspath(workspace_root)
    is_git = os.path.isdir(os.path.join(cwd, ".git"))
    plat = platform.system()
    shell = os.environ.get("SHELL", "unknown")

    # Get git branch if in a git repo
    git_branch = ""
    if is_git:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=5,
            )
            git_branch = result.stdout.strip()
        except Exception:
            pass

    lines = [
        "# Environment",
        "You have been invoked in the following environment:",
        f" - Primary working directory: {cwd}",
        f" - Is a git repository: {'Yes' if is_git else 'No'}",
        f" - Platform: {plat}",
        f" - Shell: {shell}",
    ]
    if git_branch:
        lines.append(f" - Current git branch: {git_branch}")

    return "\n".join(lines)


def build_git_status_section(workspace_root: str) -> str:
    """Build a git status section for the system prompt.

    :param workspace_root: Absolute path to the workspace directory.
    """
    cwd = os.path.abspath(workspace_root)
    if not os.path.isdir(os.path.join(cwd, ".git")):
        return ""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=5,
        )
        status = result.stdout.strip()
        if status:
            # Limit to 30 lines to avoid bloating the prompt
            status_lines = status.split("\n")[:30]
            return "# Git Status\n" + "\n".join(status_lines)
    except Exception:
        pass
    return ""


def load_project_instructions(workspace_root: str) -> str:
    """Load project instructions from .qitos/instructions.md or CLAUDE.md.

    :param workspace_root: Absolute path to the workspace directory.
    """
    cwd = os.path.abspath(workspace_root)

    # Check .qitos/instructions.md first
    instructions_path = os.path.join(cwd, ".qitos", "instructions.md")
    if os.path.isfile(instructions_path):
        try:
            with open(instructions_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass

    # Also check for CLAUDE.md at the root (Claude Code compatibility)
    claude_md_path = os.path.join(cwd, "CLAUDE.md")
    if os.path.isfile(claude_md_path):
        try:
            with open(claude_md_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            pass

    return ""


def build_memory_section() -> str:
    """Build the memory system section if memory directory exists.

    Reads from ~/.qitos/memory/ and loads MEMORY.md index if present.
    """
    memory_dir = os.path.join(os.path.expanduser("~"), ".qitos", "memory")
    if not os.path.isdir(memory_dir):
        return ""

    parts = [
        "# auto memory",
        "",
        f"You have a persistent, file-based memory system at {memory_dir}. This directory already exists — write to it directly with the Write tool.",
        "",
        "You should build up this memory system over time so that future conversations can have a complete picture of who the user is, how they'd like to collaborate with you, and the context behind the work the user gives you.",
        "",
        "If the user explicitly asks you to remember something, save it immediately as whichever type fits best. If they ask you to forget something, find and remove the relevant entry.",
        "",
        "## Types of memory",
        "- user: Information about the user's role, goals, preferences, and knowledge",
        "- feedback: Guidance the user has given you about how to approach work — both what to avoid and what to keep doing",
        "- project: Information about ongoing work, goals, or incidents not derivable from code/git",
        "- reference: Pointers to where information can be found in external systems",
        "",
        "## What NOT to save",
        "- Code patterns, architecture, file paths — derivable from reading code",
        "- Git history — git log is authoritative",
        "- Anything already documented in CLAUDE.md files",
        "- Ephemeral task details from the current conversation",
        "",
        "## How to save",
        "1. Write the memory to its own file using frontmatter: name, description, type",
        "2. Add a pointer to that file in MEMORY.md (one line per entry, under 150 chars)",
    ]

    # Load MEMORY.md index if it exists
    memory_index = os.path.join(memory_dir, "MEMORY.md")
    if os.path.isfile(memory_index):
        try:
            with open(memory_index, "r", encoding="utf-8") as f:
                index_content = f.read().strip()
            if index_content:
                parts.append("")
                parts.append("## Current memories")
                parts.append(index_content)
        except OSError:
            pass

    return "\n".join(parts)


def build_session_guidance_section() -> str:
    """Build session-specific guidance section.

    Provides tool usage hints that are generic to any coding agent
    with the CodingToolSet tools.
    """
    return (
        "# Session-specific guidance\n"
        " - If you do not understand why the user has denied a tool call, use the AskUserQuestion tool to ask them.\n"
        " - If you need the user to run a shell command themselves (e.g., an interactive login like `gcloud auth login`), "
        "suggest they type `! <command>` in the prompt — the `!` prefix runs the command in this session so its output lands directly in the conversation.\n"
        " - Use the Agent tool with subagent_type=\"Explore\" for fast codebase search when you need to quickly find files or search code.\n"
        " - Use the Agent tool with subagent_type=\"Plan\" for read-only architecture analysis when you need to design an implementation approach."
    )


def build_coding_context(
    workspace_root: str,
    *,
    include_git_status: bool = True,
    include_memory: bool = True,
    include_session_guidance: bool = True,
) -> str:
    """Compose all context sections into a single string.

    :param workspace_root: Absolute path to the workspace directory.
    :param include_git_status: Whether to include git status section.
    :param include_memory: Whether to include memory system section.
    :param include_session_guidance: Whether to include session guidance.
    """
    parts = [build_env_section(workspace_root)]

    if include_git_status:
        git_status = build_git_status_section(workspace_root)
        if git_status:
            parts.append(git_status)

    if include_memory:
        memory = build_memory_section()
        if memory:
            parts.append(memory)

    if include_session_guidance:
        parts.append(build_session_guidance_section())

    return "\n\n".join(parts)


__all__ = [
    "build_env_section",
    "build_git_status_section",
    "load_project_instructions",
    "build_memory_section",
    "build_session_guidance_section",
    "build_coding_context",
]
