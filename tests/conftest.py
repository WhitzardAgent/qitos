from __future__ import annotations

import socket
import sys
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ROOT_STR = str(ROOT)
RUN_OPTIONAL_INTEGRATIONS = os.getenv("QITOS_RUN_OPTIONAL_INTEGRATION_TESTS") == "1"
OPTIONAL_INTEGRATION_TEST_FILES = {
    "test_auditor_completeness.py",
    "test_auditor_knowledge.py",
    "test_auditor_multi_agent.py",
    "test_claude_code_streaming.py",
    "test_coder_compact_history.py",
    "test_coder_terminal_mode.py",
    "test_cyber_critic_migration.py",
    "test_pentagi_function_tool_migration.py",
    "test_pentagi_handoff_targets.py",
    "test_qitos_auditor_package.py",
    "test_qitos_zoo_package.py",
    "test_workflow_integration.py",
    "test_zoo_eval_configs.py",
}

# Keep the repository root at the front so tests import this checkout's
# `examples` package instead of relying on namespace-package resolution.
if ROOT_STR not in sys.path:
    sys.path.insert(0, ROOT_STR)

# Add qitos_zoo root so `from qitos_zoo.qitos_coder import ...` works.
# The qitos_zoo/ package lives under the project root, which is already on sys.path.
# This entry is kept as a safety net for cases where ROOT is not yet on sys.path.
ZOO_ROOT = str(ROOT / "qitos_zoo")
if ZOO_ROOT not in sys.path and ROOT_STR not in sys.path:
    sys.path.insert(0, ZOO_ROOT)


def _loopback_bind_available() -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return True
    except PermissionError:
        return False
    finally:
        sock.close()


def pytest_collection_modifyitems(config, items):
    _ = config
    if not RUN_OPTIONAL_INTEGRATIONS:
        skip_optional = pytest.mark.skip(
            reason=(
                "optional zoo/workflow integration test; set "
                "QITOS_RUN_OPTIONAL_INTEGRATION_TESTS=1 to run with matching extras"
            )
        )
        for item in items:
            if Path(str(item.path)).name in OPTIONAL_INTEGRATION_TEST_FILES:
                item.add_marker(skip_optional)

    if _loopback_bind_available():
        return
    skip_loopback = pytest.mark.skip(
        reason="loopback socket binding is not available in this sandbox"
    )
    loopback_tests = {
        "test_reflexion_and_computer_use_examples_smoke",
        "test_osworld_setup_and_eval_bridges",
        "test_osworld_runtime_and_desktop_env_use_external_controller",
    }
    for item in items:
        if item.name in loopback_tests:
            item.add_marker(skip_loopback)
