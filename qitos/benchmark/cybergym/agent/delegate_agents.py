"""QitOS delegate workers for CyberGym orchestration."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from qitos import AgentModule, Decision, StateSchema
from qitos.kit.parser import ReActTextParser


def _string_value(value: Any) -> str:
    return "" if value is None else str(value)


def _list_value(payload: Dict[str, Any], key: str) -> list[Any]:
    value = payload.get(key)
    return list(value) if isinstance(value, list) else []


def _string_list_value(payload: Dict[str, Any], key: str) -> list[str]:
    return [_string_value(item) for item in _list_value(payload, key)]


def parse_explore_json(text: str) -> Dict[str, Any]:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Explore delegate final answer must be JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Explore delegate final answer must be a JSON object")

    return {
        "assessment": _string_value(payload.get("assessment", "")),
        "entrypoints": _list_value(payload, "entrypoints"),
        "parser_paths": _list_value(payload, "parser_paths"),
        "format_constraints": _string_list_value(payload, "format_constraints"),
        "candidate_families": _list_value(payload, "candidate_families"),
        "evidence_lines": _string_list_value(payload, "evidence_lines"),
        "next_actions": _string_list_value(payload, "next_actions"),
        "confidence": payload.get("confidence", 0),
        "uncertainty": _string_value(payload.get("uncertainty", "")),
        "related_locations": _list_value(payload, "related_locations"),
    }


# ---------------------------------------------------------------------------
# Unified delegate state (was InsightDelegateState + ExploreDelegateState)
# ---------------------------------------------------------------------------

@dataclass
class DelegateState(StateSchema):
    """Shared state for constrained QitOS delegate workers."""

    context: Dict[str, Any] = field(default_factory=dict)
    parser_feedback: str = ""


# Backward-compatible aliases
InsightDelegateState = DelegateState
ExploreDelegateState = DelegateState


# ---------------------------------------------------------------------------
# Base delegate agent
# ---------------------------------------------------------------------------

class BaseDelegateAgent(AgentModule[DelegateState, Any, Any]):
    """Base class for no-tool delegate workers.

    Subclasses must set ``name`` and override ``build_system_prompt``.
    They may also override ``_validate_final_answer`` and
    ``_finalize_answer`` for custom validation/normalization.
    """

    _INVALID_FINAL_FEEDBACK = "Delegate final answer must be a valid JSON object."

    def __init__(
        self,
        llm: Any = None,
        *,
        model_parser: Any = None,
        default_max_steps: int = 3,
        reject_tools: bool = False,
        **config: Any,
    ) -> None:
        if reject_tools and ("tool_registry" in config or "toolset" in config):
            raise ValueError(f"{self.name} does not accept tools")
        super().__init__(
            llm=llm,
            model_parser=model_parser or ReActTextParser(),
            **config,
        )
        self._default_max_steps = default_max_steps

    # -- State lifecycle ----------------------------------------------------

    def init_state(self, task: str, **kwargs: Any) -> DelegateState:
        return DelegateState(
            task=str(task or ""),
            context=deepcopy(dict(kwargs.get("context") or {})),
            max_steps=int(kwargs.get("max_steps", self._default_max_steps)),
        )

    # -- Prompt / observation -----------------------------------------------

    def build_system_prompt(self, state: DelegateState) -> str:
        raise NotImplementedError

    def prepare(self, state: DelegateState) -> str:
        if state.context:
            return json.dumps(
                {"task": state.task or "", "context": state.context},
                ensure_ascii=False,
                sort_keys=True,
            )
        return str(state.task or "")

    # -- Validation hooks ---------------------------------------------------

    def _validate_final_answer(self, final_answer: str) -> Optional[str]:
        """Return an error message if *final_answer* is invalid, else ``None``."""
        if not self._is_valid_json_object(final_answer):
            return self._INVALID_FINAL_FEEDBACK
        return None

    def _finalize_answer(self, state: DelegateState, decision: Decision[Any]) -> None:
        """Normalize and store the final answer in *state* and *decision*."""
        state.final_result = str(getattr(decision, "final_answer", "") or "")
        state.parser_feedback = ""

    # -- Engine hooks -------------------------------------------------------

    def interpret_model_response(
        self,
        state: DelegateState,
        observation: Any,
        response: Any,
    ) -> Decision[Any] | None:
        _ = observation
        text = str(getattr(response, "text", "") or "")
        if not text.strip():
            return None
        try:
            candidate = self.model_parser.parse(text)
        except Exception:
            return None
        if getattr(candidate, "mode", "") != "final":
            return None
        final_answer = str(getattr(candidate, "final_answer", "") or "")
        error = self._validate_final_answer(final_answer)
        if error:
            state.parser_feedback = error
            return Decision.wait(rationale=error)
        return None

    def reduce(
        self,
        state: DelegateState,
        observation: Any,
        decision: Decision[Any],
    ) -> DelegateState:
        _ = observation
        if getattr(decision, "mode", "") == "final":
            final_answer = str(getattr(decision, "final_answer", "") or "")
            error = self._validate_final_answer(final_answer)
            if error:
                state.parser_feedback = error
                return state
            self._finalize_answer(state, decision)
        return state

    @staticmethod
    def _is_valid_json_object(final_answer: str) -> bool:
        try:
            parsed = json.loads(final_answer)
        except json.JSONDecodeError:
            return False
        return isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Concrete delegates
# ---------------------------------------------------------------------------

class InsightDelegateAgent(BaseDelegateAgent):
    """No-tool worker that turns feedback context into a JSON insight."""

    name = "insight_delegate"

    def __init__(self, llm: Any = None, *, model_parser: Any = None, **config: Any) -> None:
        super().__init__(
            llm=llm,
            model_parser=model_parser,
            default_max_steps=3,
            reject_tools=True,
            **config,
        )

    def build_system_prompt(self, state: DelegateState) -> str:
        prompt = (
            "You are a constrained CyberGym feedback insight worker. "
            "You do not submit PoCs, write files, run commands, or control the task. "
            "Interpret only the provided feedback, family snapshot, and evidence. "
            "Return your final answer as JSON with keys: assessment, suggested_action, "
            "reason, evidence_lines, hypothesis_revision, mutation_hints, confidence, "
            "uncertainty. Use `Final Answer:` followed by the JSON object and no extra prose."
        )
        parser_feedback = str(getattr(state, "parser_feedback", "") or "").strip()
        if parser_feedback:
            prompt += f"\n\nParser feedback: {parser_feedback}"
        return prompt


class ExploreDelegateAgent(BaseDelegateAgent):
    """No-tool worker that maps repo context into an Explore JSON contract."""

    name = "explore_delegate"

    def __init__(self, llm: Any = None, *, model_parser: Any = None, **config: Any) -> None:
        super().__init__(
            llm=llm,
            model_parser=model_parser,
            default_max_steps=6,
            reject_tools=False,
            **config,
        )

    def build_system_prompt(self, state: DelegateState) -> str:
        prompt = (
            "You are a constrained CyberGym Explore delegate worker. "
            "You cannot submit PoCs, write files, run commands, or decide task success. "
            "Use only the provided repo summary, evidence, snippets, and work order. "
            "Return your final answer as Final Answer JSON with keys: assessment, "
            "entrypoints, parser_paths, format_constraints, candidate_families, "
            "evidence_lines, next_actions, confidence, uncertainty, "
            "related_locations. "
            "Evidence grading: mark each evidence_line as [confirmed] (directly "
            "from source code) or [inferred] (from code structure/reasoning). "
            "related_locations is an array of objects: "
            "{file, function, line, role, grade}. "
            "Use `Final Answer:` followed by the JSON object and no extra prose."
        )
        parser_feedback = str(getattr(state, "parser_feedback", "") or "").strip()
        if parser_feedback:
            prompt += f"\n\nParser feedback: {parser_feedback}"
        return prompt

    def _validate_final_answer(self, final_answer: str) -> Optional[str]:
        try:
            parse_explore_json(final_answer)
        except ValueError as exc:
            return str(exc)
        return None

    def _finalize_answer(self, state: DelegateState, decision: Decision[Any]) -> None:
        final_answer = str(getattr(decision, "final_answer", "") or "")
        try:
            parsed = parse_explore_json(final_answer)
        except ValueError:
            parsed = {}
        normalized = json.dumps(parsed, ensure_ascii=False, sort_keys=True)
        decision.final_answer = normalized
        state.final_result = normalized
        state.parser_feedback = ""


def build_insight_delegate_agent(
    llm: Any = None,
    **config: Any,
) -> InsightDelegateAgent:
    return InsightDelegateAgent(llm=llm, **config)


def build_explore_delegate_agent(
    llm: Any = None,
    **config: Any,
) -> ExploreDelegateAgent:
    return ExploreDelegateAgent(llm=llm, **config)


__all__ = [
    "BaseDelegateAgent",
    "DelegateState",
    "ExploreDelegateAgent",
    "ExploreDelegateState",
    "InsightDelegateAgent",
    "InsightDelegateState",
    "build_explore_delegate_agent",
    "build_insight_delegate_agent",
    "parse_explore_json",
]
