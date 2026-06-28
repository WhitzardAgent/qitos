"""Agent version and execution mode metadata."""

from enum import Enum


AGENT_VERSION = "0.3.0"
AGENT_VERSION_LABEL = "agent-v0.3.0-orchestrator-alpha"
QITOS_COMPATIBILITY = "0.6"


class AgentMode(str, Enum):
    CLASSIC = "classic"
    DELEGATE_INSIGHT = "delegate_insight"
    DELEGATE_EXPLORE = "delegate_explore"
    MULTI_AGENT_ALPHA = "multi_agent_alpha"
    MULTI_AGENT_FULL = "multi_agent_full"


_MODE_ALIASES = {
    "": AgentMode.CLASSIC,
    "classic": AgentMode.CLASSIC,
    "delegate": AgentMode.DELEGATE_INSIGHT,
    "delegate-insight": AgentMode.DELEGATE_INSIGHT,
    "delegate_insight": AgentMode.DELEGATE_INSIGHT,
    "qitos-delegate-insight": AgentMode.DELEGATE_INSIGHT,
    "delegate-explore": AgentMode.DELEGATE_EXPLORE,
    "delegate_explore": AgentMode.DELEGATE_EXPLORE,
    "multi-agent-alpha": AgentMode.MULTI_AGENT_ALPHA,
    "multi_agent_alpha": AgentMode.MULTI_AGENT_ALPHA,
    "multi-agent-full": AgentMode.MULTI_AGENT_FULL,
    "multi_agent_full": AgentMode.MULTI_AGENT_FULL,
}


def normalize_agent_mode(value):
    if isinstance(value, AgentMode):
        return value
    if value is None:
        return AgentMode.CLASSIC
    normalized = str(value).strip().lower()
    if normalized in _MODE_ALIASES:
        return _MODE_ALIASES[normalized]
    raise ValueError(f"Unsupported agent mode: {value}")


def mode_uses_qitos_delegate(mode):
    return normalize_agent_mode(mode) in {
        AgentMode.DELEGATE_INSIGHT,
        AgentMode.DELEGATE_EXPLORE,
        AgentMode.MULTI_AGENT_ALPHA,
        AgentMode.MULTI_AGENT_FULL,
    }
