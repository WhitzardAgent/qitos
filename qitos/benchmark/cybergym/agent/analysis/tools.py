"""QitOS tool adapters for the analysis service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult

from .service import AnalysisService


# Each entry: (parameters_dict, required_list, tool_description)
TOOL_PARAMETERS: dict[str, tuple[dict[str, Any], list[str], str]] = {
    "discover_sink_navigation_leads": (
        {
            "entrypoint": {"type": "string", "description": "Optional harness entrypoint to constrain navigation"},
            "limit": {"type": "integer", "description": "Maximum leads (default 5, maximum 20)"},
            "description": {"type": "string", "description": "Optional task description used only as a weak prior"},
        },
        [],
        "Find source-backed functions worth reading from harness reachability, input flow, and risky operations. Results are navigation leads, not vulnerability verdicts.",
    ),
    "expand_candidate_neighborhood": (
        {
            "candidate_id": {"type": "string", "description": "Recorded candidate ID, or an exact function symbol"},
            "depth": {"type": "integer", "description": "Caller/callee neighborhood depth (default 3)"},
            "limit": {"type": "integer", "description": "Maximum alternatives (default 10)"},
        },
        ["candidate_id"],
        "Compare a candidate with nearby callers, callees, helpers, and direct operation sites to detect candidates that may be too shallow or too deep.",
    ),
    "get_sink_search_brief": (
        {"brief_id": {"type": "string", "description": "Sink search brief ID"}},
        ["brief_id"],
        "Retrieve a stored sink-navigation brief.",
    ),
    "mark_navigation_lead_reviewed": (
        {
            "lead_id": {"type": "string", "description": "Navigation lead ID"},
            "outcome": {"type": "string", "enum": ["confirmed", "rejected", "deferred"], "description": "Model review outcome"},
        },
        ["lead_id", "outcome"],
        "Record whether a static navigation lead was confirmed, rejected, or deferred.",
    ),
    "analyze_sink_candidate": (
        {
            "candidate": {
                "type": "object",
                "description": (
                    "Sink candidate to analyze. Keys: function (str), file (str), line (int), "
                    "callee (str, optional), expression (str, optional), category (str, optional), "
                    "reason (str), agent_confidence (float 0-1). "
                    "Usually auto-filled from record_sink_candidate — you rarely call this directly."
                ),
            },
            "mode": {
                "type": "string",
                "enum": ["automatic", "interactive", "deep"],
                "description": (
                    "Analysis depth: 'automatic' (fast, default), "
                    "'interactive' (deeper, more paths), 'deep' (exhaustive, slow)"
                ),
            },
            "budget_profile": {
                "type": "string",
                "description": "Optional named budget profile (reserved, leave empty)",
            },
        },
        ["candidate"],
        (
            "Find entry-to-sink call paths, extract path constraints, and trace dataflow "
            "at the sink. Automatically triggered when you call record_sink_candidate. "
            "Use manually only for re-analysis with a deeper mode after initial results "
            "are insufficient."
        ),
    ),
    "index_repository": (
        {
            "repository": {
                "type": "string",
                "description": "Repository path (auto-detected from state — usually omit)",
            },
            "languages": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Languages to index, e.g. ['c','cpp']. Default: auto-detect",
            },
        },
        [],
        (
            "Index the repository with tree-sitter to enable path finding, constraint "
            "extraction, and value tracing. Auto-runs when you use other analysis tools — "
            "call explicitly only if indexing hasn't happened yet (check Interprocedural "
            "Analysis section in the Constraint Board)."
        ),
    ),
    "find_callers": (
        {
            "symbol": {
                "type": "string",
                "description": "Function name to find callers for",
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum reverse call-chain depth (default: 3)",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of caller results (default: 10)",
            },
        },
        ["symbol"],
        (
            "Find functions that call the given symbol, using the tree-sitter structural "
            "index. Unlike CallsiteSearch (which uses text/regex search), this uses "
            "semantic analysis and can resolve callers across files with confidence scores. "
            "Prefer this for multi-hop caller chains."
        ),
    ),
    "find_paths_to_target": (
        {
            "target": {
                "type": "string",
                "description": "Target function name to find paths to",
            },
            "entrypoint_patterns": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Function name patterns for entry points "
                    "(default: auto-detect harness functions like LLVMFuzzerTestOneInput)"
                ),
            },
            "max_depth": {
                "type": "integer",
                "description": "Maximum call-chain depth (default: 8)",
            },
            "top_k": {
                "type": "integer",
                "description": "Maximum number of paths to return (default: 5)",
            },
        },
        ["target"],
        (
            "Find call paths from harness entry points to a target function. Returns "
            "scored paths with constraint hints. Use after identifying a sink candidate "
            "to understand how fuzzer input reaches the vulnerable code."
        ),
    ),
    "summarize_function": (
        {
            "symbol_id": {
                "type": "string",
                "description": (
                    "Symbol ID or function name (e.g., 'ProcessExifTag') to summarize"
                ),
            },
        },
        ["symbol_id"],
        (
            "Get a function's parameters, calls, return statements, and local definitions "
            "without reading its source. Faster than READ for understanding a function's "
            "interface and behavior at a glance."
        ),
    ),
    "extract_constraints": (
        {
            "function": {
                "type": "string",
                "description": "Function name containing the callsite",
            },
            "target_line": {
                "type": "integer",
                "description": "Source line number of the callsite within the function",
            },
            "max_paths": {
                "type": "integer",
                "description": "Maximum constraint paths to return (default: 4)",
            },
        },
        ["function", "target_line"],
        (
            "Extract path constraints (guards, branches, dispatch conditions) at a "
            "specific callsite. Use to understand what conditions must be true for a "
            "call to be reached at a given line."
        ),
    ),
    "trace_value": (
        {
            "function": {
                "type": "string",
                "description": "Function name or symbol_id containing the expression",
            },
            "line": {
                "type": "integer",
                "description": "Source line number where the expression appears",
            },
            "expression": {
                "type": "string",
                "description": (
                    "Variable or expression to trace (e.g., 'size', 'buf + offset')"
                ),
            },
            "direction": {
                "type": "string",
                "description": (
                    "Trace direction: 'backward' (default — find where value comes from)"
                ),
            },
        },
        ["function", "line", "expression"],
        (
            "Trace where a value originates by walking backward through local definitions "
            "within a function. Reveals how fuzzer input parameters flow into sink arguments."
        ),
    ),
    "get_path_details": (
        {
            "path_id": {
                "type": "string",
                "description": (
                    "Path ID from find_paths_to_target or Interprocedural Analysis section"
                ),
            },
            "include_source_evidence": {
                "type": "boolean",
                "description": "Include source code snippets as evidence (default: false)",
            },
            "include_full_bindings": {
                "type": "boolean",
                "description": (
                    "Include parameter-to-argument bindings at each edge (default: false)"
                ),
            },
            "include_all_constraints": {
                "type": "boolean",
                "description": "Include all extracted constraints, not just top ones (default: false)",
            },
        },
        ["path_id"],
        (
            "Retrieve full details of a previously discovered interprocedural path. "
            "Use after find_paths_to_target to drill into a specific path's edges, "
            "constraints, and parameter bindings."
        ),
    ),
    "explain_path": (
        {
            "path_id": {
                "type": "string",
                "description": (
                    "Path ID from find_paths_to_target or Interprocedural Analysis section"
                ),
            },
            "format": {
                "type": "string",
                "description": (
                    "Output format: 'narrative' (default, human-readable) or 'structured'"
                ),
            },
        },
        ["path_id"],
        (
            "Get a human-readable explanation of an interprocedural call path, showing "
            "the caller→callee chain with guards and parameter bindings at each hop."
        ),
    ),
    "resolve_callsite_candidates": (
        {
            "unresolved_id": {
                "type": "string",
                "description": (
                    "ID of an unresolved callsite from a previous analysis result"
                ),
            },
            "callsite_id": {
                "type": "string",
                "description": (
                    "Callsite ID to resolve (function pointer or virtual dispatch site)"
                ),
            },
            "max_candidates": {
                "type": "integer",
                "description": "Maximum candidate targets to return (default: 5)",
            },
            "include_registration_evidence": {
                "type": "boolean",
                "description": "Include evidence for each candidate target (default: false)",
            },
        },
        ["callsite_id"],
        (
            "Resolve an indirect callsite (function pointer, virtual dispatch) to "
            "candidate target functions. Use when analysis reports unresolved callsites "
            "that block path discovery."
        ),
    ),
    "get_analysis_result": (
        {
            "full_result_id": {
                "type": "string",
                "description": (
                    "Full result ID from a previous analyze_sink_candidate call"
                ),
            },
            "section": {
                "type": "string",
                "description": (
                    "Optional section to retrieve: 'paths', 'constraints', 'dataflow', "
                    "'unresolved'"
                ),
            },
            "offset": {
                "type": "integer",
                "description": "Offset for paginated results (default: 0)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum items per page (default: 20)",
            },
        },
        ["full_result_id"],
        (
            "Retrieve a stored full analysis result by ID. Use to re-examine a previous "
            "deep analysis or get paginated details of a specific section."
        ),
    ),
}


class AnalysisQueryTool(BaseTool):
    def __init__(self, name: str) -> None:
        params, required, desc = TOOL_PARAMETERS[name]
        super().__init__(ToolSpec(
            name=name,
            description=desc,
            parameters=params,
            required=required,
            permissions=ToolPermission(filesystem_read=True, filesystem_write=True),
        ))
        self.query_name = name

    def validate_input(self, args: dict[str, Any], runtime_context: Optional[dict[str, Any]] = None) -> ToolValidationResult:
        for name in TOOL_PARAMETERS[self.query_name][1]:
            if args.get(name) in (None, ""):
                return ToolValidationResult.fail(f"{name} is required")
        return ToolValidationResult.ok()

    def execute(self, args: dict[str, Any], runtime_context: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        state = (runtime_context or {}).get("state")
        repo = str(args.get("repository") or getattr(state, "repo_dir", "") or getattr(state, "workspace_root", "") or ".")
        workspace = str(getattr(state, "workspace_root", "") or Path(repo).parent)
        service = AnalysisService(repo, workspace_root=workspace)
        name = self.query_name
        call_args = dict(args)
        call_args.pop("repository", None); call_args.pop("languages", None)
        if name == "index_repository":
            result = service.index_repository()
        else:
            if name == "discover_sink_navigation_leads" and not call_args.get("description") and state is not None:
                call_args["description"] = str(getattr(state, "vulnerability_description", "") or "")
            if name == "discover_sink_navigation_leads" and state is not None and not call_args.get("crash_type"):
                crash_type = str(getattr(state, "crash_type", "") or "") or str((getattr(state, "metadata", None) or {}).get("crash_type_prior", "") or "")
                if crash_type:
                    call_args["crash_type"] = crash_type
            method = getattr(service, name)
            result = method(**call_args)
        if state is not None and name == "analyze_sink_candidate" and result.get("brief"):
            state.latest_sink_analysis_brief = result["brief"]
            state.latest_brief_id = result.get("brief_id", "")
            state.analysis_status = "BRIEF_AVAILABLE"
        return result


def analysis_tools() -> list[AnalysisQueryTool]:
    return [AnalysisQueryTool(name) for name in TOOL_PARAMETERS]
