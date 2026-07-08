from __future__ import annotations

from types import SimpleNamespace


def test_cybergym_run_task_generates_unique_inference_keys(monkeypatch, tmp_path) -> None:
    from qitos.benchmark.cybergym.agent import adapter as adapter_mod
    from qitos.benchmark.cybergym.agent import cli
    from qitos.benchmark.cybergym.agent import env as env_mod

    captured_configs = []
    captured_run_task_ids = []

    class _FakeAdapter:
        def __init__(self, *, server_url: str):
            self.server_url = server_url
            self.agent_id = "agent-123"

        def from_task_dir(self, task_dir: str, *, task_id=None, max_steps: int):
            benchmark_task_id = task_id or "arvo:3938"
            return SimpleNamespace(
                id=benchmark_task_id,
                inputs={
                    "description": "demo",
                    "task_id": benchmark_task_id,
                    "agent_id": self.agent_id,
                    "checksum": "checksum-123",
                    "server_url": self.server_url,
                    "task_root": task_dir,
                    "source_root": task_dir,
                    "repo_dir": task_dir,
                },
            )

    class _FakeAgent:
        def run(self, *, task, **kwargs):
            captured_run_task_ids.append((task.id, kwargs["task_id"]))
            return "done"

    def _fake_build_agent(**kwargs):
        captured_configs.append(dict(kwargs["llm_config"]))
        return _FakeAgent()

    monkeypatch.setattr(adapter_mod, "CyberGymAdapter", _FakeAdapter)
    monkeypatch.setattr(env_mod, "CyberGymEnv", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(cli, "build_agent", _fake_build_agent)

    task_dir = tmp_path / "task"
    task_dir.mkdir()

    cli.run_task(str(task_dir), task_id="arvo:3938", api_key="key", base_url="https://example.test/v1")
    cli.run_task(str(task_dir), task_id="arvo:3938", api_key="key", base_url="https://example.test/v1")

    keys = [config["inference_key"] for config in captured_configs]
    assert len(keys) == 2
    assert keys[0] != keys[1]
    assert all(key.startswith("cybergym-arvo-3938-") for key in keys)
    assert captured_run_task_ids == [("arvo:3938", "arvo:3938"), ("arvo:3938", "arvo:3938")]
