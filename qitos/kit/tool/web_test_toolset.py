"""Compatibility shim for experimental web testing tools.

These tools are explicit opt-in and now live under
`qitos.kit.tool.experimental.security_research`.
"""

from qitos.kit.tool.experimental.security_research.web_test_toolset import WebTestToolSet

__all__ = ["WebTestToolSet"]
