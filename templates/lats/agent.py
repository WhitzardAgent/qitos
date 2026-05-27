"""LATS multi-agent template — Monte Carlo Tree Search with language evaluation.

Provides configuration and registry setup for the LATS pattern where:
- An agent explores a solution tree using MCTS
- LLM-powered evaluation scores each node
- Self-reflection guides exploration away from failed paths

Usage:
    from templates.lats.agent import LATSConfig, build_lats_registry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from qitos.core.agent_spec import AgentSpec, ContextStrategy, HandoffContext, AgentRegistry
from qitos.core.shared_memory import InMemorySharedMemory, SharedMemoryManager


@dataclass
class LATSConfig:
    """Configuration for the LATS tree search pattern."""

    agent_name: str = "lats_searcher"
    max_simulations: int = 5
    exploration_weight: float = 1.41
    max_depth: int = 7
    context_strategy: str = "summary"
    shared_memory_fields: List[str] = field(default_factory=lambda: ["tree_state", "reflections"])


def build_lats_registry(config: LATSConfig) -> tuple[AgentRegistry, SharedMemoryManager]:
    """Build an AgentRegistry and SharedMemoryManager for the LATS pattern.

    The caller must register a concrete AgentModule instance with the
    returned spec before running the engine.

    Returns:
        (registry, shared_memory_manager)
    """
    shared_memory = SharedMemoryManager(InMemorySharedMemory())
    registry = AgentRegistry()

    registry.register(AgentSpec(
        name=config.agent_name,
        description="Explores solution space using tree search with language evaluation",
        agent=None,  # Caller must provide a concrete agent
        context_strategy=ContextStrategy.SUMMARY,
        handoff_context=HandoffContext(
            strategy=ContextStrategy.SUMMARY,
            shared_state_fields=config.shared_memory_fields,
        ),
    ))

    return registry, shared_memory
