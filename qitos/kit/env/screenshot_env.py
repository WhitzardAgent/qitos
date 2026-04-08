"""Minimal screenshot-first multimodal environment."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from ...core import (
    Env,
    EnvObservation,
    EnvStepResult,
    GUIControllerCapability,
    GUIObserverCapability,
)
from ...core.multimodal import ObservationPack, guess_mime_type


class ScreenshotObserverOps(GUIObserverCapability):
    """Simple observer that serves one local screenshot plus optional metadata."""

    def __init__(
        self,
        screenshot_path: str,
        *,
        text: str = "",
        detail: str = "high",
        dom: Any = None,
        accessibility_tree: Any = None,
        ocr: Optional[list[dict[str, Any]]] = None,
        ui_candidates: Optional[list[dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.screenshot_path = str(Path(screenshot_path).expanduser().resolve())
        self.text = str(text or "")
        self.detail = str(detail or "high")
        self.dom = dom
        self.accessibility_tree = accessibility_tree
        self.ocr = list(ocr or [])
        self.ui_candidates = list(ui_candidates or [])
        self.metadata = dict(metadata or {})

    def capture_observation(self, state: Any = None) -> Dict[str, Any]:
        _ = state
        screenshot = {
            "path": self.screenshot_path,
            "mime_type": guess_mime_type(self.screenshot_path),
            "detail": self.detail,
        }
        pack = ObservationPack(
            text=self.text,
            screenshot=screenshot,
            dom=self.dom,
            accessibility_tree=self.accessibility_tree,
            ocr=self.ocr,
            ui_candidates=self.ui_candidates,
            metadata=self.metadata,
        )
        return pack.to_dict()


class MockGUIControllerOps(GUIControllerCapability):
    """No-op GUI controller for deterministic smoke runs."""

    def __init__(self) -> None:
        self.actions: list[dict[str, Any]] = []

    def perform(self, action: Dict[str, Any], state: Any = None) -> Dict[str, Any]:
        _ = state
        payload = dict(action or {})
        self.actions.append(payload)
        return {"status": "ok", "action": payload}


class ScreenshotEnv(Env):
    """Minimal multimodal env exposing a screenshot observation and mock GUI control."""

    name = "screenshot"
    version = "0.1"

    def __init__(
        self,
        screenshot_path: str,
        *,
        text: str = "",
        detail: str = "high",
        dom: Any = None,
        accessibility_tree: Any = None,
        ocr: Optional[list[dict[str, Any]]] = None,
        ui_candidates: Optional[list[dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.observer = ScreenshotObserverOps(
            screenshot_path,
            text=text,
            detail=detail,
            dom=dom,
            accessibility_tree=accessibility_tree,
            ocr=ocr,
            ui_candidates=ui_candidates,
            metadata=metadata,
        )
        self.controller = MockGUIControllerOps()

    def reset(
        self, task: Any = None, workspace: Optional[str] = None, **kwargs: Any
    ) -> EnvObservation:
        _ = task
        _ = workspace
        _ = kwargs
        return self.observe()

    def observe(self, state: Any = None) -> EnvObservation:
        payload = self.observer.capture_observation(state=state)
        return EnvObservation(
            data={"multimodal": payload},
            metadata={"modalities": ["screenshot"]},
        )

    def step(self, action: Any, state: Any = None) -> EnvStepResult:
        action_payload = dict(action or {}) if isinstance(action, dict) else {"value": action}
        controller_result = self.controller.perform(action_payload, state=state)
        return EnvStepResult(
            observation=self.observe(state=state),
            done=False,
            info={"controller": controller_result},
        )

    def get_ops(self, group: str) -> Any:
        name = str(group or "").strip().lower()
        if name == "gui_observer":
            return self.observer
        if name == "gui_controller":
            return self.controller
        return None


__all__ = ["ScreenshotEnv", "ScreenshotObserverOps", "MockGUIControllerOps"]
