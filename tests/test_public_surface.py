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


def test_broad_kit_toolset_surface_stays_security_free() -> None:
    module = importlib.import_module("qitos.kit.toolset")

    exported = set(getattr(module, "__all__", []))
    forbidden = {
        "SecurityAuditToolSet",
        "security_audit_tools",
        "ReconToolSet",
        "NetworkToolSet",
        "ExploitToolSet",
        "PasswordToolSet",
        "VulnScanToolSet",
        "WebTestToolSet",
    }

    assert forbidden.isdisjoint(exported)
    for name in forbidden:
        assert not hasattr(module, name)


def test_import_qitos_workflow_does_not_require_qitos_dag() -> None:
    preexisting_dag_modules = {
        name for name in sys.modules if name == "qitos_dag" or name.startswith("qitos_dag.")
    }
    for name in list(sys.modules):
        if name == "qitos.workflow" or name.startswith("qitos.workflow."):
            del sys.modules[name]

    module = importlib.import_module("qitos.workflow")

    assert module.__all__
    new_dag_modules = {
        name
        for name in sys.modules
        if (name == "qitos_dag" or name.startswith("qitos_dag."))
        and name not in preexisting_dag_modules
    }
    assert not new_dag_modules
