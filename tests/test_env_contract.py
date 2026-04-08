from __future__ import annotations

from pathlib import Path

from qitos.core import Env, EnvObservation, EnvSpec, EnvStepResult
from qitos.kit.env import DesktopEnv, ScreenshotEnv


class _DummyEnv(Env):
    def __init__(self):
        self.closed = False
        self.counter = 0

    def reset(self, task=None, workspace=None, **kwargs):
        self.counter = 0
        return EnvObservation(
            data={"event": "reset", "task": task, "workspace": workspace}
        )

    def observe(self, state=None):
        return EnvObservation(
            data={"event": "observe", "counter": self.counter, "state": state}
        )

    def step(self, action, state=None):
        self.counter += 1
        done = self.counter >= 2
        return EnvStepResult(
            observation=EnvObservation(
                data={"event": "step", "action": action, "counter": self.counter}
            ),
            done=done,
            reward=float(self.counter),
            info={"state": state},
        )

    def close(self):
        self.closed = True


def test_env_spec_defaults():
    spec = EnvSpec(type="repo")
    assert spec.type == "repo"
    assert spec.config == {}
    assert spec.required_tools == []
    assert spec.capabilities == []


def test_env_contract_lifecycle_and_terminal_default():
    env = _DummyEnv()
    env.setup(task="fix bug", workspace="/tmp/work")
    assert env.health_check().get("ok") is True
    obs0 = env.reset(task="fix bug", workspace="/tmp/work")
    assert obs0.data["event"] == "reset"
    assert obs0.data["task"] == "fix bug"

    obs1 = env.observe(state={"step": 0})
    assert obs1.data["event"] == "observe"
    assert obs1.data["counter"] == 0

    r1 = env.step(action={"name": "noop"}, state={"step": 1})
    assert r1.done is False
    assert env.is_terminal(last_result=r1) is False

    r2 = env.step(action={"name": "noop"}, state={"step": 2})
    assert r2.done is True
    assert env.is_terminal(last_result=r2) is True

    env.teardown()
    assert env.closed is True


def test_screenshot_env_exposes_multimodal_observation(tmp_path: Path):
    png_path = tmp_path / "screen.png"
    png_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f\x00\x02\xeb\x01\xf5i\xf6\x81\xb7\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    env = ScreenshotEnv(
        screenshot_path=str(png_path),
        text="Observe the login button.",
        dom={"title": "Login"},
        accessibility_tree={"role": "window"},
        ocr=[{"text": "Login"}],
    )
    obs = env.reset()
    multimodal = obs.data["multimodal"]
    assert multimodal["text"] == "Observe the login button."
    assert multimodal["screenshot"]["path"] == str(png_path.resolve())
    assert env.has_ops("gui_observer") is True
    assert env.has_ops("gui_controller") is True


def test_desktop_env_mock_provider_supports_gui_loop(tmp_path: Path) -> None:
    png_path = tmp_path / "desktop.png"
    png_path.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff\x1f\x00\x02\xeb\x01\xf5i\xf6\x81\xb7\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    env = DesktopEnv.from_mock(
        screenshot_path=str(png_path),
        instruction="Click Continue",
        accessibility_tree={"role": "window", "name": "Desktop Smoke"},
        terminal="$ echo smoke\nsmoke\n$ ",
        ui_candidates=[{"label": "Continue", "x": 640, "y": 420}],
    )
    env.setup()
    obs = env.reset()
    assert "multimodal" in obs.data
    controller = env.get_ops("gui_controller")
    assert controller is not None
    action_result = controller.perform({"name": "click", "args": {"x": 640, "y": 420}})
    assert action_result["status"] == "success"
    step = env.step(
        {"decision_mode": "act", "actions": [{"name": "click", "args": {"x": 640, "y": 420}}]}
    )
    assert step.observation.data["desktop"]["provider"] == "mock_desktop"
    assert step.info["performed_actions"]
    env.teardown()
