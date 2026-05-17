"""SubtaskManager — manages the subtask plan and cursor."""

from __future__ import annotations

from typing import Any, Dict, List, Optional


class SubtaskManager:
    """Manages the subtask plan list and cursor position.

    Tracks which subtasks are completed, which are remaining,
    and supports delta patch operations from the Refiner.
    """

    def __init__(self, max_subtasks: int = 15):
        self.max_subtasks = max_subtasks
        self._subtasks: List[Dict[str, Any]] = []
        self._cursor: int = 0

    @property
    def subtasks(self) -> List[Dict[str, Any]]:
        return list(self._subtasks)

    @property
    def cursor(self) -> int:
        return self._cursor

    @property
    def current_subtask(self) -> Optional[Dict[str, Any]]:
        if self._cursor < len(self._subtasks):
            return self._subtasks[self._cursor]
        return None

    @property
    def completed_subtasks(self) -> List[Dict[str, Any]]:
        return [
            s for s in self._subtasks
            if s.get("status") == "completed"
        ]

    @property
    def remaining_subtasks(self) -> List[Dict[str, Any]]:
        return [
            s for s in self._subtasks
            if s.get("status") != "completed"
        ]

    def set_plan(self, subtasks: List[Dict[str, Any]]) -> None:
        """Set the full subtask plan."""
        self._subtasks = subtasks[:self.max_subtasks]
        self._cursor = 0
        # Assign IDs and initial status
        for i, st in enumerate(self._subtasks):
            st.setdefault("id", str(i + 1))
            st.setdefault("status", "planned")

    def advance(self) -> Optional[Dict[str, Any]]:
        """Advance the cursor and return the next subtask."""
        if self._cursor < len(self._subtasks):
            current = self._subtasks[self._cursor]
            current["status"] = "completed"
            self._cursor += 1
            return self.current_subtask
        return None

    def mark_current_completed(self, result: str = "") -> None:
        """Mark the current subtask as completed with a result."""
        if self._cursor < len(self._subtasks):
            self._subtasks[self._cursor]["status"] = "completed"
            self._subtasks[self._cursor]["result"] = result

    def mark_current_failed(self, error: str = "") -> None:
        """Mark the current subtask as failed."""
        if self._cursor < len(self._subtasks):
            self._subtasks[self._cursor]["status"] = "failed"
            self._subtasks[self._cursor]["error"] = error

    def apply_delta(self, operations: List[Dict[str, Any]]) -> None:
        """Apply delta patch operations from the Refiner.

        Operations:
        - add: {op: "add", title, description, after_id?}
        - remove: {op: "remove", id}
        - modify: {op: "modify", id, title?, description?}
        - reorder: {op: "reorder", id, after_id}
        """
        for op in operations:
            op_type = op.get("op", op.get("operation", ""))
            if op_type == "add":
                new_st = {
                    "id": op.get("id", str(len(self._subtasks) + 1)),
                    "title": op.get("title", ""),
                    "description": op.get("description", ""),
                    "status": "planned",
                }
                after_id = op.get("after_id")
                if after_id:
                    idx = next(
                        (i for i, s in enumerate(self._subtasks) if s.get("id") == after_id),
                        len(self._subtasks),
                    )
                    self._subtasks.insert(idx + 1, new_st)
                else:
                    self._subtasks.append(new_st)

            elif op_type == "remove":
                target_id = op.get("id")
                self._subtasks = [
                    s for s in self._subtasks if s.get("id") != target_id
                ]

            elif op_type == "modify":
                target_id = op.get("id")
                for s in self._subtasks:
                    if s.get("id") == target_id:
                        if op.get("title"):
                            s["title"] = op["title"]
                        if op.get("description"):
                            s["description"] = op["description"]

            elif op_type == "reorder":
                target_id = op.get("id")
                after_id = op.get("after_id")
                target_idx = next(
                    (i for i, s in enumerate(self._subtasks) if s.get("id") == target_id),
                    None,
                )
                if target_idx is not None:
                    item = self._subtasks.pop(target_idx)
                    if after_id:
                        insert_idx = next(
                            (i for i, s in enumerate(self._subtasks) if s.get("id") == after_id),
                            len(self._subtasks),
                        )
                        self._subtasks.insert(insert_idx + 1, item)
                    else:
                        self._subtasks.insert(0, item)

        # Enforce max subtasks
        if len(self._subtasks) > self.max_subtasks:
            self._subtasks = self._subtasks[:self.max_subtasks]

    @property
    def is_complete(self) -> bool:
        return self._cursor >= len(self._subtasks) or all(
            s.get("status") in ("completed", "failed") for s in self._subtasks
        )
