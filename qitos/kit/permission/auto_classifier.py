"""Auto-permission classifier for QitOS.

2-stage classifier (fast heuristic -> LLM chain-of-thought) for
AUTO permission mode. Includes denial tracking with circuit breaker.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Set


# Tool name sets for heuristic classification
SAFE_READ_TOOLS: Set[str] = {
    "file_read_v2", "read_file", "Read", "view",
    "glob_v2", "Glob", "grep_v2", "Grep",
    "list_directory", "AskUserQuestion",
}

WRITE_TOOLS: Set[str] = {
    "file_edit_v2", "write_file", "Edit", "Write",
    "str_replace", "insert", "replace_lines", "append_file",
}

BASH_TOOLS: Set[str] = {
    "bash_v2", "Bash", "run_command",
}

# Heuristically safe read-only bash commands
SAFE_BASH_PREFIXES = (
    "ls ", "cat ", "head ", "tail ", "wc ", "find ", "grep ",
    "git status", "git log", "git diff", "git branch",
    "git remote", "git show", "git stash list",
    "echo ", "pwd", "whoami", "which ", "type ",
    "python --version", "python3 --version", "node --version",
    "pip list", "pip show", "pip freeze",
    "uname ", "df ", "du ", "env", "printenv",
)

# Heuristically dangerous bash commands
DANGEROUS_BASH_PATTERNS = (
    "rm -rf", "rm -r", "sudo rm", "mkfs", "dd if=",
    "chmod 777", "chmod -R 777", ":(){ :|:&", "fork bomb",
    "git push --force", "git reset --hard", "git checkout .",
    "git clean -", "git branch -D",
)


class AutoPermissionClassifier:
    """2-stage classifier for AUTO permission mode.

    Stage 1: Fast heuristic (pattern matching on tool_name + args)
    Stage 2: LLM chain-of-thought (if heuristic is uncertain and LLM available)
    Falls back to "ask" if uncertain.

    Includes denial tracking with circuit breaker:
    - Max 3 consecutive denials
    - Max 20 total denials
    - Lock-out triggers interactive prompt
    """

    MAX_CONSECUTIVE: int = 3
    MAX_TOTAL: int = 20

    def __init__(self, llm: Any = None):
        self._llm = llm
        self._consecutive_denials = 0
        self._total_denials = 0
        self._recently_read: Set[str] = set()

    def classify(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Any = None,
    ) -> str:
        """Classify a tool call as allow/deny/ask.

        Returns one of "allow", "deny", "ask".
        """
        # Stage 1: Fast heuristic
        result = self._fast_heuristic(tool_name, args)
        if result is not None:
            return result

        # Stage 2: LLM chain-of-thought (if available)
        if self._llm is not None:
            return self._llm_classify(tool_name, args)

        # Uncertain -> ask
        return "ask"

    def _fast_heuristic(
        self, tool_name: str, args: Dict[str, Any]
    ) -> Optional[str]:
        """Stage 1: Fast pattern-based classification."""
        # Known-safe read tools
        if tool_name in SAFE_READ_TOOLS:
            return "allow"

        # Write tools: allow if path was recently read
        if tool_name in WRITE_TOOLS:
            path = args.get("path") or args.get("file_path", "")
            if path and self._was_recently_read(path):
                return "allow"
            return "ask"

        # Bash tools: heuristic classification
        if tool_name in BASH_TOOLS:
            return self._classify_bash(args.get("command", ""))

        # Unknown tools -> ask
        return None

    def _classify_bash(self, command: str) -> Optional[str]:
        """Classify a bash command using pattern heuristics."""
        if not command:
            return "ask"

        # Check dangerous patterns first
        for pattern in DANGEROUS_BASH_PATTERNS:
            if pattern in command:
                return "deny"

        # Check safe prefixes
        for prefix in SAFE_BASH_PREFIXES:
            if command.strip().startswith(prefix):
                return "allow"

        # Commands with pipes/substitution need review
        if any(c in command for c in ("|", "$(", "`", "&&", "||")):
            return "ask"

        return None

    def _was_recently_read(self, path: str) -> bool:
        """Check if a path was recently read (heuristic for auto-allow writes)."""
        import os
        return os.path.abspath(path) in self._recently_read

    def record_read(self, path: str) -> None:
        """Record that a path was recently read."""
        import os
        self._recently_read.add(os.path.abspath(path))

    def _llm_classify(
        self, tool_name: str, args: Dict[str, Any]
    ) -> str:
        """Stage 2: LLM chain-of-thought classification."""
        try:
            prompt = (
                "Classify this tool call as 'allow', 'deny', or 'ask'.\n"
                f"Tool: {tool_name}\n"
                f"Args: {args}\n"
                "Respond with exactly one word: allow, deny, or ask."
            )
            response = self._llm(prompt)
            result = str(response).strip().lower()
            if result in ("allow", "deny", "ask"):
                return result
        except Exception:
            pass
        return "ask"

    def record_denial(self) -> None:
        """Track denials for circuit breaker."""
        self._consecutive_denials += 1
        self._total_denials += 1

    def record_approval(self) -> None:
        """Reset consecutive denial counter."""
        self._consecutive_denials = 0

    @property
    def is_locked_out(self) -> bool:
        """True if too many denials - must switch to interactive."""
        return (
            self._consecutive_denials >= self.MAX_CONSECUTIVE
            or self._total_denials >= self.MAX_TOTAL
        )

    @property
    def consecutive_denials(self) -> int:
        return self._consecutive_denials

    @property
    def total_denials(self) -> int:
        return self._total_denials
