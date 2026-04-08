"""Atomic GUI/computer-use tools backed by DesktopEnv controller ops."""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult

from qitos.kit.env.desktop.actions import GUI_ACTION_NAMES, KEYBOARD_KEYS, normalize_gui_action


def _controller_from_context(runtime_context: Optional[Dict[str, Any]]) -> Any:
    runtime_context = dict(runtime_context or {})
    ops = runtime_context.get("ops")
    if isinstance(ops, dict):
        controller = ops.get("gui_controller")
        if controller is not None:
            return controller
    env = runtime_context.get("env")
    if env is not None and hasattr(env, "get_ops"):
        return env.get_ops("gui_controller")
    return None


class _GUIActionTool(BaseTool):
    action_name: str = ""
    parameters: Dict[str, Dict[str, Any]] = {}
    required: list[str] = []
    read_only: bool = False
    user_interaction: bool = True

    def __init__(self) -> None:
        spec = ToolSpec(
            name=self.action_name,
            description="",
            parameters=dict(self.parameters),
            required=list(self.required),
            permissions=ToolPermission(),
            required_ops=["gui_controller"],
            input_schema={
                "type": "object",
                "properties": dict(self.parameters),
                "required": list(self.required),
            },
            read_only=self.read_only,
            concurrency_safe=False,
            requires_user_interaction=self.user_interaction,
        )
        super().__init__(spec)

    def execute(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> Any:
        controller = _controller_from_context(runtime_context)
        if controller is None:
            raise RuntimeError("GUI controller ops are not available in the current runtime context")
        payload = normalize_gui_action({"name": self.action_name, "args": dict(args or {})})
        return controller.perform(payload, state=(runtime_context or {}).get("state"))


class MoveTo(_GUIActionTool):
    """Move the cursor to a grounded screen coordinate."""

    action_name = "move_to"
    parameters = {"x": {"type": "number"}, "y": {"type": "number"}}
    required = ["x", "y"]


class Click(_GUIActionTool):
    """Click one pointer button at the current or specified coordinate."""

    action_name = "click"
    parameters = {
        "x": {"type": "number"},
        "y": {"type": "number"},
        "button": {"type": "string"},
        "num_clicks": {"type": "integer"},
        "keys": {"type": "array"},
    }


class MouseDown(_GUIActionTool):
    """Press and hold one mouse button."""

    action_name = "mouse_down"
    parameters = {"button": {"type": "string"}}


class MouseUp(_GUIActionTool):
    """Release one mouse button."""

    action_name = "mouse_up"
    parameters = {"button": {"type": "string"}}


class RightClick(_GUIActionTool):
    """Right click at the current or specified coordinate."""

    action_name = "right_click"
    parameters = {"x": {"type": "number"}, "y": {"type": "number"}, "keys": {"type": "array"}}


class DoubleClick(_GUIActionTool):
    """Double click at the current or specified coordinate."""

    action_name = "double_click"
    parameters = {"x": {"type": "number"}, "y": {"type": "number"}, "keys": {"type": "array"}}


class DragTo(_GUIActionTool):
    """Drag the pointer to the given coordinate."""

    action_name = "drag_to"
    parameters = {"x": {"type": "number"}, "y": {"type": "number"}, "keys": {"type": "array"}}
    required = ["x", "y"]


class Scroll(_GUIActionTool):
    """Scroll by the given deltas."""

    action_name = "scroll"
    parameters = {"dx": {"type": "integer"}, "dy": {"type": "integer"}, "keys": {"type": "array"}}
    required = ["dx", "dy"]


class TypeText(_GUIActionTool):
    """Type the given text into the active desktop target."""

    action_name = "type_text"
    parameters = {"text": {"type": "string"}}
    required = ["text"]


class PressKey(_GUIActionTool):
    """Press and release one key."""

    action_name = "press_key"
    parameters = {"key": {"type": "string"}}
    required = ["key"]

    def validate_input(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> ToolValidationResult:
        _ = runtime_context
        key = str(args.get("key") or "").strip().lower()
        if key and key not in KEYBOARD_KEYS:
            return ToolValidationResult.fail(f"Unsupported key: {key}")
        return ToolValidationResult.ok()


class KeyDown(PressKey):
    """Hold one key down."""

    action_name = "key_down"


class KeyUp(PressKey):
    """Release one key."""

    action_name = "key_up"


class Hotkey(_GUIActionTool):
    """Press a key combination such as ctrl+s."""

    action_name = "hotkey"
    parameters = {"keys": {"type": "array"}}
    required = ["keys"]

    def validate_input(self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None) -> ToolValidationResult:
        _ = runtime_context
        keys = args.get("keys")
        if not isinstance(keys, list) or not keys:
            return ToolValidationResult.fail("keys must be a non-empty list")
        invalid = [str(item) for item in keys if str(item).strip().lower() not in KEYBOARD_KEYS]
        if invalid:
            return ToolValidationResult.fail(f"Unsupported keys: {', '.join(invalid)}")
        return ToolValidationResult.ok()


class Wait(_GUIActionTool):
    """Wait for the UI to change without sending new input."""

    action_name = "wait"
    parameters = {"duration": {"type": "number"}}


class Done(_GUIActionTool):
    """Mark the current desktop task as complete."""

    action_name = "done"
    parameters = {"answer": {"type": "string"}}


class Fail(_GUIActionTool):
    """Mark the current desktop task as infeasible or failed."""

    action_name = "fail"
    parameters = {"reason": {"type": "string"}}


__all__ = [
    "Click",
    "DoubleClick",
    "Done",
    "DragTo",
    "Fail",
    "GUI_ACTION_NAMES",
    "Hotkey",
    "KeyDown",
    "KeyUp",
    "MouseDown",
    "MouseUp",
    "MoveTo",
    "PressKey",
    "RightClick",
    "Scroll",
    "TypeText",
    "Wait",
]
