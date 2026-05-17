"""Parameter sweep utilities for Experiment Runner."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class SweepSpec:
    """Declares a parameter sweep over agent configuration keys.

    Each key is a dotted path into the agent config (e.g. ``model.temperature``
    or ``max_steps``). The value is a list of candidate values.

    Example::

        sweep = SweepSpec(params={
            "model.temperature": [0.0, 0.2, 0.5, 0.8],
            "max_steps": [5, 10],
        })
        for combo in sweep_product(sweep):
            print(combo)
        # {"model.temperature": 0.0, "max_steps": 5}
        # {"model.temperature": 0.0, "max_steps": 10}
        # {"model.temperature": 0.2, "max_steps": 5}
        # ...
    """

    params: Dict[str, List[Any]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """Return True if no sweep parameters are defined."""
        return not self.params


def sweep_product(sweep: SweepSpec) -> List[Dict[str, Any]]:
    """Expand a SweepSpec into the Cartesian product of all parameter values.

    Returns a list of dicts, each representing one parameter combination.
    If the sweep is empty, returns a single empty dict (one trivial run).
    """
    if sweep.is_empty():
        return [{}]

    keys = list(sweep.params.keys())
    value_lists = [sweep.params[k] for k in keys]
    combinations: List[Dict[str, Any]] = []
    for values in itertools.product(*value_lists):
        combinations.append(dict(zip(keys, values)))
    return combinations


__all__ = ["SweepSpec", "sweep_product"]
