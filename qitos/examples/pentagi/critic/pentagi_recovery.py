"""PentAGIRecoveryPolicy — combined recovery for the pentagi system.

Handles:
- LLM errors: retry up to 3 times
- Parse errors: LLM-based tool call fixing (via ToolCallFixerRecovery),
  falling back to guidance-based retry
- Tool errors: continue with alternative approach
- Other errors: delegate to standard recovery
"""

from __future__ import annotations

from typing import Any, Optional

from qitos.engine.recovery import RecoveryDecision, RecoveryPolicy
from qitos.core.errors import ErrorCategory

from .tool_call_fixer import ToolCallFixerRecovery


class PentAGIRecoveryPolicy(RecoveryPolicy):
    """Combined recovery policy for PentAGI system.

    Delegates parse errors to ToolCallFixerRecovery for LLM-based fixing.
    Handles LLM and tool errors with guidance-based retry.

    Parameters
    ----------
    llm : Any | None
        Optional LLM instance for ToolCallFixer's LLM-based fixing.
    max_recoveries_per_run : int
        Maximum total recovery attempts per run.
    max_llm_retries : int
        Maximum LLM error retries.
    tool_registry : Any | None
        Optional tool registry for ToolCallFixer schema lookups.
    """

    def __init__(
        self,
        llm: Any = None,
        max_recoveries_per_run: int = 9,
        max_llm_retries: int = 3,
        tool_registry: Optional[Any] = None,
    ):
        super().__init__(max_recoveries_per_run=max_recoveries_per_run)
        self._max_llm_retries = max_llm_retries
        self._llm_retries = 0
        self._tool_registry = tool_registry
        self._tool_call_fixer = ToolCallFixerRecovery(
            llm=llm,
            tool_registry=tool_registry,
        )

    def reset(self) -> None:
        super().reset()
        self._llm_retries = 0
        self._tool_call_fixer.reset()

    def handle(
        self, state: Any, phase: str, step_id: int, exc: Exception
    ) -> RecoveryDecision:
        from qitos.core.errors import classify_exception
        info = classify_exception(exc, phase, step_id)
        category_str = str(getattr(info.category, 'value', info.category))

        # LLM errors: retry up to max times
        if category_str == "model_error":
            if self._llm_retries < self._max_llm_retries:
                self._llm_retries += 1
                self.tracker.add(info, f"LLM retry {self._llm_retries}/{self._max_llm_retries}", "retry")
                return RecoveryDecision(
                    handled=True,
                    continue_run=True,
                    note=f"llm_retry_{self._llm_retries}",
                    instruction_patch=(
                        f"The LLM encountered an error (attempt {self._llm_retries}/{self._max_llm_retries}). "
                        "Please retry your last action. If the error persists, "
                        "try a simpler or alternative approach."
                    ),
                )
            self.tracker.add(info, "LLM retries exhausted", "stop")
            return RecoveryDecision(
                handled=True,
                continue_run=False,
                note="llm_retries_exhausted",
            )

        # Parse errors: delegate to ToolCallFixerRecovery for LLM-based fixing
        if category_str == "parse_error":
            return self._tool_call_fixer.handle(state, phase, step_id, exc)

        # Tool errors: continue with guidance
        if category_str == "tool_error":
            self._recoveries += 1
            error_msg = str(exc)[:200]
            self.tracker.add(info, "Tool execution error", "retry")
            return RecoveryDecision(
                handled=True,
                continue_run=True,
                note="tool_error_retry",
                instruction_patch=(
                    f"A tool execution error occurred: {error_msg}\n\n"
                    "Possible fixes:\n"
                    "- Check that the tool is installed and available\n"
                    "- Verify the arguments are correct\n"
                    "- Try an alternative tool or approach\n"
                    "- Check for permission issues"
                ),
            )

        # Delegate to standard recovery
        return super().handle(state, phase, step_id, exc)
