"""Private model/runtime helpers for Engine."""

from __future__ import annotations

import html
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Generic, List, Optional, TypeVar, cast

from ..core.action import Action

_logger = logging.getLogger("qitos.engine._model_runtime")
from ..core.decision import Decision
from ..core.errors import ErrorCategory, ParseExecutionError, RuntimeErrorInfo
from ..core.model_response import ModelResponse
from ..core.multimodal import (
    content_to_text,
    image_base64_block,
    image_file_block,
    image_url_block,
    normalize_content_block,
    normalize_observation_pack,
    observation_modalities,
    observation_visual_assets,
    text_block,
)
from ..core.observation import Observation
from ..protocols import get_protocol, resolve_protocol_chain
from ..core.state import StateSchema
from ._context_runtime import ContextOverflowError
from ._protocol import _EngineProtocol
from .streaming import StreamHandler, to_stream_handler
from .parser import (
    build_parser_diagnostics,
    normalize_parser_diagnostics,
    parser_contract,
    parser_name,
)
from .states import RuntimePhase, StepRecord


StateT = TypeVar("StateT", bound=StateSchema)
ObservationT = TypeVar("ObservationT")
ActionT = TypeVar("ActionT")


class _ModelRuntime(Generic[StateT, ObservationT, ActionT]):
    def __init__(self, engine: _EngineProtocol):
        self.engine = engine
        self.stream_callback: Optional[Any] = None  # Callable[[str], None] or StreamHandler

    def run_decide(
        self, state: StateT, observation: ObservationT, record: StepRecord
    ) -> Decision[ActionT]:
        engine = self.engine
        engine._dispatch_hook(
            "on_before_decide",
            engine._hook_context(
                step_id=record.step_id,
                phase=RuntimePhase.DECIDE,
                state=state,
                observation=observation,
                record=record,
            ),
        )
        engine._emit(
            record.step_id,
            RuntimePhase.DECIDE,
            payload={"stage": "state_ready", "observation": observation},
        )
        engine._memory_append("state", state.to_dict(), record.step_id)
        engine._emit(record.step_id, RuntimePhase.DECIDE, payload={"stage": "start"})
        raw_decision = engine.agent.decide(state, observation)
        model_response: ModelResponse | None = None
        if raw_decision is None:
            model_response = self._run_llm_decide(
                state=state, observation=observation, record=record
            )
            interpreted = self._interpret_model_response(
                state=state,
                observation=observation,
                response=model_response,
                record=record,
            )
            raw_decision = interpreted if interpreted is not None else model_response

        decision = self.normalize_decision(
            raw_decision, step=record.step_id, record=record
        )
        if decision.mode == "branch":
            decision = self.select_branch(state, observation, decision)

        if decision.mode not in {"act", "final", "wait", "handoff"}:
            raise ValueError(f"Invalid decision mode: {decision.mode}")

        decision.validate()
        record.decision = decision
        record.actions = list(decision.actions)
        engine._memory_append("decision", decision, record.step_id)
        engine._emit(
            record.step_id,
            RuntimePhase.DECIDE,
            payload={
                "stage": "decision_ready",
                "mode": decision.mode,
                "rationale": decision.rationale,
                "actions": decision.actions,
                "final_answer": decision.final_answer,
                "candidate_count": len(decision.candidates),
            },
        )
        engine._dispatch_hook(
            "on_after_decide",
            engine._hook_context(
                step_id=record.step_id,
                phase=RuntimePhase.DECIDE,
                state=state,
                observation=observation,
                decision=decision,
                model_response=(
                    dict(record.model_response) if record.model_response else None
                ),
                record=record,
                payload=(
                    {"model_response": dict(record.model_response)}
                    if record.model_response
                    else {}
                ),
            ),
        )
        return cast(Decision[ActionT], decision)

    def _run_llm_decide(
        self, state: StateT, observation: ObservationT, record: StepRecord
    ) -> ModelResponse:
        engine = self.engine
        if engine.agent.llm is None:
            raise ValueError("No llm configured and Agent.decide returned None")
        protocol = engine.resolve_protocol()
        setattr(engine.agent, "_runtime_observation", observation)
        setattr(engine.agent, "_runtime_step_id", record.step_id)
        setattr(engine.agent, "_runtime_protocol", protocol)
        setattr(engine.agent, "_runtime_protocol_source", engine._resolved_protocol_source)
        try:
            prompt_bundle = engine.agent.build_prompt_bundle(state)
            system_prompt = prompt_bundle.system_prompt
            prepared = engine.agent.prepare(state)
        finally:
            if hasattr(engine.agent, "_runtime_observation"):
                delattr(engine.agent, "_runtime_observation")
            if hasattr(engine.agent, "_runtime_step_id"):
                delattr(engine.agent, "_runtime_step_id")
            if hasattr(engine.agent, "_runtime_protocol"):
                delattr(engine.agent, "_runtime_protocol")
            if hasattr(engine.agent, "_runtime_protocol_source"):
                delattr(engine.agent, "_runtime_protocol_source")
        prompt_metadata = dict(getattr(prompt_bundle, "metadata", {}) or {})
        engine._last_prompt_metadata = dict(prompt_metadata)
        if engine.trace_writer is not None:
            engine.trace_writer.metadata.update(
                {
                    "prompt_hash": prompt_metadata.get("prompt_hash_full", "unknown"),
                    "prompt_hash_static": prompt_metadata.get(
                        "prompt_hash_static", "unknown"
                    ),
                    "prompt_builder": prompt_metadata.get("prompt_builder"),
                    "protocol": prompt_metadata.get("protocol"),
                }
            )
        prompt_messages = list(getattr(prompt_bundle, "message_injections", []) or [])
        prompt_user_content_blocks = list(
            getattr(prompt_bundle, "user_content_blocks", []) or []
        )
        context_runtime = engine._context_runtime
        # Apply critic patches if present
        effective_system_prompt = system_prompt if isinstance(system_prompt, str) else ""
        if getattr(engine, "_critic_modified_prompt", None) is not None:
            effective_system_prompt = engine._critic_modified_prompt
            engine._critic_modified_prompt = None  # Consume once
        if getattr(engine, "_critic_instruction_patch", None) is not None:
            patch = engine._critic_instruction_patch
            engine._critic_instruction_patch = None  # Consume once
            effective_system_prompt = effective_system_prompt + "\n\n" + patch
        pre_context = context_runtime.build_pre_request(
            llm=engine.agent.llm,
            system_prompt=effective_system_prompt,
            prepared=str(prepared),
        )
        messages: List[Dict[str, Any]] = []
        if effective_system_prompt.strip():
            system = effective_system_prompt.strip()
            messages.append({"role": "system", "content": system})
            if system != engine._last_system_prompt:
                engine._history_append(
                    "system", system, record.step_id, metadata={"source": "engine"}
                )
                engine._last_system_prompt = system
        history: List[Dict[str, Any]] = []
        query = engine.history_policy.build_query(
            step_id=record.step_id,
            phase=RuntimePhase.DECIDE.value,
            query_kind="decide",
        )
        if isinstance(query, dict):
            query.setdefault("pending_content", str(prepared))
            query.setdefault(
                "model_name", getattr(getattr(engine.agent, "llm", None), "model", None)
            )
            query.setdefault("step_id", record.step_id)
            query.setdefault(
                "warning_ratio", float(engine.context_config.warning_ratio)
            )
            history_budget = context_runtime.history_budget(pre_context)
            if history_budget is not None:
                current_max = query.get("max_tokens")
                if current_max is None:
                    query["max_tokens"] = history_budget
                else:
                    try:
                        query["max_tokens"] = min(int(current_max), int(history_budget))
                    except Exception:
                        query["max_tokens"] = history_budget
        try:
            history_impl = engine._history()
            retrieved = history_impl.retrieve(
                state=state, observation=observation, query=query
            )
            history = engine._normalize_history_messages(retrieved)
            compact_events = []
            consume_runtime_events = getattr(
                history_impl, "consume_runtime_events", None
            )
            if callable(consume_runtime_events):
                compact_events = list(consume_runtime_events() or [])
            history_metadata = []
            get_last_message_metadata = getattr(
                history_impl, "get_last_message_metadata", None
            )
            if callable(get_last_message_metadata):
                history_metadata = list(get_last_message_metadata() or [])
        except Exception:
            history = []
            history_metadata = []
            compact_events = []
        pre_context = context_runtime.finalize_input(
            llm=engine.agent.llm,
            telemetry=pre_context,
            history_messages=history,
            compact_events=compact_events,
        )
        normalized_compact_events = context_runtime.normalize_history_events(
            compact_events, pre_context
        )
        if not normalized_compact_events:
            warning_event = context_runtime.maybe_note_warning(pre_context)
            if warning_event is not None:
                normalized_compact_events = [warning_event]
        for compact_event in normalized_compact_events:
            engine._emit(record.step_id, RuntimePhase.DECIDE, payload=compact_event)
        if context_runtime.should_overflow(pre_context):
            engine._emit(
                record.step_id,
                RuntimePhase.DECIDE,
                payload=context_runtime.overflow_event(pre_context),
            )
            raise ContextOverflowError(
                f"context overflow: input_tokens={pre_context.input_tokens_total} budget={pre_context.available_input_budget}"
            )
        injection_prefixes: List[str] = []
        if self._native_tool_call_preferred():
            if os.environ.get("CYBERGYM_DISABLE_HISTORY_TRIM", "").strip().lower() not in {
                "1",
                "true",
                "yes",
                "on",
            }:
                configured_rounds = int(
                    getattr(engine.context_config, "conversation_max_rounds", 10)
                )
                if configured_rounds > 0:
                    history = self._trim_native_tool_history(
                        history,
                        max_rounds=configured_rounds,
                    )
        messages.extend(history)
        for item in prompt_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            if role == "user":
                injection_prefixes.append(content)
                continue
            messages.append({"role": role, "content": content})
        current_user_content = "\n\n".join(injection_prefixes + [str(prepared)])
        current_user = self._build_current_user_message(
            prepared_text=current_user_content,
            prompt_user_content_blocks=prompt_user_content_blocks,
            observation=observation,
            record=record,
        )
        messages.append(current_user)
        prepared_full = content_to_text(current_user.get("content"))
        self._write_assembled_messages_sidecar(state, record.step_id, messages)
        record.prompt_metadata = dict(prompt_metadata)
        record.prompt_metadata.update(
            {
                "model_input_modalities": list(record.model_input_modalities),
                "model_input_visual_count": int(record.model_input_visual_count),
                "observation_modalities": list(record.observation_modalities),
            }
        )
        record.context = context_runtime.telemetry_dict(pre_context)
        engine._last_context_telemetry = dict(record.context)
        engine._emit(
            record.step_id,
            RuntimePhase.DECIDE,
            payload={
                "stage": "model_input",
                "prepared": str(prepared),
                "prepared_full": prepared_full,
                "history_message_count": len(history),
                "history_messages_meta": history_metadata,
                "messages": messages,
                "context": dict(record.context),
                "state_stats": self._state_stats(observation, record.context),
                "prompt": dict(record.prompt_metadata),
            },
        )
        engine._history_append(
            "user", str(prepared), record.step_id, metadata={"source": "engine"}
        )
        request_options = self._build_model_request_options(
            prompt_bundle=prompt_bundle,
            protocol=protocol,
        )
        # Ensure chain consistency: every assistant tool_call must have
        # a corresponding tool response. Add placeholder responses for
        # any dangling tool calls (e.g., after error recovery).
        messages = self._ensure_chain_consistency(messages)
        llm_messages = self._strip_internal_message_keys(messages)
        raw_decision = self._call_llm(engine.agent.llm, llm_messages, request_options)
        response = self._normalize_model_response(raw_decision)
        post_context = context_runtime.finalize_output(
            llm=engine.agent.llm,
            telemetry=pre_context,
            raw_output=response.text,
        )
        record.context = context_runtime.telemetry_dict(post_context)
        record.model_response = response.to_summary_dict()
        engine._last_context_telemetry = dict(record.context)
        engine._emit(
            record.step_id,
            RuntimePhase.DECIDE,
            payload={
                "stage": "model_output",
                "raw_output": response.text,
                "reasoning_content": response.reasoning_content,
                "model_response": dict(record.model_response),
                "context": dict(record.context),
                "prompt": prompt_metadata,
            },
        )
        assistant_tool_calls = []
        if response.tool_calls and self._native_tool_call_preferred():
            assistant_tool_calls = [
                {
                    "id": item.get("id"),
                    "type": item.get("type", "function"),
                    "function": dict(item.get("function", {}))
                    if isinstance(item.get("function", {}), dict)
                    else {},
                }
                for item in list(response.tool_calls or [])
                if isinstance(item, dict)
            ]
        assistant_content: Any = response.text
        if assistant_tool_calls and not str(response.text or "").strip():
            assistant_content = None
        engine._history_append(
            "assistant",
            assistant_content,
            record.step_id,
            metadata={"source": "engine"},
            tool_calls=assistant_tool_calls,
        )

        return response

    def _write_assembled_messages_sidecar(
        self,
        state: StateT,
        step_id: int,
        messages: List[Dict[str, Any]],
    ) -> None:
        try:
            metadata = dict(getattr(state, "metadata", {}) or {})
            trace_root = str(metadata.get("trace_run_dir") or "").strip()
            if not trace_root:
                return
            step_dir = Path(trace_root) / "agent_steps" / f"step-{int(step_id):04d}"
            step_dir.mkdir(parents=True, exist_ok=True)
            (step_dir / "assembled_messages.json").write_text(
                json.dumps(messages, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            return

    def _build_model_request_options(
        self, *, prompt_bundle: Any, protocol: Any
    ) -> Dict[str, Any]:
        metadata = dict(getattr(prompt_bundle, "metadata", {}) or {})
        delivery = str(metadata.get("tool_schema_delivery") or "prompt_injection")
        payload = getattr(prompt_bundle, "tool_schema_payload", None)
        llm = getattr(self.engine.agent, "llm", None)
        options: Dict[str, Any] = {}

        # Build tool schema options
        if llm is not None and delivery in {"api_parameter", "hybrid"}:
            build_options = getattr(llm, "build_tool_schema_request_options", None)
            if callable(build_options):
                try:
                    options.update(
                        build_options(payload, protocol=protocol, delivery=delivery) or {}
                    )
                except Exception:
                    _logger.debug("build_tool_schema_request_options failed", exc_info=True)

        # Merge default_request_kwargs from the model instance
        # (e.g. chat_template_kwargs for thinking mode)
        if llm is not None:
            default_kwargs = getattr(llm, "default_request_kwargs", None)
            if isinstance(default_kwargs, dict) and default_kwargs:
                options.update(default_kwargs)

        return options

    def _call_llm(
        self, llm: Any, messages: List[Dict[str, Any]], request_options: Dict[str, Any]
    ) -> Any:
        # If streaming is requested and the model supports it, use streaming path
        if self.stream_callback is not None:
            stream_fn = getattr(llm, "stream", None)
            if callable(stream_fn):
                return self._call_llm_streaming(llm, messages, request_options)

        call_raw = getattr(llm, "call_raw", None)
        if callable(call_raw):
            if not request_options:
                return call_raw(messages)
            try:
                return call_raw(messages, **request_options)
            except TypeError:
                _logger.warning(
                    "call_raw rejected request_options (TypeError), "
                    "falling back without options. Keys: %s",
                    list(request_options.keys()),
                )
                return call_raw(messages)
        if not request_options:
            return llm(messages)
        try:
            return llm(messages, **request_options)
        except TypeError:
            _logger.warning(
                "LLM call rejected request_options (TypeError), "
                "falling back without options. Keys: %s",
                list(request_options.keys()),
            )
            return llm(messages)

    def _call_llm_streaming(
        self, llm: Any, messages: List[Dict[str, Any]], request_options: Dict[str, Any]
    ) -> Any:
        """Stream LLM response, forwarding text deltas via callback.

        Returns a synthetic dict that mimics the structure _normalize_model_response
        expects: {"text": ..., "usage": ..., "finish_reason": ..., "tool_calls": ...}.
        """
        stream_fn = getattr(llm, "stream", None)
        if not callable(stream_fn):
            return self._call_llm(llm, messages, request_options)

        handler = to_stream_handler(self.stream_callback)
        accumulated_text: List[str] = []
        final_usage: Optional[Dict[str, Any]] = None
        final_tool_calls: Optional[List[Dict[str, Any]]] = None
        started = False

        if not request_options:
            stream_iter = stream_fn(messages)
        else:
            try:
                stream_iter = stream_fn(messages, **request_options)
            except TypeError:
                stream_iter = stream_fn(messages)

        try:
            for chunk in stream_iter:
                # Handle ModelStreamChunk objects
                text = getattr(chunk, "text", None)
                done = getattr(chunk, "done", False)
                usage = getattr(chunk, "usage", None)
                tool_calls = getattr(chunk, "tool_calls", None)

                if text:
                    if not started:
                        started = True
                        if handler is not None:
                            try:
                                handler.on_start()
                            except Exception:
                                pass
                    accumulated_text.append(text)
                    if handler is not None:
                        try:
                            handler.on_delta(text)
                        except Exception:
                            pass

                if done:
                    if usage is not None and isinstance(usage, dict):
                        final_usage = usage
                    if tool_calls is not None and isinstance(tool_calls, list):
                        final_tool_calls = tool_calls
        finally:
            if handler is not None and started:
                try:
                    handler.on_end()
                except Exception:
                    pass
        if final_usage is None:
            last_usage = getattr(llm, "_last_usage", None)
            if isinstance(last_usage, dict) and last_usage:
                final_usage = last_usage

        # Return a synthetic response that _normalize_model_response can process
        full_text = "".join(accumulated_text)
        result: Dict[str, Any] = {
            "text": full_text,
            "usage": final_usage or {},
            "finish_reason": "stop",
        }
        if final_tool_calls:
            result["tool_calls"] = final_tool_calls
        return result

    def _build_current_user_message(
        self,
        *,
        prepared_text: str,
        prompt_user_content_blocks: List[Dict[str, Any]],
        observation: ObservationT,
        record: StepRecord,
    ) -> Dict[str, Any]:
        content_blocks: List[Dict[str, Any]] = []
        if str(prepared_text or "").strip():
            content_blocks.append(text_block(str(prepared_text)))

        task_blocks = self._task_visual_blocks()
        observation_blocks = self._observation_visual_blocks(observation, record)
        content_blocks.extend(
            [normalize_content_block(block) for block in prompt_user_content_blocks]
        )
        content_blocks.extend(task_blocks)
        content_blocks.extend(observation_blocks)

        record.model_input_modalities = self._content_modalities(content_blocks)
        record.model_input_visual_count = sum(
            1 for block in content_blocks if str(block.get("type") or "text") != "text"
        )
        if (
            record.model_input_visual_count > 0
            and not self._llm_supports_multimodal(getattr(self.engine.agent, "llm", None))
        ):
            raise ValueError(
                "Configured model adapter does not support multimodal input content blocks."
            )
        if record.model_input_visual_count > 0:
            return {"role": "user", "content": content_blocks}
        return {"role": "user", "content": str(prepared_text or "")}

    def _content_modalities(self, content_blocks: List[Dict[str, Any]]) -> List[str]:
        modalities: List[str] = []
        for block in content_blocks:
            block_type = str(block.get("type") or "text")
            if block_type == "text":
                if "text" not in modalities:
                    modalities.append("text")
                continue
            if block_type in {"image_url", "image_base64", "image_file"}:
                if "image" not in modalities:
                    modalities.append("image")
                continue
            if block_type not in modalities:
                modalities.append(block_type)
        return modalities

    def _llm_supports_multimodal(self, llm: Any) -> bool:
        supports = getattr(llm, "supports_multimodal_input", None)
        if callable(supports):
            try:
                return bool(supports())
            except Exception:
                return False
        return True

    def _task_workspace_root(self) -> Optional[Path]:
        task_obj = getattr(self.engine, "_active_task_obj", None)
        env_spec = getattr(task_obj, "env_spec", None)
        config = getattr(env_spec, "config", None)
        if isinstance(config, dict):
            root = str(config.get("workspace_root") or "").strip()
            if root:
                return Path(root).expanduser().resolve()
        return None

    def _task_visual_blocks(self) -> List[Dict[str, Any]]:
        task_obj = getattr(self.engine, "_active_task_obj", None)
        resources = list(getattr(task_obj, "resources", []) or [])
        workspace_root = self._task_workspace_root()
        blocks: List[Dict[str, Any]] = []
        for item in resources:
            kind = str(getattr(item, "kind", "") or "").strip().lower()
            metadata = dict(getattr(item, "metadata", {}) or {})
            modality = str(metadata.get("modality") or "").strip().lower()
            if kind != "image" and modality != "image":
                continue
            detail = str(metadata.get("detail") or "").strip() or None
            uri = str(getattr(item, "uri", "") or "").strip()
            path = str(getattr(item, "path", "") or "").strip()
            if uri:
                blocks.append(
                    image_url_block(
                        uri,
                        detail=detail,
                        metadata={"source": "task_resource", "kind": kind},
                    )
                )
                continue
            if path:
                resolved = Path(path).expanduser()
                if not resolved.is_absolute() and workspace_root is not None:
                    resolved = (workspace_root / resolved).resolve()
                blocks.append(
                    image_file_block(
                        str(resolved),
                        detail=detail,
                        metadata={"source": "task_resource", "kind": kind},
                    )
                )
        return blocks

    def _observation_visual_blocks(
        self, observation: ObservationT, record: StepRecord
    ) -> List[Dict[str, Any]]:
        env_observation = getattr(self.engine, "_last_env_observation", None)
        payload = self._observation_pack_payload(env_observation, observation)
        if payload is None:
            return []
        record.observation_modalities = observation_modalities(payload)
        record.visual_assets = observation_visual_assets(
            payload, source_step=record.step_id
        )
        record.visual_asset_count = len(record.visual_assets)
        record.has_screenshot = "screenshot" in record.observation_modalities
        record.has_dom = "dom" in record.observation_modalities
        record.has_accessibility_tree = (
            "accessibility_tree" in record.observation_modalities
        )
        pack = normalize_observation_pack(payload)
        if pack is None or not isinstance(pack.screenshot, dict):
            return []
        screenshot = dict(pack.screenshot)
        detail = str(screenshot.get("detail") or "high").strip() or "high"
        metadata: Dict[str, Any] = {"source": "env_observation"}
        if pack.metadata:
            metadata["observation"] = dict(pack.metadata)
        if screenshot.get("url"):
            return [
                image_url_block(
                    str(screenshot.get("url") or ""),
                    detail=detail,
                    mime_type=str(screenshot.get("mime_type") or ""),
                    metadata=metadata,
                )
            ]
        if screenshot.get("path"):
            return [
                image_file_block(
                    str(screenshot.get("path") or ""),
                    mime_type=str(screenshot.get("mime_type") or ""),
                    detail=detail,
                    metadata=metadata,
                )
            ]
        data_value = screenshot.get("data_url") or screenshot.get("data") or screenshot.get(
            "base64"
        )
        if data_value:
            return [
                image_base64_block(
                    str(data_value),
                    mime_type=str(screenshot.get("mime_type") or "image/png"),
                    detail=detail,
                    metadata=metadata,
                )
            ]
        return []

    def _observation_pack_payload(
        self, env_observation: Any, observation: ObservationT
    ) -> Dict[str, Any] | None:
        if env_observation is not None:
            data = getattr(env_observation, "data", None)
            if isinstance(data, dict):
                multimodal = data.get("multimodal")
                if isinstance(multimodal, dict):
                    return multimodal
                if normalize_observation_pack(data) is not None:
                    return data
        if isinstance(observation, Observation):
            env_payload = observation.env
            if isinstance(env_payload, dict):
                env_obs = env_payload.get("observation")
                if isinstance(env_obs, dict):
                    data = env_obs.get("data")
                    if isinstance(data, dict):
                        multimodal = data.get("multimodal")
                        if isinstance(multimodal, dict):
                            return multimodal
                        if normalize_observation_pack(data) is not None:
                            return data
        if isinstance(observation, dict):
            env_payload_dict = observation.get("env")
            if isinstance(env_payload_dict, dict):
                env_obs = env_payload_dict.get("observation")
                if isinstance(env_obs, dict):
                    data = env_obs.get("data")
                    if isinstance(data, dict):
                        multimodal = data.get("multimodal")
                        if isinstance(multimodal, dict):
                            return multimodal
                        if normalize_observation_pack(data) is not None:
                            return data
        return None

    def _state_stats(
        self, observation: ObservationT, context: Dict[str, Any]
    ) -> Dict[str, Any]:
        stats: Dict[str, Any] = {}
        if isinstance(observation, Observation):
            stats["action_results"] = len(observation.action_results or [])
            if isinstance(observation.state, dict):
                scratchpad = observation.state.get("scratchpad")
                if isinstance(scratchpad, list):
                    stats["scratchpad_items"] = len(scratchpad)
        if isinstance(observation, dict):
            scratchpad = observation.get("scratchpad")
            if isinstance(scratchpad, list):
                stats["scratchpad_items"] = len(scratchpad)
            elif isinstance(scratchpad, str) and scratchpad.strip():
                stats["scratchpad_items"] = 1
            memory = observation.get("memory")
            if isinstance(memory, dict) and isinstance(memory.get("records"), list):
                stats["memory_records"] = len(memory.get("records") or [])
            workspace_files = observation.get("workspace_files")
            if isinstance(workspace_files, list):
                stats["workspace_files"] = len(workspace_files)
        for key in (
            "input_tokens_total",
            "history_tokens",
            "output_tokens",
            "occupancy_ratio",
            "context_window",
        ):
            if key in context:
                stats[key] = context.get(key)
        # Extract chain/gate/memory info from state for TUI rendering.
        # Uses duck-typing so non-CyberGym agents are unaffected.
        state_obj = getattr(observation, "state", None)
        if state_obj is None and isinstance(observation, dict):
            state_obj = observation.get("state")
        if state_obj is not None:
            chain_nodes = getattr(state_obj, "call_chain_nodes", None)
            chain_gates = getattr(state_obj, "call_chain_gates", None)
            if chain_nodes or chain_gates:
                chain_summary = self._render_chain_summary(state_obj)
                if chain_summary:
                    stats["chain_summary"] = chain_summary
            # Task-persistent memory for TUI
            for field_name in ("vulnerability_analysis", "current_hypothesis"):
                val = getattr(state_obj, field_name, "")
                if isinstance(val, str) and val.strip():
                    stats[field_name] = val.strip()[:300]
            path_trace = getattr(state_obj, "path_trace", None)
            if isinstance(path_trace, list) and path_trace:
                stats["path_trace"] = path_trace[:8]
            attempt_compact = getattr(state_obj, "attempt_history_compact", None)
            if isinstance(attempt_compact, list) and attempt_compact:
                stats["attempt_history_compact"] = attempt_compact[-5:]
        return stats

    @staticmethod
    def _render_chain_summary(state_obj: Any) -> str:
        """Render a compact chain+gate summary for TUI display."""
        nodes = list(getattr(state_obj, "call_chain_nodes", []) or [])
        gates = list(getattr(state_obj, "call_chain_gates", []) or [])
        if not nodes and not gates:
            return ""
        confirmed = sum(1 for g in gates if getattr(g, "status", "") == "confirmed")
        open_g = sum(1 for g in gates if getattr(g, "status", "") in ("inferred", "unknown"))
        refuted = sum(1 for g in gates if getattr(g, "status", "") == "refuted")
        lines = [f"Chain: {len(nodes)} nodes | Gates: {confirmed}✓ {open_g}? {refuted}✗"]
        # Render nodes ordered
        if nodes:
            sorted_nodes = sorted(nodes, key=lambda n: getattr(n, "order", 0))
            for n in sorted_nodes[:6]:
                role = getattr(n, "role", "?")
                func = getattr(n, "function", "?")
                loc = getattr(n, "location", "")
                loc_short = loc.split(":")[0] if ":" in loc else loc
                status = getattr(n, "status", "?")
                badge = "✓" if status == "confirmed" else "?"
                lines.append(f"  [{getattr(n, 'order', 0)}] {badge} {role:8s} {func} ({loc_short})")
        # Render refuted gates (learning)
        for g in gates:
            if getattr(g, "status", "") == "refuted":
                desc = getattr(g, "description", "")[:80]
                hint = getattr(g, "repair_hint", "")[:60]
                line = f"  ✗ {desc}"
                if hint:
                    line += f" → {hint}"
                lines.append(line)
        # Render open gates (blockers)
        for g in gates:
            if getattr(g, "status", "") in ("inferred", "unknown"):
                desc = getattr(g, "description", "")[:80]
                cond = getattr(g, "required_condition", "")[:60]
                line = f"  ? {desc}"
                if cond:
                    line += f" → {cond}"
                lines.append(line)
        return "\n".join(lines)

    def select_branch(
        self,
        state: StateT,
        observation: ObservationT,
        branch_decision: Decision[ActionT],
    ) -> Decision[ActionT]:
        engine = self.engine
        if engine.search is not None:
            candidates = engine.search.expand(
                state, observation, branch_decision
            ) or list(branch_decision.candidates)
            scores = engine.search.score(state, observation, candidates)
            candidates = engine.search.prune(candidates, scores)
            if not candidates:
                new_state = engine.search.backtrack(state)
                if new_state is not state:
                    state.__dict__.update(new_state.__dict__)
                return Decision.wait(rationale="search backtrack")
            scores = engine.search.score(state, observation, candidates)
            selected = engine.search.select(candidates, scores)
            mark_selected = getattr(engine.search, "mark_selected", None)
            if callable(mark_selected):
                mark_selected(state, selected)
        else:
            selected = engine.branch_selector.select(
                branch_decision.candidates, state, observation
            )
        selected.validate()
        return selected

    def normalize_decision(
        self, raw_decision: Any, step: int, record: StepRecord | None = None
    ) -> Decision[ActionT]:
        if isinstance(raw_decision, Decision):
            if record is not None and not record.decision_source:
                record.decision_source = "agent"
            return raw_decision

        response = raw_decision if isinstance(raw_decision, ModelResponse) else None
        native_decision = self._decision_from_native_tool_calls(
            response=response,
            step=step,
            record=record,
        )
        if native_decision is not None:
            return native_decision
        parser_input = response.text if response is not None else raw_decision

        # When native tool calling is preferred and the model returned plain
        # text without tool_calls, treat it as a final answer — the model is
        # done acting and is giving its summary/conclusion in natural language.
        # Parsers (especially json_decision_v1) will misinterpret natural
        # language as invalid JSON and return wait(), which causes the agent
        # to loop forever without ever producing a final result.
        is_native_text_response = (
            response is not None
            and self._native_tool_call_preferred()
            and not (isinstance(response.tool_calls, list) and response.tool_calls)
            and str(response.text or "").strip()
        )

        if is_native_text_response:
            # Still try the parser chain first — if the model happened to
            # produce valid structured output (JSON with a final_answer, or
            # ReAct "Final Answer:" label), let the parser extract it.
            parse_outcome = self._parse_with_protocol_chain(
                parser_input=parser_input,
                step=step,
                record=record,
            )
            # If the parser produced a *productive* decision (act or final),
            # honour it. But if the parser could only produce wait() — which
            # is the default fallback when natural language can't be parsed as
            # JSON — override it with native_text_final.
            if parse_outcome is not None and parse_outcome.mode != "wait":
                return parse_outcome

            if record is not None:
                record.decision_source = "native_text_final"
            return Decision.final(
                answer=str(response.text).strip(),
                meta={"decision_source": "native_text_final"},
            )

        parse_outcome = self._parse_with_protocol_chain(
            parser_input=parser_input,
            step=step,
            record=record,
        )
        if parse_outcome is not None:
            return parse_outcome

        raise ValueError(
            "Agent.decide must return Decision when no parser is configured"
        )

    def _parse_with_protocol_chain(
        self,
        *,
        parser_input: Any,
        step: int,
        record: StepRecord | None,
    ) -> Decision[ActionT] | None:
        parser_attempts: List[Dict[str, Any]] = []
        last_exception: Exception | None = None
        last_diagnostics: Dict[str, Any] | None = None
        candidates = self._candidate_parsers()
        for candidate in candidates:
            parser = candidate["parser"]
            protocol = candidate.get("protocol")
            fallback_used = bool(candidate.get("fallback_used"))
            try:
                decision = parser.parse(
                    parser_input,
                    context={"step": step, "protocol": getattr(protocol, "id", None)},
                )
                normalized = normalize_parser_diagnostics(
                    getattr(decision, "meta", None),
                    parser=parser,
                    raw_output=parser_input,
                    step_id=step,
                )
                if normalized is not None:
                    normalized = dict(normalized)
                    normalized.setdefault("protocol", getattr(protocol, "id", None))
                    normalized.setdefault("selected_parser", parser_name(parser))
                    normalized.setdefault("fallback_used", fallback_used)
                    normalized.setdefault("parser_attempts", list(parser_attempts))
                parser_attempts.append(
                    {
                        "parser": parser_name(parser),
                        "contract": parser_contract(parser),
                        "protocol": getattr(protocol, "id", None),
                        "result": "success"
                        if normalized is None
                        or normalized.get("severity") != "error"
                        else "error",
                        "fallback_used": fallback_used,
                    }
                )
                if (
                    normalized is not None
                    and normalized.get("severity") == "error"
                    and candidate.get("allow_fallback", True)
                ):
                    last_diagnostics = dict(normalized)
                    continue
                self._record_parser_observability(
                    step=step,
                    raw_output=parser_input,
                    decision=decision,
                    record=record,
                    parser=parser,
                    diagnostics=normalized,
                    protocol=protocol,
                    parser_attempts=parser_attempts,
                    fallback_used=fallback_used,
                )
                return decision
            except Exception as exc:
                last_exception = exc
                parser_attempts.append(
                    {
                        "parser": parser_name(parser),
                        "contract": parser_contract(parser),
                        "protocol": getattr(protocol, "id", None),
                        "result": "exception",
                        "fallback_used": fallback_used,
                    }
                )
                last_diagnostics = build_parser_diagnostics(
                    parser=parser,
                    severity="error",
                    code="unexpected_parser_exception",
                    summary="Parser raised an unexpected exception.",
                    raw_output=parser_input,
                    details=str(exc),
                    repair_instruction="The parser failed internally before producing structured repair feedback.",
                    expected_shape="See the configured parser contract for the expected output format.",
                    step_id=step,
                )
                last_diagnostics["protocol"] = getattr(protocol, "id", None)
                last_diagnostics["selected_parser"] = parser_name(parser)
                last_diagnostics["fallback_used"] = fallback_used
                last_diagnostics["parser_attempts"] = list(parser_attempts)
                continue
        if last_diagnostics is not None:
            selected_parser = parser_name(candidates[-1]["parser"]) if candidates else "unknown_parser"
            last_diagnostics.setdefault("selected_parser", selected_parser)
            last_diagnostics.setdefault("fallback_used", any(item.get("fallback_used") for item in parser_attempts))
            last_diagnostics.setdefault("parser_attempts", parser_attempts)
            self._record_parser_observability(
                step=step,
                raw_output=parser_input,
                decision=None,
                record=record,
                parser=candidates[-1]["parser"] if candidates else "unknown_parser",
                diagnostics=last_diagnostics,
                protocol=candidates[-1].get("protocol") if candidates else None,
                parser_attempts=parser_attempts,
                fallback_used=any(item.get("fallback_used") for item in parser_attempts),
            )
            if last_exception is not None:
                info = RuntimeErrorInfo(
                    category=ErrorCategory.PARSE,
                    message=str(last_exception),
                    phase="decide",
                    step_id=step,
                    recoverable=True,
                    details={"parser_diagnostics": last_diagnostics},
                )
                raise ParseExecutionError(info) from last_exception
            return Decision.wait(
                rationale=str(last_diagnostics.get("summary") or "Parser error."),
                meta={
                    "parser_error": True,
                    "parser_feedback": str(
                        last_diagnostics.get("repair_instruction")
                        or last_diagnostics.get("summary")
                        or ""
                    ),
                    "parser_diagnostics": last_diagnostics,
                },
            )
        return None

    def _candidate_parsers(self) -> List[Dict[str, Any]]:
        engine = self.engine
        if engine.parser is not None:
            return [
                {
                    "parser": engine.parser,
                    "protocol": get_protocol(engine.protocol),
                    "fallback_used": False,
                    "allow_fallback": False,
                }
            ]
        protocol = engine.resolve_protocol()
        candidates: List[Dict[str, Any]] = []
        agent_parser = getattr(engine.agent, "model_parser", None)
        if agent_parser is not None:
            candidates.append(
                {
                    "parser": agent_parser,
                    "protocol": protocol,
                    "fallback_used": False,
                    "allow_fallback": True,
                }
            )
        for index, item in enumerate(resolve_protocol_chain(protocol)):
            try:
                parser = item.parser_factory()
            except Exception:
                continue
            if agent_parser is not None and parser.__class__ is agent_parser.__class__:
                continue
            candidates.append(
                {
                    "parser": parser,
                    "protocol": item,
                    "fallback_used": bool(agent_parser) or index > 0,
                    "allow_fallback": True,
                }
            )
        return candidates

    def _interpret_model_response(
        self,
        *,
        state: StateT,
        observation: ObservationT,
        response: ModelResponse,
        record: StepRecord,
    ) -> Decision[ActionT] | None:
        interpret = getattr(self.engine.agent, "interpret_model_response", None)
        if not callable(interpret):
            return None
        decision = interpret(state, observation, response)
        if decision is None:
            return None
        if not isinstance(decision, Decision):
            raise ValueError(
                "Agent.interpret_model_response must return Decision or None"
            )
        self.engine._emit(
            record.step_id,
            RuntimePhase.DECIDE,
            payload={
                "stage": "model_response_interpreted",
                "mode": decision.mode,
                "model_response": dict(record.model_response),
            },
        )
        record.decision_source = "agent_interpretation"
        return decision

    def _record_parser_observability(
        self,
        *,
        step: int,
        raw_output: Any,
        decision: Decision[ActionT] | None,
        record: StepRecord | None,
        parser: Any,
        diagnostics: Dict[str, Any] | None = None,
        protocol: Any = None,
        parser_attempts: List[Dict[str, Any]] | None = None,
        fallback_used: bool = False,
    ) -> None:
        engine = self.engine
        contract = parser_contract(parser)
        normalized = diagnostics or normalize_parser_diagnostics(
            getattr(decision, "meta", None),
            parser=parser,
            raw_output=raw_output,
            step_id=step,
        )
        protocol_id = getattr(protocol, "id", None) if protocol is not None else None
        attempts = list(parser_attempts or [])
        if normalized is not None:
            normalized.setdefault("protocol", protocol_id)
            normalized.setdefault("selected_parser", parser_name(parser))
            normalized.setdefault("fallback_used", bool(fallback_used))
            normalized.setdefault("parser_attempts", attempts)
        if (
            decision is not None
            and isinstance(decision.meta, dict)
            and normalized is not None
        ):
            decision.meta["parser_diagnostics"] = normalized
            if normalized.get("severity") == "error":
                decision.meta.setdefault("parser_error", True)
                decision.meta.setdefault(
                    "parser_feedback",
                    normalized.get("repair_instruction")
                    or normalized.get("summary")
                    or "",
                )
            else:
                decision.meta.setdefault(
                    "parser_warning",
                    normalized.get("salvage_summary")
                    or normalized.get("summary")
                    or "",
                )
        parsed_mode = getattr(decision, "mode", None) if decision is not None else None
        result_payload = {
            "stage": "parser_result",
            "parser": parser_name(parser),
            "contract": contract,
            "protocol": protocol_id,
            "selected_parser": parser_name(parser),
            "parsed_mode": parsed_mode,
            "has_diagnostics": normalized is not None,
            "salvage_applied": bool((normalized or {}).get("salvage_applied")),
            "severity": (normalized or {}).get("severity"),
            "fallback_used": bool(fallback_used),
            "parser_attempts": attempts,
        }
        engine._emit(step, RuntimePhase.DECIDE, payload=result_payload)
        if normalized is not None:
            engine._emit(
                step,
                RuntimePhase.DECIDE,
                payload={"stage": "parser_diagnostics", "diagnostics": normalized},
            )
            engine._trace_runtime.record_parser_diagnostics(normalized)
        if record is not None:
            record.protocol_id = protocol_id
            record.parser_selected = parser_name(parser)
            record.parser_fallback_used = bool(fallback_used)
            record.parser_attempts = attempts
            record.parser_contract = contract
            record.parser_diagnostics = dict(normalized or {})
            record.parser_salvage_applied = bool(
                (normalized or {}).get("salvage_applied")
            )
            record.decision_source = "parser"

    def _extract_reasoning_content(self, raw_output: Any) -> Optional[str]:
        """Extract reasoning_content from API response if present.

        Some providers (DeepSeek, QwQ, etc.) return a separate
        ``reasoning_content`` field alongside ``content`` and ``tool_calls``.
        This field carries the model's chain-of-thought and should be
        displayed in the TUI as the agent's "thought".
        """
        # Navigate to the message object
        message = None
        if isinstance(raw_output, dict):
            choices = raw_output.get("choices")
            if isinstance(choices, list) and choices:
                msg = choices[0].get("message") if isinstance(choices[0], dict) else None
                if isinstance(msg, dict):
                    message = msg
        if message is None:
            choices = getattr(raw_output, "choices", None)
            if isinstance(choices, list) and choices:
                message = getattr(choices[0], "message", None)
        if message is None:
            message = getattr(raw_output, "message", None)

        if message is not None:
            # dict-style message
            if isinstance(message, dict):
                rc = message.get("reasoning_content")
                if isinstance(rc, str) and rc.strip():
                    return rc
            # object-style message
            rc = getattr(message, "reasoning_content", None)
            if isinstance(rc, str) and rc.strip():
                return rc
        return None

    def _normalize_model_response(self, raw_output: Any) -> ModelResponse:
        if isinstance(raw_output, ModelResponse):
            response = raw_output
        else:
            response = ModelResponse(
                text=self._extract_response_text(raw_output),
                raw=raw_output,
                usage=self._extract_response_usage(raw_output),
                finish_reason=self._extract_finish_reason(raw_output),
                tool_calls=self._extract_tool_calls(raw_output),
                model_name=self._extract_model_name(raw_output),
                provider=self._extract_provider(raw_output),
                metadata=self._extract_response_metadata(raw_output),
                reasoning_content=self._extract_reasoning_content(raw_output),
            )
        llm = getattr(self.engine.agent, "llm", None)
        usage = response.usage
        if usage is None and llm is not None and hasattr(llm, "extract_usage"):
            try:
                extracted = llm.extract_usage(raw_output)
                if isinstance(extracted, dict):
                    usage = extracted
            except Exception:
                usage = None
        model_name = (
            response.model_name
            or getattr(llm, "model", None)
            or getattr(llm, "model_name", None)
        )
        provider = (
            response.provider
            or getattr(llm, "provider", None)
            or (llm.__class__.__name__ if llm is not None else None)
        )
        metadata = dict(response.metadata or {})
        text = str(response.text or "")
        tool_calls = (
            [dict(item) for item in (response.tool_calls or [])]
            if isinstance(response.tool_calls, list)
            else None
        )
        if not tool_calls:
            markup_tool_calls = self._extract_text_tool_call_markup(text)
            if markup_tool_calls:
                tool_calls = markup_tool_calls
                metadata["tool_call_markup_salvaged"] = True
                metadata["tool_call_markup_format"] = "glm_text_tool_call"
                if self._contains_only_text_tool_call_markup(text):
                    text = ""
        return ModelResponse(
            text=text,
            raw=response.raw,
            usage=dict(usage) if isinstance(usage, dict) else None,
            finish_reason=response.finish_reason,
            tool_calls=tool_calls,
            model_name=str(model_name) if model_name is not None else None,
            provider=str(provider) if provider is not None else None,
            metadata=metadata,
            reasoning_content=response.reasoning_content,
        )

    def _extract_text_tool_call_markup(self, text: str) -> List[Dict[str, Any]] | None:
        """Salvage GLM-style textual tool-call markup into native tool calls."""
        if "<tool_call>" not in text:
            return None
        calls: List[Dict[str, Any]] = []
        for index, match in enumerate(
            re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL),
            start=1,
        ):
            body = match.group(1)
            first_arg = re.search(r"<arg_key>", body)
            name_part = body[: first_arg.start()] if first_arg else body
            name = html.unescape(re.sub(r"<[^>]+>", "", name_part)).strip()
            if not name:
                continue
            args: Dict[str, Any] = {}
            for key, value in re.findall(
                r"<arg_key>\s*(.*?)\s*</arg_key>\s*<arg_value>\s*(.*?)\s*</arg_value>",
                body,
                re.DOTALL,
            ):
                clean_key = html.unescape(re.sub(r"<[^>]+>", "", key)).strip()
                if not clean_key:
                    continue
                args[clean_key] = self._coerce_text_tool_call_arg(value)
            calls.append(
                {
                    "id": f"call_glm_text_{index}",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(args, ensure_ascii=False),
                    },
                }
            )
        return calls or None

    def _coerce_text_tool_call_arg(self, value: str) -> Any:
        text = html.unescape(str(value or "")).strip()
        try:
            return json.loads(text)
        except Exception:
            return text

    def _contains_only_text_tool_call_markup(self, text: str) -> bool:
        stripped = str(text or "").strip()
        if not stripped:
            return False
        remainder = re.sub(
            r"<tool_call>\s*.*?\s*</tool_call>",
            "",
            stripped,
            flags=re.DOTALL,
        ).strip()
        return not remainder

    def _extract_response_text(self, raw_output: Any) -> str:
        """Extract text content from a model response.

        Returns the ``content`` field even when ``tool_calls`` are present,
        because the model may produce both (e.g. a brief explanation
        alongside tool calls).  ``reasoning_content`` is extracted
        separately by ``_extract_reasoning_content``.
        """
        if raw_output is None:
            return ""
        if isinstance(raw_output, str):
            return raw_output
        if isinstance(raw_output, dict):
            for key in ("text", "content", "output_text"):
                value = raw_output.get(key)
                if isinstance(value, str):
                    return value
            # tool_calls no longer short-circuits: content may coexist
            choices = raw_output.get("choices")
            if isinstance(choices, list) and choices:
                return self._extract_response_text(choices[0])
            message = raw_output.get("message")
            if isinstance(message, dict):
                return self._extract_response_text(message)
            return str(raw_output)
        choices = getattr(raw_output, "choices", None)
        if isinstance(choices, list) and choices:
            return self._extract_response_text(choices[0])
        message = getattr(raw_output, "message", None)
        if message is not None:
            # tool_calls no longer short-circuits: content may coexist
            content = getattr(message, "content", None)
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts: List[str] = []
                for item in content:
                    if isinstance(item, str):
                        parts.append(item)
                    elif isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(str(item.get("text")))
                    elif hasattr(item, "text") and isinstance(
                        getattr(item, "text", None), str
                    ):
                        parts.append(str(getattr(item, "text")))
                if parts:
                    return "\n".join(parts)
            reasoning = getattr(message, "reasoning_content", None)
            if isinstance(reasoning, str):
                return reasoning
            text = getattr(message, "text", None)
            if isinstance(text, str):
                return text
        for key in ("text", "content", "output_text"):
            value = getattr(raw_output, key, None)
            if isinstance(value, str):
                return value
        return str(raw_output)

    def _extract_response_usage(self, raw_output: Any) -> Dict[str, Any] | None:
        usage = (
            raw_output.get("usage")
            if isinstance(raw_output, dict)
            else getattr(raw_output, "usage", None)
        )
        if isinstance(usage, dict):
            return dict(usage)
        if usage is None:
            return None
        prompt_tokens = getattr(usage, "prompt_tokens", None)
        completion_tokens = getattr(usage, "completion_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if prompt_tokens is None and completion_tokens is None and total_tokens is None:
            return None
        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    def _extract_finish_reason(self, raw_output: Any) -> str | None:
        if isinstance(raw_output, dict):
            finish_reason = raw_output.get("finish_reason")
            if finish_reason is not None:
                return str(finish_reason)
            choices = raw_output.get("choices")
            if isinstance(choices, list) and choices:
                return self._extract_finish_reason(choices[0])
            return None
        finish_reason = getattr(raw_output, "finish_reason", None)
        if finish_reason is not None:
            return str(finish_reason)
        choices = getattr(raw_output, "choices", None)
        if isinstance(choices, list) and choices:
            return self._extract_finish_reason(choices[0])
        return None

    def _extract_tool_calls(self, raw_output: Any) -> List[Dict[str, Any]] | None:
        if isinstance(raw_output, dict):
            tool_calls = raw_output.get("tool_calls")
            if isinstance(tool_calls, list):
                return [self._normalize_tool_call(item) for item in tool_calls]
            choices = raw_output.get("choices")
            if isinstance(choices, list) and choices:
                return self._extract_tool_calls(choices[0])
            message = raw_output.get("message")
            if isinstance(message, dict):
                return self._extract_tool_calls(message)
            return None
        tool_calls = getattr(raw_output, "tool_calls", None)
        if isinstance(tool_calls, list):
            return [self._normalize_tool_call(item) for item in tool_calls]
        message = getattr(raw_output, "message", None)
        if message is not None:
            inner = getattr(message, "tool_calls", None)
            if isinstance(inner, list):
                return [self._normalize_tool_call(item) for item in inner]
        choices = getattr(raw_output, "choices", None)
        if isinstance(choices, list) and choices:
            return self._extract_tool_calls(choices[0])
        return None

    def _normalize_tool_call(self, tool_call: Any) -> Dict[str, Any]:
        if isinstance(tool_call, dict):
            payload = dict(tool_call)
            function = payload.get("function")
            if isinstance(function, dict):
                payload["function"] = dict(function)
            return payload
        function = getattr(tool_call, "function", None)
        normalized: Dict[str, Any] = {
            "id": getattr(tool_call, "id", None),
            "type": getattr(tool_call, "type", None),
        }
        if function is not None:
            normalized["function"] = {
                "name": getattr(function, "name", None),
                "arguments": getattr(function, "arguments", None),
            }
        return normalized

    def _extract_model_name(self, raw_output: Any) -> str | None:
        if isinstance(raw_output, dict):
            for key in ("model_name", "model"):
                value = raw_output.get(key)
                if value is not None:
                    return str(value)
            return None
        for key in ("model_name", "model"):
            value = getattr(raw_output, key, None)
            if value is not None:
                return str(value)
        return None

    def _extract_provider(self, raw_output: Any) -> str | None:
        if isinstance(raw_output, dict):
            value = raw_output.get("provider")
            return str(value) if value is not None else None
        value = getattr(raw_output, "provider", None)
        return str(value) if value is not None else None

    def _extract_response_metadata(self, raw_output: Any) -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}
        if isinstance(raw_output, dict):
            for key in ("id", "response_id", "created"):
                if key in raw_output:
                    metadata[key] = raw_output.get(key)
            return metadata
        for key in ("id", "response_id", "created"):
            value = getattr(raw_output, key, None)
            if value is not None:
                metadata[key] = value
        return metadata

    def _decision_from_native_tool_calls(
        self,
        *,
        response: ModelResponse | None,
        step: int,
        record: StepRecord | None,
    ) -> Decision[ActionT] | None:
        if response is None or not isinstance(response.tool_calls, list) or not response.tool_calls:
            return None
        if not self._native_tool_call_preferred():
            if record is not None and not record.decision_source:
                record.decision_source = "parser"
            return None
        actions: List[Action] = []
        for item in response.tool_calls:
            normalized = self._action_from_tool_call(item)
            if normalized is None:
                reason = "tool_call_arguments_invalid"
                if record is not None:
                    record.native_tool_call_used = False
                    record.native_tool_call_fallback_reason = reason
                self.engine._emit(
                    step,
                    RuntimePhase.DECIDE,
                    payload={
                        "stage": "native_tool_call_fallback",
                        "reason": reason,
                        "tool_call": item,
                    },
                )
                return None
            actions.append(normalized)
        decision: Decision[ActionT] = cast(
            Decision[ActionT],
            Decision.act(
                actions=actions,
                rationale=(response.text or "").strip() or None,
                meta={
                    "decision_source": "native_tool_calls",
                    "native_tool_call_count": len(actions),
                    "tool_calls": [dict(item) for item in response.tool_calls],
                },
            ),
        )
        self.engine._emit(
            step,
            RuntimePhase.DECIDE,
            payload={
                "stage": "native_tool_calls_decision",
                "tool_call_count": len(actions),
                "tool_calls": [dict(item) for item in response.tool_calls],
            },
        )
        if record is not None:
            record.decision_source = "native_tool_calls"
            record.native_tool_call_used = True
            record.native_tool_call_fallback_reason = None
        return decision

    def _native_tool_call_preferred(self) -> bool:
        llm = getattr(self.engine.agent, "llm", None)
        metadata = dict(getattr(llm, "qitos_harness_metadata", {}) or {}) if llm is not None else {}
        tool_policy = metadata.get("tool_policy")
        if isinstance(tool_policy, dict) and tool_policy.get("native_tool_call_preferred") is True:
            return True
        protocol = self.engine.resolve_protocol()
        if protocol is not None and getattr(protocol, "supports_native_tool_call_markup", False):
            return True
        return False

    def _trim_native_tool_history(
        self, history: List[Dict[str, Any]], *, max_rounds: int
    ) -> List[Dict[str, Any]]:
        if max_rounds <= 0:
            return history
        round_steps: List[int] = []
        for message in history:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "")
            if not bool(message.get("tool_calls")) and role != "tool":
                continue
            step_id = message.get("_step_id")
            if isinstance(step_id, int):
                round_steps.append(step_id)
        if not round_steps:
            return history
        keep_steps = sorted(set(round_steps))[-max_rounds:]
        earliest_step = min(keep_steps)
        trimmed: List[Dict[str, Any]] = []
        for message in history:
            step_marker = message.get("_step_id")
            if not isinstance(step_marker, int) or step_marker >= earliest_step:
                trimmed.append(message)
        return trimmed

    def _ensure_chain_consistency(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Ensure every assistant tool_call has a corresponding tool response.

        After errors or crashes, the message chain can have dangling tool
        calls (assistant messages with tool_calls that never got a response).
        The LLM API rejects such chains. This method adds placeholder tool
        responses for any missing ones.
        """
        if not messages:
            return messages

        # Collect all tool_call_ids from assistant messages
        expected_tool_ids: List[str] = []
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            tool_calls = msg.get("tool_calls", [])
            if not tool_calls:
                continue
            for tc in tool_calls:
                tc_id = tc.get("id") if isinstance(tc, dict) else None
                if tc_id:
                    expected_tool_ids.append(tc_id)

        if not expected_tool_ids:
            return messages

        # Collect all tool_call_ids that already have responses
        responded_ids: set = set()
        for msg in messages:
            if msg.get("role") == "tool":
                tc_id = msg.get("tool_call_id")
                if tc_id:
                    responded_ids.add(tc_id)

        # Find dangling tool calls and add placeholder responses
        missing_ids = [tid for tid in expected_tool_ids if tid not in responded_ids]
        if not missing_ids:
            return messages

        # Insert placeholder tool responses after the last message
        result = list(messages)
        for tid in missing_ids:
            result.append({
                "role": "tool",
                "tool_call_id": tid,
                "content": "[Tool execution was interrupted. No result available.]",
            })
        return result

    def _strip_internal_message_keys(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        cleaned: List[Dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                continue
            payload = {
                key: value
                for key, value in message.items()
                if not str(key).startswith("_")
            }
            cleaned.append(payload)
        return cleaned

    def _action_from_tool_call(self, tool_call: Dict[str, Any]) -> Action | None:
        if not isinstance(tool_call, dict):
            return None
        function = tool_call.get("function")
        if not isinstance(function, dict):
            return None
        name = str(function.get("name") or "").strip()
        if not name:
            return None
        arguments = function.get("arguments")
        args: Dict[str, Any] = {}
        if isinstance(arguments, dict):
            args = dict(arguments)
        elif isinstance(arguments, str):
            text = arguments.strip()
            if text:
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    return None
                if not isinstance(parsed, dict):
                    return None
                args = dict(parsed)
        elif arguments is not None:
            return None
        return Action(
            name=name,
            args=args,
            action_id=(str(tool_call.get("id")) if tool_call.get("id") is not None else None),
            metadata={
                "tool_call_type": tool_call.get("type"),
                "decision_source": "native_tool_calls",
            },
        )
