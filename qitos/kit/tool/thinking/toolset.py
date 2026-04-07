"""Sequential thinking toolset for structured reasoning traces."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from qitos.core.tool import tool


@dataclass
class ThoughtData:
    thought: str
    thought_number: int
    total_thoughts: int
    next_thought_needed: bool
    is_revision: Optional[bool] = None
    revises_thought: Optional[int] = None
    branch_from_thought: Optional[int] = None
    branch_id: Optional[str] = None
    needs_more_thoughts: Optional[bool] = None


class ThinkingToolSet:
    """Pre-implemented toolset for stepwise and revisable thinking."""

    name = "thinking"
    version = "1.0"

    def __init__(self):
        self.thought_history: List[ThoughtData] = []
        self.branches: Dict[str, List[ThoughtData]] = {}

    def setup(self, context: Dict[str, Any]) -> None:
        return None

    def teardown(self, context: Dict[str, Any]) -> None:
        return None

    def tools(self) -> List[Any]:
        return [self.sequential_thinking, self.get_thoughts, self.clear_thoughts]

    @tool(
        name="sequential_thinking",
        description="Record one thought step with optional revision/branch metadata",
    )
    def sequential_thinking(
        self,
        thought: str,
        thought_number: int,
        total_thoughts: int,
        next_thought_needed: bool,
        is_revision: Optional[bool] = None,
        revises_thought: Optional[int] = None,
        branch_from_thought: Optional[int] = None,
        branch_id: Optional[str] = None,
        needs_more_thoughts: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Record one structured thought step, optionally as a revision or branch.

        :param thought: Thought content to record.
        :param thought_number: One-based index of this thought.
        :param total_thoughts: Planned total number of thoughts in the sequence.
        :param next_thought_needed: Whether another thought step should follow.
        :param is_revision: Whether this thought revises an earlier one.
        :param revises_thought: One-based index of the thought being revised.
        :param branch_from_thought: One-based index where a branch starts.
        :param branch_id: Optional branch identifier.
        :param needs_more_thoughts: Whether the current total should be expanded.
        """
        if thought_number < 1:
            return {
                "status": "error",
                "message": "thought_number must be a positive integer",
            }
        if total_thoughts < 1:
            return {
                "status": "error",
                "message": "total_thoughts must be a positive integer",
            }
        if thought_number > total_thoughts:
            total_thoughts = thought_number

        if is_revision and revises_thought is not None:
            if revises_thought < 1 or revises_thought > len(self.thought_history):
                return {
                    "status": "error",
                    "message": (
                        f"revises_thought index {revises_thought} is out of range for thought history "
                        f"of length {len(self.thought_history)}"
                    ),
                }

        if branch_from_thought is not None:
            if branch_from_thought < 1 or branch_from_thought > len(
                self.thought_history
            ):
                return {
                    "status": "error",
                    "message": (
                        f"branch_from_thought index {branch_from_thought} is out of range for thought history "
                        f"of length {len(self.thought_history)}"
                    ),
                }

        thought_data = ThoughtData(
            thought=thought,
            thought_number=thought_number,
            total_thoughts=total_thoughts,
            next_thought_needed=next_thought_needed,
            is_revision=is_revision,
            revises_thought=revises_thought,
            branch_from_thought=branch_from_thought,
            branch_id=branch_id,
            needs_more_thoughts=needs_more_thoughts,
        )

        if branch_id is not None and branch_from_thought is not None:
            self.branches.setdefault(branch_id, []).append(thought_data)
        else:
            self.thought_history.append(thought_data)

        current_summary = f"{thought_number}/{total_thoughts}"
        if needs_more_thoughts:
            current_summary += " (more thoughts needed)"

        advice_parts: List[str] = []
        if next_thought_needed:
            advice_parts.append("continue_with_next_thought")
        if needs_more_thoughts:
            advice_parts.append("increase_total_thoughts")
        if is_revision:
            advice_parts.append(f"revision_of_{revises_thought}")
        if branch_id is not None:
            advice_parts.append(f"branch:{branch_id}")

        return {
            "status": "success",
            "current_thought_summary": current_summary,
            "thought_history_count": len(self.thought_history),
            "has_active_branches": bool(self.branches),
            "active_branch_count": len(self.branches),
            "advice": " ".join(advice_parts) if advice_parts else "continue_or_finish",
            "thought_data": {
                "thought_number": thought_number,
                "total_thoughts": total_thoughts,
                "is_revision": is_revision,
                "branch_id": branch_id,
            },
        }

    @tool(name="get_thoughts", description="Return thought history and branch traces")
    def get_thoughts(self) -> Dict[str, Any]:
        """
        Return the full recorded thought history and all branch traces.

        Exposes the internal state accumulated by `sequential_thinking`.
        """
        return {
            "status": "success",
            "history": [asdict(item) for item in self.thought_history],
            "branches": {
                bid: [asdict(item) for item in items]
                for bid, items in self.branches.items()
            },
            "history_count": len(self.thought_history),
            "branch_count": len(self.branches),
        }

    @tool(name="clear_thoughts", description="Clear all recorded thoughts and branches")
    def clear_thoughts(self) -> Dict[str, Any]:
        """
        Clear all stored thoughts and branch metadata from the toolset state.

        Use this to reset the thinking toolset between independent tasks.
        """
        self.thought_history = []
        self.branches = {}
        return {"status": "success", "message": "cleared"}


__all__ = ["ThoughtData", "ThinkingToolSet"]
