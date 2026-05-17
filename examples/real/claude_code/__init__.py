"""Claude Code — full-functionality QitOS replication of Claude Code.

This example demonstrates how to build a Claude Code-equivalent AI coding
assistant using the QitOS framework.  It wires together:

- ClaudeCodeAgent (AgentModule subclass with plan mode, read-before-write, etc.)
- Full tool system (30+ tools at Claude Code parity)
- Permission pipeline (multi-tier deny/ask/allow + auto-classifier)
- MCP client integration
- Streaming REPL
- Hook system
- Project instructions (.qitos/instructions.md)
"""

from .agent import ClaudeCodeAgent, ClaudeCodeState
from .cli import main

__all__ = ["ClaudeCodeAgent", "ClaudeCodeState", "main"]
