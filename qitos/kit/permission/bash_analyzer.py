"""Bash command safety analyzer for QitOS.

Analyzes bash commands using pattern matching (20+ validators)
to classify them as safe, needs_review, or unsafe.
Mirrors Claude Code's command safety analysis.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class CommandSafety(str, Enum):
    SAFE = "safe"
    NEEDS_REVIEW = "needs_review"
    UNSAFE = "unsafe"


@dataclass
class BashAnalysisResult:
    """Result of bash command safety analysis."""

    safety: CommandSafety
    explanation: str
    detected_patterns: List[str] = field(default_factory=list)
    is_read_only: bool = False
    extracted_paths: List[str] = field(default_factory=list)


# ── Pattern definitions ────────────────────────────────────────────────────────

# Destructive commands that are always unsafe
DESTRUCTIVE_PATTERNS: List[Tuple[str, str]] = [
    (r"\brm\s+.*-[a-zA-Z]*r[a-zA-Z]*f", "recursive forced delete"),
    (r"\brm\s+.*-[a-zA-Z]*f[a-zA-Z]*r", "forced recursive delete"),
    (r"\brm\s+-rf\b", "rm -rf"),
    (r"\brm\s+-fr\b", "rm -fr"),
    (r"\brm\s+--no-preserve-root", "delete with no-preserve-root"),
    (r"\bsudo\s+rm\b", "sudo delete"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+if=", "raw disk copy"),
    (r":\(\)\s*\{", "fork bomb"),
    (r"\bchmod\s+-R\s+777\b", "recursive world-writable"),
    (r"\bchown\s+-R\b", "recursive ownership change"),
    (r"\bshutdown\b", "system shutdown"),
    (r"\breboot\b", "system reboot"),
    (r"\binit\s+[06]\b", "change runlevel to halt/reboot"),
    (r"\bhalt\b", "halt system"),
    (r"\bpoweroff\b", "power off system"),
    (r">\s*/dev/sd", "write directly to disk device"),
]

# Git destructive operations
GIT_DESTRUCTIVE_PATTERNS: List[Tuple[str, str]] = [
    (r"\bgit\s+push\s+.*--force", "force push"),
    (r"\bgit\s+push\s+-f\b", "force push (short flag)"),
    (r"\bgit\s+reset\s+--hard", "hard reset"),
    (r"\bgit\s+checkout\s+\.\s*$", "discard all working changes"),
    (r"\bgit\s+clean\s+-[a-zA-Z]*f", "force clean untracked files"),
    (r"\bgit\s+branch\s+-[dD]\b", "delete branch"),
    (r"\bgit\s+stash\s+drop\b", "drop stash"),
    (r"\bgit\s+reflog\s+expire\b", "expire reflog"),
    (r"\bgit\s+filter-branch\b", "rewrite history"),
    (r"\bgit\s+rebase\b.*--no-verify", "rebase without verification"),
]

# Shell metacharacters that need review
SHELL_METACHAR_PATTERNS: List[Tuple[str, str]] = [
    (r"(?<!\\)\|(?!\|)", "pipe"),
    (r"(?<!\\)&(?!&)", "background operator"),
    (r"(?<!\\);", "command separator"),
    (r"(?<!\\)&&", "and operator"),
    (r"(?<!\\)\|\|", "or operator"),
    (r"(?<!\\)>", "output redirect"),
    (r"(?<!\\)>>", "append redirect"),
]

# Command substitution patterns
SUBSTITUTION_PATTERNS: List[Tuple[str, str]] = [
    (r"\$\(", "command substitution $(...)"),
    (r"\$\{", "variable expansion ${...}"),
    (r"`[^`]+`", "backtick substitution"),
]

# Obfuscation patterns
OBFUSCATION_PATTERNS: List[Tuple[str, str]] = [
    (r"\\x[0-9a-fA-F]{2}", "hex escape sequence"),
    (r"\\u[0-9a-fA-F]{4}", "unicode escape sequence"),
    (r"\\[0-7]{3}", "octal escape sequence"),
    (r"[\u200b-\u200f\u2028-\u202f]", "zero-width/invisible unicode character"),
    (r"\beval\s+", "eval (dynamic execution)"),
    (r"\bexec\s+", "exec (replace process)"),
    (r"\bsource\s+", "source (execute in current shell)"),
    (r"\b\.\s+/", "dot-source script"),
]

# Interactive commands that don't make sense in non-interactive context
INTERACTIVE_COMMANDS: List[str] = [
    "vim", "vi", "nano", "emacs", "less", "more", "top", "htop",
    "screen", "tmux", "ssh", "telnet", "ftp", "nc",
    "mysql", "psql", "sqlite3", "redis-cli",
    "python", "python3", "node", "irb", "php -a",
]

# Network commands that may need review
NETWORK_PATTERNS: List[Tuple[str, str]] = [
    (r"\bcurl\s+", "curl request"),
    (r"\bwget\s+", "wget download"),
    (r"\bnc\s+", "netcat"),
    (r"\bncat\s+", "ncat"),
    (r"\bsocat\s+", "socat"),
    (r"\bscp\s+", "scp transfer"),
    (r"\brsync\s+", "rsync transfer"),
]

# Read-only command prefixes
READ_ONLY_PREFIXES: List[str] = [
    "ls", "cat", "head", "tail", "wc", "find", "grep", "egrep", "fgrep",
    "git status", "git log", "git diff", "git branch", "git remote",
    "git show", "git stash list", "git tag", "git describe",
    "echo", "printf", "pwd", "whoami", "which", "type", "whereis",
    "uname", "hostname", "date", "uptime",
    "df", "du", "free", "vmstat", "iostat",
    "env", "printenv", "set", "export",
    "python --version", "python3 --version", "node --version",
    "pip list", "pip show", "pip freeze", "npm list",
    "test", "[", "[[",
    "file", "stat", "md5sum", "sha256sum", "cksum",
    "sort", "uniq", "cut", "tr", "tee", "xargs",
    "awk", "sed -n", "diff", "comm", "paste", "join", "column",
    "ps", "lsof", "top -b", "netstat",
]

# Write/dangerous command prefixes (commands that modify state)
WRITE_COMMAND_PREFIXES: List[str] = [
    "rm ", "mv ", "cp ", "mkdir ", "touch ", "chmod ", "chown ",
    "git add", "git commit", "git push", "git merge", "git rebase",
    "git checkout", "git stash", "git cherry-pick",
    "pip install", "pip uninstall", "npm install", "npm uninstall",
    "docker rm", "docker rmi", "docker stop", "docker kill",
    "kill ", "killall ", "pkill ",
    "sed -i", "tee ", "truncate",
]


class BashCommandAnalyzer:
    """Analyzes bash commands for safety using pattern matching.

    Uses shlex.split() for tokenization where possible, falls back to
    pattern matching for complex shell constructs.
    """

    def analyze(self, command: str) -> BashAnalysisResult:
        """Analyze a bash command and return safety classification.

        Runs all checks. If any UNSAFE pattern found -> unsafe.
        If any NEEDS_REVIEW pattern found -> needs_review.
        Otherwise -> safe.
        """
        if not command or not command.strip():
            return BashAnalysisResult(
                safety=CommandSafety.SAFE,
                explanation="Empty command.",
                is_read_only=True,
            )

        detected: List[str] = []
        is_unsafe = False
        needs_review = False

        # Check destructive commands
        for pattern, desc in DESTRUCTIVE_PATTERNS:
            if re.search(pattern, command):
                detected.append(desc)
                is_unsafe = True

        # Check git destructive
        for pattern, desc in GIT_DESTRUCTIVE_PATTERNS:
            if re.search(pattern, command):
                detected.append(desc)
                is_unsafe = True

        # Check obfuscation
        for pattern, desc in OBFUSCATION_PATTERNS:
            if re.search(pattern, command):
                detected.append(desc)
                needs_review = True

        # Check command substitution
        for pattern, desc in SUBSTITUTION_PATTERNS:
            if re.search(pattern, command):
                detected.append(desc)
                needs_review = True

        # Check shell metacharacters
        for pattern, desc in SHELL_METACHAR_PATTERNS:
            if re.search(pattern, command):
                detected.append(desc)
                needs_review = True

        # Check interactive commands
        first_token = self._get_first_token(command)
        if first_token in INTERACTIVE_COMMANDS:
            detected.append(f"interactive command: {first_token}")
            needs_review = True

        # Check network commands
        for pattern, desc in NETWORK_PATTERNS:
            if re.search(pattern, command):
                detected.append(desc)
                needs_review = True

        # Determine safety level
        if is_unsafe:
            safety = CommandSafety.UNSAFE
            explanation = f"Unsafe patterns detected: {', '.join(detected)}"
        elif needs_review:
            safety = CommandSafety.NEEDS_REVIEW
            explanation = f"Review needed: {', '.join(detected)}"
        else:
            safety = CommandSafety.SAFE
            explanation = "No dangerous patterns detected."

        return BashAnalysisResult(
            safety=safety,
            explanation=explanation,
            detected_patterns=detected,
            is_read_only=self.is_read_only(command),
            extracted_paths=self.extract_paths(command),
        )

    def is_read_only(self, command: str) -> bool:
        """Determine if a command is read-only (no side effects)."""
        if not command or not command.strip():
            return True

        stripped = command.strip()

        # Check against read-only prefixes
        for prefix in READ_ONLY_PREFIXES:
            if stripped.startswith(prefix) or stripped.startswith(f"  {prefix}"):
                # Make sure it's not a write variant (e.g., "sed -i")
                for write_prefix in WRITE_COMMAND_PREFIXES:
                    if stripped.startswith(write_prefix):
                        return False
                return True

        # Check for write operations
        for write_prefix in WRITE_COMMAND_PREFIXES:
            if stripped.startswith(write_prefix):
                return False

        # Check for redirection
        if re.search(r"(?<!\\)>", command) or re.search(r"(?<!\\)>>", command):
            return False

        return False

    def extract_paths(self, command: str) -> List[str]:
        """Extract file paths referenced in the command.

        Uses shlex.split() for tokenization, then identifies
        tokens that look like file paths.
        """
        paths: List[str] = []

        try:
            tokens = shlex.split(command)
        except ValueError:
            # Fallback: simple whitespace split
            tokens = command.split()

        for token in tokens:
            # Skip flags/options
            if token.startswith("-"):
                continue
            # Skip common commands
            if token in INTERACTIVE_COMMANDS or token in (
                "sudo", "time", "nice", "ionice", "strace", "ltrace",
                "env", "xargs", "exec", "eval",
            ):
                continue
            # Looks like a file path
            if "/" in token or token.startswith(".") or token.startswith("~"):
                paths.append(token)
            # Looks like a file with extension
            elif "." in token and not token.startswith(("http:", "https:", "ftp:")):
                # Check if it has a common extension
                base = token.rsplit(".", 1)[-1].lower()
                if base.isalpha() and len(base) <= 10:
                    paths.append(token)

        return paths

    def _get_first_token(self, command: str) -> str:
        """Extract the first token of a command."""
        stripped = command.strip()
        if not stripped:
            return ""

        # Handle sudo
        if stripped.startswith("sudo "):
            stripped = stripped[5:].strip()

        # Handle time/nice prefixes
        for prefix in ("time ", "nice ", "ionice "):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):].strip()

        # Get first word
        match = re.match(r"([a-zA-Z0-9_.-]+)", stripped)
        return match.group(1) if match else ""
