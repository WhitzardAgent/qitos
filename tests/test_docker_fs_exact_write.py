from __future__ import annotations

from qitos.kit.env.docker_env import DockerFSCapability


class _RecordingCommand:
    def __init__(self) -> None:
        self.command = ""

    def run(self, command: str, timeout: int = 30):
        self.command = command
        return {"returncode": 0, "stdout": "", "stderr": ""}


def test_docker_write_does_not_double_backslashes() -> None:
    fs = DockerFSCapability("task-container", "/workspace")
    recorder = _RecordingCommand()
    fs.cmd = recorder

    fs.write_text("build.py", "payload = b'\\x00\\xff'\n")

    assert "\\\\x00" not in recorder.command
    assert "\\x00\\xff" in recorder.command

