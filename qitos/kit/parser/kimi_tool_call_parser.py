"""Parser for Kimi (Moonshot) native tool-call markup.

Kimi models output tool calls in this format::

    <|tool_calls_section_begin|>
    <|tool_call_begin|> functions.tool_name:0 <|tool_call_argument_begin|> {"arg": "value"} <|tool_call_end|>
    <|tool_call_begin|> functions.tool_name:1 <|tool_call_argument_begin|> {"arg": "value"} <|tool_call_end|>
    <|tool_calls_section_end|>

Final answers use::

    <|tool_call_begin|> functions:0 <|tool_call_argument_begin|> {"content": "final answer text"} <|tool_call_end|>

Or plain text without any tool call markers.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from qitos.core.decision import Decision
from qitos.engine.parser import BaseParser, attach_parser_warning, parser_wait_decision


class KimiToolCallParser(BaseParser[dict[str, Any]]):
    contract_id = "kimi_tool_call_v1"

    # Regex to extract individual tool calls
    _TOOL_CALL_RE = re.compile(
        r"<\|tool_call_begin\|\>\s*"
        r"functions?\.?(\w*):(\d+)\s*"
        r"<\|tool_call_argument_begin\|\>\s*"
        r"(\{.*?\})\s*"
        r"<\|tool_call_end\|\>",
        re.DOTALL,
    )

    def parse(self, raw_output: Any, context: Optional[Dict[str, Any]] = None) -> Decision[dict[str, Any]]:
        text = str(raw_output or "")

        # Try to extract tool calls
        actions, warnings = self._extract_tool_calls(text)

        if actions:
            # Salvage reasoning from text before/after tool calls
            before = self._text_before_calls(text)
            meta: Dict[str, Any] = {
                "analysis": before.strip(),
                "plan": "",
                "output_format": "kimi_tool_call",
            }
            if warnings:
                meta = attach_parser_warning(
                    meta,
                    parser=self,
                    code="salvaged_kimi_payload",
                    summary="Parser warnings recorded while reading Kimi tool-call output.",
                    raw_output=text,
                    details="; ".join(warnings),
                    expected_shape=self._expected_shape(),
                    extraction_mode="regex",
                    salvage_applied=True,
                    salvage_summary="; ".join(warnings),
                )
            return Decision.act(actions=actions, rationale=before.strip(), meta=meta)

        # No tool calls found — check if this is a final answer
        cleaned = self._strip_kimi_markers(text).strip()
        if cleaned:
            return Decision.final(answer=cleaned, meta={"output_format": "kimi_plain_text"})

        # Empty or unparseable output
        return parser_wait_decision(
            parser=self,
            code="invalid_kimi_format",
            summary="No valid Kimi tool-call or text output found.",
            raw_output=text,
            details="Expected <|tool_call_begin|> markers or plain text.",
            repair_instruction=(
                "Return tool calls using: <|tool_call_begin|> functions.tool_name:0 "
                "<|tool_call_argument_begin|> {\"arg\": \"value\"} <|tool_call_end|> "
                "Or respond with plain text for a final answer."
            ),
            expected_shape=self._expected_shape(),
            extraction_mode="regex",
        )

    def _extract_tool_calls(self, text: str) -> tuple[List[Dict[str, Any]], List[str]]:
        """Extract tool calls from Kimi's native markup."""
        actions: List[Dict[str, Any]] = []
        warnings: List[str] = []

        # Remove the section markers
        clean = text.replace("<|tool_calls_section_begin|>", "").replace("<|tool_calls_section_end|>", "")

        for match in self._TOOL_CALL_RE.finditer(clean):
            tool_name = match.group(1).strip()
            tool_id = match.group(2).strip()
            args_str = match.group(3).strip()

            # Handle final answer format: functions:0 with {"content": "..."}
            if not tool_name or tool_name.lower() in ("functions", ""):
                try:
                    parsed = json.loads(args_str)
                    content = parsed.get("content", parsed.get("text", ""))
                    if content:
                        # This is actually a final answer, not a tool call
                        return [], []  # Will be handled as plain text
                except json.JSONDecodeError:
                    pass
                continue

            try:
                args = json.loads(args_str)
                if not isinstance(args, dict):
                    args = {"raw": args}
            except json.JSONDecodeError:
                # Try to fix common JSON issues
                try:
                    # Fix unquoted values
                    fixed = args_str.replace("'", '"')
                    args = json.loads(fixed)
                except json.JSONDecodeError:
                    warnings.append(f"Could not parse JSON args for {tool_name}: {args_str[:80]}")
                    args = {"_raw_args": args_str}

            actions.append({
                "name": tool_name,
                "args": args,
            })

        return actions, warnings

    def _text_before_calls(self, text: str) -> str:
        """Extract any text before the first tool call marker."""
        idx = text.find("<|tool_call_begin|>")
        if idx > 0:
            return text[:idx].strip()
        idx = text.find("<|tool_calls_section_begin|>")
        if idx > 0:
            return text[:idx].strip()
        return ""

    def _strip_kimi_markers(self, text: str) -> str:
        """Remove all Kimi-specific markers from text."""
        # Remove all tool call sections
        result = re.sub(r"<\|tool_calls_section_begin\|>.*?<\|tool_calls_section_end\|>", "", text, flags=re.DOTALL)
        # Remove individual markers
        for marker in (
            "<|tool_calls_section_begin|>", "<|tool_calls_section_end|>",
            "<|tool_call_begin|>", "<|tool_call_end|>",
            "<|tool_call_argument_begin|>", "<|tool_call_argument_end|>",
        ):
            result = result.replace(marker, "")
        return result.strip()

    def _expected_shape(self) -> str:
        return (
            '<|tool_call_begin|> functions.tool_name:0 '
            '<|tool_call_argument_begin|> {"arg": "value"} <|tool_call_end|>'
        )


__all__ = ["KimiToolCallParser"]
