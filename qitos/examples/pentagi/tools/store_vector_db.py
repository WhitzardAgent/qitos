"""Vector DB store tools — persist data to pgvector."""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolSpec


class StoreVectorGuideTool(BaseTool):
    """Store a guide to the pgvector database."""

    def __init__(self, vector_store: Optional[Any] = None):
        self._vector_store = vector_store
        super().__init__(
            ToolSpec(
                name="store_vector_guide",
                description="Store a methodology guide directly to the vector database.",
                parameters={
                    "title": {"type": "string", "description": "Guide title"},
                    "content": {"type": "string", "description": "Guide content"},
                    "tags": {"type": "array", "description": "Optional tags"},
                },
                required=["title", "content"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        store = self._vector_store or (runtime_context or {}).get("vector_store")
        if store is None:
            return {"status": "error", "message": "Vector store not configured"}
        title = str(args.get("title", ""))
        content = str(args.get("content", ""))
        tags = args.get("tags", [])
        try:
            import uuid
            store.upsert(
                id=str(uuid.uuid4()),
                vector=None,  # auto-embedded by store if embedder configured
                metadata={"type": "guide", "title": title, "tags": tags},
                text=content,
            )
            return {"status": "ok", "message": f"Stored guide: {title}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class StoreVectorAnswerTool(BaseTool):
    """Store a Q&A pair to the pgvector database."""

    def __init__(self, vector_store: Optional[Any] = None):
        self._vector_store = vector_store
        super().__init__(
            ToolSpec(
                name="store_vector_answer",
                description="Store a Q&A pair directly to the vector database.",
                parameters={
                    "question": {"type": "string", "description": "The question"},
                    "answer": {"type": "string", "description": "The answer"},
                    "tags": {"type": "array", "description": "Optional tags"},
                },
                required=["question", "answer"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        store = self._vector_store or (runtime_context or {}).get("vector_store")
        if store is None:
            return {"status": "error", "message": "Vector store not configured"}
        question = str(args.get("question", ""))
        answer = str(args.get("answer", ""))
        tags = args.get("tags", [])
        try:
            import uuid
            content = f"Q: {question}\nA: {answer}"
            store.upsert(
                id=str(uuid.uuid4()),
                vector=None,
                metadata={"type": "answer", "question": question, "tags": tags},
                text=content,
            )
            return {"status": "ok", "message": f"Stored answer for: {question[:50]}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class StoreVectorCodeTool(BaseTool):
    """Store a code snippet to the pgvector database."""

    def __init__(self, vector_store: Optional[Any] = None):
        self._vector_store = vector_store
        super().__init__(
            ToolSpec(
                name="store_vector_code",
                description="Store a code snippet directly to the vector database.",
                parameters={
                    "description": {"type": "string", "description": "Code description"},
                    "code": {"type": "string", "description": "The code content"},
                    "language": {"type": "string", "description": "Programming language"},
                },
                required=["description", "code"],
            )
        )

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        store = self._vector_store or (runtime_context or {}).get("vector_store")
        if store is None:
            return {"status": "error", "message": "Vector store not configured"}
        description = str(args.get("description", ""))
        code = str(args.get("code", ""))
        language = str(args.get("language", ""))
        try:
            import uuid
            store.upsert(
                id=str(uuid.uuid4()),
                vector=None,
                metadata={"type": "code", "language": language, "description": description},
                text=code,
            )
            return {"status": "ok", "message": f"Stored code: {description[:50]}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


__all__ = ["StoreVectorGuideTool", "StoreVectorAnswerTool", "StoreVectorCodeTool"]
