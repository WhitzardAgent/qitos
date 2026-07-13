from __future__ import annotations

from qitos.protocols import (
    render_minimax_tool_schema,
    render_react_tool_schema,
    render_xml_tool_schema,
)


class _Registry:
    def list_tools(self):
        return ["GREP"]

    def describe_tool(self, name):
        assert name == "GREP"
        return {
            "name": "GREP",
            "description": "Search source.",
            "prompt": "",
            "input_schema": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Literal text or regular expression.",
                    },
                    "syntax": {
                        "type": "string",
                        "description": "Pattern interpretation.",
                        "enum": ["literal", "regex"],
                        "default": "literal",
                    },
                },
                "required": ["pattern"],
            },
        }


def test_text_protocols_preserve_parameter_contract() -> None:
    registry = _Registry()
    react = render_react_tool_schema(registry)
    xml = render_xml_tool_schema(registry)
    minimax = render_minimax_tool_schema(registry)

    assert "pattern: string (required) — Literal text or regular expression." in react
    assert "one of literal | regex; default='literal'" in react
    assert 'constraints="required"' in xml
    assert "Pattern interpretation." in xml
    assert "one of literal | regex" in minimax
