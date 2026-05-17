"""Terminal and file tools wrapping DockerEnv capabilities."""

from __future__ import annotations

from typing import Any, Dict, Optional

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec


class TerminalTool(BaseTool):
    """Execute commands in a Docker container.

    This tool wraps DockerEnv's command capability to provide
    terminal access for penetration testing agents.
    """

    def __init__(self, timeout: int = 120):
        self._timeout = timeout
        super().__init__(
            ToolSpec(
                name="terminal",
                description="Execute a command in the Docker container. "
                "Returns the command output (stdout + stderr). "
                "For long-running processes, consider running in background.",
                parameters={
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": f"Timeout in seconds (default: {timeout})",
                    },
                },
                required=["command"],
                permissions=ToolPermission(command=True),
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        runtime_context = runtime_context or {}
        command = str(args.get("command", ""))
        timeout = int(args.get("timeout", self._timeout))

        if not command:
            return {"status": "error", "message": "command is required"}

        env = runtime_context.get("env")
        if env is None or not hasattr(env, "cmd"):
            return {"status": "error", "message": "Docker environment not available"}

        try:
            result = env.cmd.run(command, timeout=timeout)
            return {
                "status": "ok",
                "exit_code": result.get("exit_code", result.get("returncode", -1)),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


class ReadFileTool(BaseTool):
    """Read a file from the Docker container."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="read_file",
                description="Read the contents of a file from the Docker container.",
                parameters={
                    "path": {
                        "type": "string",
                        "description": "Path to the file (absolute or relative to workspace)",
                    },
                },
                required=["path"],
                permissions=ToolPermission(filesystem_read=True),
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        runtime_context = runtime_context or {}
        path = str(args.get("path", ""))

        if not path:
            return {"status": "error", "message": "path is required"}

        env = runtime_context.get("env")
        if env is None or not hasattr(env, "fs"):
            return {"status": "error", "message": "Docker environment not available"}

        try:
            content = env.fs.read_text(path)
            return {"status": "ok", "content": content, "path": path}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class WriteFileTool(BaseTool):
    """Write a file to the Docker container."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="write_file",
                description="Write content to a file in the Docker container.",
                parameters={
                    "path": {
                        "type": "string",
                        "description": "Path to the file (absolute or relative to workspace)",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write to the file",
                    },
                },
                required=["path", "content"],
                permissions=ToolPermission(filesystem_write=True),
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        runtime_context = runtime_context or {}
        path = str(args.get("path", ""))
        content = str(args.get("content", ""))

        if not path:
            return {"status": "error", "message": "path is required"}

        env = runtime_context.get("env")
        if env is None or not hasattr(env, "fs"):
            return {"status": "error", "message": "Docker environment not available"}

        try:
            env.fs.write_text(path, content)
            return {"status": "ok", "message": f"Written {len(content)} chars to {path}"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class ListFilesTool(BaseTool):
    """List files in a directory in the Docker container."""

    def __init__(self):
        super().__init__(
            ToolSpec(
                name="list_files",
                description="List files in a directory in the Docker container.",
                parameters={
                    "path": {
                        "type": "string",
                        "description": "Directory path (default: workspace root)",
                    },
                },
                required=[],
                permissions=ToolPermission(filesystem_read=True),
            )
        )

    def execute(
        self, args: Dict[str, Any], runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        runtime_context = runtime_context or {}
        path = str(args.get("path", "."))

        env = runtime_context.get("env")
        if env is None or not hasattr(env, "fs"):
            return {"status": "error", "message": "Docker environment not available"}

        try:
            files = env.fs.list_files(path)
            return {"status": "ok", "files": files, "path": path}
        except Exception as e:
            return {"status": "error", "message": str(e)}


__all__ = ["TerminalTool", "ReadFileTool", "WriteFileTool", "ListFilesTool"]
