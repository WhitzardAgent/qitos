from __future__ import annotations

import json
from pathlib import Path

import pytest
from examples._support import SequenceModel
from qitos import HistoryPolicy
from qitos.harness import (
    build_harness_policy,
    build_model_for_preset,
    resolve_family_preset,
)
from qitos.models.profile_registry import infer_default_protocol, infer_model_profile


def _coder_preset_agent():
    return pytest.importorskip(
        "qitos_zoo.qitos_coder.preset_agent",
        reason="qitos-zoo coder package is not installed in the core CI environment",
    )


def test_resolve_family_preset_for_gold_families() -> None:
    assert resolve_family_preset("Qwen/Qwen3-8B").id == "qwen"
    assert resolve_family_preset("qwen-plus").id == "qwen"
    assert resolve_family_preset("qwen-max").id == "qwen"
    assert resolve_family_preset("moonshot-v1-128k").id == "kimi"
    assert resolve_family_preset("MiniMax-M2.5").id == "minimax"
    assert resolve_family_preset("gpt-oss-120b").id == "gpt-oss"
    assert resolve_family_preset("gemma-4-31b-it").id == "gemma-4"


def test_profile_registry_is_derived_from_presets() -> None:
    assert infer_model_profile("moonshot-v1-128k").default_protocol == "json_decision_v1"
    assert infer_model_profile("gpt-oss-120b").default_protocol == "json_decision_v1"
    assert infer_model_profile("gemma-4-31b-it").default_protocol == "json_decision_v1"
    assert infer_default_protocol("MiniMax-M2.5") == "minimax_tool_call_v1"


def test_build_harness_policy_keeps_minimax_native_chain() -> None:
    harness = build_harness_policy(model_name="MiniMax-M2.5")
    assert harness.family_preset.id == "minimax"
    assert harness.protocol.id == "minimax_tool_call_v1"
    assert harness.protocol.fallback_protocols == (
        "terminus_xml_v1",
        "terminus_json_v1",
        "json_decision_v1",
    )
    assert harness.tool_policy.primary_delivery == "api_parameter"
    assert harness.parser_name == "MiniMaxToolCallParser"


def test_build_model_for_preset_attaches_harness_metadata() -> None:
    llm = build_model_for_preset(
        family_id="qwen",
        model_name="Qwen/Qwen3-8B",
        api_key="test-key",
        base_url="https://example.invalid/v1",
    )
    assert llm.context_window == 128_000
    metadata = dict(getattr(llm, "qitos_harness_metadata", {}) or {})
    assert metadata["family_preset"] == "qwen"
    assert metadata["protocol"] == "json_decision_v1"
    assert metadata["adapter_kind"] == "openai-compatible"
    assert metadata["native_tool_call_preferred"] is True
    assert metadata["decision_lane_preference"] == "native_tool_calls"
    assert metadata["effective_tool_delivery"] == "api_parameter"


def test_claude_code_runtime_config_prefers_cli_over_env() -> None:
    preset_agent = _coder_preset_agent()
    config = preset_agent._resolve_runtime_config(
        type(
            "_Args",
            (),
            {
                "model_family": "minimax",
                "model_name": "MiniMax-M2.5",
                "base_url": "https://api.minimax.chat/v1",
                "api_key": "cli-key",
                "protocol": "minimax_tool_call_v1",
            },
        )(),
        env={
            "QITOS_MODEL_FAMILY": "kimi",
            "QITOS_MODEL": "moonshot-v1-128k",
            "OPENAI_BASE_URL": "https://api.moonshot.ai/v1",
            "OPENAI_API_KEY": "env-key",
            "QITOS_PROTOCOL": "react_text_v1",
        },
    )
    assert config["model_family"] == "minimax"
    assert config["model_name"] == "MiniMax-M2.5"
    assert config["base_url"] == "https://api.minimax.chat/v1"
    assert config["api_key"] == "cli-key"
    assert config["protocol"] == "minimax_tool_call_v1"


def test_same_claude_code_agent_switches_across_gold_families(tmp_path: Path) -> None:
    preset_agent = _coder_preset_agent()
    ClaudeCodeAgent = preset_agent.ClaudeCodeAgent
    final_outputs = {
        "qwen": '{"thought":"done","final_answer":"ok"}',
        "kimi": '{"thought":"done","final_answer":"ok"}',
        "gpt-oss": '{"thought":"done","final_answer":"ok"}',
        "gemma-4": '{"thought":"done","final_answer":"ok"}',
        "minimax": (
            "<minimax:response>"
            "<analysis>done</analysis>"
            "<plan>finish</plan>"
            "<task_complete>true</task_complete>"
            "<final_answer>ok</final_answer>"
            "</minimax:response>"
        ),
    }
    for family_id, output in final_outputs.items():
        harness = build_harness_policy(family_id=family_id)
        llm = SequenceModel([output], model=f"{family_id}-model")
        setattr(
            llm,
            "qitos_harness_metadata",
            {
                "family_preset": harness.family_preset.id,
                "adapter_kind": harness.adapter.kind,
                "protocol": harness.protocol.id,
                "parser": harness.parser_name,
                "tool_policy": harness.tool_policy.to_dict(),
                "context_policy": harness.context_policy.to_dict(),
            },
        )
        workspace = tmp_path / family_id
        workspace.mkdir(parents=True, exist_ok=True)
        agent = ClaudeCodeAgent(
            llm=llm,
            workspace_root=str(workspace),
            model_parser=harness.parser,
            model_protocol=harness.protocol,
        )
        result = agent.run(
            task="finish",
            workspace=str(workspace),
            max_steps=2,
            render=False,
            trace=False,
            history_policy=HistoryPolicy(max_messages=8, max_tokens=1200),
            return_state=True,
        )
        assert result.state.stop_reason == "final"


def test_harness_metadata_reaches_trace_manifest(tmp_path: Path) -> None:
    preset_agent = _coder_preset_agent()
    ClaudeCodeAgent = preset_agent.ClaudeCodeAgent
    harness = build_harness_policy(family_id="kimi")
    llm = SequenceModel(['{"thought":"done","final_answer":"ok"}'], model="moonshot-v1-128k")
    setattr(
        llm,
        "qitos_harness_metadata",
        {
            "family_preset": harness.family_preset.id,
            "adapter_kind": harness.adapter.kind,
            "protocol": harness.protocol.id,
            "parser": harness.parser_name,
            "tool_policy": harness.tool_policy.to_dict(),
            "context_policy": harness.context_policy.to_dict(),
        },
    )
    workspace = tmp_path / "kimi"
    workspace.mkdir(parents=True, exist_ok=True)
    agent = ClaudeCodeAgent(
        llm=llm,
        workspace_root=str(workspace),
        model_parser=harness.parser,
        model_protocol=harness.protocol,
    )
    agent.run(
        task="finish",
        workspace=str(workspace),
        max_steps=2,
        render=False,
        trace=True,
        trace_logdir=str(tmp_path / "runs"),
        return_state=False,
    )
    manifests = list((tmp_path / "runs").glob("*/manifest.json"))
    assert manifests
    manifest = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert manifest["model_family"] == "kimi"
    assert manifest["prompt_protocol"] == "json_decision_v1"
    assert manifest["run_spec"]["metadata"]["family_preset"] == "kimi"
    assert manifest["run_spec"]["metadata"]["harness_policy"]["protocol"] == "json_decision_v1"
