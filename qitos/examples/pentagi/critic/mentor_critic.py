"""Mentor hook — periodically injects adviser analysis into agent context.

Every N steps, calls the adviser LLM to review progress and injects
a <mentor_analysis> section into the agent's next observation. This
matches the original pentagi's mentor system where a senior adviser
periodically reviews agent progress.

Uses EngineHook (not Critic) to avoid triggering retry logic —
mentor analysis is advisory, not corrective.
"""

from __future__ import annotations

from typing import Any, Optional

from qitos.engine.hooks import EngineHook, HookContext
from ..prompts.summarizer_prompt import SUMMARIZER_SYSTEM_PROMPT
from ..prompts.mentor_prompt import MENTOR_QUESTION_PROMPT


class MentorHook(EngineHook):
    """Periodically injects adviser mentor analysis into agent context.

    Every `trigger_interval` steps, calls the adviser LLM to review
    progress and appends <mentor_analysis> to the last tool result.

    Parameters
    ----------
    llm : Any
        The LLM to use for adviser calls.
    execution_context : str
        Current execution context for the subtask.
    trigger_interval : int
        Number of steps between mentor reviews (default 5).
    """

    def __init__(
        self,
        llm: Any = None,
        execution_context: str = "",
        trigger_interval: int = 5,
    ):
        self.llm = llm
        self.execution_context = execution_context
        self.trigger_interval = trigger_interval
        self._step_count = 0
        self._last_analysis: Optional[str] = None

    def on_after_step(self, ctx: HookContext, engine: Any) -> None:
        """After each step, check if mentor review is needed."""
        if not self.llm:
            return

        self._step_count = ctx.step_id

        # Only trigger on interval (skip step 0)
        if self._step_count == 0 or self._step_count % self.trigger_interval != 0:
            return

        # Build mentor question from context
        state = ctx.state
        subtask_title = getattr(state, 'task', 'Unknown task')
        subtask_description = ""

        # Extract recent actions from decision
        recent_actions = self._extract_recent_actions(ctx)

        # Extract findings
        findings = getattr(state, 'findings', [])
        findings_summary = str(findings[-3:]) if findings else "No findings yet"

        # Build the question
        mentor_question = MENTOR_QUESTION_PROMPT.format(
            step_count=self._step_count,
            subtask_title=subtask_title[:200],
            subtask_description=subtask_description[:300],
            recent_count=min(3, len(recent_actions)),
            recent_actions=recent_actions or "No recent actions recorded",
            findings_summary=findings_summary[:500],
            execution_context=self.execution_context[:2000],
        )

        # Call adviser LLM
        try:
            response = self.llm.chat([
                {"role": "system", "content": SUMMARIZER_SYSTEM_PROMPT},
                {"role": "user", "content": mentor_question},
            ])

            if isinstance(response, str):
                analysis = response
            elif isinstance(response, dict) and "content" in response:
                analysis = response["content"]
            else:
                analysis = str(response)

            self._last_analysis = analysis

            # Inject into last action result
            self._inject_analysis(ctx, analysis)

        except Exception:
            # Mentor failure should not break the agent
            pass

    def _extract_recent_actions(self, ctx: HookContext) -> str:
        """Extract a summary of recent tool calls from the step record."""
        record = ctx.record
        if not record:
            return ""

        actions = []
        invocations = list(getattr(record, "tool_invocations", []) or [])
        results = list(getattr(record, "action_results", []) or [])

        for i, inv in enumerate(invocations[-3:]):
            if isinstance(inv, dict):
                name = inv.get("tool_name", "?")
                args = inv.get("tool_args", {})
                result_preview = ""
                if i < len(results):
                    r = results[i]
                    if isinstance(r, dict):
                        result_preview = str(r.get("output", ""))[:150]
                    else:
                        result_preview = str(r)[:150]
                actions.append(f"- {name}({str(args)[:100]}): {result_preview}")

        return "\n".join(actions)

    def _inject_analysis(self, ctx: HookContext, analysis: str) -> None:
        """Append mentor analysis to the last action result."""
        if not ctx.action_results:
            return

        last_result = ctx.action_results[-1]
        if isinstance(last_result, dict):
            output = last_result.get("output", "")
            mentor_section = (
                f"\n\n<enhanced_response>"
                f"\n<mentor_analysis>\n{analysis}\n</mentor_analysis>"
                f"\n</enhanced_response>"
            )
            last_result["output"] = str(output) + mentor_section


__all__ = ["MentorHook"]
