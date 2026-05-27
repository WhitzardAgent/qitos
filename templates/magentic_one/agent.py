"""Magentic-One multi-agent template — orchestrator with specialist workers.

Provides configuration and registry setup for the Magentic-One pattern where:
- An orchestrator maintains a Task Ledger and Fact Bank
- Specialist agents handle specific subtask types
- The orchestrator tracks progress and re-plans when stuck

Usage:
    from templates.magentic_one.agent import MagenticOneConfig, build_magentic_one_registry
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from qitos.core.agent_spec import AgentSpec, ContextStrategy, HandoffContext, AgentRegistry
from qitos.core.shared_memory import InMemorySharedMemory, SharedMemoryManager


@dataclass
class SpecialistDef:
    """Definition of a specialist agent."""

    name: str
    description: str
    capabilities: List[str] = field(default_factory=list)


@dataclass
class MagenticOneConfig:
    """Configuration for the Magentic-One pattern."""

    orchestrator_name: str = "orchestrator"
    specialists: List[SpecialistDef] = field(default_factory=lambda: [
        SpecialistDef(name="coder", description="Writes and executes code", capabilities=["code", "analysis"]),
        SpecialistDef(name="researcher", description="Searches and reads information", capabilities=["search", "read"]),
    ])
    max_stalls: int = 3
    context_strategy: str = "summary"
    shared_memory_fields: List[str] = field(default_factory=lambda: [
        "fact_bank", "task_ledger", "completed_tasks", "progress_ledger"
    ])


def build_magentic_one_registry(config: MagenticOneConfig) -> tuple[AgentRegistry, SharedMemoryManager]:
    """Build an AgentRegistry and SharedMemoryManager for the Magentic-One pattern.

    The caller must register concrete AgentModule instances with the
    returned specs before running the engine.

    Returns:
        (registry, shared_memory_manager)
    """
    shared_memory = SharedMemoryManager(InMemorySharedMemory())
    registry = AgentRegistry()

    # Register orchestrator with full context
    registry.register(AgentSpec(
        name=config.orchestrator_name,
        description="Plans, delegates, tracks progress, and re-plans when stuck",
        agent=None,
        context_strategy=ContextStrategy.FULL,
        handoff_context=HandoffContext(
            strategy=ContextStrategy.FULL,
            shared_state_fields=config.shared_memory_fields,
        ),
    ))

    # Register specialist agents
    for specialist in config.specialists:
        registry.register(AgentSpec(
            name=specialist.name,
            description=specialist.description,
            agent=None,
            context_strategy=ContextStrategy.SUMMARY,
            handoff_context=HandoffContext(
                strategy=ContextStrategy.SUMMARY,
                shared_state_fields=config.shared_memory_fields,
            ),
        ))

    return registry, shared_memory
