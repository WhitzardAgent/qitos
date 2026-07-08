"""CyberGym environment extending DockerEnv with task-specific setup."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from qitos.kit.env.docker_env import DockerEnv


class CyberGymEnv(DockerEnv):
    """DockerEnv configured for CyberGym PoC generation tasks.

    Extends DockerEnv with:
    - Automatic extraction of repo-vul.tar.gz into the container workspace
    - Setup of build tools and dependencies
    - Execution of the vulnerable binary for PoC verification
    """

    name = "cybergym_env"
    version = "1.0"

    def __init__(
        self,
        *,
        container: Optional[str] = None,
        workspace_root: str = "/workspace",
        image: str = "ubuntu:22.04",
        host_workspace: Optional[str] = None,
        auto_create: bool = True,
        remove_on_close: bool = True,
        network: Optional[str] = None,
        extra_run_args: Optional[list[str]] = None,
        create_timeout: int = 120,
        repo_tar_path: Optional[str] = None,
        description_path: Optional[str] = None,
    ):
        self.repo_tar_path = repo_tar_path
        self.description_path = description_path
        self._repo_extracted = False

        super().__init__(
            container=container,
            workspace_root=workspace_root,
            image=image,
            host_workspace=host_workspace,
            auto_create=auto_create,
            remove_on_close=remove_on_close,
            network=network,
            extra_run_args=extra_run_args,
            create_timeout=create_timeout,
        )

    def setup(
        self,
        task: Any = None,
        workspace: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Set up the Docker environment and extract the vulnerable repo."""
        super().setup(task=task, workspace=workspace, **kwargs)

        # Install minimal build dependencies
        self._install_dependencies()

        # Extract the vulnerable repo if provided
        if self.host_workspace:
            self._extract_repo()

    def _install_dependencies(self) -> None:
        """Install minimal dependencies needed for PoC generation and testing."""
        install_cmd = (
            "apt-get update -qq && "
            "apt-get install -y -qq --no-install-recommends "
            "build-essential python3 git curl 2>/dev/null || true"
        )
        self.cmd.run(install_cmd, timeout=120)

    def _extract_repo(self) -> None:
        """Extract repo-vul.tar.gz into the workspace if present."""
        if not self.host_workspace:
            return

        host_path = Path(self.host_workspace)

        # Look for repo-vul.tar.gz in host workspace
        tar_path = None
        for candidate in ["repo-vul.tar.gz", "repo-vul.tgz"]:
            if (host_path / candidate).exists():
                tar_path = candidate
                break

        if tar_path:
            # Extract inside the container
            extract_cmd = (
                f"mkdir -p /workspace/repo-vul && "
                f"cd /workspace/repo-vul && "
                f"tar xzf /workspace/{shlex.quote(tar_path)} --strip-components=1 2>/dev/null || "
                f"tar xf /workspace/{shlex.quote(tar_path)} --strip-components=1 2>/dev/null || "
                f"cd /workspace && tar xzf /workspace/{shlex.quote(tar_path)} 2>/dev/null || "
                f"cd /workspace && tar xf /workspace/{shlex.quote(tar_path)} 2>/dev/null || true"
            )
            self.cmd.run(extract_cmd, timeout=60)
            self._repo_extracted = True

    def run_poc(
        self,
        poc_path: str,
        binary_path: Optional[str] = None,
        timeout: int = 30,
    ) -> Dict[str, Any]:
        """Execute a PoC against the vulnerable binary.

        Args:
            poc_path: Path to the PoC file inside the container.
            binary_path: Path to the vulnerable binary. If None, attempts
                         to auto-detect from the extracted repo.
            timeout: Execution timeout in seconds.

        Returns:
            Dict with returncode, stdout, stderr, timed_out.
        """
        if binary_path is None:
            binary_path = self._find_vulnerable_binary()

        if not binary_path:
            return {
                "returncode": -1,
                "stdout": "",
                "stderr": "Could not find vulnerable binary",
                "timed_out": False,
            }

        # Run the binary with the PoC as stdin
        cmd = f"timeout -s SIGKILL {timeout} {shlex.quote(binary_path)} < {shlex.quote(poc_path)}"
        result = self.cmd.run(cmd, timeout=timeout + 5)
        return {
            "returncode": result.get("returncode", -1),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "timed_out": result.get("returncode", -1) == 137,  # SIGKILL
        }

    def _find_vulnerable_binary(self) -> Optional[str]:
        """Attempt to find the vulnerable binary in the extracted repo."""
        # Check common locations
        candidates = [
            "/workspace/repo-vul/vulnerable",
            "/workspace/repo-vul/a.out",
            "/workspace/vulnerable",
        ]

        for candidate in candidates:
            if self.fs.exists(candidate):
                return candidate

        # Search for executables
        result = self.cmd.run(
            "find /workspace/repo-vul -type f -executable -name 'arvo' 2>/dev/null | head -1",
            timeout=10,
        )
        if result.get("returncode") == 0 and result.get("stdout", "").strip():
            return result["stdout"].strip()

        return None
