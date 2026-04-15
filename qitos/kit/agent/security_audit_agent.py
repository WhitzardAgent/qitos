"""Reusable security-audit agent template for codebase investigation loops."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from qitos import Action, AgentModule, Decision, Observation, StateSchema, ToolResult
from qitos.kit.planning import PhaseEngine, PhaseSpec, TransitionRule
from qitos.kit.planning.state_ops import format_action
from qitos.kit.prompts import SECURITY_AUDIT_SYSTEM_PROMPT, render_prompt
from qitos.kit.parser import ReActTextParser
from qitos.kit.tool import CodingToolSet, SecurityAuditToolSet, TaskToolSet
from qitos.kit.tool.workspace_aware import WorkspaceAwareMixin


def default_security_audit_phase_engine() -> PhaseEngine:
    """Build the canonical four-phase security audit workflow."""

    return PhaseEngine(
        phases=[
            PhaseSpec(
                name="ingestion",
                max_steps=2,
                prompt_template=(
                    "Phase=ingestion: understand task scope, assets, and attack surface."
                ),
                transitions=[
                    TransitionRule(
                        target="investigation",
                        condition=lambda s: bool(
                            str(getattr(s, "vulnerability_description", "")).strip()
                        ),
                        priority=10,
                    ),
                    TransitionRule(target="investigation", force_at_step=2, priority=1),
                ],
            ),
            PhaseSpec(
                name="investigation",
                max_steps=10,
                prompt_template=(
                    "Phase=investigation: map entrypoints, sinks, trust boundaries, and candidate vulnerable files."
                ),
                transitions=[
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: bool(list(getattr(s, "findings", []) or [])),
                        priority=10,
                    ),
                    TransitionRule(target="formulation", force_at_step=10, priority=1),
                ],
            ),
            PhaseSpec(
                name="formulation",
                prompt_template=(
                    "Phase=formulation: draft exploit hypothesis and generate reproducible PoC artifacts."
                ),
                transitions=[
                    TransitionRule(
                        target="verification",
                        condition=lambda s: bool(getattr(s, "metadata", {}).get("poc_path")),
                        priority=10,
                    ),
                    TransitionRule(target="verification", force_at_step=14, priority=1),
                ],
            ),
            PhaseSpec(
                name="verification",
                prompt_template=(
                    "Phase=verification: validate PoC and gather evidence. If verification fails, revise strategy."
                ),
                transitions=[
                    TransitionRule(
                        target="formulation",
                        condition=lambda s: int(getattr(s, "poc_attempts", 0)) < 3
                        and not bool(getattr(s, "metadata", {}).get("verified")),
                        priority=8,
                    ),
                    TransitionRule(
                        target="investigation",
                        condition=lambda s: int(getattr(s, "poc_attempts", 0)) >= 3
                        and not bool(getattr(s, "metadata", {}).get("verified")),
                        priority=7,
                    ),
                ],
            ),
        ]
    )


@dataclass
class SecurityAuditState(StateSchema):
    """Default state for `SecurityAuditAgent`."""

    current_phase: str = "ingestion"
    vulnerability_description: str = ""
    scratchpad: List[str] = field(default_factory=list)
    findings: List[Dict[str, Any]] = field(default_factory=list)
    poc_attempts: int = 0


class SecurityAuditAgent(AgentModule[SecurityAuditState, Observation, Action]):
    """Four-phase security-audit template with reusable runtime conventions."""

    def __init__(
        self,
        *,
        llm: Any,
        workspace_root: str,
        phase_engine: PhaseEngine | None = None,
        bug_strategies: Dict[str, str] | None = None,
        max_steps: int = 18,
        include_external: bool = False,
        model_parser: Any | None = None,
        model_protocol: Any = None,
    ):
        self.workspace = WorkspaceAwareMixin(workspace_root=workspace_root)
        self.phase_engine = phase_engine or default_security_audit_phase_engine()
        self.bug_strategies = dict(bug_strategies or {})
        self.default_max_steps = int(max_steps)
        super().__init__(
            toolset=[
                SecurityAuditToolSet(
                    workspace_root=workspace_root,
                    include_external=include_external,
                    max_matches=120,
                ),
                CodingToolSet(
                    workspace_root=workspace_root,
                    include_notebook=False,
                    enable_lsp=False,
                    enable_tasks=False,
                    enable_web=False,
                    expose_legacy_aliases=True,
                    expose_modern_names=False,
                    profile="codebase",
                ),
                TaskToolSet(workspace_root=workspace_root),
            ],
            llm=llm,
            model_parser=model_parser or ReActTextParser(),
            model_protocol=model_protocol,
        )

    def init_state(self, task: str, **kwargs: Any) -> SecurityAuditState:
        return SecurityAuditState(
            task=task,
            max_steps=int(kwargs.get("max_steps", self.default_max_steps)),
            vulnerability_description=str(
                kwargs.get("vulnerability_description", "")
            ).strip(),
            current_phase=str(kwargs.get("initial_phase", "ingestion")),
        )

    def build_system_prompt(self, state: SecurityAuditState) -> str | None:
        tool_schema = self.tool_registry.get_tool_descriptions()
        strategies = "\n".join(
            f"- {name}: {text}" for name, text in sorted(self.bug_strategies.items())
        )
        workspace = self.workspace.workspace_summary(max_entries=25, max_depth=2)
        return render_prompt(
            SECURITY_AUDIT_SYSTEM_PROMPT,
            {
                "tool_schema": tool_schema,
                "phase_model": "ingestion -> investigation -> formulation -> verification",
                "workspace_summary": workspace,
                "bug_strategies": strategies or "None configured.",
                "current_phase": state.current_phase,
            },
        )

    def prepare(self, state: SecurityAuditState) -> str:
        phase_prompt = self.phase_engine.get_prompt_section(state, state.current_step)
        lines = [
            f"Audit task: {state.task}",
            f"Workspace: {self.workspace.workspace_root}",
            f"Phase: {state.current_phase}",
            f"Step: {state.current_step}/{state.max_steps}",
            phase_prompt,
        ]
        if state.findings:
            lines.append("Current findings:")
            for item in state.findings[-8:]:
                title = str(item.get("title", "finding"))
                file_path = str(item.get("file", "?"))
                line_no = item.get("line", "?")
                lines.append(f"- {title} @ {file_path}:{line_no}")
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-12:])
        return "\n".join([line for line in lines if str(line).strip()])

    def reduce(
        self,
        state: SecurityAuditState,
        observation: Observation,
        decision: Decision[Action],
    ) -> SecurityAuditState:
        obs = Observation.from_value(observation)
        action_results = [ToolResult.from_value(item) for item in obs.action_results]
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if action_results:
            first = action_results[0]
            state.scratchpad.append(
                f"Observation({first.status}): {first.text[:220]}"
            )
            self._consume_tool_result(state, first)
        state.current_phase = self.phase_engine.advance(state, state.current_step)
        state.metadata["phase"] = state.current_phase
        state.scratchpad = state.scratchpad[-60:]
        state.findings = state.findings[-30:]
        return state

    def _consume_tool_result(self, state: SecurityAuditState, result: ToolResult) -> None:
        output = result.output
        if result.error:
            if "poc" in str(result.error).lower():
                state.poc_attempts += 1
            return
        if not isinstance(output, dict):
            return
        findings = output.get("findings")
        if isinstance(findings, list):
            for item in findings:
                if isinstance(item, dict):
                    state.findings.append(dict(item))
        data = output.get("data")
        if isinstance(data, dict) and isinstance(data.get("findings"), list):
            for item in data.get("findings", []):
                if isinstance(item, dict):
                    state.findings.append(dict(item))
        poc_path = output.get("poc_path") or (
            data.get("poc_path") if isinstance(data, dict) else None
        )
        if isinstance(poc_path, str) and poc_path.strip():
            state.metadata["poc_path"] = poc_path
            self.workspace.note_recent_file(poc_path)
        verified = output.get("verified")
        if isinstance(verified, bool):
            state.metadata["verified"] = verified
        if output.get("attempt") is not None:
            try:
                state.poc_attempts = max(state.poc_attempts, int(output.get("attempt")))
            except Exception:
                pass


__all__ = [
    "SecurityAuditAgent",
    "SecurityAuditState",
    "default_security_audit_phase_engine",
]
