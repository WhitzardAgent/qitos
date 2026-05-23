from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ROOT_STR = str(ROOT)

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
