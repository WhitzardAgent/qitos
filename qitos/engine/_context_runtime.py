"""Private context-length telemetry helpers for Engine."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from ._protocol import _EngineProtocol
from .states import ContextConfig, ContextTelemetry


class ContextOverflowError(RuntimeError):
    """Raised when one model request still exceeds the effective input budget."""


class DecisionContextConfigurationError(RuntimeError):
    """The stable controller failed to render one authoritative context."""


class _ContextRuntime:
    def __init__(self, engine: _EngineProtocol):
        self.engine = engine
        self.config = ContextConfig()
        self.reset()

    def reset(self) -> None:
        self.prompt_tokens_total = 0
        self.completion_tokens_total = 0
        self.tokens_total = 0
        self.peak_input_tokens = 0
        self.peak_occupancy_ratio = 0.0
        self.warning_count = 0
        self.compact_counts: Dict[str, int] = {}
        self.last_request: Optional[ContextTelemetry] = None
        self.reactive_compact_attempts = 0

    def apply_config(self, config: ContextConfig | Dict[str, Any] | None) -> None:
        if config is None:
            return
        if isinstance(config, ContextConfig):
            self.config = config
            return
        payload = asdict(self.config)
        payload.update({str(k): v for k, v in dict(config).items()})
        self.config = ContextConfig(**payload)

    def enabled(self) -> bool:
        return bool(
            self.config.enabled and getattr(self.engine.agent, "llm", None) is not None
        )

    def context_window(self, llm: Any) -> Optional[int]:
        if not self.enabled():
            return None
        raw = getattr(llm, "context_window", None)
        if isinstance(raw, int) and raw > 0:
            return raw
        metadata = dict(getattr(llm, "qitos_harness_metadata", {}) or {})
        context_policy = dict(metadata.get("context_policy", {}) or {})
        hint = context_policy.get("context_window_hint")
        if isinstance(hint, int) and hint > 0:
            return hint
        fallback_hint = context_policy.get("fallback_context_window")
        if isinstance(fallback_hint, int) and fallback_hint > 0:
            return fallback_hint
        fallback = int(self.config.default_context_window)
        return fallback if fallback > 0 else None

    def resolve_request_budget(
        self, llm: Any, *, max_output_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        window = self.context_window(llm)
        configured_max_output = int(getattr(llm, "max_tokens", 0) or 0)
        max_output = int(
            configured_max_output if max_output_tokens is None else max_output_tokens
        )
        reserve = 0
        if window is not None and window > 0:
            if self.config.safety_reserve_tokens is not None:
                reserve = max(0, int(self.config.safety_reserve_tokens))
            else:
                reserve = max(
                    int(window * float(self.config.safety_reserve_ratio)),
                    int(self.config.min_safety_reserve_tokens),
                )
            reserve = min(reserve, max(0, window - max_output))
            hard = max(1, window - max_output - reserve)
            utilization_target = max(
                1,
                int(float(window) * float(self.config.target_utilization)) - max_output,
            )
            # The soft target is for preventive history eviction only.  It
            # must always sit below the hard provider capacity, unlike the
            # p11 configuration where strict enforcement happened before the
            # 150k history slider could run.
            headroom_target = max(1, hard - int(self.config.compaction_headroom_tokens))
            soft = min(hard, utilization_target, headroom_target)
        else:
            hard = None
            soft = None
        return {
            "context_window": window,
            "max_output_tokens": max_output,
            "configured_max_output_tokens": configured_max_output,
            "reserve_tokens": reserve,
            # Keep this legacy field as the strict/hard value so consumers
            # cannot accidentally enforce the soft compaction target.
            "available_input_budget": hard,
            "hard_input_budget": hard,
            "soft_input_target": soft,
        }

    def count_tokens(self, payload: Any, llm: Any) -> tuple[int, str]:
        if payload is None:
            return 0, "disabled"
        counter = getattr(llm, "count_tokens", None)
        if callable(counter):
            try:
                value = counter(payload)
                if isinstance(value, int) and value >= 0:
                    return int(value), "model_count"
            except Exception:
                pass
        return self.engine._estimate_tokens(payload), "engine_estimate"

    def build_pre_request(
        self,
        *,
        llm: Any,
        system_prompt: Optional[str],
        prepared: str,
    ) -> ContextTelemetry:
        budget = self.resolve_request_budget(llm)
        system_tokens, system_mode = self.count_tokens(system_prompt or "", llm)
        prepared_tokens, prepared_mode = self.count_tokens(prepared, llm)
        telemetry = ContextTelemetry(
            context_window=budget["context_window"],
            available_input_budget=budget["available_input_budget"],
            hard_input_budget=budget["hard_input_budget"],
            soft_input_target=budget["soft_input_target"],
            system_prompt_tokens=system_tokens,
            prepared_tokens=prepared_tokens,
            warning_threshold_ratio=float(self.config.warning_ratio),
            counting_mode=self._merge_counting_mode([system_mode, prepared_mode]),
            reserve_tokens=int(budget["reserve_tokens"] or 0),
            max_output_tokens=int(budget["max_output_tokens"] or 0),
            configured_max_output_tokens=int(
                budget["configured_max_output_tokens"] or 0
            ),
        )
        return telemetry

    def history_budget(self, telemetry: ContextTelemetry) -> Optional[int]:
        if telemetry.available_input_budget is None:
            return None
        remaining = (
            int(telemetry.available_input_budget)
            - int(telemetry.system_prompt_tokens)
            - int(telemetry.prepared_tokens)
        )
        return max(1, remaining)

    def apply_effective_output_limit(
        self,
        *,
        llm: Any,
        telemetry: ContextTelemetry,
        max_output_tokens: int,
    ) -> ContextTelemetry:
        """Recompute hard/soft input budgets for one recovered request."""
        budget = self.resolve_request_budget(
            llm, max_output_tokens=max(1, int(max_output_tokens))
        )
        telemetry.available_input_budget = budget["available_input_budget"]
        telemetry.hard_input_budget = budget["hard_input_budget"]
        telemetry.soft_input_target = budget["soft_input_target"]
        telemetry.reserve_tokens = int(budget["reserve_tokens"] or 0)
        telemetry.max_output_tokens = int(budget["max_output_tokens"] or 0)
        telemetry.configured_max_output_tokens = int(
            budget["configured_max_output_tokens"] or 0
        )
        telemetry.history_budget = self.history_budget(telemetry)
        budget_value = telemetry.hard_input_budget or telemetry.available_input_budget
        telemetry.occupancy_ratio = (
            min(1.0, float(telemetry.input_tokens_total) / float(budget_value))
            if isinstance(budget_value, int) and budget_value > 0
            else 0.0
        )
        return telemetry

    def emergency_output_limit(
        self,
        *,
        llm: Any,
        input_tokens: int,
        current_max_output_tokens: int,
    ) -> Optional[int]:
        """Return a smaller valid output cap, or None when even 1k cannot fit."""
        window = self.context_window(llm)
        if window is None:
            return None
        reserve = int(self.resolve_request_budget(llm)["reserve_tokens"] or 0)
        maximum = int(window) - reserve - int(input_tokens)
        minimum = max(1, int(self.config.min_output_reserve_tokens))
        if maximum < minimum:
            return None
        return min(int(current_max_output_tokens), maximum)

    def finalize_input(
        self,
        *,
        llm: Any,
        telemetry: ContextTelemetry,
        history_messages: List[Dict[str, Any]],
        compact_events: List[Dict[str, Any]],
    ) -> ContextTelemetry:
        history_tokens, history_mode = self.count_tokens(history_messages, llm)
        telemetry.history_tokens = history_tokens
        telemetry.input_tokens_total = (
            int(telemetry.system_prompt_tokens)
            + int(telemetry.history_tokens)
            + int(telemetry.prepared_tokens)
        )
        telemetry.history_message_count = len(history_messages)
        telemetry.compact_events = [
            dict(x) for x in compact_events if isinstance(x, dict)
        ]
        telemetry.history_budget = self.history_budget(telemetry)
        budget = telemetry.available_input_budget
        telemetry.occupancy_ratio = 0.0
        if isinstance(budget, int) and budget > 0:
            telemetry.occupancy_ratio = min(
                1.0, float(telemetry.input_tokens_total) / float(budget)
            )
        telemetry.counting_mode = self._merge_counting_mode(
            [telemetry.counting_mode, history_mode]
        )
        return telemetry

    def finalize_assembled_input(
        self,
        *,
        llm: Any,
        telemetry: ContextTelemetry,
        messages: List[Dict[str, Any]],
        compact_events: List[Dict[str, Any]],
    ) -> ContextTelemetry:
        """Account for the exact OpenAI-compatible message payload.

        Custom MessageBuilders can replace the default prompt entirely.  The
        pre-builder estimate is still useful to budget history retrieval, but
        it must never be reported as, or enforced like, the final request.
        """
        system_messages = [m for m in messages if m.get("role") == "system"]
        anchor_messages = [m for m in messages if m.get("role") == "user"]
        history_messages = [
            m for m in messages if m.get("role") in {"assistant", "tool"}
        ]
        system_tokens, system_mode = self.count_tokens(system_messages, llm)
        anchor_tokens, anchor_mode = self.count_tokens(anchor_messages, llm)
        history_tokens, history_mode = self.count_tokens(history_messages, llm)
        input_tokens, input_mode = self.count_tokens(messages, llm)

        telemetry.system_prompt_tokens = system_tokens
        # ``prepared_tokens`` remains the wire-compatible field name; for a
        # custom builder it is the durable task-anchor/user-token count.
        telemetry.prepared_tokens = anchor_tokens
        telemetry.history_tokens = history_tokens
        telemetry.input_tokens_total = input_tokens
        telemetry.history_message_count = len(history_messages)
        telemetry.compact_events = [
            dict(x) for x in compact_events if isinstance(x, dict)
        ]
        telemetry.history_budget = self.history_budget(telemetry)
        budget = telemetry.available_input_budget
        telemetry.occupancy_ratio = 0.0
        if isinstance(budget, int) and budget > 0:
            telemetry.occupancy_ratio = min(
                1.0, float(input_tokens) / float(budget)
            )
        telemetry.counting_mode = self._merge_counting_mode(
            [system_mode, anchor_mode, history_mode, input_mode]
        )
        return telemetry

    def apply_prompt_meter(
        self, telemetry: ContextTelemetry, result: Dict[str, Any] | None
    ) -> ContextTelemetry:
        """Apply an optional provider-native preflight token measurement."""
        if not isinstance(result, dict):
            return telemetry
        telemetry.meter_status = str(result.get("status") or "unavailable")
        telemetry.meter_source = str(result.get("meter_source") or "provider_tokenize")
        telemetry.meter_error = str(result.get("meter_error") or "")[:500]
        planned = result.get("planned_prompt_tokens")
        if isinstance(planned, int) and planned >= 0:
            telemetry.planned_prompt_tokens = planned
            telemetry.input_tokens_total = planned
            budget = telemetry.available_input_budget
            telemetry.occupancy_ratio = (
                min(1.0, float(planned) / float(budget))
                if isinstance(budget, int) and budget > 0 else 0.0
            )
            telemetry.counting_mode = "sglang_tokenize"
        return telemetry

    def finalize_output(
        self,
        *,
        llm: Any,
        telemetry: ContextTelemetry,
        raw_output: Any,
    ) -> ContextTelemetry:
        usage = self._extract_usage(llm)
        if usage is not None:
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")
            total_tokens = usage.get("total_tokens")
            if isinstance(prompt_tokens, int) and prompt_tokens >= 0:
                telemetry.provider_prompt_tokens = int(prompt_tokens)
                if telemetry.planned_prompt_tokens is not None:
                    telemetry.token_estimate_error = int(prompt_tokens) - int(telemetry.planned_prompt_tokens)
                # The provider owns the final prompt measurement.  This is
                # what TUI, trace and cumulative utilization must display.
                telemetry.input_tokens_total = int(prompt_tokens)
                budget = telemetry.available_input_budget
                telemetry.occupancy_ratio = (
                    min(1.0, float(prompt_tokens) / float(budget))
                    if isinstance(budget, int) and budget > 0 else 0.0
                )
                telemetry.counting_mode = "provider_usage"
                telemetry.meter_source = (
                    "sglang_usage" if telemetry.meter_status == "ready" else "provider_usage"
                )
            cached = usage.get("cached_tokens")
            if isinstance(cached, int) and cached >= 0:
                telemetry.cached_tokens = cached
            if isinstance(completion_tokens, int) and completion_tokens >= 0:
                telemetry.provider_completion_tokens = int(completion_tokens)
                telemetry.output_tokens = int(completion_tokens)
            else:
                telemetry.output_tokens = self.count_tokens(raw_output, llm)[0]
            if isinstance(total_tokens, int) and total_tokens >= 0:
                telemetry.provider_total_tokens = int(total_tokens)
                step_total = int(total_tokens)
            else:
                step_total = int(telemetry.provider_prompt_tokens or telemetry.input_tokens_total) + int(
                    telemetry.output_tokens
                )
        else:
            telemetry.output_tokens = self.count_tokens(raw_output, llm)[0]
            step_total = int(telemetry.input_tokens_total) + int(
                telemetry.output_tokens
            )

        self.prompt_tokens_total += int(
            telemetry.provider_prompt_tokens or telemetry.input_tokens_total
        )
        self.completion_tokens_total += int(telemetry.output_tokens)
        self.tokens_total += int(step_total)
        self.peak_input_tokens = max(
            self.peak_input_tokens, int(telemetry.input_tokens_total)
        )
        self.peak_occupancy_ratio = max(
            self.peak_occupancy_ratio, float(telemetry.occupancy_ratio)
        )
        telemetry.prompt_tokens_total = self.prompt_tokens_total
        telemetry.completion_tokens_total = self.completion_tokens_total
        telemetry.tokens_total = self.tokens_total
        telemetry.peak_input_tokens = self.peak_input_tokens
        telemetry.peak_occupancy_ratio = self.peak_occupancy_ratio
        self.last_request = telemetry
        self.engine._token_usage = self.tokens_total
        return telemetry

    def maybe_note_warning(
        self, telemetry: ContextTelemetry
    ) -> Optional[Dict[str, Any]]:
        ratio = float(telemetry.occupancy_ratio or 0.0)
        if ratio < float(self.config.warning_ratio):
            return None
        self.warning_count += 1
        return self._context_event(
            stage="warning",
            telemetry=telemetry,
            detail={
                "before_tokens": telemetry.input_tokens_total,
                "after_tokens": telemetry.input_tokens_total,
                "saved_tokens": 0,
                "messages_before": telemetry.history_message_count,
                "messages_after": telemetry.history_message_count,
                "strategy": "engine_context_monitor",
            },
        )

    def should_overflow(self, telemetry: ContextTelemetry) -> bool:
        if not self.enabled() or not self.config.strict_overflow:
            return False
        budget = telemetry.hard_input_budget or telemetry.available_input_budget
        if not isinstance(budget, int) or budget <= 0:
            return False
        return int(telemetry.input_tokens_total) > int(budget)

    def overflow_event(self, telemetry: ContextTelemetry) -> Dict[str, Any]:
        return {
            "stage": "context_overflow",
            "context": self.telemetry_dict(telemetry),
        }

    def normalize_history_events(
        self,
        events: Iterable[Dict[str, Any]],
        telemetry: ContextTelemetry,
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("stage") == "context_history" and isinstance(
                event.get("context"), dict
            ):
                ctx = dict(event["context"])
                kind = str(ctx.get("stage") or "within_budget")
                if "warning" in kind:
                    self.warning_count += 1
                if kind not in {"within_budget", "warning"}:
                    self.compact_counts[kind] = self.compact_counts.get(kind, 0) + 1
                ctx.setdefault("warning_ratio", float(self.config.warning_ratio))
                ctx.setdefault("occupancy_ratio", telemetry.occupancy_ratio)
                normalized.append({"stage": "context_history", "context": ctx})
                continue
        return normalized

    def telemetry_dict(self, telemetry: ContextTelemetry) -> Dict[str, Any]:
        return {
            "context_window": telemetry.context_window,
            "available_input_budget": telemetry.available_input_budget,
            "hard_input_budget": telemetry.hard_input_budget,
            "soft_input_target": telemetry.soft_input_target,
            "system_prompt_tokens": telemetry.system_prompt_tokens,
            "history_tokens": telemetry.history_tokens,
            "prepared_tokens": telemetry.prepared_tokens,
            "input_tokens_total": telemetry.input_tokens_total,
            "output_tokens": telemetry.output_tokens,
            "provider_prompt_tokens": telemetry.provider_prompt_tokens,
            "provider_completion_tokens": telemetry.provider_completion_tokens,
            "provider_total_tokens": telemetry.provider_total_tokens,
            "planned_prompt_tokens": telemetry.planned_prompt_tokens,
            "cached_tokens": telemetry.cached_tokens,
            "meter_source": telemetry.meter_source,
            "meter_status": telemetry.meter_status,
            "meter_error": telemetry.meter_error,
            "token_estimate_error": telemetry.token_estimate_error,
            "occupancy_ratio": telemetry.occupancy_ratio,
            "warning_threshold_ratio": telemetry.warning_threshold_ratio,
            "counting_mode": telemetry.counting_mode,
            "prompt_tokens_total": telemetry.prompt_tokens_total,
            "completion_tokens_total": telemetry.completion_tokens_total,
            "tokens_total": telemetry.tokens_total,
            "peak_input_tokens": telemetry.peak_input_tokens,
            "peak_occupancy_ratio": telemetry.peak_occupancy_ratio,
            "history_message_count": telemetry.history_message_count,
            "compact_events": list(telemetry.compact_events),
            "reserve_tokens": telemetry.reserve_tokens,
            "max_output_tokens": telemetry.max_output_tokens,
            "configured_max_output_tokens": telemetry.configured_max_output_tokens,
            "history_budget": telemetry.history_budget,
        }

    def run_summary(self) -> Dict[str, Any]:
        return {
            "prompt_tokens_total": self.prompt_tokens_total,
            "completion_tokens_total": self.completion_tokens_total,
            "tokens_total": self.tokens_total,
            "peak_input_tokens": self.peak_input_tokens,
            "peak_occupancy_ratio": self.peak_occupancy_ratio,
            "compact_counts": dict(self.compact_counts),
            "warning_count": self.warning_count,
            "last_request": (
                self.telemetry_dict(self.last_request)
                if self.last_request is not None
                else None
            ),
        }

    def run_meta(self, llm: Any) -> Dict[str, Any]:
        budget = (
            self.resolve_request_budget(llm)
            if llm is not None
            else {
                "context_window": None,
                "reserve_tokens": 0,
                "available_input_budget": None,
                "hard_input_budget": None,
                "soft_input_target": None,
                "max_output_tokens": 0,
            }
        )
        return {
            "context_window": budget.get("context_window"),
            "reserve_tokens": budget.get("reserve_tokens"),
            "available_input_budget": budget.get("available_input_budget"),
            "hard_input_budget": budget.get("hard_input_budget"),
            "soft_input_target": budget.get("soft_input_target"),
            "max_output_tokens": budget.get("max_output_tokens"),
            "configured_max_output_tokens": budget.get("configured_max_output_tokens"),
            "last_effective_max_output_tokens": (
                self.last_request.max_output_tokens
                if self.last_request is not None
                else budget.get("max_output_tokens")
            ),
            "counting_mode": (
                self.last_request.counting_mode
                if self.last_request is not None
                else ("disabled" if llm is None else "hybrid")
            ),
            "warning_ratio": float(self.config.warning_ratio),
            "compact_ratio": float(self.config.compact_ratio),
            "strict_overflow": bool(self.config.strict_overflow),
        }

    def _extract_usage(self, llm: Any) -> Optional[Dict[str, Any]]:
        extractor = getattr(llm, "extract_usage", None)
        if callable(extractor):
            try:
                usage = extractor()
                if isinstance(usage, dict):
                    return usage
            except Exception:
                return None
        return None

    def _merge_counting_mode(self, modes: Iterable[str]) -> str:
        cleaned = [str(m) for m in modes if m and str(m) != "disabled"]
        if not cleaned:
            return "disabled"
        if "provider_usage" in cleaned:
            return "provider_usage"
        if "model_count" in cleaned:
            return "model_count"
        return cleaned[0]

    def _context_event(
        self,
        *,
        stage: str,
        telemetry: ContextTelemetry,
        detail: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload = dict(detail)
        payload.setdefault("budget", telemetry.available_input_budget)
        payload.setdefault("pending_tokens", telemetry.prepared_tokens)
        payload.setdefault("messages_before", telemetry.history_message_count)
        payload.setdefault("messages_after", telemetry.history_message_count)
        payload.setdefault("warning_ratio", float(self.config.warning_ratio))
        payload.setdefault("occupancy_ratio", telemetry.occupancy_ratio)
        payload.setdefault("context_window", telemetry.context_window)
        return {"stage": "context_history", "context": {"stage": stage, **payload}}


__all__ = ["ContextOverflowError", "_ContextRuntime"]
