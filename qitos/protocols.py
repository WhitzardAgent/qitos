"""Model-native interaction protocol selection and rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


ToolSchemaRenderer = Callable[[Any], str]
ContractRenderer = Callable[[Any], str]
FeedbackRenderer = Callable[[str], str]


@dataclass(frozen=True)
class ModelProtocol:
    id: str
    display_name: str
    parser_factory: Callable[[], Any]
    prompt_renderer: Callable[[str, Any], str]
    contract_renderer: ContractRenderer
    tool_schema_renderer: ToolSchemaRenderer
    tool_schema_delivery: str = "prompt_injection"
    repair_renderer: Optional[FeedbackRenderer] = None
    continuation_renderer: Optional[FeedbackRenderer] = None
    prompt_builder_policy: Dict[str, Any] = field(default_factory=dict)
    repair_injection_mode: str = "message_injection"
    continuation_injection_mode: str = "message_injection"
    contract_version: str = "v1"
    supports_multi_action: bool = False
    supports_native_tool_call_markup: bool = False
    diagnostic_style: str = "structured"
    fallback_protocols: tuple[str, ...] = field(default_factory=tuple)


def _tool_specs(tool_registry: Any) -> List[Dict[str, Any]]:
    if tool_registry is None:
        return []
    if hasattr(tool_registry, "list_tools") and hasattr(tool_registry, "describe_tool"):
        out: List[Dict[str, Any]] = []
        for name in tool_registry.list_tools():
            try:
                out.append(tool_registry.describe_tool(name))
            except Exception:
                continue
        return out
    return []


def _param_rows(spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    schema = spec.get("input_schema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    required = set(schema.get("required") or []) if isinstance(schema, dict) else set()
    rows: List[Dict[str, Any]] = []
    if isinstance(props, dict):
        for key, value in props.items():
            if isinstance(value, dict):
                rows.append(
                    {
                        "name": str(key),
                        "type": str(value.get("type") or "any"),
                        "description": str(value.get("description") or ""),
                        "required": key in required,
                        "default": value.get("default"),
                        "has_default": "default" in value,
                        "enum": list(value.get("enum") or []),
                    }
                )
            else:
                rows.append({"name": str(key), "type": "any", "description": "", "required": key in required, "has_default": False, "enum": []})
    return rows


def _param_annotation(row: Dict[str, Any]) -> str:
    notes = ["required" if row.get("required") else "optional"]
    if row.get("enum"):
        notes.append("one of " + " | ".join(str(item) for item in row["enum"]))
    if row.get("has_default"):
        notes.append(f"default={row.get('default')!r}")
    return "; ".join(notes)


def render_react_tool_schema(tool_registry: Any) -> str:
    lines: List[str] = []
    for spec in _tool_specs(tool_registry):
        prompt_text = spec.get("prompt") or ""
        desc = spec.get("description") or ""
        # Use the full prompt if available, otherwise fall back to short description
        display = prompt_text if prompt_text else desc
        lines.append(f"- {spec['name']}: {display}".rstrip())
        params = _param_rows(spec)
        if params:
            lines.append("  Parameters:")
            for row in params:
                detail = f" — {row['description']}" if row.get("description") else ""
                lines.append(f"  - {row['name']}: {row['type']} ({_param_annotation(row)}){detail}")
    return "\n".join(lines).strip()


def render_json_tool_schema(tool_registry: Any) -> str:
    import json

    payload: List[Dict[str, Any]] = []
    for spec in _tool_specs(tool_registry):
        prompt_text = spec.get("prompt") or ""
        desc = spec.get("description") or ""
        # Use the full prompt if available, otherwise fall back to short description
        description = prompt_text if prompt_text else desc
        payload.append(
            {
                "name": spec.get("name"),
                "description": description,
                "parameters": (spec.get("input_schema") or {}).get("properties", {}),
                "required": (spec.get("input_schema") or {}).get("required", []),
            }
        )
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_xml_tool_schema(tool_registry: Any) -> str:
    lines: List[str] = []
    for spec in _tool_specs(tool_registry):
        prompt_text = spec.get("prompt") or ""
        desc = spec.get("description") or ""
        description = prompt_text if prompt_text else desc
        lines.append(f'<tool name="{spec["name"]}">')
        if description:
            lines.append(f"  <description>{description}</description>")
        params = _param_rows(spec)
        if params:
            lines.append("  <parameters>")
            for row in params:
                lines.append(
                    f'    <param name="{row["name"]}" type="{row["type"]}" '
                    f'constraints="{_param_annotation(row)}">{row.get("description") or ""}</param>'
                )
            lines.append("  </parameters>")
        lines.append("</tool>")
    return "\n".join(lines).strip()


def render_minimax_tool_schema(tool_registry: Any) -> str:
    lines: List[str] = []
    for spec in _tool_specs(tool_registry):
        prompt_text = spec.get("prompt") or ""
        desc = spec.get("description") or ""
        description = prompt_text if prompt_text else desc
        lines.append(f'<invoke name="{spec["name"]}">')
        if description:
            lines.append(f"  <description>{description}</description>")
        for row in _param_rows(spec):
            description = row.get("description") or "value"
            lines.append(
                f'  <parameter name="{row["name"]}" type="{row["type"]}" '
                f'constraints="{_param_annotation(row)}">{description}</parameter>'
            )
        lines.append("</invoke>")
    return "\n".join(lines).strip()


def _render_simple_contract(contract: str) -> str:
    return str(contract or "").strip()


def _render_repair_message(feedback: str) -> str:
    text = str(feedback or "").strip()
    if not text:
        return ""
    return (
        "Parser feedback from previous response:\n"
        f"{text}\n\n"
        "Previous response did not satisfy the required output contract.\n"
        "Return one corrected response in the active protocol format."
    )


def _render_continuation_message(feedback: str) -> str:
    text = str(feedback or "").strip()
    if not text:
        return ""
    return (
        "Timeout feedback:\n"
        f"{text}\n\n"
        "Continue from the current run state.\n"
        "Do not repeat unnecessary prior actions."
    )


_REACT_CONTRACT = """Output contract:
- Tool call:
Thought: <brief reasoning>
Action: tool_name(arg=value, ...)

- Final answer:
Final Answer: <answer>"""

_JSON_CONTRACT = """Output contract:
- Single tool call:
{"thought":"...","action":{"name":"tool_name","args":{"key":"value"}}}

- Multiple independent tool calls (use when actions have no data dependencies):
{"thought":"...","actions":[{"name":"tool_name","args":{"key":"value"}},{"name":"tool_name","args":{"key":"value"}}]}

- Final answer:
{"thought":"...","final_answer":"..."}"""

_JSON_MULTI_CONTRACT = """Output contract:
- Single tool call:
{"thought":"...","action":{"name":"tool_name","args":{"key":"value"}}}

- Multiple independent tool calls (PREFERRED when actions have no dependencies):
{"thought":"read two source files in parallel","actions":[{"name":"tool1","args":{"key":"value"}},{"name":"tool2","args":{"key":"value"}}]}

- Final answer:
{"thought":"...","final_answer":"..."}

Rules for multiple tool calls:
- Use the "actions" array when you need multiple independent operations in one step.
- Only combine actions with NO data dependencies on each other.
- Read-only tools (file reads, searches, inspections) can always be combined.
- NEVER combine write actions (file writes, shell commands) with reads or with each other in the same step.
- Limit each batch to at most 4 actions.
- When you have only one tool to call, use the "action" object (not an array)."""

_XML_CONTRACT = """Output contract:
- Tool call:
<decision mode="act"><think>...</think><action name="tool_name"><arg name="key">value</arg></action></decision>

- Final answer:
<decision mode="final"><think>...</think><final_answer>...</final_answer></decision>"""

_TERMINUS_JSON_CONTRACT = """Output contract:
{
  "analysis": "...",
  "plan": "...",
  "commands": [{"keystrokes":"...", "duration":0.1}],
  "tools": [{"name":"tool_name","args":{}}],
  "task_complete": false
}"""

_TERMINUS_XML_CONTRACT = """Output contract:
<response>
  <analysis>...</analysis>
  <plan>...</plan>
  <commands><keystrokes duration="0.1">...</keystrokes></commands>
  <tools><tool name="tool_name"><arg name="key">value</arg></tool></tools>
  <task_complete>false</task_complete>
</response>"""

_MINIMAX_CONTRACT = """Output contract:
- Tool call:
<minimax:tool_call>
  <invoke name="tool_name">
    <parameter name="key">value</parameter>
  </invoke>
</minimax:tool_call>

- Completion:
<minimax:response>
  <analysis>...</analysis>
  <plan>...</plan>
  <task_complete>true</task_complete>
</minimax:response>

Rules:
- Do not emit markdown fences.
- Use one or more <invoke> blocks when calling tools.
- Use <task_complete>true</task_complete> only when the task is done."""

_DESKTOP_JSON_CONTRACT = """Output contract:
- Ground one next desktop action from the current screenshot/accessibility state.
- Return valid JSON only.

Action mode:
{"thought":"visible UI state and grounding","plan":"why this next action is appropriate","action":{"name":"click","args":{"x":640,"y":420}}}

Wait mode:
{"thought":"why the UI may still be changing","plan":"wait for the next observation","action":{"name":"wait","args":{"duration":1.0}}}

Failure mode:
{"thought":"why the task is blocked","plan":"stop and report the blocker","action":{"name":"fail","args":{"reason":"..."}}}

Final mode:
{"thought":"why the objective is complete","final_answer":"what was completed"}"""

_DESKTOP_XML_CONTRACT = """Output contract:
- Ground one next desktop action from the current screenshot/accessibility state.
- Return XML only.

Action mode:
<decision mode="act">
  <think>visible UI state and grounding</think>
  <plan>why this next action is appropriate</plan>
  <action name="click">
    <arg name="x">640</arg>
    <arg name="y">420</arg>
  </action>
</decision>

Wait mode:
<decision mode="act">
  <think>why the UI may still be changing</think>
  <plan>wait for the next observation</plan>
  <action name="wait">
    <arg name="duration">1.0</arg>
  </action>
</decision>

Final mode:
<decision mode="final">
  <think>why the objective is complete</think>
  <final_answer>what was completed</final_answer>
</decision>"""


def _compose_prompt(
    base_prompt: str, tool_registry: Any, *, contract: str, renderer: ToolSchemaRenderer
) -> str:
    base = str(base_prompt or "").strip()
    parts: List[str] = [base] if base else []
    schema = renderer(tool_registry)
    if schema:
        parts.append(f"Available tools:\n{schema}")
    parts.append(contract.strip())
    return "\n\n".join(part for part in parts if part)


_TOOL_USE_XML_CONTRACT = """Output contract — you MUST follow one of these two formats exactly:

Format 1 — Tool call (use when you need to run a tool):
<tool_use>
<tool_name>the_tool_name_here</tool_name>
<arguments>{"arg1": "value1", "arg2": "value2"}</arguments>
</tool_use>

Format 2 — Final answer (use when the task is complete and no more tools are needed):
<final_answer>Your final answer text here</final_answer>

Rules:
- Always wrap tool calls inside <tool_use>...</tool_use> tags.
- Always put the tool name inside <tool_name>...</tool_name> tags.
- Always put the arguments as a JSON object inside <arguments>...</arguments> tags.
- Do NOT put arguments directly after the tool name. Always use proper <tool_name> and <arguments> tags.
- You may include a brief thought before the <tool_use> block.
- Examples:

<tool_use>
<tool_name>bash_v2</tool_name>
<arguments>{"command": "ls -la"}</arguments>
</tool_use>

<tool_use>
<tool_name>glob_v2</tool_name>
<arguments>{"pattern": "**/*.py"}</arguments>
</tool_use>

<tool_use>
<tool_name>file_read_v2</tool_name>
<arguments>{"path": "/tmp/test.py"}</arguments>
</tool_use>"""


_KIMI_CONTRACT = """Output contract — you MUST follow one of these two formats exactly:

Format 1 — Tool call (use when you need to run a tool):
<|tool_calls_section_begin|>
<|tool_call_begin|> functions.tool_name:0 <|tool_call_argument_begin|> {"arg1": "value1", "arg2": "value2"} <|tool_call_end|>
<|tool_calls_section_end|>

Format 2 — Final answer (use when the task is complete and no more tools are needed):
Respond with plain text. Do not include any <|tool_call|> markers.

Rules:
- Always wrap tool calls inside <|tool_calls_section_begin|> and <|tool_calls_section_end|> markers.
- Each tool call starts with <|tool_call_begin|> followed by functions.tool_name:N where N is the call index.
- Arguments must be a valid JSON object inside <|tool_call_argument_begin|> and before <|tool_call_end|>.
- You may call multiple tools in a single response by adding more <|tool_call_begin|> blocks.
- Examples:

<|tool_calls_section_begin|>
<|tool_call_begin|> functions.Bash:0 <|tool_call_argument_begin|> {"command": "ls -la"} <|tool_call_end|>
<|tool_call_begin|> functions.Glob:1 <|tool_call_argument_begin|> {"pattern": "**/*.py"} <|tool_call_end|>
<|tool_calls_section_end|>

<|tool_calls_section_begin|>
<|tool_call_begin|> functions.Read:0 <|tool_call_argument_begin|> {"file_path": "/tmp/test.py"} <|tool_call_end|>
<|tool_calls_section_end|>"""


def _react_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_REACT_CONTRACT,
        renderer=render_react_tool_schema,
    )


def _tool_use_xml_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_TOOL_USE_XML_CONTRACT,
        renderer=render_react_tool_schema,
    )


def _kimi_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_KIMI_CONTRACT,
        renderer=render_react_tool_schema,
    )


def _json_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_JSON_CONTRACT,
        renderer=render_json_tool_schema,
    )


def _json_multi_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_JSON_MULTI_CONTRACT,
        renderer=render_json_tool_schema,
    )


def _xml_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_XML_CONTRACT,
        renderer=render_xml_tool_schema,
    )


def _terminus_json_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_TERMINUS_JSON_CONTRACT,
        renderer=render_json_tool_schema,
    )


def _terminus_xml_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_TERMINUS_XML_CONTRACT,
        renderer=render_xml_tool_schema,
    )


def _minimax_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_MINIMAX_CONTRACT,
        renderer=render_minimax_tool_schema,
    )


def _desktop_json_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_DESKTOP_JSON_CONTRACT,
        renderer=render_json_tool_schema,
    )


def _desktop_xml_prompt(base_prompt: str, tool_registry: Any) -> str:
    return _compose_prompt(
        base_prompt,
        tool_registry,
        contract=_DESKTOP_XML_CONTRACT,
        renderer=render_xml_tool_schema,
    )


def _protocol_table() -> Dict[str, ModelProtocol]:
    from qitos.kit.parser import (
        JsonDecisionParser,
        KimiToolCallParser,
        ReActTextParser,
        TerminusJsonParser,
        TerminusXmlParser,
        XmlDecisionParser,
    )
    from qitos.kit.parser.minimax_tool_call_parser import MiniMaxToolCallParser
    from qitos.kit.parser.tool_use_parser import ToolUseXmlParser

    return {
        "react_text_v1": ModelProtocol(
            id="react_text_v1",
            display_name="ReAct Text",
            parser_factory=ReActTextParser,
            prompt_renderer=_react_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(_REACT_CONTRACT),
            tool_schema_renderer=render_react_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
        ),
        "json_decision_v1": ModelProtocol(
            id="json_decision_v1",
            display_name="JSON Decision",
            parser_factory=JsonDecisionParser,
            prompt_renderer=_json_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(_JSON_CONTRACT),
            tool_schema_renderer=render_json_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            tool_schema_delivery="hybrid",
            supports_native_tool_call_markup=True,
            supports_multi_action=True,
        ),
        "json_decision_multi_v1": ModelProtocol(
            id="json_decision_multi_v1",
            display_name="JSON Decision (Multi-Action)",
            parser_factory=JsonDecisionParser,
            prompt_renderer=_json_multi_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(_JSON_MULTI_CONTRACT),
            tool_schema_renderer=render_json_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            tool_schema_delivery="hybrid",
            supports_native_tool_call_markup=True,
            supports_multi_action=True,
            fallback_protocols=("json_decision_v1", "xml_decision_v1", "react_text_v1"),
        ),
        "xml_decision_v1": ModelProtocol(
            id="xml_decision_v1",
            display_name="XML Decision",
            parser_factory=XmlDecisionParser,
            prompt_renderer=_xml_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(_XML_CONTRACT),
            tool_schema_renderer=render_xml_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
        ),
        "terminus_json_v1": ModelProtocol(
            id="terminus_json_v1",
            display_name="Terminus JSON",
            parser_factory=TerminusJsonParser,
            prompt_renderer=_terminus_json_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(
                _TERMINUS_JSON_CONTRACT
            ),
            tool_schema_renderer=render_json_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            supports_multi_action=True,
            fallback_protocols=("terminus_xml_v1", "json_decision_v1"),
        ),
        "terminus_xml_v1": ModelProtocol(
            id="terminus_xml_v1",
            display_name="Terminus XML",
            parser_factory=TerminusXmlParser,
            prompt_renderer=_terminus_xml_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(
                _TERMINUS_XML_CONTRACT
            ),
            tool_schema_renderer=render_xml_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            supports_multi_action=True,
            fallback_protocols=("terminus_json_v1", "xml_decision_v1"),
        ),
        "minimax_tool_call_v1": ModelProtocol(
            id="minimax_tool_call_v1",
            display_name="MiniMax Tool Call",
            parser_factory=MiniMaxToolCallParser,
            prompt_renderer=_minimax_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(
                _MINIMAX_CONTRACT
            ),
            tool_schema_renderer=render_minimax_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            supports_multi_action=True,
            supports_native_tool_call_markup=True,
            fallback_protocols=(
                "terminus_xml_v1",
                "terminus_json_v1",
                "json_decision_v1",
            ),
        ),
        "desktop_actions_json_v1": ModelProtocol(
            id="desktop_actions_json_v1",
            display_name="Desktop Actions JSON",
            parser_factory=JsonDecisionParser,
            prompt_renderer=_desktop_json_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(
                _DESKTOP_JSON_CONTRACT
            ),
            tool_schema_renderer=render_json_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            supports_multi_action=False,
            fallback_protocols=("desktop_actions_xml_v1", "json_decision_v1"),
        ),
        "desktop_actions_xml_v1": ModelProtocol(
            id="desktop_actions_xml_v1",
            display_name="Desktop Actions XML",
            parser_factory=XmlDecisionParser,
            prompt_renderer=_desktop_xml_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(
                _DESKTOP_XML_CONTRACT
            ),
            tool_schema_renderer=render_xml_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            supports_multi_action=False,
            fallback_protocols=("desktop_actions_json_v1", "xml_decision_v1"),
        ),
        "kimi_tool_call_v1": ModelProtocol(
            id="kimi_tool_call_v1",
            display_name="Kimi Tool Call",
            parser_factory=KimiToolCallParser,
            prompt_renderer=_kimi_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(_KIMI_CONTRACT),
            tool_schema_renderer=render_react_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            supports_multi_action=True,
            supports_native_tool_call_markup=True,
            fallback_protocols=("json_decision_v1", "react_text_v1"),
        ),
        "tool_use_xml_v1": ModelProtocol(
            id="tool_use_xml_v1",
            display_name="Tool Use XML",
            parser_factory=ToolUseXmlParser,
            prompt_renderer=_tool_use_xml_prompt,
            contract_renderer=lambda _protocol: _render_simple_contract(
                _TOOL_USE_XML_CONTRACT
            ),
            tool_schema_renderer=render_react_tool_schema,
            repair_renderer=_render_repair_message,
            continuation_renderer=_render_continuation_message,
            supports_multi_action=True,
            fallback_protocols=("json_decision_v1", "react_text_v1"),
        ),
    }


def list_protocols() -> List[str]:
    return sorted(_protocol_table().keys())


def get_protocol(protocol: str | ModelProtocol | None) -> Optional[ModelProtocol]:
    if protocol is None:
        return None
    if isinstance(protocol, ModelProtocol):
        return protocol
    return _protocol_table().get(str(protocol).strip())


def require_protocol(protocol: str | ModelProtocol) -> ModelProtocol:
    resolved = get_protocol(protocol)
    if resolved is None:
        raise ValueError(f"Unknown model protocol: {protocol}")
    return resolved


def infer_protocol_from_parser(parser: Any) -> Optional[ModelProtocol]:
    contract = str(getattr(parser, "contract_id", "") or "").strip()
    if not contract:
        return None
    table = _protocol_table()
    if contract in table:
        return table[contract]
    for item in table.values():
        if str(getattr(item.parser_factory(), "contract_id", "")) == contract:
            return item
    return None


def parser_from_protocol(protocol_id: str) -> Optional[Any]:
    """Create a parser instance from a protocol ID.

    Uses the canonical protocol table as the single source of truth
    for protocol-to-parser mapping.

    :param protocol_id: Protocol identifier (e.g. "json_decision_v1").
    :returns: Parser instance, or None if protocol not found.
    """
    proto = get_protocol(protocol_id)
    if proto is None:
        return None
    try:
        return proto.parser_factory()
    except Exception:
        return None


def resolve_protocol_chain(primary: str | ModelProtocol | None) -> List[ModelProtocol]:
    start = get_protocol(primary)
    if start is None:
        return []
    out: List[ModelProtocol] = []
    seen: set[str] = set()

    def visit(item: ModelProtocol) -> None:
        if item.id in seen:
            return
        seen.add(item.id)
        out.append(item)
        for fallback in item.fallback_protocols:
            resolved = get_protocol(fallback)
            if resolved is not None:
                visit(resolved)

    visit(start)
    return out


def render_protocol_prompt(
    base_prompt: str, protocol: str | ModelProtocol | None, tool_registry: Any
) -> str:
    resolved = get_protocol(protocol)
    if resolved is None:
        return str(base_prompt or "")
    return resolved.prompt_renderer(str(base_prompt or ""), tool_registry)


def render_protocol_tool_schema(
    tool_registry: Any, protocol: str | ModelProtocol | None
) -> str:
    resolved = get_protocol(protocol)
    if resolved is None:
        return render_react_tool_schema(tool_registry)
    return resolved.tool_schema_renderer(tool_registry)


def render_protocol_contract(protocol: str | ModelProtocol | None) -> str:
    resolved = get_protocol(protocol)
    if resolved is None:
        return ""
    return str(resolved.contract_renderer(resolved) or "").strip()


def protocol_summary(protocol: str | ModelProtocol | None) -> Dict[str, Any]:
    resolved = get_protocol(protocol)
    if resolved is None:
        return {}
    return {
        "id": resolved.id,
        "display_name": resolved.display_name,
        "tool_schema_delivery": resolved.tool_schema_delivery,
        "repair_injection_mode": resolved.repair_injection_mode,
        "continuation_injection_mode": resolved.continuation_injection_mode,
        "contract_version": resolved.contract_version,
        "supports_multi_action": resolved.supports_multi_action,
        "supports_native_tool_call_markup": resolved.supports_native_tool_call_markup,
        "fallback_protocols": list(resolved.fallback_protocols),
    }


__all__ = [
    "ModelProtocol",
    "get_protocol",
    "require_protocol",
    "list_protocols",
    "resolve_protocol_chain",
    "render_protocol_prompt",
    "render_protocol_tool_schema",
    "render_protocol_contract",
    "render_react_tool_schema",
    "render_json_tool_schema",
    "render_xml_tool_schema",
    "render_minimax_tool_schema",
    "infer_protocol_from_parser",
    "protocol_summary",
]
