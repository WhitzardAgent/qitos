"""ToolCallFixerRecovery — repairs malformed tool call arguments.

Replicates pentagi's ToolCallFixer: when a tool call fails due to
malformed arguments, this recovery handler attempts to fix them.

Two strategies:
1. LLM-based fixing: Uses the toolcall_fixer prompt templates to generate
   corrected JSON via a separate LLM call (primary strategy).
2. Rule-based fixing: Falls back to regex-based JSON repair when no LLM
   is available or LLM fixing fails.

In pentagi, the ToolCallFixer makes a separate LLM call with the broken
args + error message + JSON schema, and returns a single line of corrected
JSON. The tool is then retried with the fixed args.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from qitos.engine.recovery import RecoveryDecision, RecoveryPolicy
from qitos.core.errors import ErrorCategory

from ..prompts.toolcall_fixer_prompt import TOOLCALL_FIXER_SYSTEM_PROMPT, TOOLCALL_FIXER_USER_PROMPT


class ToolCallFixerRecovery(RecoveryPolicy):
    """Recovery policy that attempts to fix malformed tool call arguments.

    On parse errors, tries:
    1. LLM-based fixing (if LLM provided) — primary strategy
    2. JSON repair (unbalanced quotes, missing brackets)
    3. Regex-based tool call extraction
    4. Parameter name correction (if tool_registry available)

    Falls back to standard RecoveryPolicy for non-parse errors.

    Parameters
    ----------
    llm : Any | None
        Optional LLM instance for generating fixed arguments.
        If None, uses rule-based fallback.
    max_recoveries_per_run : int
        Maximum recovery attempts per run.
    tool_registry : Any | None
        Optional tool registry for schema lookups.
    """

    def __init__(
        self,
        llm: Any = None,
        max_recoveries_per_run: int = 9,
        tool_registry: Optional[Any] = None,
    ):
        super().__init__(max_recoveries_per_run=max_recoveries_per_run)
        self._llm = llm
        self._tool_registry = tool_registry

    def handle(
        self, state: Any, phase: str, step_id: int, exc: Exception
    ) -> RecoveryDecision:
        from qitos.core.errors import classify_exception
        info = classify_exception(exc, phase, step_id)

        # For parse errors, try to fix and provide guidance
        if info.category == ErrorCategory.PARSE or "parse" in str(info.category).lower():
            error_msg = str(exc)

            # Try LLM-based fixing first
            llm_fixed = self._try_llm_fix(error_msg, state)
            if llm_fixed:
                self._recoveries += 1
                self.tracker.add(info, "LLM fix applied", decision="retry_with_llm_fix")
                return RecoveryDecision(
                    handled=True,
                    continue_run=True,
                    note="tool_call_llm_fix",
                    instruction_patch=(
                        "Your tool call arguments were automatically repaired. "
                        f"Original error: {error_msg[:200]}\n"
                        "Please continue with the corrected format."
                    ),
                    state_patch={"_tool_call_fixed_args": llm_fixed},
                )

            # Fall back to rule-based fixing
            fix_hint = self._try_generate_fix_hint(error_msg)
            self._recoveries += 1
            self.tracker.add(info, fix_hint, decision="retry_with_fix")

            # Try to fix the JSON directly
            fixed_args = self._try_fix_from_error(error_msg, state)
            if fixed_args:
                return RecoveryDecision(
                    handled=True,
                    continue_run=True,
                    note="tool_call_auto_fix",
                    instruction_patch=(
                        "Your tool call had a formatting error that was automatically repaired.\n"
                        f"Error: {error_msg[:200]}\n"
                        "Please continue with the corrected format."
                    ),
                    state_patch={"_tool_call_fixed_args": fixed_args},
                )

            return RecoveryDecision(
                handled=True,
                continue_run=True,
                note=f"tool_call_fix_attempt:{fix_hint[:80]}",
                instruction_patch=(
                    "Your last tool call had a formatting error. Please fix it:\n"
                    f"Error: {error_msg[:200]}\n"
                    f"Hint: {fix_hint}\n\n"
                    "Common fixes:\n"
                    "- Ensure JSON arguments are properly formatted\n"
                    "- Use double quotes for strings in JSON\n"
                    "- Check for missing or extra commas\n"
                    "- Verify parameter names match the tool schema"
                ),
            )

        # Delegate to standard recovery for other error types
        return super().handle(state, phase, step_id, exc)

    def _try_llm_fix(self, error_msg: str, state: Any) -> Optional[Dict[str, Any]]:
        """Try to fix tool call args via LLM call."""
        if self._llm is None:
            return None

        try:
            # Extract broken args and tool name from error/state
            broken_args = ""
            tool_name = ""
            tool_schema = "{}"

            if hasattr(state, '_last_tool_call_args'):
                broken_args = str(state._last_tool_call_args)
            if hasattr(state, '_last_tool_call_name'):
                tool_name = str(state._last_tool_call_name)
            if self._tool_registry and tool_name:
                try:
                    schema = self._tool_registry.get_tool_schema(tool_name)
                    tool_schema = json.dumps(schema, indent=2)
                except Exception:
                    pass

            if not broken_args:
                return None

            system_prompt = TOOLCALL_FIXER_SYSTEM_PROMPT
            user_prompt = TOOLCALL_FIXER_USER_PROMPT.format(
                tool_call_name=tool_name,
                tool_call_args=broken_args,
                tool_call_error=error_msg[:500],
                tool_call_schema=tool_schema,
            )

            # Call LLM
            response_text = ""
            if hasattr(self._llm, 'invoke'):
                response_text = str(self._llm.invoke(
                    system_prompt + "\n\n" + user_prompt
                ))
            elif hasattr(self._llm, 'predict'):
                response_text = str(self._llm.predict(
                    user_prompt, system_prompt=system_prompt,
                ))
            elif callable(self._llm):
                response_text = str(self._llm(user_prompt))

            # Parse the fixed JSON from the LLM response
            if response_text:
                # The LLM should return a single line of JSON
                response_text = response_text.strip()
                # Try to extract JSON if there's surrounding text
                json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                if json_match:
                    response_text = json_match.group()
                return json.loads(response_text)

        except Exception:
            pass

        return None

    def _try_fix_from_error(self, error_msg: str, state: Any) -> Optional[Dict[str, Any]]:
        """Try to fix JSON from the error state."""
        if hasattr(state, '_last_tool_call_args'):
            raw = str(state._last_tool_call_args)
            return self.try_fix_json(raw)
        return None

    def _try_generate_fix_hint(self, error_msg: str) -> str:
        """Generate a fix hint based on the error message."""
        msg_lower = error_msg.lower()

        if "unterminated string" in msg_lower or "eof" in msg_lower:
            return "String literal not properly closed. Check for missing closing quotes."

        if "expecting" in msg_lower and "delimiter" in msg_lower:
            return "Missing comma or bracket in JSON. Check argument structure."

        if "unexpected" in msg_lower and ("}" in error_msg or "]" in error_msg):
            return "Extra closing bracket. Remove trailing commas or extra braces."

        if "key" in msg_lower and "not found" in msg_lower:
            return "Unknown parameter name. Check the tool's parameter schema."

        if "required" in msg_lower:
            return "Missing required parameter. Check the tool's required fields."

        if "json" in msg_lower:
            return "Invalid JSON format. Ensure proper quoting and structure."

        return "Check tool call syntax and parameter format."

    @staticmethod
    def try_fix_json(raw: str) -> Optional[Dict[str, Any]]:
        """Attempt to fix and parse malformed JSON."""
        # Try direct parse first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Try fixing common issues
        fixed = raw

        # Remove trailing commas before } or ]
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)

        # Add missing closing brackets
        open_braces = fixed.count('{') - fixed.count('}')
        open_brackets = fixed.count('[') - fixed.count(']')
        fixed += '}' * max(0, open_braces)
        fixed += ']' * max(0, open_brackets)

        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from surrounding text
        json_match = re.search(r'\{.*\}', fixed, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        return None
