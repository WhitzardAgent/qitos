"""Mock CyberGym server for testing the agent without a real verification server.

Usage:
    python -m cybergym_agent.mock_server --port 8666

Or use directly in run_local.py with --mock-server flag.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse


class MockCyberGymHandler(BaseHTTPRequestHandler):
    """HTTP handler that mimics the CyberGym verification server.

    Supports POST /submit-vul endpoint. Runs the PoC against a binary
    specified in the metadata and returns verification results.
    """

    # Class-level config set by the server
    binary_path: Optional[str] = None
    fix_binary_path: Optional[str] = None

    def do_POST(self):
        if self.path == "/submit-vul":
            self._handle_submit()
        else:
            self.send_error(404, "Not found")

    def _handle_submit(self):
        content_type = self.headers.get("Content-Type", "")

        # Parse multipart form data
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
        except Exception:
            self._send_json(400, {"error": "Failed to read request body"})
            return

        # Simple multipart parser
        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):]
                break

        if not boundary:
            self._send_json(400, {"error": "No boundary in multipart"})
            return

        # Parse parts
        metadata = {}
        poc_data = b""
        poc_filename = "poc"

        parts = body.split(f"--{boundary}".encode())
        for part in parts:
            if not part or part.strip() == b"--" or part.strip() == b"--\r\n":
                continue

            # Split headers from body
            try:
                header_end = part.index(b"\r\n\r\n")
                headers_raw = part[:header_end].decode("utf-8", errors="replace")
                body_data = part[header_end + 4:]
                # Remove trailing \r\n
                if body_data.endswith(b"\r\n"):
                    body_data = body_data[:-2]
            except ValueError:
                continue

            # Extract field name and filename from Content-Disposition
            if 'name="metadata"' in headers_raw:
                try:
                    metadata = json.loads(body_data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
            elif 'name="file"' in headers_raw:
                poc_data = body_data
                # Extract filename
                for h_line in headers_raw.split("\r\n"):
                    if "filename=" in h_line:
                        fn_start = h_line.index("filename=") + len("filename=")
                        fn_end = h_line.find('"', fn_start + 1)
                        if fn_start < fn_end:
                            poc_filename = h_line[fn_start + 1:fn_end]

        if not poc_data:
            self._send_json(400, {"error": "No PoC file provided"})
            return

        # Run the PoC against the vulnerable binary
        vul_exit_code = 0
        fix_exit_code = None
        vul_stdout = ""
        vul_stderr = ""
        fix_stdout = ""
        fix_stderr = ""

        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{poc_filename}") as tmp:
            tmp.write(poc_data)
            poc_tmp_path = tmp.name

        try:
            # Run against vulnerable binary
            if self.binary_path and os.path.isfile(self.binary_path):
                try:
                    result = subprocess.run(
                        [self.binary_path],
                        stdin=open(poc_tmp_path, "rb"),
                        capture_output=True,
                        timeout=30,
                    )
                    vul_exit_code = result.returncode
                    vul_stdout = result.stdout.decode("utf-8", errors="replace")[:5000]
                    vul_stderr = result.stderr.decode("utf-8", errors="replace")[:5000]
                except subprocess.TimeoutExpired:
                    vul_exit_code = 137  # SIGKILL
                    vul_stderr = "Process timed out"
                except Exception as e:
                    vul_stderr = f"Execution error: {e}"
            else:
                # No binary available -- mock based on PoC file size
                # This is a heuristic: larger PoCs are more likely to trigger bugs
                poc_size = len(poc_data)
                if poc_size > 100:
                    vul_exit_code = 1
                    vul_stderr = f"Mock: PoC triggered vulnerability (size={poc_size})"
                else:
                    vul_exit_code = 0
                    vul_stderr = f"Mock: PoC did not trigger (size={poc_size} too small)"

            # Run against fixed binary
            if self.fix_binary_path and os.path.isfile(self.fix_binary_path):
                try:
                    result = subprocess.run(
                        [self.fix_binary_path],
                        stdin=open(poc_tmp_path, "rb"),
                        capture_output=True,
                        timeout=30,
                    )
                    fix_exit_code = result.returncode
                    fix_stdout = result.stdout.decode("utf-8", errors="replace")[:5000]
                    fix_stderr = result.stderr.decode("utf-8", errors="replace")[:5000]
                except subprocess.TimeoutExpired:
                    fix_exit_code = 137
                    fix_stderr = "Process timed out"
                except Exception as e:
                    fix_stderr = f"Execution error: {e}"
        finally:
            os.unlink(poc_tmp_path)

        # Build response
        poc_id = uuid.uuid4().hex
        response = {
            "vul_exit_code": vul_exit_code,
            "fix_exit_code": fix_exit_code,
            "poc_id": poc_id,
            "vul_stderr": vul_stderr,
            "fix_stderr": fix_stderr,
            "vul_stdout": vul_stdout,
            "fix_stdout": fix_stdout,
        }

        self._send_json(200, response)

    def _send_json(self, code: int, data: Dict[str, Any]):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def log_message(self, format, *args):
        """Quiet logging -- only print to stderr."""
        print(f"[MockServer] {args[0]}", file=sys.stderr)


def run_mock_server(
    port: int = 8666,
    binary_path: Optional[str] = None,
    fix_binary_path: Optional[str] = None,
):
    """Start a mock CyberGym verification server."""
    MockCyberGymHandler.binary_path = binary_path
    MockCyberGymHandler.fix_binary_path = fix_binary_path

    server = HTTPServer(("0.0.0.0", port), MockCyberGymHandler)
    print(f"[MockServer] Starting on port {port}")
    if binary_path:
        print(f"[MockServer] Vulnerable binary: {binary_path}")
    if fix_binary_path:
        print(f"[MockServer] Fixed binary: {fix_binary_path}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[MockServer] Shutting down")
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Mock CyberGym verification server")
    parser.add_argument("--port", type=int, default=8666, help="Port to listen on")
    parser.add_argument("--binary", type=str, default=None, help="Path to vulnerable binary")
    parser.add_argument("--fix-binary", type=str, default=None, help="Path to fixed binary")
    args = parser.parse_args()

    run_mock_server(
        port=args.port,
        binary_path=args.binary,
        fix_binary_path=args.fix_binary,
    )
