"""Canonical example: build a code security audit agent with QitOS presets."""

# This full application is scheduled to move to qitos-zoo and is not part of QitOS core examples.

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qitos import Action, AgentModule, Decision, HistoryPolicy, StateSchema
from qitos.kit import (
    CodingToolSet,
    ReActTextParser,
    TaskToolSet,
    format_action,
    render_prompt,
)
from qitos.kit.prompts import SECURITY_AUDIT_SYSTEM_PROMPT
from qitos.kit.tool.experimental.security_research import SecurityAuditToolSet
from qitos.models import OpenAICompatibleModel

TASK = "Audit this repository for meaningful code security risks. Prioritize entrypoints, dangerous sinks, secrets, configuration, and dependency clues."
WORKSPACE = Path("./playground/code_security_audit_agent")
MODEL_NAME = os.getenv("QITOS_MODEL", "Qwen/Qwen3-8B")
MODEL_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.siliconflow.cn/v1/")
MAX_STEPS = 10


@dataclass
class SecurityAuditState(StateSchema):
    scratchpad: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


class CodeSecurityAuditAgent(AgentModule[SecurityAuditState, dict[str, Any], Action]):
    def __init__(self, llm: Any, workspace_root: str):
        super().__init__(
            toolset=[
                SecurityAuditToolSet(
                    workspace_root=workspace_root,
                    include_external=False,
                    max_matches=80,
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
            model_parser=ReActTextParser(),
        )

    def init_state(self, task: str, **kwargs: Any) -> SecurityAuditState:
        return SecurityAuditState(
            task=task, max_steps=int(kwargs.get("max_steps", MAX_STEPS))
        )

    def build_system_prompt(self, state: SecurityAuditState) -> str | None:
        return render_prompt(
            SECURITY_AUDIT_SYSTEM_PROMPT,
            {"tool_schema": self.tool_registry.get_tool_descriptions()},
        )

    def prepare(self, state: SecurityAuditState) -> str:
        lines = [
            f"Audit task: {state.task}",
            f"Workspace: {WORKSPACE}",
            f"Step: {state.current_step}/{state.max_steps}",
            "Suggested flow: inventory -> entrypoints -> sinks/secrets/config/dependencies -> hotspots -> final ranked findings.",
        ]
        if state.scratchpad:
            lines.append("Recent trajectory:")
            lines.extend(state.scratchpad[-10:])
        return "\n".join(lines)

    def reduce(
        self,
        state: SecurityAuditState,
        observation: dict[str, Any],
        decision: Decision[Action],
    ) -> SecurityAuditState:
        results = (
            observation.get("action_results", [])
            if isinstance(observation, dict)
            else []
        )
        if decision.rationale:
            state.scratchpad.append(f"Thought: {decision.rationale}")
        if decision.actions:
            state.scratchpad.append(f"Action: {format_action(decision.actions[0])}")
        if results:
            first = results[0]
            state.scratchpad.append(f"Observation: {first}")
            if isinstance(first, dict):
                data = (
                    first.get("data", {})
                    if isinstance(first.get("data", {}), dict)
                    else {}
                )
                for item in list(data.get("findings", []) or [])[:3]:
                    title = str(item.get("title", "finding"))
                    location = f"{item.get('file', '?')}:{item.get('line', '?')}"
                    state.findings.append(f"{title} @ {location}")
                if decision.mode == "final":
                    state.final_result = str(decision.final_answer or "")
        state.scratchpad = state.scratchpad[-40:]
        state.findings = state.findings[-20:]
        return state


def build_model() -> OpenAICompatibleModel:
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("QITOS_API_KEY") or "").strip()
    if not api_key:
        raise ValueError(
            "Set OPENAI_API_KEY or QITOS_API_KEY before running this example."
        )
    return OpenAICompatibleModel(
        model=MODEL_NAME,
        api_key=api_key,
        base_url=MODEL_BASE_URL,
        temperature=0.1,
        max_tokens=2048,
    )


def _seed_demo_repo() -> None:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    app = WORKSPACE / "app.py"
    if not app.exists():
        app.write_text(
            "from flask import Flask, request\n"
            "import subprocess\n\n"
            "app = Flask(__name__)\n"
            "DEBUG = True\n"
            "SECRET_KEY = 'prod-secret-value-123456'\n\n"
            "@app.route('/run')\n"
            "def run():\n"
            "    subprocess.run(request.args.get('cmd'), shell=True)\n"
            "    return 'ok'\n",
            encoding="utf-8",
        )
    req = WORKSPACE / "requirements.txt"
    if not req.exists():
        req.write_text("flask\nrequests==2.31.0\n", encoding="utf-8")
    notes = WORKSPACE / "notes.py"
    if not notes.exists():
        notes.write_text("# TODO: harden auth flow\n", encoding="utf-8")


def main() -> None:
    _seed_demo_repo()
    agent = CodeSecurityAuditAgent(llm=build_model(), workspace_root=str(WORKSPACE))
    result = agent.run(
        task=TASK,
        workspace=str(WORKSPACE),
        max_steps=MAX_STEPS,
        history_policy=HistoryPolicy(max_messages=14),
        return_state=True,
    )
    print("workspace:", WORKSPACE)
    print("final_result:", result.state.final_result)
    print("stop_reason:", result.state.stop_reason)


if __name__ == "__main__":
    main()
