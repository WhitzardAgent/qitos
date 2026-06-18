"""QitOS Workflow Integration Layer.

This module integrates qitos-dag with QitOS, providing workflow nodes
that leverage QitOS's AgentModule, Engine, ToolRegistry, and qita
tracing infrastructure.

Bidirectional DAG-Engine bridge:
- DAG → Engine: AgentNode runs Engine with full injection (shared_memory,
  tracing_provider, agent_registry, hooks)
- Engine → DAG: WorkflowTool triggers DAG workflows from agent tools
- Shared: VariablePool, SharedMemory, Tracing, Events bridged across
"""

from __future__ import annotations

import importlib

_EXPORTS = {
    "QitosNodeFactory": ".factory",
    "WorkflowRunner": ".runner",
    "SharedMemoryAdapter": ".adapter",
    "EngineToDagHook": ".event_bridge",
    "DagToEngineLayer": ".event_bridge",
    "WorkflowRegistry": ".registry",
    "WorkflowSpec": ".registry",
}


def __getattr__(name: str):
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("qitos_dag"):
            raise ModuleNotFoundError(
                "qitos.workflow requires the optional workflow extra. "
                'Install it with `pip install "qitos[workflow]"`.'
            ) from exc
        raise
    value = getattr(module, name)
    globals()[name] = value
    return value

__all__ = [
    "QitosNodeFactory",
    "WorkflowRunner",
    "SharedMemoryAdapter",
    "EngineToDagHook",
    "DagToEngineLayer",
    "WorkflowRegistry",
    "WorkflowSpec",
]
