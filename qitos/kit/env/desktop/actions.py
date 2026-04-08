"""Provider-neutral GUI action vocabulary inspired by OSWorld ACTION_SPACE."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping


KEYBOARD_KEYS = [
    "tab",
    "enter",
    "esc",
    "escape",
    "space",
    "backspace",
    "delete",
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "pageup",
    "pagedown",
    "ctrl",
    "shift",
    "alt",
    "meta",
    "command",
    "option",
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "f6",
    "f7",
    "f8",
    "f9",
    "f10",
    "f11",
    "f12",
]

GUI_ACTION_NAMES = (
    "move_to",
    "click",
    "mouse_down",
    "mouse_up",
    "right_click",
    "double_click",
    "drag_to",
    "scroll",
    "type_text",
    "press_key",
    "key_down",
    "key_up",
    "hotkey",
    "wait",
    "done",
    "fail",
)

ACTION_ALIASES = {
    "move": "move_to",
    "mousemove": "move_to",
    "moveto": "move_to",
    "type": "type_text",
    "typing": "type_text",
    "keypress": "press_key",
    "press": "press_key",
    "doubleclick": "double_click",
    "rightclick": "right_click",
    "drag": "drag_to",
}

OSWORLD_ACTION_MAP = {
    "MOVE_TO": "move_to",
    "CLICK": "click",
    "MOUSE_DOWN": "mouse_down",
    "MOUSE_UP": "mouse_up",
    "RIGHT_CLICK": "right_click",
    "DOUBLE_CLICK": "double_click",
    "DRAG_TO": "drag_to",
    "SCROLL": "scroll",
    "TYPING": "type_text",
    "PRESS": "press_key",
    "KEY_DOWN": "key_down",
    "KEY_UP": "key_up",
    "HOTKEY": "hotkey",
    "WAIT": "wait",
    "DONE": "done",
    "FAIL": "fail",
}


def normalize_gui_action_name(name: str) -> str:
    token = str(name or "").strip()
    if not token:
        return ""
    upper = token.upper()
    if upper in OSWORLD_ACTION_MAP:
        return OSWORLD_ACTION_MAP[upper]
    lowered = token.strip().lower().replace("-", "_").replace(" ", "_")
    return ACTION_ALIASES.get(lowered, lowered)


def normalize_gui_action(payload: Mapping[str, Any]) -> Dict[str, Any]:
    action_type = normalize_gui_action_name(
        str(payload.get("action_type") or payload.get("name") or "")
    )
    args = dict(payload.get("args") or {}) if isinstance(payload.get("args"), Mapping) else {}
    for key, value in payload.items():
        if key in {"action_type", "name", "args", "metadata"}:
            continue
        args.setdefault(str(key), value)
    metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), Mapping) else {}
    return {
        "name": action_type,
        "action_type": action_type,
        "args": args,
        "metadata": metadata,
    }


def to_osworld_action(payload: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = normalize_gui_action(payload)
    action_type = normalized["action_type"]
    reverse = {v: k for k, v in OSWORLD_ACTION_MAP.items()}
    osworld_type = reverse.get(action_type, action_type.upper())
    result: Dict[str, Any] = {"action_type": osworld_type}
    result.update(dict(normalized.get("args") or {}))
    return result


def action_result_payload(
    *,
    action: Mapping[str, Any],
    status: str = "success",
    message: str = "",
    provider: str = "desktop",
    metadata: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized = normalize_gui_action(action)
    payload = {
        "status": str(status or "success"),
        "action": normalized,
        "provider": str(provider or "desktop"),
    }
    if str(message or "").strip():
        payload["message"] = str(message)
    if isinstance(metadata, Mapping) and metadata:
        payload["metadata"] = dict(metadata)
    return payload


def supported_gui_actions() -> List[str]:
    return list(GUI_ACTION_NAMES)


__all__ = [
    "ACTION_ALIASES",
    "GUI_ACTION_NAMES",
    "KEYBOARD_KEYS",
    "OSWORLD_ACTION_MAP",
    "action_result_payload",
    "normalize_gui_action",
    "normalize_gui_action_name",
    "supported_gui_actions",
    "to_osworld_action",
]
