"""SubmitPoCTool -- POST PoC file to the CyberGym verification server."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from qitos.core.tool import BaseTool, ToolPermission, ToolSpec, ToolValidationResult


class SubmitPoCTool(BaseTool):
    """Submit a PoC file to the CyberGym server for verification.

    The server runs the PoC against both the vulnerable and patched binaries
    to confirm differential behavior. Returns verification result with
    vul_exit_code and fix_exit_code.
    """

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.api_key_env_var = "CYBERGYM_API_KEY"
        self.api_key_header = "X-API-Key"
        super().__init__(
            ToolSpec(
                name="submit_poc",
                description=(
                    "Submit a PoC file to the CyberGym server for verification. "
                    "The server runs the PoC against the vulnerable and patched binaries. "
                    "Returns {vul_exit_code, fix_exit_code, poc_id}."
                ),
                parameters={
                    "poc_path": {
                        "type": "string",
                        "description": "Path to the PoC file within the workspace",
                    },
                    "task_id": {
                        "type": "string",
                        "description": "The CyberGym task ID (e.g., 'arvo:10400')",
                    },
                    "agent_id": {
                        "type": "string",
                        "description": "The agent ID for this run",
                    },
                    "checksum": {
                        "type": "string",
                        "description": "SHA-256 checksum for task verification",
                    },
                    "require_flag": {
                        "type": "boolean",
                        "description": "Whether to require a flag in the response (CTF mode)",
                    },
                },
                required=["poc_path", "task_id", "agent_id", "checksum"],
                permissions=ToolPermission(network=True, filesystem_read=True),
            )
        )

    def validate_input(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> ToolValidationResult:
        poc_path = args.get("poc_path")
        if not poc_path:
            return ToolValidationResult.fail("poc_path is required")

        task_id = args.get("task_id")
        if not task_id:
            return ToolValidationResult.fail("task_id is required")

        agent_id = args.get("agent_id")
        if not agent_id:
            return ToolValidationResult.fail("agent_id is required")

        checksum = args.get("checksum")
        if not checksum:
            return ToolValidationResult.fail("checksum is required")

        return ToolValidationResult.ok()

    def execute(
        self,
        args: Dict[str, Any],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Submit the PoC file to the CyberGym verification server."""
        poc_path = str(args["poc_path"])
        task_id = str(args["task_id"])
        agent_id = str(args["agent_id"])
        checksum = str(args["checksum"])
        require_flag = bool(args.get("require_flag", False))

        # Resolve the PoC file path
        poc_file = Path(poc_path)
        if not poc_file.is_absolute():
            # Try to resolve relative to workspace
            workspace = None
            if runtime_context:
                env = runtime_context.get("env")
                if env and hasattr(env, "workspace_root"):
                    workspace = env.workspace_root
            if workspace:
                poc_file = Path(workspace) / poc_path

        if not poc_file.exists():
            return {
                "status": "error",
                "error": f"PoC file not found: {poc_file}",
            }

        # Build the metadata payload
        metadata = json.dumps({
            "task_id": task_id,
            "agent_id": agent_id,
            "checksum": checksum,
            "require_flag": require_flag,
        })

        # POST to the server
        try:
            with open(poc_file, "rb") as f:
                response = httpx.post(
                    f"{self.server_url}/submit-vul",
                    data={"metadata": metadata},
                    files={"file": (poc_file.name, f, "application/octet-stream")},
                    timeout=120.0,  # generous timeout for server-side execution
                )
        except httpx.TimeoutException:
            return {
                "status": "error",
                "error": "Server request timed out after 120 seconds",
            }
        except httpx.ConnectError as e:
            return {
                "status": "error",
                "error": f"Could not connect to CyberGym server: {e}",
            }

        if response.status_code != 200:
            return {
                "status": "error",
                "error": f"Server returned HTTP {response.status_code}: {response.text[:500]}",
            }

        try:
            result = response.json()
        except Exception:
            return {
                "status": "error",
                "error": f"Invalid JSON response from server: {response.text[:500]}",
            }

        vul_exit_code = result.get("vul_exit_code")
        fix_exit_code = result.get("fix_exit_code")
        if vul_exit_code is None and "exit_code" in result:
            vul_exit_code = result.get("exit_code")

        verification_scope = "partial"
        api_key = os.getenv(self.api_key_env_var, "").strip()
        if api_key:
            headers = {self.api_key_header: api_key}
            try:
                verify_response = httpx.post(
                    f"{self.server_url}/verify-agent-pocs",
                    json={"agent_id": agent_id},
                    headers=headers,
                    timeout=120.0,
                )
                if verify_response.status_code == 200:
                    query_response = httpx.post(
                        f"{self.server_url}/query-poc",
                        json={"agent_id": agent_id, "task_id": task_id},
                        headers=headers,
                        timeout=60.0,
                    )
                    if query_response.status_code == 200:
                        records = query_response.json()
                        for record in records:
                            if record.get("poc_id") == result.get("poc_id"):
                                vul_exit_code = record.get("vul_exit_code", vul_exit_code)
                                fix_exit_code = record.get("fix_exit_code")
                                verification_scope = "full"
                                break
            except Exception:
                verification_scope = "partial"

        # Public submit only has vuln-side output. Mirror exit_code to avoid
        # treating vuln-only submissions as fully verified successes.
        if fix_exit_code is None:
            fix_exit_code = vul_exit_code
            verification_scope = "vul_only"

        return {
            "status": "success",
            "vul_exit_code": vul_exit_code,
            "fix_exit_code": fix_exit_code,
            "poc_id": result.get("poc_id"),
            "flag": result.get("flag"),
            "raw_output": result.get("output", ""),
            "verification_scope": verification_scope,
            "vul_stderr": result.get("vul_stderr", ""),
            "fix_stderr": result.get("fix_stderr", ""),
            "vul_stdout": result.get("vul_stdout", ""),
            "fix_stdout": result.get("fix_stdout", ""),
        }
