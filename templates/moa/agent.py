"""MoA multi-agent template — parallel proposals and aggregation.

Provides configuration and registry setup for the Mixture-of-Agents pattern where:
- Multiple proposers independently generate responses
- An aggregator synthesizes proposals into a final answer

Usage:
    from templates.moa.agent import MoAConfig, build_moa_registry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from qitos.core.agent_spec import AgentSpec, ContextStrategy, HandoffContext, AgentRegistry
from qitos.core.shared_memory import InMemorySharedMemory, SharedMemoryManager


@dataclass
class ProposerDef:
    """Definition of a proposer agent."""

    name: str
    description: str
    perspective: str = ""  # Unique perspective or role description


@dataclass
class MoAConfig:
    """Configuration for the Mixture-of-Agents pattern."""

    aggregator_name: str = "aggregator"
    proposers: List[ProposerDef] = field(default_factory=lambda: [
        ProposerDef(name="analyst_a", description="Independent analyst A"),
        ProposerDef(name="analyst_b", description="Independent analyst B"),
        ProposerDef(name="analyst_c", description="Independent analyst C"),
    ])
    proposer_max_steps: int = 5
    aggregator_max_steps: int = 10
    context_strategy: str = "isolated"
    shared_memory_fields: List[str] = field(default_factory=lambda: ["proposals", "synthesis"])


def build_moa_registry(config: MoAConfig) -> tuple[AgentRegistry, SharedMemoryManager]:
    """Build an AgentRegistry and SharedMemoryManager for the MoA pattern.

    The caller must register concrete AgentModule instances with the
    returned specs before running the engine.

    Returns:
        (registry, shared_memory_manager)
    """
    shared_memory = SharedMemoryManager(InMemorySharedMemory())
    registry = AgentRegistry()

    # Register proposer agents with isolated context
    for proposer in config.proposers:
        registry.register(AgentSpec(
            name=proposer.name,
            description=proposer.description,
            agent=None,
            context_strategy=ContextStrategy.ISOLATED,
            handoff_context=HandoffContext(
                strategy=ContextStrategy.ISOLATED,
                shared_state_fields=config.shared_memory_fields,
            ),
        ))

    # Register aggregator with full context
    registry.register(AgentSpec(
        name=config.aggregator_name,
        description="Synthesizes proposals into a unified answer",
        agent=None,
        context_strategy=ContextStrategy.FULL,
        handoff_context=HandoffContext(
            strategy=ContextStrategy.FULL,
            shared_state_fields=config.shared_memory_fields + ["verdict"],
        ),
    ))

    return registry, shared_memory
