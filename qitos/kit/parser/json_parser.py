"""JSON decision parser with configurable key mapping."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence

from qitos.core.decision import Decision
from qitos.engine.parser import BaseParser, attach_parser_warning, parser_wait_decision
from qitos.kit.parser.parser_utils import (
    extract_json_actions,
    first_dict_value,
    json_payload_details,
    norm,
)


class JsonDecisionParser(BaseParser[dict[str, Any]]):
    contract_id = "json_decision_v1"

    # Known barrier/result tool names that produce structured output.
    # When a raw JSON payload contains these as keys, we try to salvage
    # the output as a tool call instead of returning an error.
    _SALVAGE_TOOL_NAMES = frozenset({
        "subtask_list", "subtask_patch", "hack_result", "code_result",
        "maintenance_result", "search_result", "memorist_result",
        "enricher_result", "report_result", "done", "generate_subtasks",
    })

    # Mapping from likely payload key names to the tool that should be called.
    # E.g., when the model outputs {"subtasks": [...]}, the key "subtasks"
    # maps to the "subtask_list" tool.
    _SALVAGE_KEY_TO_TOOL = {
        "subtasks": "subtask_list",
        "subtask_list": "subtask_list",
        "delta_operations": "subtask_patch",
        "subtask_patch": "subtask_patch",
        "hack_result": "hack_result",
        "code_result": "code_result",
        "maintenance_result": "maintenance_result",
        "search_result": "search_result",
        "memorist_result": "memorist_result",
        "enricher_result": "enricher_result",
        "report_result": "report_result",
        "report": "report_result",
        "done": "done",
        "generate_subtasks": "subtask_list",
        "generate_report": "report_result",
    }

    def __init__(
        self,
        *,
        thought_keys: Optional[Sequence[str]] = None,
        reflection_keys: Optional[Sequence[str]] = None,
        action_keys: Optional[Sequence[str]] = None,
        final_keys: Optional[Sequence[str]] = None,
    ):
        self.thought_keys = tuple(
            norm(x)
            for x in (thought_keys or ("thought", "thinking", "think", "rationale"))
        )
        self.reflection_keys = tuple(
            norm(x)
            for x in (reflection_keys or ("reflection", "reflect", "selfreflection"))
        )
        self.action_keys = tuple(
            norm(x) for x in (action_keys or ("action", "tool", "call"))
        )
        self.final_keys = tuple(
            norm(x) for x in (final_keys or ("finalanswer", "final", "answer"))
        )

    def parse(
        self, raw_output: Any, context: Optional[Dict[str, Any]] = None
    ) -> Decision[dict[str, Any]]:
        try:
            payload, warnings, extraction_mode = json_payload_details(raw_output)
        except Exception:
            # Try multiple repair strategies in order
            repaired = None
            repair_method = None

            # Strategy 1: Fix unescaped quotes inside JSON string values
            # Models often output: "subtasks":"[{"id":"1",...}]" instead of "subtasks":"[{\"id\":\"1\",...}]"
            fixed_nested = self._try_fix_nested_json_strings(raw_output)
            if fixed_nested is not None:
                repaired = fixed_nested
                repair_method = "nested_json_strings"

            # Strategy 2: Fix truncated JSON (unclosed brackets from max_tokens limit)
            if repaired is None:
                fixed_truncated = self._try_fix_truncated_json(raw_output)
                if fixed_truncated is not None:
                    repaired = fixed_truncated
                    repair_method = "truncated_json"

            # Strategy 3: Try both fixes in sequence (nested then truncated)
            if repaired is None and fixed_nested is not None:
                fixed_both = self._try_fix_truncated_json(fixed_nested)
                if fixed_both is not None:
                    repaired = fixed_both
                    repair_method = "nested_then_truncated"

            if repaired is not None:
                try:
                    payload, warnings, extraction_mode = json_payload_details(repaired)
                    warnings = warnings or []
                    warnings.insert(
                        0,
                        f"AUTO-CORRECTED: repaired JSON output ({repair_method}).",
                    )
                except Exception as fix_exc:
                    return parser_wait_decision(
                        parser=self,
                        code="invalid_json",
                        summary="Could not parse a valid JSON decision object.",
                        raw_output=raw_output,
                        details=str(fix_exc),
                        repair_instruction="Return valid JSON only, with either an action, actions, final_answer, or mode='wait'.",
                        expected_shape='{"thought":"...","action":{"name":"tool_name","args":{...}}} or {"thought":"...","final_answer":"..."}',
                    )
            else:
                return parser_wait_decision(
                    parser=self,
                    code="invalid_json",
                    summary="Could not parse a valid JSON decision object.",
                    raw_output=raw_output,
                    details="Could not parse any extracted JSON-like object.",
                    repair_instruction="Return valid JSON only, with either an action, actions, final_answer, or mode='wait'.",
                    expected_shape='{"thought":"...","action":{"name":"tool_name","args":{...}}} or {"thought":"...","final_answer":"..."}',
                )
        thought = first_dict_value(payload, self.thought_keys)
        reflection = first_dict_value(payload, self.reflection_keys)
        mode = norm(str(payload.get("mode", "")))
        meta = {"reflection": reflection} if reflection else {}
        if warnings:
            meta = attach_parser_warning(
                meta,
                parser=self,
                code="salvaged_json_payload",
                summary="Parser warnings were recorded while reading JSON decision output.",
                raw_output=raw_output,
                details="; ".join(warnings),
                expected_shape='{"thought":"...","action":{"name":"tool_name","args":{...}}} or {"thought":"...","final_answer":"..."}',
                extraction_mode=extraction_mode,
                salvage_applied=extraction_mode not in {"", "direct"},
                salvage_summary="; ".join(warnings),
            )
        final_answer = (
            first_dict_value(payload, self.final_keys)
            or first_dict_value(payload, ("final_answer",))
            or first_dict_value(payload, ("answer",))
        )

        if mode == "wait":
            return Decision.wait(rationale=thought, meta=meta)
        if mode == "final":
            if not final_answer:
                return parser_wait_decision(
                    parser=self,
                    code="missing_required_field",
                    summary="JSON final mode is missing final_answer.",
                    raw_output=raw_output,
                    details="The payload set mode='final' but did not provide final_answer or answer.",
                    repair_instruction="When mode is 'final', include `final_answer` as a string.",
                    expected_shape='{"mode":"final","thought":"...","final_answer":"..."}',
                    issue_path="final_answer",
                    rationale=thought or None,
                    extra_meta=meta,
                )
            return Decision.final(answer=final_answer, rationale=thought, meta=meta)

        actions = extract_json_actions(payload)
        if actions:
            return Decision.act(actions=actions, rationale=thought, meta=meta)
        if final_answer:
            return Decision.final(answer=final_answer, rationale=thought, meta=meta)

        # Salvage: if the raw JSON payload contains a key matching a known
        # barrier/result tool name, treat it as a tool call. Models sometimes
        # output {"subtasks": [...]} instead of {"action": {"name": "subtask_list", "args": {"subtasks": ...}}}.
        salvaged = self._try_salvage_tool_call(payload, thought, meta, raw_output)
        if salvaged is not None:
            return salvaged

        return parser_wait_decision(
            parser=self,
            code="missing_action_or_final",
            summary="JSON output did not contain an action or final answer.",
            raw_output=raw_output,
            details="The parser did not find parseable action fields, actions, or a final answer.",
            repair_instruction='Return JSON with either an `action`, an `actions` array, a `final_answer`, or `mode: "wait"`.',
            expected_shape='{"thought":"...","action":{"name":"tool_name","args":{...}}} or {"mode":"final","final_answer":"..."}',
            rationale=thought or None,
            extra_meta=meta,
        )

    def _try_salvage_tool_call(
        self,
        payload: Dict[str, Any],
        thought: Optional[str],
        meta: Dict[str, Any],
        raw_output: Any,
    ) -> Optional[Decision[dict[str, Any]]]:
        """Try to salvage a raw JSON payload as a tool call.

        When models output something like {"subtasks": [...]} instead of
        {"action": {"name": "subtask_list", "args": {"subtasks": "..."}}},
        we check if any key in the payload matches a known tool or tool-arg
        name and treat the payload as that tool's arguments.
        """
        if not isinstance(payload, dict) or not payload:
            return None

        # Check if any key in the payload maps to a known tool name
        matched_key = None
        matched_tool = None
        for key in payload:
            key_norm = norm(str(key))
            # Direct tool name match (e.g., "subtask_list")
            if key_norm in {norm(t) for t in self._SALVAGE_TOOL_NAMES}:
                matched_key = str(key)
                matched_tool = str(key)
                break
            # Key-to-tool mapping (e.g., "subtasks" -> "subtask_list")
            for map_key, map_tool in self._SALVAGE_KEY_TO_TOOL.items():
                if norm(str(key)) == norm(map_key):
                    matched_key = str(key)
                    matched_tool = map_tool
                    break
            if matched_tool:
                break

        if matched_tool is None:
            return None

        # Build args from the payload — map the matched key's value to the
        # appropriate arg name for the tool
        tool_value = payload[matched_key]

        # Serialize complex values to JSON strings (barrier tools expect string args)
        if isinstance(tool_value, (list, dict)):
            serialized = json.dumps(tool_value, ensure_ascii=False)
        else:
            serialized = str(tool_value)

        args = {matched_key: serialized}

        # Include any other payload keys as additional args (skip thought/mode keys)
        skip_keys = {norm(t) for t in ("thought", "thinking", "think", "rationale", "reflection", "mode")}
        for k, v in payload.items():
            if k != matched_key and norm(str(k)) not in skip_keys:
                if isinstance(v, (list, dict)):
                    args[k] = json.dumps(v, ensure_ascii=False)
                else:
                    args[k] = str(v)

        salvage_meta = dict(meta)
        salvage_meta["salvage_applied"] = True
        salvage_meta["salvage_method"] = "raw_key_to_tool_call"
        salvage_meta["salvaged_tool_name"] = matched_tool
        salvage_meta["salvaged_key"] = matched_key

        return Decision.act(
            actions=[{"name": matched_tool, "args": args}],
            rationale=thought,
            meta=salvage_meta,
        )

    def _try_fix_nested_json_strings(self, raw_output: Any) -> Optional[str]:
        """Try to fix unescaped quotes inside JSON string values.

        Common LLM issue: models output
          "subtasks":"[{"id":"1","title":"..."}]"
        instead of
          "subtasks":"[{\"id\":\"1\",\"title\":\"...\"}]"

        This method finds string values that look like they contain
        nested JSON (start with '[' or '{' after a colon+quote) and
        properly escapes the internal quotes.
        """
        if not isinstance(raw_output, str):
            return None
        text = raw_output.strip()
        if not text:
            return None

        # Quick check: does the text even look like JSON with the problem?
        # Pattern: ":[{ or ": {
        import re
        if not re.search(r'"\s*:\s*"\s*[\[{]', text):
            return None

        # Strategy: find all positions where a string value starts with [ or {
        # (indicating nested JSON content), then escape internal quotes within those regions.
        # We track the nesting depth of [] and {} to know which quotes are "internal"
        # (inside nested structures) vs "closing" the string value.

        # Find regions that need fixing: [start, end) where start is the position
        # right after the opening " and end is the position of the closing ".
        regions: List[tuple[int, int]] = []
        for m in re.finditer(r'"\s*:\s*"([\[{])', text):
            # m.start(1) is the position of the [ or { character
            # The opening quote is at m.start(1) - 1
            # We need to find the matching closing quote
            open_quote_pos = m.start(1) - 1  # position of the " before [ or {
            pos = m.start(1)  # position of [ or {
            depth_brace = 0
            depth_bracket = 0

            if text[pos] == '{':
                depth_brace = 1
            elif text[pos] == '[':
                depth_bracket = 1

            pos += 1
            while pos < len(text):
                ch = text[pos]
                if ch == '\\' and pos + 1 < len(text):
                    pos += 2
                    continue
                if ch == '{':
                    depth_brace += 1
                elif ch == '}':
                    depth_brace -= 1
                elif ch == '[':
                    depth_bracket += 1
                elif ch == ']':
                    depth_bracket -= 1
                elif ch == '"':
                    if depth_brace <= 0 and depth_bracket <= 0:
                        # This is the closing quote for the string value
                        regions.append((open_quote_pos + 1, pos))
                        break
                pos += 1

        if not regions:
            return None

        # Now escape all " characters within the identified regions
        # that are not already escaped
        chars = list(text)
        changed = False
        # Process from end to start to avoid offset issues
        for start, end in reversed(regions):
            for pos in range(start, end):
                if chars[pos] == '"' and (pos == 0 or chars[pos - 1] != '\\'):
                    chars[pos] = '\\"'
                    changed = True

        if not changed:
            return None

        fixed = ''.join(chars)
        # Verify the fix produces valid JSON
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            return None

    def _try_fix_truncated_json(self, raw_output: Any) -> Optional[str]:
        """Try to fix truncated JSON by closing open brackets.

        LLM output often gets truncated due to max_tokens limits,
        leaving unclosed brackets. This method counts open vs closed
        brackets and appends the necessary closing characters.
        """
        if not isinstance(raw_output, str):
            return None
        text = raw_output.strip()
        if not text:
            return None

        # Quick check: does it look like a truncated JSON object?
        if not text.startswith('{'):
            return None

        # Count bracket balance
        depth_brace = 0
        depth_bracket = 0
        in_string = False
        escape_next = False
        for ch in text:
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth_brace += 1
            elif ch == '}':
                depth_brace -= 1
            elif ch == '[':
                depth_bracket += 1
            elif ch == ']':
                depth_bracket -= 1

        # If brackets are balanced, no truncation
        if depth_brace == 0 and depth_bracket == 0:
            return None

        # If we're in a string, close it first
        suffix = ""
        if in_string:
            suffix += '"'

        # Close open brackets (innermost first)
        # We need to figure out the correct order — just close brackets
        # and strings in a reasonable way
        suffix += ']' * max(0, depth_bracket)
        suffix += '}' * max(0, depth_brace)

        fixed = text + suffix
        try:
            json.loads(fixed)
            return fixed
        except json.JSONDecodeError:
            # More aggressive fix: try to find the last complete value
            # and truncate there, then close brackets
            # Find the last , or : that's not inside a string
            last_comma = -1
            last_colon = -1
            in_string = False
            escape_next = False
            for i, ch in enumerate(text):
                if escape_next:
                    escape_next = False
                    continue
                if ch == '\\':
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if ch == ',':
                    last_comma = i
                elif ch == ':':
                    last_colon = i

            # Try truncating at the last comma and closing brackets
            if last_comma > 0:
                truncated = text[:last_comma]
                # Recount balance
                depth_brace = 0
                depth_bracket = 0
                in_string = False
                escape_next = False
                for ch in truncated:
                    if escape_next:
                        escape_next = False
                        continue
                    if ch == '\\':
                        escape_next = True
                        continue
                    if ch == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == '{':
                        depth_brace += 1
                    elif ch == '}':
                        depth_brace -= 1
                    elif ch == '[':
                        depth_bracket += 1
                    elif ch == ']':
                        depth_bracket -= 1

                suffix2 = ""
                if in_string:
                    suffix2 += '"'
                suffix2 += ']' * max(0, depth_bracket)
                suffix2 += '}' * max(0, depth_brace)

                fixed2 = truncated + suffix2
                try:
                    json.loads(fixed2)
                    return fixed2
                except json.JSONDecodeError:
                    pass

            return None


__all__ = ["JsonDecisionParser"]
