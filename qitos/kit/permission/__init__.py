"""Permission and safety system for QitOS."""

from .pipeline import PermissionMode, PermissionPipeline
from .bash_analyzer import BashCommandAnalyzer, CommandSafety, BashAnalysisResult
from .read_before_write import ReadBeforeWriteEnforcer, FileReadState
from .auto_classifier import AutoPermissionClassifier
from .rules import (
    PROTECTED_PATHS,
    build_default_deny_rules,
    build_default_ask_rules,
    is_protected_path,
)

__all__ = [
    "PermissionMode",
    "PermissionPipeline",
    "BashCommandAnalyzer",
    "CommandSafety",
    "BashAnalysisResult",
    "ReadBeforeWriteEnforcer",
    "FileReadState",
    "AutoPermissionClassifier",
    "PROTECTED_PATHS",
    "build_default_deny_rules",
    "build_default_ask_rules",
    "is_protected_path",
]
