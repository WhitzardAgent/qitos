"""Store agent result tools — persist findings, guides, answers, code."""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.memory import MemoryRecord
from qitos.core.tool import BaseTool, ToolSpec


class _StoreTool(BaseTool):
    """Base class for store tools."""

    _store_type: str = ""

    def __init__(self, name: str, description: str, memory: Optional[Any] = None):
        self._memory = memory
        super().__init__(
            ToolSpec(
                name=name,
                description=description,
                parameters={
                    "content": {
                        "type": "string",
                        "description": "The content to store",
                    },
                    "title": {
                        "type": "string",
                        "description": "Title or summary of the content",
                    },
                    "tags": {
                        "type": "array",
                        "description": "Optional tags for categorization",
                    },
                },
                required=["content"],
            )
        )

    def _store(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        memory = self._memory or (runtime_context or {}).get("pentagi_memory")
        if memory is None:
            return {"status": "error", "message": "Memory not configured"}

        content = str(args.get("content", ""))
        title = str(args.get("title", ""))
        tags = args.get("tags", [])

        if not content:
            return {"status": "error", "message": "content is required"}

        try:
            record = MemoryRecord(
                role="system",
                content=content,
                step_id=0,
                metadata={
                    "type": self._store_type,
                    "title": title,
                    "tags": tags,
                },
            )
            memory.append(record)
            return {"status": "ok", "message": f"Stored {self._store_type}: {title or content[:50]}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class StoreGuideTool(_StoreTool):
    _store_type = "guide"

    def __init__(self, memory: Optional[Any] = None):
        super().__init__(
            name="store_guide",
            description="Store a methodology guide or technique for future reference. "
            "Anonymize any real target information before storing.",
            memory=memory,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._store(args, runtime_context)


class StoreAnswerTool(_StoreTool):
    _store_type = "answer"

    def __init__(self, memory: Optional[Any] = None):
        super().__init__(
            name="store_answer",
            description="Store a Q&A pair for future reference. "
            "Include both the question and the answer in the content.",
            memory=memory,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._store(args, runtime_context)


class StoreCodeTool(_StoreTool):
    _store_type = "code"

    def __init__(self, memory: Optional[Any] = None):
        super().__init__(
            name="store_code",
            description="Store a code snippet for future reference. "
            "Include the language, purpose, and usage instructions.",
            memory=memory,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._store(args, runtime_context)


class StoreFindingTool(_StoreTool):
    _store_type = "finding"

    def __init__(self, memory: Optional[Any] = None):
        super().__init__(
            name="store_finding",
            description="Store a security finding from the penetration test. "
            "Include target (anonymized), vulnerability, severity, and evidence.",
            memory=memory,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._store(args, runtime_context)


class StoreSubtaskResultTool(_StoreTool):
    _store_type = "subtask_result"

    def __init__(self, memory: Optional[Any] = None):
        super().__init__(
            name="store_subtask_result",
            description="Store the result of a completed subtask for future reference.",
            memory=memory,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._store(args, runtime_context)


class StoreEvidenceTool(_StoreTool):
    _store_type = "evidence"

    def __init__(self, memory: Optional[Any] = None):
        super().__init__(
            name="store_evidence",
            description="Store evidence from the penetration test (command output, screenshots, etc.).",
            memory=memory,
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self._store(args, runtime_context)


__all__ = [
    "StoreGuideTool",
    "StoreAnswerTool",
    "StoreCodeTool",
    "StoreFindingTool",
    "StoreSubtaskResultTool",
    "StoreEvidenceTool",
]
