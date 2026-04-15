"""Memdir-style file memory with a lightweight MEMORY.md index."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from qitos.core.memory import Memory, MemoryRecord

_VALID_TYPES = {"user", "feedback", "project", "reference", "runtime"}


class MemdirMemory(Memory):
    """Persist memory records into markdown files with YAML frontmatter."""

    def __init__(
        self,
        memory_dir: str = ".qitos/memory",
        *,
        global_memory_dir: str | None = None,
        max_index_entries: int = 200,
        max_index_chars: int = 25_000,
    ):
        self.memory_dir = Path(memory_dir).expanduser().resolve()
        self.global_memory_dir = (
            Path(global_memory_dir).expanduser().resolve()
            if global_memory_dir
            else None
        )
        self.max_index_entries = max(10, int(max_index_entries))
        self.max_index_chars = max(2000, int(max_index_chars))
        self._records: List[MemoryRecord] = []
        self._ensure_layout()

    def append(self, record: MemoryRecord) -> None:
        self._records.append(record)
        memory_type = self._memory_type_from_record(record)
        folder = self.memory_dir / memory_type
        folder.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        stem = _slugify(f"{record.role}_{record.step_id}_{ts}")
        path = folder / f"{stem}.md"
        created_at = datetime.now(timezone.utc).isoformat()
        body = str(record.content or "").strip()
        frontmatter = [
            "---",
            f"type: {memory_type}",
            f"role: {record.role}",
            f"step_id: {int(record.step_id)}",
            f"created_at: {created_at}",
            "---",
            "",
            body,
            "",
        ]
        path.write_text("\n".join(frontmatter), encoding="utf-8")
        self._append_index_entry(path=path, record=record, memory_type=memory_type)

    def retrieve(
        self,
        query: Optional[Dict[str, Any]] = None,
        state: Any = None,
        observation: Any = None,
    ) -> List[MemoryRecord]:
        _ = state
        _ = observation
        query = query or {}
        roles = (
            {str(item) for item in list(query.get("roles") or [])}
            if isinstance(query.get("roles"), list)
            else None
        )
        memory_type = str(query.get("type") or "").strip().lower() or None
        contains = str(query.get("contains") or "").strip().lower() or None
        max_items = max(1, int(query.get("max_items", 50) or 50))

        items: List[MemoryRecord] = []
        for path in self._iter_memory_files():
            parsed = self._read_memory_file(path)
            if parsed is None:
                continue
            if roles and parsed.role not in roles:
                continue
            meta_type = str(parsed.metadata.get("type") or "").strip().lower()
            if memory_type and meta_type != memory_type:
                continue
            if contains and contains not in str(parsed.content).lower():
                continue
            items.append(parsed)
        if self._records:
            items.extend(self._records[-max_items:])
        items = sorted(items, key=lambda item: int(item.step_id))
        if max_items > 0:
            items = items[-max_items:]
        return items

    def summarize(self, max_items: int = 30) -> str:
        index_path = self.memory_dir / "MEMORY.md"
        if not index_path.exists():
            return ""
        text = index_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        if max_items > 0 and len(lines) > max_items + 2:
            lines = lines[:2] + lines[-max_items:]
        joined = "\n".join(lines)
        return joined[: self.max_index_chars]

    def evict(self) -> int:
        if len(self._records) <= self.max_index_entries:
            return 0
        removed = len(self._records) - self.max_index_entries
        self._records = self._records[-self.max_index_entries :]
        return removed

    def reset(self, run_id: Optional[str] = None) -> None:
        _ = run_id
        self._records = []

    def _ensure_layout(self) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        for bucket in sorted(_VALID_TYPES):
            (self.memory_dir / bucket).mkdir(parents=True, exist_ok=True)
        index = self.memory_dir / "MEMORY.md"
        if not index.exists():
            index.write_text(
                "# MEMORY\n\n<!-- memdir index: newest entries appended below -->\n",
                encoding="utf-8",
            )

    def _append_index_entry(
        self, *, path: Path, record: MemoryRecord, memory_type: str
    ) -> None:
        index = self.memory_dir / "MEMORY.md"
        rel = str(path.relative_to(self.memory_dir))
        snippet = str(record.content or "").replace("\n", " ").strip()[:150]
        line = (
            f"- type={memory_type} role={record.role} step={record.step_id} "
            f"path={rel} note={snippet}"
        )
        existing = index.read_text(encoding="utf-8").splitlines()
        header = existing[:2] if len(existing) >= 2 else ["# MEMORY", ""]
        body = existing[2:] if len(existing) >= 2 else []
        body.append(line)
        if len(body) > self.max_index_entries:
            body = body[-self.max_index_entries :]
        merged = "\n".join(header + body).strip() + "\n"
        if len(merged) > self.max_index_chars:
            merged = merged[-self.max_index_chars :]
            if not merged.startswith("# MEMORY"):
                merged = "# MEMORY\n\n" + merged
        index.write_text(merged, encoding="utf-8")

    def _memory_type_from_record(self, record: MemoryRecord) -> str:
        raw = str((record.metadata or {}).get("type") or "").strip().lower()
        if raw in _VALID_TYPES:
            return raw
        role = str(record.role or "").strip().lower()
        if role in {"feedback", "user", "reference"}:
            return role
        return "project"

    def _iter_memory_files(self) -> List[Path]:
        roots = [self.memory_dir]
        if self.global_memory_dir is not None:
            roots.append(self.global_memory_dir)
        files: List[Path] = []
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.md")):
                if path.name == "MEMORY.md":
                    continue
                files.append(path)
        return files

    def _read_memory_file(self, path: Path) -> MemoryRecord | None:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            return None
        metadata: Dict[str, Any] = {}
        body = text
        if text.startswith("---\n"):
            marker = "\n---\n"
            end = text.find(marker, 4)
            if end > 0:
                header = text[4:end]
                body = text[end + len(marker) :]
                for raw in header.splitlines():
                    line = raw.strip()
                    if ":" not in line:
                        continue
                    key, value = line.split(":", 1)
                    metadata[str(key).strip()] = str(value).strip()
        role = str(metadata.get("role") or "memory")
        step_id = 0
        step_raw = metadata.get("step_id")
        if str(step_raw or "").isdigit():
            step_id = int(str(step_raw))
        else:
            hit = re.search(r"step=(\d+)", text)
            if hit:
                step_id = int(hit.group(1))
        metadata.setdefault("path", str(path))
        return MemoryRecord(role=role, content=body.strip(), step_id=step_id, metadata=metadata)


def _slugify(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "memory"


__all__ = ["MemdirMemory"]
