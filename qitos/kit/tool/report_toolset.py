"""Compatibility shim for reporting tools.

Prefer importing `ReportToolSet` from `qitos.kit.tool.report` in new code.
"""

from qitos.kit.tool.report.toolset import ReportToolSet

__all__ = ["ReportToolSet"]
