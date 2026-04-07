"""Security-audit preset toolset and registry builder."""

from __future__ import annotations

from qitos.core.tool_registry import ToolRegistry
from qitos.kit.tool.security_audit import SecurityAuditToolSet


def security_audit_tools(
    workspace_root: str,
    *,
    include_external: bool = False,
    external_timeout: int = 120,
    max_matches: int = 200,
) -> ToolRegistry:
    """Build a registry containing the code security audit bundle."""
    return ToolRegistry().include_toolset(
        SecurityAuditToolSet(
            workspace_root=workspace_root,
            include_external=include_external,
            external_timeout=external_timeout,
            max_matches=max_matches,
        )
    )


__all__ = ["SecurityAuditToolSet", "security_audit_tools"]
