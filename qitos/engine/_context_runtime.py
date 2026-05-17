"""Private context-length telemetry helpers for Engine."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, Iterable, List, Optional

from .states import ContextConfig, ContextTelemetry


class ContextOverflowError(RuntimeError):
    """Raised when one model request still exceeds the effective input budget."""


class _ContextRuntime:
    def __init__(self, engine: Any):
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

    def resolve_request_budget(self, llm: Any) -> Dict[str, Any]:
        window = self.context_window(llm)
        max_output = int(getattr(llm, "max_tokens", 0) or 0)
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
            available = max(1, window - max_output - reserve)
            target = max(
                1,
                int(float(window) * float(self.config.target_utilization)) - max_output,
            )
            available = min(available, target)
        else:
            available = None
        return {
            "context_window": window,
            "max_output_tokens": max_output,
            "reserve_tokens": reserve,
            "available_input_budget": available,
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
            system_prompt_tokens=system_tokens,
            prepared_tokens=prepared_tokens,
            warning_threshold_ratio=float(self.config.warning_ratio),
            counting_mode=self._merge_counting_mode([system_mode, prepared_mode]),
            reserve_tokens=int(budget["reserve_tokens"] or 0),
            max_output_tokens=int(budget["max_output_tokens"] or 0),
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
                telemetry.input_tokens_total = int(prompt_tokens)
                budget = telemetry.available_input_budget
                if isinstance(budget, int) and budget > 0:
                    telemetry.occupancy_ratio = min(
                        1.0, float(telemetry.input_tokens_total) / float(budget)
                    )
            if isinstance(completion_tokens, int) and completion_tokens >= 0:
                telemetry.output_tokens = int(completion_tokens)
            else:
                telemetry.output_tokens = self.count_tokens(raw_output, llm)[0]
            if isinstance(total_tokens, int) and total_tokens >= 0:
                step_total = int(total_tokens)
            else:
                step_total = int(telemetry.input_tokens_total) + int(
                    telemetry.output_tokens
                )
            telemetry.counting_mode = "provider_usage"
        else:
            telemetry.output_tokens = self.count_tokens(raw_output, llm)[0]
            step_total = int(telemetry.input_tokens_total) + int(
                telemetry.output_tokens
            )

        self.prompt_tokens_total += int(telemetry.input_tokens_total)
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
        budget = telemetry.available_input_budget
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
            "system_prompt_tokens": telemetry.system_prompt_tokens,
            "history_tokens": telemetry.history_tokens,
            "prepared_tokens": telemetry.prepared_tokens,
            "input_tokens_total": telemetry.input_tokens_total,
            "output_tokens": telemetry.output_tokens,
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
                "max_output_tokens": 0,
            }
        )
        return {
            "context_window": budget.get("context_window"),
            "reserve_tokens": budget.get("reserve_tokens"),
            "available_input_budget": budget.get("available_input_budget"),
            "max_output_tokens": budget.get("max_output_tokens"),
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
