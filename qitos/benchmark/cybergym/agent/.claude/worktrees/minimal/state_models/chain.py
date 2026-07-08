"""Chain and path constraint data models for the CyberGym PoC Generation Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class PathConstraint:
    """One evidence-backed or open condition on the entry-to-sink path.

    DEPRECATED: retained for serialization compat.  New code should use
    ChainNode + ChainGate instead.
    """

    description: str
    source_location: str = ""
    status: str = "unknown"  # confirmed | hypothesized | unknown
    required_values: str = ""
    constraint_type: str = "path_gate"


@dataclass
class ChainNode:
    """One node in the ordered entry-to-sink call chain.

    Nodes are ordered from harness entry (order=0) to the vulnerability
    sink (highest order).  Each node records the function, its role in
    the data-flow chain, and whether the agent has confirmed it from
    source code.
    """

    location: str        # e.g. "attribute.c:1880"
    function: str        # e.g. "GenerateEXIFAttribute"
    role: str            # "entry" | "parser" | "dispatch" | "guard" | "sink"
    description: str     # e.g. "IFD entry parsing loop"
    status: str          # "confirmed" | "inferred" | "unknown"
    evidence: str        # e.g. "READ attribute.c:1870-1910"
    order: int           # Position in chain (0 = harness entry)
    sink_id: str = ""    # Links node to a specific SinkCandidate; empty = unassigned


@dataclass
class ChainGate:
    """A condition at a ChainNode that input must satisfy to reach the sink.

    Gates represent **positive constraints**: "what must be true" for the
    PoC to pass through this point in the call chain.  When a submission
    fails, gates are *refuted* (not deleted) so the agent learns from
    failures and derives repair hints.
    """

    node_order: int      # Which ChainNode this gate belongs to
    gate_type: str       # "format_gate" | "path_gate" | "dispatch_gate" | "bounds_gate" | "value_gate"
    description: str     # e.g. "Must match 'Exif\\0\\0' magic (memcmp at attribute.c:1865)"
    required_condition: str  # Positive condition for PoC construction
    status: str          # "confirmed" | "inferred" | "refuted" | "bypassed" | "questioned"
    evidence: str        # e.g. "READ attribute.c:1887 — overflow detection present"
    repair_hint: str     # e.g. "Try oval+n wrap-around instead of n=0"
    # Added by the Level-1 constraint analyzer.  Defaults keep old serialized
    # states loadable without migrations.
    role: str = "reachability"
    path_id: str = ""
    source_span: Dict[str, Any] = field(default_factory=dict)
    sink_id: str = ""    # Links gate to a specific SinkCandidate; empty = unassigned
    input_mapping_id: str = ""
