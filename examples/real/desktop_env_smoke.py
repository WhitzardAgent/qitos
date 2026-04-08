"""Minimal DesktopEnv smoke script for GUI/controller loop verification."""

from __future__ import annotations

from pathlib import Path

from qitos.kit.env import DesktopEnv
from qitos.kit.toolset import computer_use_tools

from examples._support import write_tiny_png


WORKSPACE = Path("./playground/desktop_env_smoke")
SCREENSHOT_FILE = "desktop.png"


def main(smoke: bool = False) -> None:
    _ = smoke
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    screenshot_path = WORKSPACE / SCREENSHOT_FILE
    if not screenshot_path.exists():
        write_tiny_png(screenshot_path)

    env = DesktopEnv.from_mock(
        screenshot_path=str(screenshot_path),
        instruction="Click the visible Continue button and report the result.",
        accessibility_tree={"role": "window", "name": "Desktop Smoke"},
        terminal="$ echo desktop\ndesktop\n$ ",
        ui_candidates=[{"label": "Continue", "role": "button", "x": 640, "y": 420}],
    )
    env.setup()
    first = env.reset()

    registry = computer_use_tools()
    click_result = registry.call(
        "click",
        runtime_context={
            "env": env,
            "ops": {"gui_controller": env.get_ops("gui_controller")},
        },
        x=640,
        y=420,
    )
    step = env.step(
        action={
            "decision_mode": "act",
            "actions": [{"name": "click", "args": {"x": 640, "y": 420}}],
            "action_results": [click_result],
        }
    )
    env.teardown()

    print("first_observation_keys:", sorted(first.data.keys()))
    print("click_result:", click_result)
    print("step_done:", step.done)
    print("step_info:", step.info)


if __name__ == "__main__":
    main()
