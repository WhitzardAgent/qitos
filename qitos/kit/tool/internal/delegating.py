"""Shared delegating wrappers for stable atomic tool exports."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, FunctionTool, ToolPermissionDecision, ToolValidationResult


class DelegatingTool(BaseTool):
    """Thin BaseTool adapter that delegates behavior to one callable tool."""

    def __init__(self, delegate: Any):
        self._delegate = FunctionTool(delegate)
        super().__init__(deepcopy(self._delegate.spec))
        self.spec.description = str(self._delegate.spec.description)

    def validate_input(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolValidationResult:
        return self._delegate.validate_input(args, runtime_context=runtime_context)

    def check_permissions(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolPermissionDecision:
        return self._delegate.check_permissions(args, runtime_context=runtime_context)

    def run(self, **kwargs: Any) -> Any:
        return self._delegate.run(**kwargs)

    def call(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        return self._delegate.call(args, runtime_context=runtime_context)

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Any:
        return self._delegate.execute(args, runtime_context=runtime_context)


__all__ = ["DelegatingTool"]
