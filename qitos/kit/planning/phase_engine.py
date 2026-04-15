"""Declarative phase state machine for multi-stage agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional


ConditionFn = Callable[[Any], bool]


@dataclass(frozen=True)
class TransitionRule:
    """One transition rule from the current phase to a target phase."""

    target: str
    condition: Optional[ConditionFn] = None
    force_at_step: Optional[int] = None
    priority: int = 0

    def is_condition_met(self, state: Any) -> bool:
        if self.condition is None:
            return False
        try:
            return bool(self.condition(state))
        except Exception:
            return False

    def is_forced(self, step: int) -> bool:
        return self.force_at_step is not None and int(step) >= int(self.force_at_step)


@dataclass(frozen=True)
class PhaseSpec:
    """Configuration for one named phase."""

    name: str
    transitions: List[TransitionRule] = field(default_factory=list)
    max_steps: Optional[int] = None
    prompt_template: Optional[str] = None


class PhaseEngine:
    """Evaluate transition rules and keep phase movement deterministic."""

    def __init__(
        self,
        phases: Iterable[PhaseSpec],
        *,
        initial_phase: str | None = None,
        state_attr: str = "current_phase",
    ):
        phase_items = list(phases)
        if not phase_items:
            raise ValueError("PhaseEngine requires at least one PhaseSpec")
        self._phase_map: Dict[str, PhaseSpec] = {}
        for spec in phase_items:
            key = str(spec.name or "").strip()
            if not key:
                raise ValueError("Phase name cannot be empty")
            if key in self._phase_map:
                raise ValueError(f"Duplicate phase name: '{key}'")
            self._phase_map[key] = spec
        self.initial_phase = (
            str(initial_phase).strip()
            if initial_phase is not None
            else str(phase_items[0].name)
        )
        if self.initial_phase not in self._phase_map:
            raise ValueError(f"Unknown initial phase: '{self.initial_phase}'")
        self.state_attr = str(state_attr or "current_phase")

    @property
    def phases(self) -> Dict[str, PhaseSpec]:
        return dict(self._phase_map)

    def current_phase(self, state: Any) -> str:
        value = getattr(state, self.state_attr, None)
        phase = str(value or self.initial_phase).strip() or self.initial_phase
        if phase not in self._phase_map:
            return self.initial_phase
        return phase

    def advance(self, state: Any, step: int) -> str:
        current = self.current_phase(state)
        spec = self._phase_map[current]
        ordered = self._ordered_rules(spec.transitions)

        for rule in ordered:
            if rule.is_condition_met(state):
                return self._set_phase(state, rule.target)

        for rule in ordered:
            if rule.is_forced(step):
                return self._set_phase(state, rule.target)

        if spec.max_steps is not None and int(step) >= int(spec.max_steps):
            for rule in ordered:
                return self._set_phase(state, rule.target)

        return self._set_phase(state, current)

    def get_prompt_section(self, state: Any, step: int) -> str:
        current = self.current_phase(state)
        spec = self._phase_map[current]
        sections: List[str] = []
        if spec.prompt_template:
            sections.append(str(spec.prompt_template).strip())
        urgency = self._urgency_notice(spec, step)
        if urgency:
            sections.append(urgency)
        return "\n\n".join([item for item in sections if item]).strip()

    def _urgency_notice(self, spec: PhaseSpec, step: int) -> str:
        notices: List[str] = []
        for rule in self._ordered_rules(spec.transitions):
            if rule.force_at_step is None:
                continue
            remaining = int(rule.force_at_step) - int(step)
            if remaining <= 2:
                notices.append(
                    f"Phase transition pressure: switch to '{rule.target}' in {max(0, remaining)} step(s)."
                )
        if spec.max_steps is not None:
            remaining = int(spec.max_steps) - int(step)
            if remaining <= 2:
                notices.append(
                    f"Phase '{spec.name}' max step budget nearly exhausted ({max(0, remaining)} step(s) left)."
                )
        return "\n".join(notices)

    def _ordered_rules(self, rules: List[TransitionRule]) -> List[TransitionRule]:
        return sorted(
            [r for r in list(rules or []) if isinstance(r, TransitionRule)],
            key=lambda item: int(item.priority),
            reverse=True,
        )

    def _set_phase(self, state: Any, target: str) -> str:
        phase = str(target or "").strip()
        if phase not in self._phase_map:
            return self.initial_phase
        if hasattr(state, self.state_attr):
            setattr(state, self.state_attr, phase)
        return phase


__all__ = ["PhaseEngine", "PhaseSpec", "TransitionRule"]
