"""Default permission rules for QitOS.

Provides deny rules for protected paths and ask rules for
sensitive operations, mirroring Claude Code's safety defaults.
"""

from __future__ import annotations

import fnmatch
import os
from typing import List

from ...core.tool import ToolPermissionRule


# Paths that are always blocked for writes
PROTECTED_PATHS: List[str] = [
    ".git/",
    ".qitos/",
    ".bashrc",
    ".zshrc",
    ".profile",
    ".bash_profile",
    ".env",
    "credentials.json",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
    "authorized_keys",
    "known_hosts",
    ".ssh/config",
    ".netrc",
    ".aws/credentials",
    ".aws/config",
]

# Write tool names that should be checked against protected paths
WRITE_TOOL_NAMES = frozenset({
    "file_edit_v2", "write_file", "Edit", "Write",
    "str_replace", "insert", "replace_lines", "append_file",
    "create", "make_directory",
})

# Bash tool names
BASH_TOOL_NAMES = frozenset({
    "bash_v2", "Bash", "run_command",
})


def is_protected_path(path: str) -> bool:
    """Check if a path matches any protected path pattern.

    Normalizes the path and checks against all PROTECTED_PATHS patterns.
    Both the full path and path components are checked.
    """
    if not path:
        return False

    # Normalize path separators
    normalized = path.replace("\\", "/")

    # Remove leading ./ if present
    if normalized.startswith("./"):
        normalized = normalized[2:]

    for pattern in PROTECTED_PATHS:
        # Strip trailing slash for matching
        pat = pattern.rstrip("/")

        # Check if the path ends with the pattern (filename match)
        basename = os.path.basename(normalized)
        if fnmatch.fnmatch(basename, pat):
            return True

        # Check if the pattern appears as a directory component
        if f"/{pat}/" in f"/{normalized}/" or normalized.startswith(f"{pat}/"):
            return True

        # Direct match
        if fnmatch.fnmatch(normalized, pat):
            return True

    return False


def build_default_deny_rules() -> List[ToolPermissionRule]:
    """Generate deny rules for protected paths and dangerous operations."""
    rules = []

    # Deny writing to protected paths
    for pattern in PROTECTED_PATHS:
        scope = pattern
        for tool_name in WRITE_TOOL_NAMES:
            rules.append(
                ToolPermissionRule(
                    effect="deny",
                    tool_name=tool_name,
                    scope=scope,
                    message=f"Writing to protected path '{pattern}' is denied.",
                )
            )

    return rules


def build_default_ask_rules() -> List[ToolPermissionRule]:
    """Generate ask rules for sensitive operations."""
    rules = []

    # Bash commands need confirmation by default
    for tool_name in BASH_TOOL_NAMES:
        rules.append(
            ToolPermissionRule(
                effect="ask",
                tool_name=tool_name,
                message="Bash commands require confirmation.",
            )
        )

    # File write operations on sensitive scopes
    for tool_name in WRITE_TOOL_NAMES:
        rules.append(
            ToolPermissionRule(
                effect="ask",
                tool_name=tool_name,
                message=f"File write operation '{tool_name}' requires confirmation.",
            )
        )

    return rules
