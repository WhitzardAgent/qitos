"""OSWorld-inspired desktop environment components."""

from .actions import GUI_ACTION_NAMES, KEYBOARD_KEYS, normalize_gui_action, to_osworld_action
from .controller import DesktopControllerOps, DesktopObserverOps
from .env import DesktopEnv
from .providers import ContainerDesktopProvider, DesktopProvider, MockDesktopProvider

__all__ = [
    "ContainerDesktopProvider",
    "DesktopControllerOps",
    "DesktopEnv",
    "DesktopObserverOps",
    "DesktopProvider",
    "GUI_ACTION_NAMES",
    "KEYBOARD_KEYS",
    "MockDesktopProvider",
    "normalize_gui_action",
    "to_osworld_action",
]
