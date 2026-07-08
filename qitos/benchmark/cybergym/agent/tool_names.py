"""Canonical tool name constants for the CyberGym agent.

All tool names use snake_case. Old name strings are preserved as
aliases in result_processors.py and render.py for backward compat
with saved trajectories.

Usage:
    from tool_names import READ, SUBMIT_POC, EVIDENCE_TOOLS
"""

# ---------------------------------------------------------------------------
# File / code tools
# ---------------------------------------------------------------------------

READ = "read"
GREP = "grep"
GLOB = "glob"
WRITE = "write"
BASH = "bash"

# ---------------------------------------------------------------------------
# Evidence / inspection tools
# ---------------------------------------------------------------------------

FIND_SYMBOLS = "find_symbols"
CALLSITE_SEARCH = "callsite_search"
REPO_MAP = "repo_map"
FILE_INFO = "file_info"
HEX_VIEW = "hex_view"
STRUCT_PROBE = "struct_probe"
CORPUS_INSPECT = "corpus_inspect"

# ---------------------------------------------------------------------------
# Domain tools
# ---------------------------------------------------------------------------

SUBMIT_POC = "submit_poc"
RUN_CANDIDATE = "run_candidate"             # removed — kept for compat
PROBE_RUNTIME_FRONTIER = "probe_runtime_frontier"  # deprecated — replaced by GDB_DEBUG
GDB_DEBUG = "gdb_debug"
RECORD_CHAIN_NODE = "record_chain_node"
RECORD_GATE = "record_gate"
RECORD_SINK_CANDIDATE = "record_sink_candidate"
CONFIRM_FORMAT = "confirm_format"
SWITCH_PHASE = "switch_phase"
ANALYSIS_QUERY_TOOLS = frozenset({
    "discover_sink_navigation_leads",
})

# ---------------------------------------------------------------------------
# Legacy name aliases (for backward compat with saved trajectories)
# ---------------------------------------------------------------------------

LEGACY_ALIASES = {
    "READ": READ,
    "GREP": GREP,
    "GLOB": GLOB,
    "WRITE": WRITE,
    "BASH": BASH,
    "FindSymbols": FIND_SYMBOLS,
    "CallsiteSearch": CALLSITE_SEARCH,
    "RepoMap": REPO_MAP,
    "FileInfo": FILE_INFO,
    "HexView": HEX_VIEW,
    "StructProbe": STRUCT_PROBE,
    "CorpusInspect": CORPUS_INSPECT,
}

# ---------------------------------------------------------------------------
# Aggregate sets
# ---------------------------------------------------------------------------

EVIDENCE_TOOLS = frozenset({
    FIND_SYMBOLS,
    CALLSITE_SEARCH,
    REPO_MAP,
    FILE_INFO,
    HEX_VIEW,
    STRUCT_PROBE,
    CORPUS_INSPECT,
})

TRACKING_TOOLS = frozenset({
    RECORD_CHAIN_NODE,
    RECORD_GATE,
    RECORD_SINK_CANDIDATE,
    CONFIRM_FORMAT,
    SWITCH_PHASE,
})

READ_ONLY_TOOLS = frozenset({
    READ,
    GREP,
    GLOB,
    *EVIDENCE_TOOLS,
})

WRITE_TOOLS = frozenset({
    WRITE,
})
