"""Long-term memory implementing the memdir protocol over QitOS Memory ABC.

The memdir system provides a file-based, typed, cross-session persistent
memory architecture with four types: user, feedback, project, reference.
Each memory is a Markdown file with YAML frontmatter, indexed by MEMORY.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from qitos.core.memory import Memory, MemoryRecord

VALID_MEMORY_TYPES = {"user", "feedback", "project", "reference"}
INDEX_FILENAME = "MEMORY.md"
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25_000
ENTRY_MAX_CHARS = 150


class CyberGymMemory(Memory):
    """Long-term memory implementing the memdir protocol over QitOS Memory ABC.

    Each memory type is stored in its own subdirectory:
    - user/  -> global preferences (cross-project)
    - feedback/ -> verified behavioral rules
    - project/ -> task-specific knowledge
    - reference/ -> external resource pointers

    The MEMORY.md index file serves as the entry point, auto-loaded into
    context at the start of each task.
    """

    def __init__(
        self,
        memory_dir: str,
        global_memory_dir: str | None = None,
    ):
        self.memory_dir = Path(memory_dir)
        self.global_memory_dir = (
            Path(global_memory_dir) if global_memory_dir else None
        )
        self._records: List[MemoryRecord] = []
        self._index_cache: str | None = None

    def append(self, record: MemoryRecord) -> None:
        """Save a new memory file and update the index."""
        mem_type = record.metadata.get("type", "project")
        if mem_type not in VALID_MEMORY_TYPES:
            mem_type = "project"

        name = record.metadata.get("name", f"memory_{record.step_id}")
        description = record.metadata.get("description", "")[:ENTRY_MAX_CHARS]

        # user-type memories go to global directory
        target_dir = (
            self.global_memory_dir / "user"
            if mem_type == "user" and self.global_memory_dir
            else self.memory_dir / mem_type
        )
        target_dir.mkdir(parents=True, exist_ok=True)

        # Write memory file with frontmatter
        file_path = target_dir / f"{name}.md"
        content = record.content if isinstance(record.content, str) else str(record.content)
        file_path.write_text(
            f"---\nname: {name}\ndescription: {description}\ntype: {mem_type}\n---\n\n{content}",
            encoding="utf-8",
        )

        # Update index
        self._update_index(mem_type, name, description)
        self._records.append(record)
        self._index_cache = None  # invalidate cache

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> Any:
        """Retrieve memories, loading the index and matching files."""
        query = query or {}
        mem_type = query.get("type")
        text_query = query.get("text", "")

        # If type filter specified, load only that type's files
        if mem_type and mem_type in VALID_MEMORY_TYPES:
            dirs = [self.memory_dir / mem_type]
            if mem_type == "user" and self.global_memory_dir:
                dirs.append(self.global_memory_dir / "user")
        else:
            dirs = [self.memory_dir / t for t in VALID_MEMORY_TYPES if t != "user"]
            if self.global_memory_dir:
                dirs.append(self.global_memory_dir / "user")

        records: List[MemoryRecord] = []
        for d in dirs:
            if not d.exists():
                continue
            for f in d.glob("*.md"):
                records.append(self._read_memory_file(f))

        if text_query:
            records = [
                r for r in records if text_query.lower() in str(r.content).lower()
            ]

        return records

    def summarize(self, max_items: int = 5) -> str:
        """Return the MEMORY.md index as a summary."""
        return self._load_index() or ""

    def evict(self) -> int:
        """Evict by truncating the index if it exceeds limits."""
        index_path = self.memory_dir / INDEX_FILENAME
        if not index_path.exists():
            return 0
        content = index_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        original = len(lines)
        if len(lines) > INDEX_MAX_LINES:
            lines = lines[:INDEX_MAX_LINES]
            content = "\n".join(lines)
        if len(content.encode("utf-8")) > INDEX_MAX_BYTES:
            while len(content.encode("utf-8")) > INDEX_MAX_BYTES and lines:
                lines.pop()
            content = "\n".join(lines)
        index_path.write_text(content, encoding="utf-8")
        return original - len(lines)

    def reset(self, run_id: Optional[str] = None) -> None:
        """Reset in-memory records but preserve files (long-term memory persists)."""
        self._records = []
        self._index_cache = None

    def load_index_content(self) -> str:
        """Public accessor for the MEMORY.md index content."""
        return self._load_index()

    def _load_index(self) -> str:
        if self._index_cache is not None:
            return self._index_cache
        index_path = self.memory_dir / INDEX_FILENAME
        if index_path.exists():
            content = index_path.read_text(encoding="utf-8")
            self._index_cache = content
            return content
        return ""

    def _update_index(self, mem_type: str, name: str, description: str) -> None:
        index_path = self.memory_dir / INDEX_FILENAME
        index_path.parent.mkdir(parents=True, exist_ok=True)

        entry = f"- [{name}]({mem_type}/{name}.md) -- {description}"
        if len(entry) > ENTRY_MAX_CHARS:
            entry = entry[: ENTRY_MAX_CHARS - 3] + "..."

        existing = self._load_index()
        lines = existing.splitlines() if existing else []

        # Remove old entry for same name if exists
        lines = [l for l in lines if f"]({mem_type}/{name}.md)" not in l]
        lines.append(entry)

        # Enforce limits
        if len(lines) > INDEX_MAX_LINES:
            lines = lines[-INDEX_MAX_LINES:]
        content = "\n".join(lines)
        if len(content.encode("utf-8")) > INDEX_MAX_BYTES:
            while len(content.encode("utf-8")) > INDEX_MAX_BYTES and lines:
                lines.pop(0)
            content = "\n".join(lines)

        index_path.write_text(content, encoding="utf-8")
        self._index_cache = content

    def _read_memory_file(self, path: Path) -> MemoryRecord:
        raw = path.read_text(encoding="utf-8")
        metadata: Dict[str, Any] = {}
        content = raw

        # Parse YAML frontmatter
        if raw.startswith("---"):
            parts = raw.split("---", 2)
            if len(parts) >= 3:
                try:
                    metadata = yaml.safe_load(parts[1]) or {}
                except Exception:
                    pass
                content = parts[2].strip()

        return MemoryRecord(
            role=metadata.get("type", "project"),
            content=content,
            step_id=0,
            metadata=metadata,
        )
