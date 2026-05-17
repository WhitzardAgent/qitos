"""Multi-tier permission pipeline for QitOS.

Mirrors Claude Code's 10-step permission pipeline:
deny -> ask -> tool_specific -> safety -> bypass -> allow
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from ...core.tool import (
    ToolPermission,
    ToolPermissionContext,
    ToolPermissionDecision,
    ToolPermissionRule,
    ToolSpec,
)
from .bash_analyzer import BashCommandAnalyzer, CommandSafety
from .read_before_write import ReadBeforeWriteEnforcer
from .auto_classifier import AutoPermissionClassifier
from .rules import is_protected_path


class PermissionMode(str, Enum):
    """Permission mode controlling how tool calls are authorized."""

    DEFAULT = "default"
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"
    BYPASS = "bypass"
    AUTO = "auto"


# Tool name sets for mode-based decisions
WRITE_TOOL_NAMES = frozenset({
    "file_edit_v2", "write_file", "Edit", "Write",
    "str_replace", "insert", "replace_lines", "append_file",
    "create", "make_directory",
})

READ_TOOL_NAMES = frozenset({
    "file_read_v2", "read_file", "Read", "view",
    "Glob", "Grep",
})

BASH_TOOL_NAMES = frozenset({
    "bash_v2", "Bash", "run_command",
})


class PermissionPipeline:
    """Multi-stage permission evaluation pipeline.

    Stages (short-circuits on first deny/ask):
    1. DENY: check deny_rules + protected paths + mode-specific denials
    2. ASK: check ask_rules
    3. TOOL_SPECIFIC: read-before-write enforcement, mode-specific overrides
    4. SAFETY: BashCommandAnalyzer results for bash tools
    5. BYPASS: if mode==BYPASS, allow everything
    6. ALLOW: check allow_rules -> default decision
    """

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.DEFAULT,
        context: Optional[ToolPermissionContext] = None,
        bash_analyzer: Optional[BashCommandAnalyzer] = None,
        rbw_enforcer: Optional[ReadBeforeWriteEnforcer] = None,
        auto_classifier: Optional[AutoPermissionClassifier] = None,
    ):
        self.mode = mode
        self._context = context or ToolPermissionContext()
        self._bash_analyzer = bash_analyzer or BashCommandAnalyzer()
        self._rbw_enforcer = rbw_enforcer
        self._auto_classifier = auto_classifier

    def evaluate(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Optional[ToolSpec] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolPermissionDecision:
        """Evaluate whether a tool call should be allowed.

        Returns a ToolPermissionDecision (allow/deny/ask).
        """
        scope = self._build_scope(tool_name, args, tool_spec)

        # Stage 0: BYPASS — allow everything if in bypass mode
        if self.mode == PermissionMode.BYPASS:
            return ToolPermissionDecision.allow(scope=scope)

        # Stage 1: DENY — hard deny rules + protected paths + mode-specific
        deny = self._check_deny(tool_name, args, tool_spec, scope)
        if deny is not None:
            return deny

        # Stage 2: ASK — ask rules from context
        ask = self._check_ask(tool_name, args, tool_spec, scope)
        if ask is not None:
            return ask

        # Stage 3: TOOL_SPECIFIC — mode overrides + read-before-write
        specific = self._check_tool_specific(tool_name, args, tool_spec, scope)
        if specific is not None:
            return specific

        # Stage 4: SAFETY — bash command analysis
        safety = self._check_safety(tool_name, args, tool_spec, scope)
        if safety is not None:
            return safety

        # Stage 5: ALLOW — context allow rules + default
        return self._check_allow(tool_name, args, tool_spec, scope)

    def _build_scope(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Optional[ToolSpec],
    ) -> str:
        if tool_spec is not None and tool_spec.rule_scope_builder is not None:
            try:
                value = tool_spec.rule_scope_builder(dict(args))
                return str(value or "")
            except Exception:
                pass
        # Fallback: check common arg names
        for key in ("path", "file_path", "filename", "url", "command"):
            val = args.get(key)
            if val and isinstance(val, str):
                return val
        return ""

    def _check_deny(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Optional[ToolSpec],
        scope: str,
    ) -> Optional[ToolPermissionDecision]:
        # Check context deny rules
        for rule in self._context.deny_rules:
            if rule.matches(tool_name, scope):
                return ToolPermissionDecision.deny(
                    rule.message or f"Tool '{tool_name}' is denied by rule.",
                    scope=scope,
                    matched_rule=rule,
                )

        # Check protected paths for write tools
        if tool_name in WRITE_TOOL_NAMES:
            path = args.get("path") or args.get("file_path", "")
            if path and is_protected_path(path):
                return ToolPermissionDecision.deny(
                    f"Writing to protected path '{path}' is denied.",
                    scope=scope,
                )

        # PLAN mode: deny all writes
        if self.mode == PermissionMode.PLAN:
            if tool_spec is not None:
                perms = tool_spec.permissions
                if perms.filesystem_write or perms.command:
                    return ToolPermissionDecision.deny(
                        f"Tool '{tool_name}' is denied in plan mode (write/command operation).",
                        scope=scope,
                    )
            elif tool_name in WRITE_TOOL_NAMES or tool_name in BASH_TOOL_NAMES:
                return ToolPermissionDecision.deny(
                    f"Tool '{tool_name}' is denied in plan mode.",
                    scope=scope,
                )

        return None

    def _check_ask(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Optional[ToolSpec],
        scope: str,
    ) -> Optional[ToolPermissionDecision]:
        # Check context ask rules
        for rule in self._context.ask_rules:
            if rule.matches(tool_name, scope):
                return ToolPermissionDecision.ask(
                    rule.message or f"Tool '{tool_name}' requires confirmation.",
                    scope=scope,
                    matched_rule=rule,
                )
        return None

    def _check_tool_specific(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Optional[ToolSpec],
        scope: str,
    ) -> Optional[ToolPermissionDecision]:
        # ACCEPT_EDITS mode: auto-allow file edits, still ask for bash
        if self.mode == PermissionMode.ACCEPT_EDITS:
            if tool_name in WRITE_TOOL_NAMES:
                return ToolPermissionDecision.allow(scope=scope)
            if tool_name in BASH_TOOL_NAMES:
                return ToolPermissionDecision.ask(
                    f"Bash command requires confirmation.",
                    scope=scope,
                )

        # Read-before-write enforcement
        if self._rbw_enforcer is not None and tool_name in WRITE_TOOL_NAMES:
            path = args.get("path") or args.get("file_path", "")
            if path:
                allowed, reason = self._rbw_enforcer.check_write(path)
                if not allowed:
                    return ToolPermissionDecision.deny(
                        reason,
                        scope=scope,
                    )

        # AUTO mode: delegate to classifier
        if self.mode == PermissionMode.AUTO and self._auto_classifier is not None:
            if self._auto_classifier.is_locked_out:
                return ToolPermissionDecision.ask(
                    "Auto-permission locked out due to too many denials. "
                    "Please confirm manually.",
                    scope=scope,
                )
            classification = self._auto_classifier.classify(
                tool_name, args, tool_spec
            )
            if classification == "allow":
                return ToolPermissionDecision.allow(scope=scope)
            if classification == "deny":
                return ToolPermissionDecision.deny(
                    f"Auto-classifier denied tool '{tool_name}'.",
                    scope=scope,
                )
            # "ask" — fall through to allow pipeline to decide

        return None

    def _check_safety(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Optional[ToolSpec],
        scope: str,
    ) -> Optional[ToolPermissionDecision]:
        # Bash command safety analysis
        if tool_name in BASH_TOOL_NAMES:
            command = args.get("command", "")
            if command:
                result = self._bash_analyzer.analyze(command)
                if result.safety == CommandSafety.UNSAFE:
                    return ToolPermissionDecision.deny(
                        f"Command is unsafe: {result.explanation}",
                        scope=scope,
                    )
                if result.safety == CommandSafety.NEEDS_REVIEW:
                    return ToolPermissionDecision.ask(
                        f"Command needs review: {result.explanation}",
                        scope=scope,
                    )
        return None

    def _check_allow(
        self,
        tool_name: str,
        args: Dict[str, Any],
        tool_spec: Optional[ToolSpec],
        scope: str,
    ) -> ToolPermissionDecision:
        # Check context allow rules
        for rule in self._context.allow_rules:
            if rule.matches(tool_name, scope):
                return ToolPermissionDecision.allow(scope=scope)

        # Default decision from context
        if self._context.default_decision == "deny":
            return ToolPermissionDecision.deny(
                f"Tool '{tool_name}' is denied by default policy.",
                scope=scope,
            )
        if self._context.default_decision == "ask":
            return ToolPermissionDecision.ask(
                f"Tool '{tool_name}' requires confirmation by default policy.",
                scope=scope,
            )

        return ToolPermissionDecision.allow(scope=scope)
