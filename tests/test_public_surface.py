from __future__ import annotations

import importlib
import sys


def test_top_level_public_core_symbols_importable() -> None:
    import qitos

    stable = {
        "AgentModule",
        "StateSchema",
        "Decision",
        "Action",
        "BaseTool",
        "ToolRegistry",
        "Engine",
        "RunSpec",
        "ExperimentSpec",
        "BenchmarkRunResult",
    }
    assert stable.issubset(set(qitos.__all__))
    for name in stable:
        assert getattr(qitos, name) is not None


def test_top_level_does_not_export_product_or_security_symbols() -> None:
    import qitos

    forbidden = {
        "ClaudeCodeAgent",
        "PentAGIRunner",
        "SecurityAuditToolSet",
        "security_audit_tools",
        "WhitzardAgent",
        "SkillHubGitHubAgent",
        "EpubReaderAgent",
        "ComputerUseAgent",
    }
    exported = set(qitos.__all__)
    assert forbidden.isdisjoint(exported)
    for name in forbidden:
        assert not hasattr(qitos, name)


def test_import_qitos_has_no_experimental_security_side_effects() -> None:
    for name in list(sys.modules):
        if name.startswith("qitos.kit.tool.experimental.security_research"):
            del sys.modules[name]

    importlib.import_module("qitos")

    assert "qitos.kit.tool.experimental.security_research" not in sys.modules


def test_import_qitos_kit_has_no_experimental_security_side_effects() -> None:
    for name in list(sys.modules):
        if name.startswith("qitos.kit.tool.experimental.security_research"):
            del sys.modules[name]

    importlib.import_module("qitos.kit")

    assert "qitos.kit.tool.experimental.security_research" not in sys.modules
