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
            "crash_type": {"type": "string", "description": "Inferred ASAN crash type for crash-type-aware scoring"},
        },
        [],
        "Find source-backed functions worth reading from harness reachability, input flow, and risky operations. Results are navigation leads, not vulnerability verdicts.",
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
        if name == "discover_sink_navigation_leads" and not call_args.get("description") and state is not None:
            call_args["description"] = str(getattr(state, "vulnerability_description", "") or "")
        if name == "discover_sink_navigation_leads" and state is not None and not call_args.get("crash_type"):
            crash_type = str(getattr(state, "crash_type", "") or "") or str((getattr(state, "metadata", None) or {}).get("crash_type_prior", "") or "")
            if crash_type:
                call_args["crash_type"] = crash_type
        method = getattr(service, name)
        result = method(**call_args)

        # Fuse reachable_functions_from_entry results into navigation leads.
        # This adds crash-type-scored candidates from BFS reachability that
        # the navigation service may miss (e.g. deep call-chain functions).
        if name == "discover_sink_navigation_leads" and result.get("status") == "success":
            try:
                entrypoint = str(call_args.get("entrypoint") or "")
                crash_type_arg = str(call_args.get("crash_type") or "")
                description_arg = str(call_args.get("description") or "")
                limit_arg = int(call_args.get("limit") or 5)
                reachable_result = service.reachable_functions_from_entry(
                    entrypoint=entrypoint,
                    limit=limit_arg * 2,
                    crash_type=crash_type_arg,
                    description=description_arg,
                )
                if reachable_result.get("status") == "success" and reachable_result.get("candidates"):
                    nav_lead_ids = {lead.get("symbol_id") for lead in result.get("leads", [])}
                    reachable_candidates = reachable_result.get("candidates", [])
                    # Boost existing leads with crash_type_score
                    for rc in reachable_candidates:
                        rc_sid = rc.get("symbol_id", "")
                        if rc_sid in nav_lead_ids:
                            for lead in result.get("leads", []):
                                if lead.get("symbol_id") == rc_sid:
                                    lead.setdefault("crash_type_score", rc.get("score", 0))
                                    break
                        else:
                            rc["source"] = "reachable_from_entry"
                    # Append supplementary leads (non-overlapping) up to limit
                    supplementary = [rc for rc in reachable_candidates if rc.get("source") == "reachable_from_entry"]
                    result["supplementary_leads"] = supplementary[:limit_arg]
                    result["reachable_summary"] = {
                        "total_reachable": reachable_result.get("total_reachable", 0),
                        "entry_functions": reachable_result.get("entry_functions", []),
                        "crash_type": crash_type_arg,
                    }
            except Exception:
                pass  # Non-critical enrichment

        return result


def analysis_tools() -> list[AnalysisQueryTool]:
    return [AnalysisQueryTool(name) for name in TOOL_PARAMETERS]
