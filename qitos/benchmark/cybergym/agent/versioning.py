"""Agent version and execution mode metadata."""

from enum import Enum


AGENT_VERSION = "0.3.0"
AGENT_VERSION_LABEL = "agent-v0.3.0-orchestrator-alpha"
QITOS_COMPATIBILITY = "0.6"


class AgentMode(str, Enum):
    CLASSIC = "classic"


_MODE_ALIASES = {
    "": AgentMode.CLASSIC,
    "classic": AgentMode.CLASSIC,
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
