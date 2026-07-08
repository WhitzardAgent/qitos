"""Minimal prompt-rendering mixin for the Minimal CyberGym Agent.

Simple system prompt: persona + task description + tool list.
No phase-specific guidance, no procedure memory, no format locking.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...state import CyberGymState

from ...context import PROJECT_ARTIFACT_ROOT
from .prompt_resources import prompt_resource, render_prompt_resource


class PromptsMixin:
    """Minimal prompt rendering — persona + task + tools."""

    def base_persona_prompt(self, state: CyberGymState) -> str:
        return render_prompt_resource("system/base_persona.md", project_root=PROJECT_ARTIFACT_ROOT.as_posix())

    def task_policy_prompt(self, state: CyberGymState) -> str:
        parts = []
        if state.bug_type:
            parts.append(f"\n## Bug Type: {state.bug_type}")
        if state.cve_id:
            parts.append(f"\n## CVE ID: {state.cve_id}")
        return "\n".join(parts)

    def runtime_context_protocol_prompt(self, state: CyberGymState) -> str:
        return prompt_resource("system/runtime_context_protocol.md")

    def extra_instructions_prompt(self, state: CyberGymState) -> str:
        return prompt_resource("system/execution_policy.md")

    def tool_usage_hint_prompt(self, state: CyberGymState) -> str:
        return render_prompt_resource("system/tool_usage.md", POC_OUTPUT_DIR="pocs", delegate_hint="")

    def _multi_action_guidance_prompt(self, state: CyberGymState) -> str:
        return prompt_resource("system/multi_action.md")
