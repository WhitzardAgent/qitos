from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qitos import AgentModule, Decision, StateSchema, ToolRegistry
from qitos.core.tool import tool
from qitos.engine import Engine
from qitos.kit import MiniMaxToolCallParser
from qitos.kit.parser import ReActTextParser
from qitos.models.profile_registry import infer_default_protocol, infer_model_profile
from qitos.protocols import ModelProtocol, get_protocol


def test_model_profile_registry_infers_minimax_protocol() -> None:
    profile = infer_model_profile("MiniMax-M2.5")
    assert profile is not None
    assert profile.default_protocol == "minimax_tool_call_v1"
    assert infer_default_protocol("unknown-model") == "react_text_v1"


def test_tool_registry_renders_minimax_schema() -> None:
    registry = ToolRegistry()

    @tool(name="send_terminal_keys")
    def send_terminal_keys(keystrokes: str, duration_sec: float = 0.1) -> dict[str, Any]:
        """
        Send keystrokes to the terminal.

        :param keystrokes: Raw terminal input.
        :param duration_sec: Time to wait after sending input.
        """

        return {"ok": True}

    registry.register(send_terminal_keys)
    rendered = registry.get_tool_descriptions(protocol="minimax_tool_call_v1")
    assert "<invoke name=\"send_terminal_keys\">" in rendered
    assert "<parameter name=\"keystrokes\"" in rendered


def test_minimax_parser_handles_wrapped_tool_call() -> None:
    parser = MiniMaxToolCallParser()
    decision = parser.parse(
        """I will call the tool now.
<minimax:tool_call>
  <invoke name="send_terminal_keys">
    <parameter name="keystrokes">pwd\n</parameter>
    <parameter name="duration_sec">0.5</parameter>
  </invoke>
</minimax:tool_call>
Done."""
    )
    assert decision.mode == "act"
    assert decision.actions[0]["name"] == "send_terminal_keys"
    assert decision.meta["parser_diagnostics"]["salvage_applied"] is True
    assert "I will call the tool now." in decision.meta["analysis"]


def test_minimax_parser_salvages_reasoning_and_completion_markup() -> None:
    parser = MiniMaxToolCallParser()
    decision = parser.parse(
        """Analysis: We found a likely command execution path and should finish with the confirmed result.
Plan: Mark the audit as complete with the confirmed report.
<minimax:response>
  <task_complete>true</task_complete>
  <final_answer>Report written to security_report.md</final_answer>
</minimax:response>
"""
    )
    assert decision.mode == "final"
    assert decision.final_answer == "Report written to security_report.md"
    assert "command execution path" in decision.meta["analysis"]
    assert "Mark the audit as complete" in decision.meta["plan"]


@dataclass
class _ProtocolState(StateSchema):
    last_rationale: str = ""


class _DummyModel:
    def __init__(self, model: str, output: str):
        self.model = model
        self.output = output
        self.calls: list[list[dict[str, str]]] = []

    def __call__(self, messages):
        self.calls.append(list(messages))
        return self.output


class _ProtocolAgent(AgentModule[_ProtocolState, dict[str, Any], dict[str, Any]]):
    name = "protocol_demo"

    def __init__(self, llm: Any):
        super().__init__(tool_registry=ToolRegistry(), llm=llm)

    def init_state(self, task: str, **kwargs: Any) -> _ProtocolState:
        return _ProtocolState(task=task, max_steps=int(kwargs.get("max_steps", 3)))

    def build_system_prompt(self, state: _ProtocolState) -> str | None:
        _ = state
        return self.compose_system_prompt("Return one completion or tool action.", protocol=self.active_protocol())

    def reduce(
        self,
        state: _ProtocolState,
        observation: dict[str, Any],
        decision: Decision[dict[str, Any]],
    ) -> _ProtocolState:
        _ = observation
        if decision.meta.get("task_complete_requested"):
            state.final_result = decision.rationale or "done"
            state.stop_reason = "success"
        state.last_rationale = decision.rationale or ""
        return state


def test_engine_uses_protocol_fallback_chain() -> None:
    llm = _DummyModel(
        model="MiniMax-M2.5",
        output='{"analysis":"done","plan":"finish","commands":[],"task_complete":true}',
    )
    result = Engine(agent=_ProtocolAgent(llm=llm)).run("finish the task")
    assert result.state.stop_reason == "success"
    assert result.records[0].protocol_id == "terminus_json_v1"
    assert result.records[0].parser_selected == "TerminusJsonParser"
    assert result.records[0].parser_fallback_used is True
    assert any(
        item.get("protocol") == "minimax_tool_call_v1" and item.get("result") == "error"
        for item in result.records[0].parser_attempts
    )
    assert any(
        item.get("protocol") == "terminus_json_v1" and item.get("result") == "success"
        for item in result.records[0].parser_attempts
    )


def test_get_protocol_returns_builtin_protocol() -> None:
    protocol = get_protocol("minimax_tool_call_v1")
    assert protocol is not None
    assert protocol.id == "minimax_tool_call_v1"
    assert protocol.supports_native_tool_call_markup is True


def test_get_protocol_returns_desktop_builtin_protocols() -> None:
    json_protocol = get_protocol("desktop_actions_json_v1")
    xml_protocol = get_protocol("desktop_actions_xml_v1")
    assert json_protocol is not None
    assert xml_protocol is not None
    assert json_protocol.id == "desktop_actions_json_v1"
    assert xml_protocol.id == "desktop_actions_xml_v1"


def test_default_prompt_builder_supplies_contract_and_tool_schema() -> None:
    registry = ToolRegistry()

    @tool(name="lookup")
    def lookup(query: str) -> dict[str, Any]:
        """
        Look up a string.

        :param query: Query text.
        """

        return {"ok": True}

    registry.register(lookup)

    class _CaptureModel:
        model = "gpt-4o-mini"

        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        def __call__(self, messages):
            self.calls.append(list(messages))
            return '{"thought":"done","final_answer":"ok"}'

    class _DefaultPromptAgent(AgentModule[_ProtocolState, dict[str, Any], dict[str, Any]]):
        name = "default_prompt_agent"

        def __init__(self, llm: Any) -> None:
            super().__init__(tool_registry=registry, llm=llm)

        def init_state(self, task: str, **kwargs: Any) -> _ProtocolState:
            return _ProtocolState(task=task, max_steps=int(kwargs.get("max_steps", 2)))

        def base_persona_prompt(self, state: _ProtocolState) -> str:
            _ = state
            return "You are a careful assistant."

        def reduce(
            self,
            state: _ProtocolState,
            observation: dict[str, Any],
            decision: Decision[dict[str, Any]],
        ) -> _ProtocolState:
            _ = observation
            state.final_result = decision.final_answer or "ok"
            state.stop_reason = "success"
            return state

    llm = _CaptureModel()
    result = Engine(agent=_DefaultPromptAgent(llm=llm)).run("help")
    assert result.state.final_result == "ok"
    system_message = llm.calls[0][0]["content"]
    assert "You are a careful assistant." in system_message
    assert "Available tools:" in system_message
    assert '"final_answer"' in system_message


def test_api_parameter_tool_schema_delivery_reaches_supported_model() -> None:
    registry = ToolRegistry()

    @tool(name="lookup")
    def lookup(query: str) -> dict[str, Any]:
        """
        Look up a string.

        :param query: Query text.
        """

        return {"ok": True}

    registry.register(lookup)

    custom_protocol = ModelProtocol(
        id="api_tool_protocol_v1",
        display_name="API Tool Protocol",
        parser_factory=ReActTextParser,
        prompt_renderer=lambda base_prompt, _tools: str(base_prompt or ""),
        contract_renderer=lambda _protocol: "Output contract:\nFinal Answer: <answer>",
        tool_schema_renderer=lambda _registry: "",
        tool_schema_delivery="api_parameter",
        repair_renderer=lambda text: text,
        continuation_renderer=lambda text: text,
    )

    class _ApiModel:
        model = "custom-api-model"

        def __init__(self) -> None:
            self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

        def supports_tool_schema_delivery(self, delivery: str, protocol: Any = None) -> bool:
            _ = protocol
            return delivery == "api_parameter"

        def build_tool_schema_request_options(
            self,
            tool_schema_payload: list[dict[str, Any]] | None,
            *,
            protocol: Any = None,
            delivery: str = "prompt_injection",
        ) -> dict[str, Any]:
            _ = protocol
            return {"tools": list(tool_schema_payload or []), "tool_choice": "auto", "delivery": delivery}

        def __call__(self, messages, **kwargs):
            self.calls.append((list(messages), dict(kwargs)))
            return "Final Answer: ok"

    class _ApiAgent(AgentModule[_ProtocolState, dict[str, Any], dict[str, Any]]):
        name = "api_protocol_agent"

        def __init__(self, llm: Any) -> None:
            super().__init__(tool_registry=registry, llm=llm, model_protocol=custom_protocol)

        def init_state(self, task: str, **kwargs: Any) -> _ProtocolState:
            return _ProtocolState(task=task, max_steps=int(kwargs.get("max_steps", 2)))

        def reduce(
            self,
            state: _ProtocolState,
            observation: dict[str, Any],
            decision: Decision[dict[str, Any]],
        ) -> _ProtocolState:
            _ = observation
            state.final_result = decision.final_answer or "ok"
            state.stop_reason = "success"
            return state

    llm = _ApiModel()
    result = Engine(agent=_ApiAgent(llm=llm)).run("help")
    assert result.state.final_result == "ok"
    _messages, kwargs = llm.calls[0]
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["delivery"] == "api_parameter"
    assert kwargs["tools"][0]["function"]["name"] == "lookup"


def test_desktop_json_protocol_prompt_and_parser_roundtrip() -> None:
    protocol = get_protocol("desktop_actions_json_v1")
    assert protocol is not None
    rendered = protocol.contract_renderer(protocol)
    assert "wait" in rendered.lower()
    parser = protocol.parser_factory()
    decision = parser.parse(
        '{"thought":"The Continue button is centered and visible.","plan":"Click the CTA.","action":{"name":"click","args":{"x":640,"y":420}}}'
    )
    assert decision.mode == "act"
    assert decision.actions[0]["name"] == "click"


def test_desktop_xml_protocol_parser_roundtrip() -> None:
    protocol = get_protocol("desktop_actions_xml_v1")
    assert protocol is not None
    parser = protocol.parser_factory()
    decision = parser.parse(
        "<decision mode=\"act\"><think>The button is clearly visible.</think><plan>Click the CTA.</plan><action name=\"click\"><arg name=\"x\">640</arg><arg name=\"y\">420</arg></action></decision>"
    )
    assert decision.mode == "act"
    assert decision.actions[0]["name"] == "click"


def test_manual_build_system_prompt_keeps_api_parameter_tool_schema() -> None:
    registry = ToolRegistry()

    @tool(name="lookup")
    def lookup(query: str) -> dict[str, Any]:
        """
        Look up a string.

        :param query: Query text.
        """

        return {"ok": True}

    registry.register(lookup)

    custom_protocol = ModelProtocol(
        id="manual_api_tool_protocol_v1",
        display_name="Manual API Tool Protocol",
        parser_factory=ReActTextParser,
        prompt_renderer=lambda base_prompt, _tools: str(base_prompt or ""),
        contract_renderer=lambda _protocol: "Output contract:\nFinal Answer: <answer>",
        tool_schema_renderer=lambda _registry: "",
        tool_schema_delivery="api_parameter",
        repair_renderer=lambda text: text,
        continuation_renderer=lambda text: text,
    )

    class _ApiModel:
        model = "custom-api-model"

        def __init__(self) -> None:
            self.calls: list[tuple[list[dict[str, str]], dict[str, Any]]] = []

        def supports_tool_schema_delivery(
            self, delivery: str, protocol: Any = None
        ) -> bool:
            _ = protocol
            return delivery == "api_parameter"

        def build_tool_schema_request_options(
            self,
            tool_schema_payload: list[dict[str, Any]] | None,
            *,
            protocol: Any = None,
            delivery: str = "prompt_injection",
        ) -> dict[str, Any]:
            _ = protocol
            return {
                "tools": list(tool_schema_payload or []),
                "tool_choice": "auto",
                "delivery": delivery,
            }

        def __call__(self, messages, **kwargs):
            self.calls.append((list(messages), dict(kwargs)))
            return "Final Answer: ok"

    class _ManualPromptAgent(AgentModule[_ProtocolState, dict[str, Any], dict[str, Any]]):
        name = "manual_prompt_agent"

        def __init__(self, llm: Any) -> None:
            super().__init__(
                tool_registry=registry, llm=llm, model_protocol=custom_protocol
            )

        def init_state(self, task: str, **kwargs: Any) -> _ProtocolState:
            return _ProtocolState(task=task, max_steps=int(kwargs.get("max_steps", 2)))

        def build_system_prompt(self, state: _ProtocolState) -> str | None:
            _ = state
            return self.compose_system_prompt("Use the handwritten system prompt.")

        def reduce(
            self,
            state: _ProtocolState,
            observation: dict[str, Any],
            decision: Decision[dict[str, Any]],
        ) -> _ProtocolState:
            _ = observation
            state.final_result = decision.final_answer or "ok"
            state.stop_reason = "success"
            return state

    llm = _ApiModel()
    result = Engine(agent=_ManualPromptAgent(llm=llm)).run("help")
    assert result.state.final_result == "ok"
    messages, kwargs = llm.calls[0]
    assert messages[0]["content"].startswith("Use the handwritten system prompt.")
    assert kwargs["tool_choice"] == "auto"
    assert kwargs["delivery"] == "api_parameter"
    assert kwargs["tools"][0]["function"]["name"] == "lookup"
