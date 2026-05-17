"""PentAGIRunner — main entry point for running PentAGI penetration tests."""

from __future__ import annotations

from typing import Any, Dict, Optional

from .config.defaults import PentAGIConfig
from .orchestrator.flow import PentAGIFlow, PentAGIResult


class PentAGIRunner:
    """Main entry point for running PentAGI penetration tests.

    Usage::

        from qitos.examples.pentagi import PentAGIRunner, PentAGIConfig

        config = PentAGIConfig(
            model_provider="openai-compatible",
            model_name="qwen-plus",
            api_key="your-api-key",
            base_url="https://api.example.com/v1",
            docker_profile="kali",
            authorized_targets=["192.168.1.0/24"],
            language="en",
        )

        runner = PentAGIRunner(config)
        result = runner.run("Penetration test against 192.168.1.100")
        print(result.report)

    Parameters
    ----------
    config : PentAGIConfig
        Configuration for the penetration test run.
    llm : Any | None
        Optional pre-configured LLM instance. If None, created from config.
    """

    def __init__(self, config: PentAGIConfig, llm: Optional[Any] = None):
        self.config = config
        self.llm = llm

    def run(self, task: str, **kwargs: Any) -> PentAGIResult:
        """Execute a complete penetration test run.

        Parameters
        ----------
        task : str
            The penetration test task description (e.g., target, scope, objectives).
        **kwargs
            Additional keyword arguments passed to PentAGIFlow.run().

        Returns
        -------
        PentAGIResult
            Complete result including report, subtasks, and findings.
        """
        flow = PentAGIFlow(config=self.config, llm=self.llm)
        return flow.run(task, **kwargs)

    def run_with_docker(self, task: str, **kwargs: Any) -> PentAGIResult:
        """Execute a penetration test with Docker environment setup.

        Creates a Docker container from the configured profile,
        runs the test, and tears down the container.
        """
        from .config.docker_profiles import get_docker_config

        docker_config = get_docker_config(self.config.docker_profile)
        if self.config.docker_image:
            docker_config["image"] = self.config.docker_image

        # Create DockerEnv
        try:
            from qitos.kit.env.docker_env import DockerEnv
            docker_env = DockerEnv(
                image=docker_config["image"],
                workspace_root=docker_config["workspace_root"],
                host_workspace=self.config.workspace,
                auto_create=True,
                remove_on_close=True,
                extra_run_args=docker_config.get("extra_run_args", []),
            )
            docker_env.setup()
        except ImportError:
            # DockerEnv not available — run without Docker
            return self.run(task, **kwargs)
        except Exception as e:
            # Docker not available — run without it
            return self.run(task, **kwargs)

        try:
            flow = PentAGIFlow(config=self.config, llm=self.llm)
            flow._docker_env = docker_env
            return flow.run(task, **kwargs)
        finally:
            try:
                docker_env.close()
            except Exception:
                pass


__all__ = ["PentAGIRunner"]
