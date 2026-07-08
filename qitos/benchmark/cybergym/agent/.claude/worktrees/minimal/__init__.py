"""CyberGym PoC agent exports.

The public API is loaded lazily so offline analysis helpers can be imported
without importing the QitOS runtime.  Attribute access remains backwards
compatible with the previous eager re-exports.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

from .versioning import AGENT_VERSION, AGENT_VERSION_LABEL, QITOS_COMPATIBILITY


_LAZY_EXPORTS = {
    "CyberGymState": (".state", "CyberGymState"),
    "ChainNode": (".state", "ChainNode"),
    "ChainGate": (".state", "ChainGate"),
    "CyberGymAgent": (".agent", "CyberGymAgent"),
    "SnipCompactor": (".context", "SnipCompactor"),
    "CompactionCircuitBreaker": (".context", "CompactionCircuitBreaker"),
    "PostCompactRestorer": (".context", "PostCompactRestorer"),
    "CyberGymContextHistory": (".context", "CyberGymContextHistory"),
    "SubmitPoCTool": (".submit_tool", "SubmitPoCTool"),
    "RecordHypothesisTool": (".tracking_tools", "RecordHypothesisTool"),
    "RecordAttemptTool": (".tracking_tools", "RecordAttemptTool"),
    "RecordReflectionTool": (".tracking_tools", "RecordReflectionTool"),
    "RecordChainNodeTool": (".tracking_tools", "RecordChainNodeTool"),
    "RecordGateTool": (".tracking_tools", "RecordGateTool"),
    "RecordSinkCandidateTool": (".tracking_tools", "SinkTool"),
    "SinkTool": (".tracking_tools", "SinkTool"),
    "AnalyzeDescriptionTool": (".tracking_tools", "AnalyzeDescriptionTool"),
    "SetCrashTypeTool": (".tracking_tools", "SetCrashTypeTool"),
    "AnalysisService": (".analysis", "AnalysisService"),
    "AnalysisConfig": (".analysis", "AnalysisConfig"),
    "CyberGymEnv": (".env", "CyberGymEnv"),
    "CyberGymAdapter": (".adapter", "CyberGymAdapter"),
    "PoCVerificationCriteria": (".stop_criteria", "PoCVerificationCriteria"),
    "run_local": (".run_local", "run_local"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


if TYPE_CHECKING:
    from .adapter import CyberGymAdapter
    from .agent import CyberGymAgent
    from .analysis import AnalysisConfig, AnalysisService
    from .context import (
        CompactionCircuitBreaker,
        CyberGymContextHistory,
        PostCompactRestorer,
        SnipCompactor,
    )
    from .env import CyberGymEnv
    from .run_local import run_local
    from .state import ChainGate, ChainNode, CyberGymState
    from .stop_criteria import PoCVerificationCriteria
    from .submit_tool import SubmitPoCTool
    from .tracking_tools import (
        AnalyzeDescriptionTool,
        RecordAttemptTool,
        RecordChainNodeTool,
        RecordGateTool,
        RecordHypothesisTool,
        RecordReflectionTool,
        RecordSinkCandidateTool,
        SinkTool,
        SetCrashTypeTool,
    )


__all__ = [
    "AGENT_VERSION",
    "AGENT_VERSION_LABEL",
    "QITOS_COMPATIBILITY",
    *_LAZY_EXPORTS,
]
