"""Canonical tool name constants for the CyberGym agent.

The string values are the names the LLM sends in tool_calls.
DO NOT change these values — doing so breaks saved trajectories
and model familiarity.

Usage:
    from tool_names import READ, SUBMIT_POC, EVIDENCE_TOOLS
"""

# ---------------------------------------------------------------------------
# File / code tools (UPPER_SNAKE — primitive shell-like operations)
# ---------------------------------------------------------------------------

READ = "READ"
GREP = "GREP"
GLOB = "GLOB"
WRITE = "WRITE"
BASH = "BASH"
APPEND = "APPEND"
INSERT = "INSERT"
REPLACE_LINES = "REPLACE_LINES"
STR_REPLACE = "STR_REPLACE"

# ---------------------------------------------------------------------------
# Evidence / inspection tools (PascalCase — structured read-only queries)
# ---------------------------------------------------------------------------

FIND_SYMBOLS = "FindSymbols"
CALLSITE_SEARCH = "CallsiteSearch"
REPO_MAP = "RepoMap"
FILE_INFO = "FileInfo"
HEX_VIEW = "HexView"
STRUCT_PROBE = "StructProbe"
CORPUS_INSPECT = "CorpusInspect"

# ---------------------------------------------------------------------------
# Domain tools (snake_case — domain-specific actions)
# ---------------------------------------------------------------------------

SUBMIT_POC = "submit_poc"
RUN_CANDIDATE = "run_candidate"
PROBE_RUNTIME_FRONTIER = "probe_runtime_frontier"
RECORD_HYPOTHESIS = "record_hypothesis"
RECORD_ATTEMPT = "record_attempt"
RECORD_REFLECTION = "record_reflection"
RECORD_CHAIN_NODE = "record_chain_node"
RECORD_GATE = "record_gate"
RECORD_SINK_CANDIDATE = "record_sink_candidate"
SET_CRASH_TYPE = "set_crash_type"
SWITCH_PHASE = "switch_phase"
ANALYZE_SINK_CANDIDATE = "analyze_sink_candidate"
ANALYSIS_QUERY_TOOLS = frozenset({
    "discover_sink_navigation_leads", "expand_candidate_neighborhood",
    "get_sink_search_brief", "mark_navigation_lead_reviewed",
    "index_repository", "find_callers", "find_paths_to_target", "summarize_function",
    "extract_constraints", "trace_value", "get_path_details", "explain_path",
    "resolve_callsite_candidates", "get_analysis_result",
    "reachable_functions_from_entry",
})

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
    RECORD_HYPOTHESIS,
    RECORD_ATTEMPT,
    RECORD_REFLECTION,
    RECORD_CHAIN_NODE,
    RECORD_GATE,
    RECORD_SINK_CANDIDATE,
    SET_CRASH_TYPE,
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
    APPEND,
    INSERT,
    REPLACE_LINES,
    STR_REPLACE,
})
