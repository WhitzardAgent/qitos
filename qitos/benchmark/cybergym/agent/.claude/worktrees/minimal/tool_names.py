"""Canonical tool name constants for the Minimal CyberGym agent."""

# ---------------------------------------------------------------------------
# Core tools (from CodingToolSet)
# ---------------------------------------------------------------------------

READ = "read"
GREP = "grep"
GLOB = "glob"
WRITE = "write"
BASH = "bash"

# ---------------------------------------------------------------------------
# Domain tools
# ---------------------------------------------------------------------------

GDB = "GDB"
SINK = "SINK"
GATE = "GATE"
SUBMIT_POC = "submit_poc"

# ---------------------------------------------------------------------------
# Aggregate sets
# ---------------------------------------------------------------------------

READ_ONLY_TOOLS = frozenset({READ, GREP, GLOB})
WRITE_TOOLS = frozenset({WRITE})
TRACKING_TOOLS = frozenset({SINK, GATE})
