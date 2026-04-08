"""Desktop environment providers inspired by OSWorld, with a container-first default."""

from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from qitos.core.multimodal import guess_mime_type

from .actions import action_result_payload, normalize_gui_action


class DesktopProvider(ABC):
    """Abstract desktop runtime provider."""

    name: str = "desktop_provider"
    version: str = "0.1"

    @abstractmethod
    def start(self) -> None:
        """Start or attach to the runtime."""

    @abstractmethod
    def reset(self, task: Any = None, workspace: Optional[str] = None) -> None:
        """Reset provider state for one task run."""

    @abstractmethod
    def stop(self) -> None:
        """Stop or release provider resources."""

    @abstractmethod
    def capture_state(self) -> Dict[str, Any]:
        """Return screenshot/a11y/terminal/instruction-like state."""

    @abstractmethod
    def execute_action(self, action: Mapping[str, Any], state: Any = None) -> Dict[str, Any]:
        """Execute one normalized GUI action."""

    def health_check(self) -> Dict[str, Any]:
        return {"ok": True, "provider": self.name, "version": self.version}


class MockDesktopProvider(DesktopProvider):
    """Deterministic in-memory desktop provider for smoke tests and local examples."""

    name = "mock_desktop"
    version = "0.1"

    def __init__(
        self,
        *,
        screenshot_path: str,
        instruction: str = "",
        accessibility_tree: Any = None,
        terminal: str = "",
        dom: Any = None,
        ocr: Optional[List[Dict[str, Any]]] = None,
        ui_candidates: Optional[List[Dict[str, Any]]] = None,
        screen_size: tuple[int, int] = (1920, 1080),
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.screenshot_path = str(Path(screenshot_path).expanduser().resolve())
        self.instruction = str(instruction or "")
        self.accessibility_tree = accessibility_tree
        self.terminal = str(terminal or "")
        self.dom = dom
        self.ocr = list(ocr or [])
        self.ui_candidates = list(ui_candidates or [])
        self.screen_size = tuple(screen_size)
        self.metadata = dict(metadata or {})
        self.actions: List[Dict[str, Any]] = []
        self.started = False

    def start(self) -> None:
        self.started = True

    def reset(self, task: Any = None, workspace: Optional[str] = None) -> None:
        _ = task
        _ = workspace
        self.started = True
        self.actions = []

    def stop(self) -> None:
        self.started = False

    def capture_state(self) -> Dict[str, Any]:
        return {
            "screenshot": {
                "path": self.screenshot_path,
                "mime_type": guess_mime_type(self.screenshot_path),
                "detail": "original",
            },
            "accessibility_tree": self.accessibility_tree,
            "terminal": self.terminal,
            "dom": self.dom,
            "ocr": list(self.ocr),
            "ui_candidates": list(self.ui_candidates),
            "instruction": self.instruction,
            "screen_size": {"width": int(self.screen_size[0]), "height": int(self.screen_size[1])},
            "metadata": dict(self.metadata),
            "action_history": list(self.actions),
        }

    def execute_action(self, action: Mapping[str, Any], state: Any = None) -> Dict[str, Any]:
        _ = state
        normalized = normalize_gui_action(action)
        self.actions.append(normalized)
        message = f"Executed {normalized['action_type']} in mock desktop runtime."
        return action_result_payload(
            action=normalized,
            status="success",
            message=message,
            provider=self.name,
            metadata={"screen_size": list(self.screen_size)},
        )


class ContainerDesktopProvider(DesktopProvider):
    """Container-first desktop provider with deterministic screenshot observation.

    This provider is intentionally lightweight in v0.5 phase 1: it verifies a Docker
    container, captures screenshot-backed state from configured assets, and records
    normalized GUI actions for a harness/controller to execute. It keeps the provider
    boundary aligned with OSWorld's desktop_env without hard-wiring provider-native
    model protocols into QitOS.
    """

    name = "container_desktop"
    version = "0.1"

    def __init__(
        self,
        *,
        container: str,
        screenshot_path: str,
        workspace_root: str = "/workspace",
        instruction: str = "",
        accessibility_tree: Any = None,
        terminal: str = "",
        dom: Any = None,
        ocr: Optional[List[Dict[str, Any]]] = None,
        ui_candidates: Optional[List[Dict[str, Any]]] = None,
        screen_size: tuple[int, int] = (1920, 1080),
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.container = str(container or "").strip()
        self.workspace_root = str(workspace_root or "/workspace")
        self.screenshot_path = str(Path(screenshot_path).expanduser().resolve())
        self.instruction = str(instruction or "")
        self.accessibility_tree = accessibility_tree
        self.terminal = str(terminal or "")
        self.dom = dom
        self.ocr = list(ocr or [])
        self.ui_candidates = list(ui_candidates or [])
        self.screen_size = tuple(screen_size)
        self.metadata = dict(metadata or {})
        self.actions: List[Dict[str, Any]] = []
        self.started = False

    def start(self) -> None:
        self._ensure_container_exists()
        self.started = True

    def reset(self, task: Any = None, workspace: Optional[str] = None) -> None:
        _ = task
        _ = workspace
        self._ensure_container_exists()
        self.actions = []
        self.started = True

    def stop(self) -> None:
        self.started = False

    def capture_state(self) -> Dict[str, Any]:
        return {
            "screenshot": {
                "path": self.screenshot_path,
                "mime_type": guess_mime_type(self.screenshot_path),
                "detail": "original",
            },
            "accessibility_tree": self.accessibility_tree,
            "terminal": self.terminal,
            "dom": self.dom,
            "ocr": list(self.ocr),
            "ui_candidates": list(self.ui_candidates),
            "instruction": self.instruction,
            "screen_size": {"width": int(self.screen_size[0]), "height": int(self.screen_size[1])},
            "metadata": {
                "container": self.container,
                "workspace_root": self.workspace_root,
                **dict(self.metadata),
            },
            "action_history": list(self.actions),
        }

    def execute_action(self, action: Mapping[str, Any], state: Any = None) -> Dict[str, Any]:
        _ = state
        normalized = normalize_gui_action(action)
        self.actions.append(normalized)
        return action_result_payload(
            action=normalized,
            status="accepted",
            message=f"Queued {normalized['action_type']} for container desktop runtime.",
            provider=self.name,
            metadata={"container": self.container, "workspace_root": self.workspace_root},
        )

    def health_check(self) -> Dict[str, Any]:
        if not self.container:
            return {"ok": False, "message": "container is empty", "provider": self.name}
        try:
            proc = subprocess.run(
                ["docker", "inspect", self.container],
                capture_output=True,
                text=True,
                timeout=20,
            )
        except Exception as exc:
            return {"ok": False, "message": str(exc), "provider": self.name}
        if proc.returncode != 0:
            return {
                "ok": False,
                "message": "docker inspect failed",
                "stderr": proc.stderr,
                "provider": self.name,
                "container": self.container,
            }
        return {
            "ok": True,
            "provider": self.name,
            "container": self.container,
            "workspace_root": self.workspace_root,
        }

    def _ensure_container_exists(self) -> None:
        health = self.health_check()
        if not bool(health.get("ok", False)):
            raise RuntimeError(str(health.get("message", "container desktop provider unavailable")))


__all__ = [
    "ContainerDesktopProvider",
    "DesktopProvider",
    "MockDesktopProvider",
]
