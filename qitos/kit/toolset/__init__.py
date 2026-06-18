"""Scenario-oriented preset toolsets and registry builders."""

from __future__ import annotations

from qitos.kit.tool.notebook import NotebookToolSet
from qitos.kit.tool.report import ReportToolSet
from qitos.kit.tool.skill import SkillToolSet
from qitos.kit.tool.task import TaskToolSet
from qitos.kit.tool.thinking import ThinkingToolSet
from qitos.kit.tool.toolset import BaseToolSet, StaticToolSet, ToolSet, toolset_from_tools
from .advanced import AdvancedCodingToolSet, advanced_coding_tools
from .builders import math_tools
from .codebase import CodebaseToolSet, FilesToolSet, codebase_tools
from .computer_use import ComputerUseToolSet, computer_use_tools
from .coding import CodingToolSet, FullCodingToolSet, coding_tools
from .editor import EditorToolSet, editor_tools
from .epub import EpubToolSet, epub_tools
from .notebook import notebook_tools
from .report import report_tools
from .task import task_tools
from .thinking import thinking_tools
from .web import WebToolSet, web_tools

__all__ = [
    "AdvancedCodingToolSet",
    "BaseToolSet",
    "CodebaseToolSet",
    "ComputerUseToolSet",
    "CodingToolSet",
    "EditorToolSet",
    "EpubToolSet",
    "FilesToolSet",
    "FullCodingToolSet",
    "NotebookToolSet",
    "ReportToolSet",
    "SkillToolSet",
    "StaticToolSet",
    "TaskToolSet",
    "ThinkingToolSet",
    "ToolSet",
    "WebToolSet",
    "advanced_coding_tools",
    "codebase_tools",
    "computer_use_tools",
    "coding_tools",
    "editor_tools",
    "epub_tools",
    "math_tools",
    "notebook_tools",
    "report_tools",
    "task_tools",
    "thinking_tools",
    "toolset_from_tools",
    "web_tools",
]
