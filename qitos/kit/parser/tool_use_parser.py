"""Parser for <tool_use> XML format emitted by many OpenAI-compatible models.

Handles formats like:
    <tool_use>
    <bash_v2>
    <command>ls -la</command>
    </bash_v2>
    </tool_use>

Or with JSON arguments:
    <tool_use>
    <glob_v2>
    {"pattern": "*.py"}
    </glob_v2>
    </tool_use>

Or nested tool arguments as child tags:
    <tool_use>
    <file_read_v2>
    <path>/tmp/test.py</path>
    </file_read_v2>
    </tool_use>

Or GLM-style with separate tool_name/arguments:
    <tool_use>
    <server_name>filesystem</server_name>
    <tool_name>glob_v2</tool_name>
    <arguments>{"pattern": "*.py"}</arguments>
    </tool_use>
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Sequence

from qitos.core.action import Action
from qitos.core.decision import Decision
from qitos.engine.parser import BaseParser, parser_wait_decision
from qitos.kit.parser.parser_utils import norm, parse_xml_root


class ToolUseXmlParser(BaseParser[dict[str, Any]]):
    """Parse <tool_use><tool_name>...</tool_name></tool_use> XML output.

    Many OpenAI-compatible models (GLM, DeepSeek, Qwen, etc.) emit tool calls
    in this format when not using native function calling. This parser extracts
    the tool name and arguments from the XML structure.

    Also handles plain text final answers when no <tool_use> blocks are found.
    """

    contract_id = "tool_use_xml_v1"

    # Tags that wrap tool calls
    TOOL_USE_TAGS = ("tool_use", "tool_call", "tool")

    # Known final answer tag names (raw, NOT norm-processed)
    FINAL_ANSWER_TAGS = ("final_answer", "final", "answer", "result")

    # Tags that are metadata, not tool names (norm-matched, so no underscores)
    _METADATA_TAGS_NORM = frozenset({"servername", "toolname", "arguments"})

    def __init__(
        self,
        *,
        tool_use_tags: Optional[Sequence[str]] = None,
        final_answer_tags: Optional[Sequence[str]] = None,
    ):
        self.tool_use_tags = tuple(
            norm(x) for x in (tool_use_tags or self.TOOL_USE_TAGS)
        )
        # Keep final_answer_tags raw (not norm'd) so regex matches actual XML tags
        self.final_answer_tags = tuple(
            x.strip() for x in (final_answer_tags or self.FINAL_ANSWER_TAGS)
        )

    def parse(
        self, raw_output: Any, context: Optional[Dict[str, Any]] = None
    ) -> Decision[dict[str, Any]]:
        if not isinstance(raw_output, str):
            return parser_wait_decision(
                parser=self,
                code="invalid_output_type",
                summary="Expected string output for ToolUse XML parsing.",
                raw_output=raw_output,
                details="ToolUseXmlParser expects a string response.",
                repair_instruction="Return XML with <tool_use> blocks or a plain text answer.",
                expected_shape="<tool_use><tool_name>args</tool_name></tool_use>",
            )

        text = raw_output.strip()
        if not text:
            return parser_wait_decision(
                parser=self,
                code="empty_output",
                summary="Model output was empty.",
                raw_output=raw_output,
                details="The response did not contain any content.",
                repair_instruction="Provide a response with either tool calls or a final answer.",
                expected_shape="<tool_use><tool_name>args</tool_name></tool_use>",
            )

        # Try to parse <tool_use> blocks
        actions = self._extract_tool_use_actions(text)
        if actions:
            return Decision.act(
                actions=actions,
                rationale=self._extract_thought(text),
            )

        # Try to parse <final_answer> blocks
        final = self._extract_final_answer(text)
        if final:
            return Decision.final(
                answer=final,
                rationale=self._extract_thought(text),
            )

        # If no XML structure found, check context for step info.
        if not self._has_tool_use_tags(text):
            step = self._get_step(context)
            # In early steps, the model should be calling tools, not chatting.
            # Return Decision.wait() to trigger the repair loop.
            # We do NOT use parser_wait_decision() because that attaches
            # error-severity diagnostics which causes engine to fallback
            # to other parsers instead of retrying with the same parser.
            if step < 3:
                return Decision.wait(
                    rationale=(
                        "Please use tools to accomplish the task. "
                        "Format tool calls as: "
                        "<tool_use><tool_name><arg>value</arg></tool_name></tool_use>\n"
                        "For example: <tool_use><bash_v2><command>ls</command></bash_v2></tool_use>\n"
                        "Or: <tool_use><glob_v2><pattern>*.py</pattern></glob_v2></tool_use>"
                    ),
                )
            # Later steps — treat as genuine final answer
            return Decision.final(
                answer=text,
                rationale=None,
            )

        # Has tool_use tags but couldn't parse them
        return parser_wait_decision(
            parser=self,
            code="unparseable_tool_use",
            summary="Found <tool_use> tags but could not parse tool calls.",
            raw_output=raw_output,
            details="The tool call XML structure could not be parsed.",
            repair_instruction="Format tool calls as: <tool_use><tool_name><arg>value</arg></tool_name></tool_use>",
            expected_shape="<tool_use><bash_v2><command>ls</command></bash_v2></tool_use>",
        )

    @staticmethod
    def _get_step(context: Optional[Dict[str, Any]]) -> int:
        """Extract step number from context."""
        if not context or not isinstance(context, dict):
            return 0
        step_obj = context.get("step")
        if step_obj is None:
            return 0
        # If it's an int directly, use it
        if isinstance(step_obj, int):
            return step_obj
        # If it's an object with step_id attr
        step_id = getattr(step_obj, "step_id", None)
        if isinstance(step_id, int):
            return step_id
        return 0

    def _has_tool_use_tags(self, text: str) -> bool:
        """Check if text contains any tool_use-style tags."""
        for tag in self.TOOL_USE_TAGS:
            if f"<{tag}>" in text.lower():
                return True
        return bool(re.search(r"<(?:tool_use|tool_call|tool)>", text, re.IGNORECASE))

    def _extract_tool_use_actions(self, text: str) -> List[Action]:
        """Extract all <tool_use>...</tool_use> blocks and convert to Actions."""
        actions: List[Action] = []

        # Find all <tool_use>...</tool_use> blocks
        for match in re.finditer(
            r"<(?:tool_use|tool_call|tool)>(.*?)</(?:tool_use|tool_call|tool)>",
            text,
            re.DOTALL,
        ):
            block = match.group(1).strip()
            action = self._parse_tool_block(block)
            if action is not None:
                actions.append(action)

        # Also try: bare JSON inside <tool_use> without nested tags
        # e.g. <tool_use>{"name": "glob_v2", "arguments": {"pattern": "*.py"}}</tool_use>
        if not actions:
            for match in re.finditer(
                r"<(?:tool_use|tool_call|tool)>(.*?)</(?:tool_use|tool_call|tool)>",
                text,
                re.DOTALL,
            ):
                block = match.group(1).strip()
                action = self._parse_json_tool_call(block)
                if action is not None:
                    actions.append(action)

        return actions

    def _parse_tool_block(self, block: str) -> Optional[Action]:
        """Parse a single tool_use block content into an Action.

        Handles formats:
        1. <bash_v2><command>ls</command></bash_v2>  (tool_name as XML tag)
        2. <glob_v2>{"pattern": "*.py"}</glob_v2>   (tool_name as XML tag + JSON body)
        3. <server_name>fs</server_name><tool_name>glob_v2</tool_name><arguments>...</arguments>
           (GLM-style with separate tool_name and arguments elements)
        4. <file_read_v2><path>/tmp/test.py</path></file_read_v2> (child tags as args)
        """
        # Try XML parsing first
        try:
            root = parse_xml_root(block)

            # Format 3: GLM-style with <tool_name> and <arguments> as siblings
            tool_name_el = None
            arguments_el = None
            for child in root:
                tag_norm = norm(child.tag)
                if tag_norm == "toolname":
                    tool_name_el = child
                elif tag_norm == "arguments":
                    arguments_el = child
                # server_name is metadata, skip
            if tool_name_el is not None:
                tool_name = "".join(tool_name_el.itertext()).strip()
                if tool_name:
                    args = {}
                    if arguments_el is not None:
                        args_text = "".join(arguments_el.itertext()).strip()
                        if args_text.startswith("{"):
                            try:
                                args = json.loads(args_text)
                            except json.JSONDecodeError:
                                args = self._parse_args_content(args_text, tool_name)
                        else:
                            args = self._parse_args_content(args_text, tool_name)
                    return Action(name=tool_name, args=args)

            # Format 1/2/4: tool_name as the XML tag itself
            # NOTE: Use root.tag directly, NOT norm(), to preserve underscores
            # in tool names like "bash_v2"
            if norm(root.tag) == "root":
                # Look for first non-metadata child as the tool element
                for child in root:
                    child_tag_norm = norm(child.tag)
                    if child_tag_norm in self._METADATA_TAGS_NORM:
                        continue
                    # Use original tag (preserves underscores like bash_v2)
                    tool_name = child.tag
                    args = self._extract_args_from_element(child)
                    if tool_name and args is not None:
                        return Action(name=tool_name, args=args)
                    break
            else:
                # Root tag IS the tool name
                tool_name = root.tag
                args = self._extract_args_from_element(root)
                if args is not None:
                    return Action(name=tool_name, args=args)
        except Exception:
            pass

        # Fallback: regex extraction
        # Match <tool_name>...</tool_name>
        m = re.match(r"<(\w+)>(.*?)</\1>", block, re.DOTALL)
        if m:
            tool_name = m.group(1)
            content = m.group(2).strip()
            args = self._parse_args_content(content, tool_name)
            return Action(name=tool_name, args=args)

        return None

    def _extract_args_from_element(self, element: Any) -> Optional[Dict[str, Any]]:
        """Extract args from an XML element.

        Handles:
        - Child tags as arg names: <command>ls</command> → {"command": "ls"}
        - JSON content: {"command": "ls"} → {"command": "ls"}
        - <arg name="key">value</arg> format
        """
        args: Dict[str, Any] = {}

        # Check for <arg name="..."> format
        arg_elements = element.findall(".//arg")
        if arg_elements:
            for arg_el in arg_elements:
                key = arg_el.attrib.get("name", "").strip()
                if key:
                    args[key] = "".join(arg_el.itertext()).strip()
            if args:
                return args

        # Check child tags as arg names
        # NOTE: Use original child.tag, NOT norm(), to preserve underscores
        children = list(element)
        if children:
            for child in children:
                tag = child.tag  # Preserve original tag (e.g. "pattern", "command")
                tag_norm = norm(tag)
                if tag_norm == "root":
                    continue
                value = "".join(child.itertext()).strip()
                if tag and value:
                    args[tag] = value

            # If no child tags produced args, try the text content
            if not args:
                text_content = "".join(element.itertext()).strip()
                if text_content:
                    return self._parse_args_content(text_content, element.tag)

            return args if args else None

        # No children — try text content as JSON or plain value
        text_content = "".join(element.itertext()).strip()
        if text_content:
            return self._parse_args_content(text_content, element.tag)

        return {}

    def _parse_args_content(self, content: str, tool_name: str) -> Dict[str, Any]:
        """Parse argument content (could be JSON, key-value, or single value)."""
        # Try JSON first
        content = content.strip()
        if content.startswith("{"):
            try:
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass

        # Try to extract nested XML-style args: <key>value</key>
        args: Dict[str, Any] = {}
        for m in re.finditer(r"<(\w+)>(.*?)</\1>", content, re.DOTALL):
            key = m.group(1)
            value = m.group(2).strip()
            args[key] = value

        if args:
            return args

        # Single value — map to the most common arg name for known tools
        if content:
            arg_name = self._infer_arg_name(tool_name)
            return {arg_name: content}

        return {}

    @staticmethod
    def _infer_arg_name(tool_name: str) -> str:
        """Infer the primary argument name for a tool."""
        name = norm(tool_name)
        # Bash tools
        if name in ("bashv2", "bash", "run_command", "bash_v2"):
            return "command"
        # File read tools
        if name in ("filereadv2", "read_file", "read", "view", "file_read_v2"):
            return "path"
        # File write/edit tools
        if name in ("fileeditv2", "write_file", "edit", "write", "str_replace", "file_edit_v2"):
            return "path"
        # Glob/Grep tools
        if name in ("globv2", "glob", "glob_v2"):
            return "pattern"
        if name in ("grepv2", "grep", "grep_v2"):
            return "pattern"
        # Web tools
        if name in ("webfetchv2", "webfetch", "web_fetch_v2"):
            return "url"
        # Default
        return "input"

    def _parse_json_tool_call(self, block: str) -> Optional[Action]:
        """Parse a JSON tool call like {"name": "tool", "arguments": {...}}.

        Handles GLM's format where the JSON is directly inside <tool_use> tags
        without nested XML for the tool name.
        """
        block = block.strip()
        if not block.startswith("{"):
            return None

        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None

        # OpenAI function-calling style: {"name": "...", "arguments": {...}}
        tool_name = parsed.get("name") or parsed.get("tool_name") or parsed.get("function", {}).get("name")
        if not tool_name:
            return None

        args = parsed.get("arguments") or parsed.get("args") or parsed.get("function", {}).get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        if not isinstance(args, dict):
            args = {}

        return Action(name=str(tool_name), args=args)

    def _extract_thought(self, text: str) -> Optional[str]:
        """Extract thinking/rationale text before tool_use blocks."""
        # Find text before the first <tool_use>
        idx = text.find("<tool_use>")
        if idx == -1:
            idx = text.find("<tool_call>")
        if idx == -1:
            idx = text.find("<tool>")
        if idx == -1:
            idx = text.find("<tool_call>")
        if idx > 0:
            thought = text[:idx].strip()
            if thought:
                return thought
        return None

    def _extract_final_answer(self, text: str) -> Optional[str]:
        """Extract final answer from <final_answer> or similar tags."""
        for tag in self.final_answer_tags:
            pattern = rf"<{tag}>(.*?)</{tag}>"
            m = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return None


__all__ = ["ToolUseXmlParser"]
