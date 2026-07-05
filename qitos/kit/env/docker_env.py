"""Docker-backed environment and capabilities."""

from __future__ import annotations

import shlex
import subprocess
import threading
import atexit
import os
import signal
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from qitos.core.env import CommandCapability, FileSystemCapability
from qitos.kit.env.host_env import HostEnv


def _run(cmd: list[str], timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


_MANAGED_CONTAINERS: dict[str, "DockerEnv"] = {}
_CLEANUP_LOCK = threading.RLock()
_ATEXIT_INSTALLED = False
_SIGNALS_INSTALLED = False
_PREVIOUS_SIGNAL_HANDLERS: dict[int, Any] = {}


def _cleanup_managed_containers() -> None:
    """Best-effort cleanup for DockerEnv containers created by this process."""
    with _CLEANUP_LOCK:
        envs = list(_MANAGED_CONTAINERS.values())
    for env in envs:
        try:
            env.close()
        except Exception:
            pass


def _install_cleanup_handlers() -> None:
    global _ATEXIT_INSTALLED, _SIGNALS_INSTALLED
    if not _ATEXIT_INSTALLED:
        atexit.register(_cleanup_managed_containers)
        _ATEXIT_INSTALLED = True
    if _SIGNALS_INSTALLED or threading.current_thread() is not threading.main_thread():
        return

    def _handle_signal(signum: int, frame: Any) -> None:
        _cleanup_managed_containers()
        previous = _PREVIOUS_SIGNAL_HANDLERS.get(signum)
        if callable(previous):
            previous(signum, frame)
            return
        if previous == signal.SIG_IGN:
            return
        raise SystemExit(128 + int(signum))

    for signame in ("SIGTERM", "SIGINT", "SIGHUP"):
        signum = getattr(signal, signame, None)
        if signum is None:
            continue
        try:
            _PREVIOUS_SIGNAL_HANDLERS[signum] = signal.getsignal(signum)
            signal.signal(signum, _handle_signal)
            _SIGNALS_INSTALLED = True
        except (OSError, RuntimeError, ValueError):
            continue


class DockerCommandCapability(CommandCapability):
    def __init__(self, container: str, workdir: str = "/workspace"):
        self.container = container
        self.workdir = workdir

    def run(self, command: str, timeout: int = 30) -> Dict[str, Any]:
        if not command or not command.strip():
            return {"status": "error", "error": "empty command"}
        docker_cmd = [
            "docker",
            "exec",
            "-w",
            self.workdir,
            self.container,
            "sh",
            "-lc",
            command,
        ]
        try:
            r = _run(docker_cmd, timeout=timeout)
            return {
                "status": "success" if r.returncode == 0 else "partial",
                "returncode": r.returncode,
                "stdout": r.stdout,
                "stderr": r.stderr,
                "command": command,
                "container": self.container,
            }
        except Exception as exc:
            return {
                "status": "error",
                "error": str(exc),
                "command": command,
                "container": self.container,
            }


class DockerFSCapability(FileSystemCapability):
    def __init__(self, container: str, workdir: str = "/workspace"):
        self.container = container
        self.workdir = workdir.rstrip("/") or "/workspace"
        self.cmd = DockerCommandCapability(container=container, workdir=workdir)

    def read_text(self, path: str) -> str:
        inner = self._inner_path(path)
        result = self.cmd.run(f"cat {shlex.quote(inner)}")
        if result.get("returncode", 1) != 0:
            raise RuntimeError(str(result.get("stderr", "failed to read file")))
        return str(result.get("stdout", ""))

    def write_text(self, path: str, content: str) -> None:
        inner = self._inner_path(path)
        encoded = content.replace("\\", "\\\\").replace("'", "'\"'\"'")
        cmd = f"mkdir -p {shlex.quote(str(Path(inner).parent))} && printf '%s' '{encoded}' > {shlex.quote(inner)}"
        result = self.cmd.run(cmd)
        if result.get("returncode", 1) != 0:
            raise RuntimeError(str(result.get("stderr", "failed to write file")))

    def list_files(self, path: str = ".", limit: int = 200) -> list[str]:
        inner = self._inner_path(path)
        cmd = f"find {shlex.quote(inner)} -type f | head -n {int(limit)}"
        result = self.cmd.run(cmd)
        if result.get("returncode", 1) != 0:
            return []
        prefix = self.workdir.rstrip("/") + "/"
        out: list[str] = []
        for line in str(result.get("stdout", "")).splitlines():
            line = line.strip()
            if not line:
                continue
            out.append(line[len(prefix) :] if line.startswith(prefix) else line)
        return out

    def exists(self, path: str) -> bool:
        inner = self._inner_path(path)
        result = self.cmd.run(f"test -e {shlex.quote(inner)}")
        return int(result.get("returncode", 1)) == 0

    def _inner_path(self, path: str) -> str:
        p = str(path)
        # Absolute paths are used verbatim (valid under a same-path bind mount
        # or an explicit in-container absolute path). Only relative paths are
        # resolved against the container workdir.
        if p.startswith("/"):
            return p
        return f"{self.workdir}/{p}" if p else self.workdir


class DockerEnv(HostEnv):
    """HostEnv-compatible action interpreter executed inside Docker.

    Supports two modes:
    1. Attach existing container: pass `container`.
    2. Auto-create ephemeral container: pass `image` and set `auto_create=True`.
    """

    name = "docker_env"
    version = "1.1"

    def __init__(
        self,
        container: Optional[str] = None,
        workspace_root: str = "/workspace",
        *,
        image: Optional[str] = None,
        host_workspace: Optional[str] = None,
        auto_create: bool = False,
        remove_on_close: bool = False,
        network: Optional[str] = None,
        extra_run_args: Optional[list[str]] = None,
        container_env: Optional[Dict[str, str]] = None,
        create_timeout: int = 60,
    ):
        self.container = str(container).strip() if container else ""
        self.container_workspace = workspace_root
        self.image = str(image or "").strip()
        self.host_workspace = str(host_workspace).strip() if host_workspace else ""
        self.auto_create = bool(auto_create)
        self.remove_on_close = bool(remove_on_close)
        self.network = network
        self.extra_run_args = list(extra_run_args or [])
        self.container_env = dict(container_env) if container_env else None
        self.create_timeout = int(create_timeout)
        self._created_here = False
        self._closed = False

        if not self.container and self.auto_create:
            self.container = f"qitos_{Path(self.host_workspace or 'workspace').name}_{threading.get_ident()}"

        fs = DockerFSCapability(container=self.container or "", workdir=workspace_root)
        cmd = DockerCommandCapability(
            container=self.container or "", workdir=workspace_root
        )
        super().__init__(workspace_root=workspace_root, fs=fs, cmd=cmd)

    def setup(
        self, task: Any = None, workspace: Optional[str] = None, **kwargs: Any
    ) -> None:
        if workspace and not self.host_workspace:
            self.host_workspace = str(Path(workspace).resolve())
        if self.auto_create:
            self._ensure_container()
        if not self.container:
            raise ValueError(
                "DockerEnv requires `container` or `auto_create=True` with `image`"
            )

        self.fs = DockerFSCapability(
            container=self.container, workdir=self.container_workspace
        )
        self.cmd = DockerCommandCapability(
            container=self.container, workdir=self.container_workspace
        )

    def reset(self, task: Any = None, workspace: Optional[str] = None, **kwargs: Any):
        self.setup(task=task, workspace=workspace, **kwargs)
        self.workspace_root = workspace or self.container_workspace
        self._last_error = None
        return self.observe(state=None)

    def health_check(self) -> Dict[str, Any]:
        if not self.container:
            return {"ok": False, "message": "container is empty"}

        inspect = _run(["docker", "inspect", self.container], timeout=20)
        if inspect.returncode != 0:
            return {
                "ok": False,
                "message": "docker inspect failed",
                "container": self.container,
                "stderr": inspect.stderr,
            }

        probe = self.cmd.run("pwd", timeout=10)
        if int(probe.get("returncode", 1)) != 0:
            return {
                "ok": False,
                "message": "docker exec probe failed",
                "container": self.container,
                "stderr": probe.get("stderr", ""),
            }
        return {
            "ok": True,
            "container": self.container,
            "workspace_root": self.workspace_root,
        }

    def close(self) -> None:
        if not self.container or self._closed:
            return
        self._closed = True
        with _CLEANUP_LOCK:
            _MANAGED_CONTAINERS.pop(self.container, None)
        if self.remove_on_close and self._created_here:
            _run(["docker", "rm", "-f", self.container], timeout=30)

    def _ensure_container(self) -> None:
        if not self.container:
            raise ValueError("auto_create needs container name")

        inspect = _run(["docker", "inspect", self.container], timeout=20)
        if inspect.returncode == 0:
            start = _run(["docker", "start", self.container], timeout=20)
            if start.returncode != 0:
                raise RuntimeError(
                    f"Failed to start container {self.container}: {start.stderr}"
                )
            # Auto-created DockerEnv names are owned by this run even if a stale
            # container with the same name already existed.  Mark it removable so
            # close()/atexit cleanup does not keep recycling leaked containers.
            if self.auto_create and self.remove_on_close:
                self._created_here = True
                self._closed = False
                with _CLEANUP_LOCK:
                    _MANAGED_CONTAINERS[self.container] = self
                _install_cleanup_handlers()
            return

        if not self.image:
            raise ValueError("auto_create requires `image`")

        run_cmd = ["docker", "run", "-d", "--name", self.container]
        run_cmd += [
            "--label",
            "qitos.managed=true",
            "--label",
            "qitos.env=docker_env",
            "--label",
            f"qitos.owner_pid={os.getpid()}",
        ]
        if self.network:
            run_cmd += ["--network", self.network]

        if self.container_env:
            for k, v in self.container_env.items():
                run_cmd += ["-e", f"{k}={v}"]

        mount_src = ""
        if self.host_workspace:
            host = str(Path(self.host_workspace).resolve())
            mount_src = host
            run_cmd += ["-v", f"{host}:{self.container_workspace}"]

        if self.extra_run_args:
            run_cmd += list(self.extra_run_args)

        run_cmd += [self.image, "sh", "-lc", "while true; do sleep 3600; done"]
        proc = _run(run_cmd, timeout=self.create_timeout)
        if proc.returncode != 0:
            raise RuntimeError(
                f"Failed to create container {self.container}: {proc.stderr}"
            )
        self._created_here = True
        self._closed = False
        with _CLEANUP_LOCK:
            _MANAGED_CONTAINERS[self.container] = self
        _install_cleanup_handlers()


class DockerEnvScheduler:
    """Simple bounded scheduler for per-task DockerEnv creation.

    Useful for benchmark batch runs to control concurrent docker containers.
    """

    def __init__(self, max_active: int = 1):
        self.max_active = max(1, int(max_active))
        self._sem = threading.Semaphore(self.max_active)

    @contextmanager
    def allocate(
        self,
        *,
        image: str,
        host_workspace: str,
        workspace_root: str = "/workspace",
        network: Optional[str] = None,
        extra_run_args: Optional[list[str]] = None,
    ) -> Iterator[DockerEnv]:
        self._sem.acquire()
        env = DockerEnv(
            workspace_root=workspace_root,
            image=image,
            host_workspace=host_workspace,
            auto_create=True,
            remove_on_close=True,
            network=network,
            extra_run_args=extra_run_args,
        )
        try:
            env.setup(workspace=host_workspace)
            yield env
        finally:
            try:
                env.close()
            finally:
                self._sem.release()


__all__ = [
    "DockerCommandCapability",
    "DockerFSCapability",
    "DockerEnv",
    "DockerEnvScheduler",
]
