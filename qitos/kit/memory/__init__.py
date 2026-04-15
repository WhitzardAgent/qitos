"""Concrete memory implementations."""

from qitos.core.memory import Memory, MemoryRecord

from .markdown_file_memory import MarkdownFileMemory
from .memdir_memory import MemdirMemory
from .window_memory import WindowMemory
from .summary_memory import SummaryMemory
from .vector_memory import VectorMemory


def window_memory(window_size: int = 20) -> WindowMemory:
    return WindowMemory(window_size=window_size)


def summary_memory(keep_last: int = 10) -> SummaryMemory:
    return SummaryMemory(keep_last=keep_last)


def vector_memory(top_k: int = 5) -> VectorMemory:
    return VectorMemory(top_k=top_k)


def markdown_file_memory(
    path: str = "memory.md", max_in_memory: int = 200
) -> MarkdownFileMemory:
    return MarkdownFileMemory(path=path, max_in_memory=max_in_memory)


def memdir_memory(
    memory_dir: str = ".qitos/memory",
    global_memory_dir: str | None = None,
) -> MemdirMemory:
    return MemdirMemory(memory_dir=memory_dir, global_memory_dir=global_memory_dir)


__all__ = [
    "Memory",
    "MemoryRecord",
    "MarkdownFileMemory",
    "MemdirMemory",
    "WindowMemory",
    "SummaryMemory",
    "VectorMemory",
    "markdown_file_memory",
    "memdir_memory",
    "window_memory",
    "summary_memory",
    "vector_memory",
]
