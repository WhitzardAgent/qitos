"""Canonical coding-oriented toolset backed by method-style tool definitions."""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests

from qitos.core.tool import ToolPermission, tool
from qitos.kit.tool.internal.coding_utils import (
    build_diff,
    default_rule_scope,
    detect_line_ending,
    resolve_tool_workspace_path,
    truncate_text,
    utc_now,
)
from qitos.kit.tool.notebook import NotebookToolSet

try:  # optional dependency
    from bs4 import BeautifulSoup
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore[assignment]


TASK_STATUSES = {"pending", "in_progress", "blocked", "completed", "cancelled"}


def _utc_now() -> str:
    return utc_now()


def _resolve_workspace_path(root_dir: str, path: str) -> Path:
    return resolve_tool_workspace_path(root_dir, path)


def _detect_line_ending(raw: bytes) -> str:
    return detect_line_ending(raw)


def _truncate_text(text: str, max_chars: int) -> tuple[str, bool]:
    return truncate_text(text, max_chars)


def _build_diff(old_content: str, new_content: str, path: str) -> str:
    return build_diff(old_content, new_content, path)


def _default_rule_scope(args: Dict[str, Any]) -> Optional[str]:
    return default_rule_scope(args)


class CodingToolSet:
    """Canonical coding toolset with one stable, traditional tool surface."""

    name = "coding"
    version = "2"

    def __init__(
        self,
        workspace_root: str = ".",
        shell_timeout: int = 30,
        include_notebook: bool = True,
        *,
        enable_lsp: bool = True,
        enable_tasks: bool = True,
        enable_web: bool = True,
        expose_legacy_aliases: bool = True,
        expose_modern_names: bool = False,
        profile: str = "full",
        include_http_tools: bool = False,
    ):
        self.workspace_root = os.path.abspath(workspace_root)
        self.shell_timeout = int(shell_timeout)
        self.include_notebook = bool(include_notebook)
        self.enable_lsp = bool(enable_lsp)
        self.enable_tasks = bool(enable_tasks)
        self.enable_web = bool(enable_web)
        self.expose_legacy_aliases = bool(expose_legacy_aliases)
        self.expose_modern_names = bool(expose_modern_names)
        self.profile = str(profile or "full")
        self.include_http_tools = bool(include_http_tools)
        self._notebook = (
            NotebookToolSet(workspace_root=self.workspace_root)
            if self.include_notebook
            else None
        )
        self._session_tasks: Dict[str, Dict[str, Any]] = {}
        self._task_counter = 0

    def setup(self, context: Dict[str, Any]) -> None:
        _ = context

    def teardown(self, context: Dict[str, Any]) -> None:
        _ = context

    def tools(self) -> List[Any]:
        items: List[Any] = []
        # Claude Code modern-name aliases (Read, Edit, Write, Glob, Grep, Bash, etc.)
        if self.expose_modern_names:
            items.extend(
                [
                    self.Read,
                    self.Edit,
                    self.Write,
                    self.Glob,
                    self.Grep,
                    self.Bash,
                    self.WebFetch,
                    self.AskUserQuestion,
                ]
            )
        if self.profile in {"full", "editor"} and self.expose_legacy_aliases:
            items.extend(
                [
                    self.view,
                    self.create,
                    self.str_replace,
                    self.insert,
                    self.search,
                    self.list_tree,
                    self.replace_lines,
                ]
            )
        if self.profile in {"full", "codebase"} and self.expose_legacy_aliases:
            items.extend(
                [
                    self.glob_files,
                    self.grep_files,
                    self.read_file_range,
                    self.append_file,
                    self.make_directory,
                ]
            )
        if self.profile in {"full", "codebase", "files"} and self.expose_legacy_aliases:
            items.extend([self.read_file, self.write_file, self.list_files])
        if self.profile in {"full", "shell"} and self.expose_legacy_aliases:
            items.append(self.run_command)
        if self.profile in {"full", "web"} and self.enable_web:
            if self.expose_legacy_aliases:
                items.append(self.web_fetch)
            if self.include_http_tools:
                items.extend(
                    [
                        self.http_request,
                        self.http_get,
                        self.http_post,
                        self.extract_web_text,
                    ]
                )
        if self.profile == "full":
            items.extend(
                [
                    self.ask_user_choice,
                    self.todo_write,
                    self.tool_search,
                    self.enter_plan_mode,
                    self.exit_plan_mode,
                    self.enter_worktree,
                    self.exit_worktree,
                    self.mcp_list_resources,
                    self.mcp_read_resource,
                    self.agent_spawn,
                    self.cron_create,
                    self.cron_delete,
                    self.cron_list,
                ]
            )
            if self.enable_lsp:
                items.append(self.lsp_query)
            if self.enable_tasks:
                items.extend(
                    [self.task_create, self.task_get, self.task_list, self.task_update]
                )
            if self._notebook is not None:
                items.extend(self._notebook.tools())
        return items

    def _read_text_file(self, path: Path) -> tuple[str, str, float]:
        raw = path.read_bytes()
        return (
            raw.decode("utf-8", errors="ignore"),
            _detect_line_ending(raw),
            path.stat().st_mtime,
        )

    def _write_text_file(
        self, path: Path, content: str, line_ending: str = "\n"
    ) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        normalized = (
            content.replace("\r\n", "\n").replace("\r", "\n").replace("\n", line_ending)
        )
        path.write_text(normalized, encoding="utf-8", newline="")

    def _iter_files(self, base_dir: Path, include_hidden: bool = False) -> List[Path]:
        files: List[Path] = []
        for root, dirs, names in os.walk(base_dir):
            if not include_hidden:
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in {"__pycache__", "node_modules", ".venv"}
                ]
                names = [n for n in names if not n.startswith(".")]
            for name in names:
                files.append(Path(root) / name)
        return files

    def _run_rg_files(
        self, target_dir: Path, pattern: str, include_hidden: bool
    ) -> Optional[List[str]]:
        cmd = ["rg", "--files", str(target_dir), "-g", pattern]
        if include_hidden:
            cmd.insert(1, "--hidden")
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return None
        if result.returncode not in {0, 1}:
            return None
        rows = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        files: List[str] = []
        for line in rows:
            rel = os.path.relpath(line, self.workspace_root)
            files.append(rel)
        return sorted(files)

    def _run_rg_grep(
        self,
        pattern: str,
        target_dir: Path,
        glob: Optional[str],
        case_sensitive: bool,
        regex: bool,
        files_with_matches: bool,
    ) -> Optional[List[Dict[str, Any]]]:
        cmd = ["rg", "--color", "never"]
        if files_with_matches:
            cmd.append("-l")
        else:
            cmd.extend(["--line-number", "--with-filename"])
        if not regex:
            cmd.append("-F")
        if not case_sensitive:
            cmd.append("-i")
        if glob:
            cmd.extend(["-g", glob])
        cmd.extend([pattern, str(target_dir)])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            return None
        if result.returncode not in {0, 1}:
            return None
        rows = [
            line.rstrip("\n") for line in result.stdout.splitlines() if line.strip()
        ]
        matches: List[Dict[str, Any]] = []
        for row in rows:
            if files_with_matches:
                matches.append({"path": os.path.relpath(row, self.workspace_root)})
                continue
            parts = row.split(":", 2)
            if len(parts) != 3:
                continue
            file_path, line_no, text = parts
            matches.append(
                {
                    "path": os.path.relpath(file_path, self.workspace_root),
                    "line": int(line_no),
                    "text": text,
                }
            )
        return matches

    def _tree_lines(self, path: Path, depth: int) -> List[str]:
        lines: List[str] = [f"{path.name or path}/"]

        def walk(current: Path, indent: str, current_depth: int) -> None:
            if current_depth >= depth:
                return
            items = sorted(
                [p for p in current.iterdir() if not p.name.startswith(".")],
                key=lambda p: (p.is_file(), p.name),
            )
            for index, item in enumerate(items):
                is_last = index == len(items) - 1
                connector = "`-- " if is_last else "|-- "
                lines.append(f"{indent}{connector}{item.name}")
                if item.is_dir():
                    walk(
                        item,
                        indent + ("    " if is_last else "|   "),
                        current_depth + 1,
                    )

        walk(path, "", 1)
        return lines

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        allow_redirects: bool = True,
        max_content_chars: int = 120_000,
    ) -> Dict[str, Any]:
        parsed = urlparse(str(url or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {
                "status": "error",
                "message": "URL must be an absolute http or https URL",
                "url": url,
            }
        try:
            response = requests.request(
                method=str(method or "GET").upper(),
                url=url,
                params=params,
                data=data,
                json=json_data,
                headers=headers,
                timeout=int(timeout or 30),
                verify=verify_tls,
                allow_redirects=allow_redirects,
            )
            text, truncated = _truncate_text(response.text, max_content_chars)
            payload: Dict[str, Any] = {
                "status": "success" if response.status_code < 400 else "error",
                "ok": bool(response.ok),
                "method": str(method or "GET").upper(),
                "url": response.url,
                "status_code": response.status_code,
                "reason": response.reason,
                "headers": dict(response.headers),
                "content_type": response.headers.get("Content-Type", ""),
                "content": text,
                "content_length": len(text),
                "truncated": truncated,
                "history": [item.url for item in response.history],
            }
            try:
                payload["json"] = response.json()
            except Exception:
                pass
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e), "url": url, "method": method}

    def _extract_html_text(self, html: str) -> Dict[str, Any]:
        if BeautifulSoup is not None:
            soup = BeautifulSoup(html or "", "html.parser")
            title = (
                soup.title.string.strip() if soup.title and soup.title.string else ""
            )
            text = "\n".join(
                line.strip()
                for line in soup.get_text("\n").splitlines()
                if line.strip()
            )
            return {"status": "success", "title": title, "text": text}
        title_match = re.search(
            r"<title>(.*?)</title>", html or "", re.IGNORECASE | re.DOTALL
        )
        title = title_match.group(1).strip() if title_match else ""
        text = re.sub(r"<[^>]+>", " ", html or "")
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        return {"status": "success", "title": title, "text": text}

    def _next_task_id(self) -> str:
        self._task_counter += 1
        return f"task-{self._task_counter:03d}"

    @tool(
        name="bash_v2",
        permissions=ToolPermission(command=True),
        supports_background=True,
        rule_scope_builder=_default_rule_scope,
    )
    def bash_v2(
        self,
        command: str,
        read_only: bool = False,
        allow_destructive: bool = False,
        run_in_background: bool = False,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Run one shell command inside the workspace.

        :param command: Shell command to execute.
        :param read_only: Whether the command should avoid mutating the workspace.
        :param allow_destructive: Whether destructive commands are explicitly allowed.
        :param run_in_background: Whether to detach the command and return a log path.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        text = str(command or "").strip()
        if not text:
            return {"status": "error", "message": "Command cannot be empty"}

        # Use BashCommandAnalyzer for safety classification
        from qitos.kit.permission.bash_analyzer import BashCommandAnalyzer, CommandSafety

        analyzer = BashCommandAnalyzer()
        analysis = analyzer.analyze(text)

        if not allow_destructive and analysis.safety == CommandSafety.UNSAFE:
            return {
                "status": "error",
                "message": f"Destructive command blocked: {analysis.explanation}",
                "error_category": "destructive_command",
                "detected_patterns": analysis.detected_patterns,
            }

        if read_only and not analysis.is_read_only:
            return {
                "status": "error",
                "message": "Command appears to write to the workspace in read-only mode",
            }

        python_inline_smoke = text.startswith(("python -c ", "python3 -c "))
        if (
            analysis.safety == CommandSafety.NEEDS_REVIEW
            and not allow_destructive
            and not python_inline_smoke
        ):
            return {
                "status": "needs_user_input",
                "message": f"Command needs review: {analysis.explanation}",
                "detected_patterns": analysis.detected_patterns,
            }
        if run_in_background:
            fd, stdout_path = tempfile.mkstemp(
                prefix="qitos_bash_", suffix=".log", dir=self.workspace_root
            )
            os.close(fd)
            with open(stdout_path, "w", encoding="utf-8") as handle:
                process = subprocess.Popen(
                    text,
                    cwd=self.workspace_root,
                    shell=True,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                )
            return {
                "status": "success",
                "command": text,
                "pid": process.pid,
                "stdout_path": stdout_path,
            }
        try:
            result = subprocess.run(
                text,
                cwd=self.workspace_root,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.shell_timeout,
            )
            return {
                "status": "success" if result.returncode == 0 else "partial",
                "command": text,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "cwd": self.workspace_root,
            }
        except subprocess.TimeoutExpired as e:
            return {
                "status": "error",
                "message": f"Command timed out after {self.shell_timeout}s",
                "command": text,
                "stdout": e.stdout or "",
                "stderr": e.stderr or "",
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "command": text}

    @tool(
        name="run_command",
        permissions=ToolPermission(command=True),
        rule_scope_builder=_default_rule_scope,
    )
    def run_command(
        self, command: str, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Execute one shell command inside the configured working directory.

        :param command: Shell command string to execute.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        return self.bash_v2(command=command, runtime_context=runtime_context)

    @tool(
        name="file_read_v2",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def file_read_v2(
        self,
        path: str,
        offset: int = 0,
        limit: int = 200,
        max_chars: int = 20_000,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Read one workspace file with line metadata.

        :param path: Path relative to the workspace root.
        :param offset: Zero-based starting line offset.
        :param limit: Maximum number of lines to return.
        :param max_chars: Maximum number of characters to return.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            if not resolved.exists():
                return {"status": "error", "message": f"File not found: {path}"}
            if resolved.is_dir():
                return {"status": "error", "message": f"Path is a directory: {path}"}
            content, line_ending, _mtime = self._read_text_file(resolved)
            lines = content.splitlines()
            start = max(0, int(offset))
            size = max(1, int(limit))
            chunk = lines[start : start + size]
            chunk_text = "\n".join(chunk)
            chunk_text, truncated = _truncate_text(chunk_text, int(max_chars))
            return {
                "status": "success",
                "path": str(path),
                "content": chunk_text,
                "line_ending": line_ending,
                "offset": start,
                "limit": size,
                "total_lines": len(lines),
                "lines": [
                    {"line": start + index + 1, "text": text}
                    for index, text in enumerate(chunk)
                ],
                "has_more": start + size < len(lines),
                "truncated": truncated,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="read_file",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def read_file(
        self, path: str, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Read the full text content of a workspace file.

        :param path: Path relative to the workspace root.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.file_read_v2(
            path=path,
            offset=0,
            limit=100_000,
            max_chars=200_000,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            return result
        return {
            "status": "success",
            "path": path,
            "content": result.get("content", ""),
            "size": len(result.get("content", "")),
        }

    @tool(
        name="view",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def view(self, path: str, view_range: Optional[List[int]] = None) -> Dict[str, Any]:
        """
        View a file or directory under the workspace root.

        :param path: Path relative to the workspace root (e.g., `src/main.py` or `src/`).
        :param view_range: Optional inclusive line range `[start, end]` to show for files.

        For files, returns structured line content. For directories, returns a
        readable listing of immediate child entries.
        """
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            if resolved.is_dir():
                entries = []
                for item in sorted(
                    resolved.iterdir(), key=lambda p: (p.is_file(), p.name)
                ):
                    if item.name.startswith("."):
                        continue
                    entries.append(
                        {
                            "name": item.name,
                            "type": "directory" if item.is_dir() else "file",
                        }
                    )
                return {
                    "status": "success",
                    "kind": "directory",
                    "path": path,
                    "entries": entries,
                    "count": len(entries),
                }
            if not resolved.exists():
                return {"status": "error", "message": f"File not found: {path}"}
            start = 0
            limit = 200
            if isinstance(view_range, list) and len(view_range) == 2:
                view_start = int(view_range[0])
                view_end = int(view_range[1])
                start = max(0, view_start - 1)
                limit = 100_000 if view_end == -1 else max(1, view_end - view_start + 1)
            return self.file_read_v2(path=path, offset=start, limit=limit)
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="list_files",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def list_files(
        self, path: str = ".", runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        List files and directories under a workspace-relative path.

        :param path: Directory path relative to the workspace root.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            if not resolved.is_dir():
                return {
                    "status": "error",
                    "message": f"Path is not a directory: {path}",
                }
            items = []
            for item in sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name)):
                if item.name.startswith("."):
                    continue
                items.append(
                    {
                        "name": item.name,
                        "type": "directory" if item.is_dir() else "file",
                        "size": item.stat().st_size if item.is_file() else None,
                    }
                )
            return {
                "status": "success",
                "path": path,
                "count": len(items),
                "files": items,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="write_file",
        permissions=ToolPermission(filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def write_file(
        self,
        path: str,
        content: str,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Write text content to a workspace file.

        :param path: Path relative to the workspace root.
        :param content: Full text content to write into the file.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            self._write_text_file(resolved, str(content), "\n")
            return {"status": "success", "path": path, "size": len(content)}
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="create",
        permissions=ToolPermission(filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def create(self, path: str, content: str = "") -> Dict[str, Any]:
        """
        Create a new file with the given content.

        :param path: Path relative to the workspace root (e.g., `new_file.py`).
        :param content: Content to write to the new file.
        """
        result = self.write_file(path=path, content=content)
        if result.get("status") != "success":
            return result
        return {
            "status": "success",
            "path": path,
            "message": f"Created file: {path}",
            "size": len(content),
        }

    @tool(
        name="file_edit_v2",
        permissions=ToolPermission(filesystem_read=True, filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def file_edit_v2(
        self,
        path: str,
        action: str,
        old_text: str = "",
        new_text: str = "",
        insert_line: int = 0,
        start_line: int = 0,
        end_line: int = 0,
        replacement: str = "",
        expected_mtime: Optional[float] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Edit one workspace file using a structured action.

        :param path: Path relative to the workspace root.
        :param action: Edit action such as `str_replace`, `insert`, or `replace_lines`.
        :param old_text: Old text for `str_replace`.
        :param new_text: New text for `str_replace`.
        :param insert_line: Line number after which to insert new text.
        :param start_line: Starting line number for `replace_lines`.
        :param end_line: Ending line number for `replace_lines`.
        :param replacement: Replacement content for `replace_lines`.
        :param expected_mtime: Optional optimistic concurrency check.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            if not resolved.exists():
                return {
                    "status": "error",
                    "message": f"File not found: {path}",
                    "path": path,
                }
            old_content, line_ending, current_mtime = self._read_text_file(resolved)
            if (
                expected_mtime is not None
                and abs(float(expected_mtime) - float(current_mtime)) > 1e-6
            ):
                return {
                    "status": "error",
                    "message": "File was modified since the expected mtime.",
                    "path": path,
                }
            normalized_action = str(action or "").strip()
            if normalized_action == "str_replace":
                if not old_text:
                    return {
                        "status": "error",
                        "message": "old_text cannot be empty",
                        "path": path,
                    }
                count = old_content.count(old_text)
                if count == 0:
                    return {
                        "status": "error",
                        "message": f"Text not found in {path}",
                        "path": path,
                    }
                if count > 1:
                    return {
                        "status": "error",
                        "message": "Text replacement must be unique",
                        "path": path,
                        "occurrences": count,
                    }
                new_content = old_content.replace(old_text, new_text, 1)
                message = f"Replaced one occurrence in {path}"
            elif normalized_action == "insert":
                try:
                    insert_line = int(insert_line)
                except Exception:
                    return {
                        "status": "error",
                        "message": f"Invalid insert_line: {insert_line}",
                        "path": path,
                    }
                lines = old_content.splitlines()
                if insert_line < 0 or insert_line > len(lines):
                    return {
                        "status": "error",
                        "message": f"Invalid insert_line: {insert_line}",
                        "path": path,
                    }
                updated_lines = lines[:insert_line] + [new_text] + lines[insert_line:]
                new_content = "\n".join(updated_lines)
                message = f"Inserted content after line {insert_line} in {path}"
            elif normalized_action == "replace_lines":
                try:
                    start_line = int(start_line)
                    end_line = int(end_line)
                except Exception:
                    return {
                        "status": "error",
                        "message": "Invalid line range",
                        "path": path,
                    }
                lines = old_content.splitlines()
                if start_line <= 0 or end_line < start_line or end_line > len(lines):
                    return {
                        "status": "error",
                        "message": "Invalid line range",
                        "path": path,
                    }
                if (
                    isinstance(replacement, str)
                    and replacement
                    and not replacement[:1].isspace()
                    and start_line == end_line
                ):
                    old_line = lines[start_line - 1]
                    indent = old_line[: len(old_line) - len(old_line.lstrip())]
                    if indent:
                        replacement = indent + replacement
                updated_lines = (
                    lines[: start_line - 1] + [replacement] + lines[end_line:]
                )
                new_content = "\n".join(updated_lines)
                message = f"Replaced lines {start_line}-{end_line} in {path}"
            else:
                return {
                    "status": "error",
                    "message": f"Unsupported action: {normalized_action}",
                    "path": path,
                }
            self._write_text_file(resolved, new_content, line_ending)
            return {
                "status": "success",
                "path": path,
                "message": message,
                "diff": _build_diff(old_content, new_content, path),
                "line_ending": line_ending,
                "expected_mtime": expected_mtime,
                "current_mtime": resolved.stat().st_mtime,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="str_replace",
        permissions=ToolPermission(filesystem_read=True, filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def str_replace(self, path: str, old_str: str, new_str: str = "") -> Dict[str, Any]:
        """
        Replace one unique string fragment in a file.

        :param path: Path relative to the workspace root.
        :param old_str: The exact string to replace. Must be unique in the file.
        :param new_str: The new string to replace old_str with.
        """
        return self.file_edit_v2(
            path=path, action="str_replace", old_text=old_str, new_text=new_str
        )

    @tool(
        name="insert",
        permissions=ToolPermission(filesystem_read=True, filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def insert(self, path: str, insert_line: int, new_str: str) -> Dict[str, Any]:
        """
        Insert new text after a given line number.

        :param path: Path relative to the workspace root.
        :param insert_line: Line number after which to insert new_str.
        :param new_str: String to insert.
        """
        return self.file_edit_v2(
            path=path, action="insert", insert_line=insert_line, new_text=new_str
        )

    @tool(
        name="replace_lines",
        permissions=ToolPermission(filesystem_read=True, filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def replace_lines(
        self, path: str, start_line: int, end_line: int, replacement: str = ""
    ) -> Dict[str, Any]:
        """
        Replace an inclusive line range with new content.

        :param path: Path relative to the workspace root.
        :param start_line: Starting line number.
        :param end_line: Ending line number, inclusive.
        :param replacement: Text to replace the specified lines with.
        """
        return self.file_edit_v2(
            path=path,
            action="replace_lines",
            start_line=start_line,
            end_line=end_line,
            replacement=replacement,
        )

    @tool(
        name="append_file",
        permissions=ToolPermission(filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def append_file(
        self,
        path: str,
        content: str,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Append text to the end of a workspace file.

        :param path: File path relative to the workspace root.
        :param content: Text to append.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            with resolved.open("a", encoding="utf-8") as handle:
                handle.write(content)
            return {
                "status": "success",
                "path": path,
                "appended_size": len(content),
                "size": resolved.stat().st_size,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="make_directory",
        permissions=ToolPermission(filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
    )
    def make_directory(
        self, path: str, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create a directory inside the workspace.

        :param path: Directory path relative to the workspace root.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            resolved.mkdir(parents=True, exist_ok=True)
            return {"status": "success", "path": path}
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="glob_v2",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def glob_v2(
        self,
        pattern: str,
        path: str = ".",
        include_hidden: bool = False,
        limit: int = 100,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Find files under the workspace that match a glob pattern.

        :param pattern: Glob pattern such as `*.py` or `src/**/*.md`.
        :param path: Directory path, relative to the workspace root, to search in.
        :param include_hidden: Whether to include hidden files and directories.
        :param limit: Maximum number of matching files to return.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        if not str(pattern or "").strip():
            return {"status": "error", "message": "Pattern cannot be empty"}
        try:
            target = _resolve_workspace_path(self.workspace_root, path)
            matches = self._run_rg_files(target, pattern, include_hidden)
            if matches is None:
                matches = []
                for item in self._iter_files(target, include_hidden=include_hidden):
                    rel = os.path.relpath(str(item), self.workspace_root)
                    if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(
                        item.name, pattern
                    ):
                        matches.append(rel)
                matches = sorted(matches)
            capped = matches[: max(1, int(limit))]
            return {
                "status": "success",
                "pattern": pattern,
                "path": path,
                "files": capped,
                "match_count": len(capped),
                "truncated": len(matches) > len(capped),
                "context": {"include_hidden": include_hidden},
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
                "pattern": pattern,
                "path": path,
            }

    @tool(
        name="glob_files",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def glob_files(
        self,
        pattern: str,
        path: str = ".",
        include_hidden: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Find files under the workspace that match a glob pattern.

        :param pattern: Glob pattern such as `*.py`.
        :param path: Directory path relative to the workspace root.
        :param include_hidden: Whether to include hidden files and directories.
        :param limit: Maximum number of matching files to return.
        """
        result = self.glob_v2(
            pattern=pattern, path=path, include_hidden=include_hidden, limit=limit
        )
        if result.get("status") == "success":
            result["num_files"] = result.get("match_count", 0)
        return result

    @tool(
        name="grep_v2",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def grep_v2(
        self,
        pattern: str,
        path: str = ".",
        glob: Optional[str] = None,
        case_sensitive: bool = False,
        regex: bool = True,
        files_with_matches: bool = False,
        limit: int = 100,
        context: int = 0,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Search workspace files for a regex or literal text pattern.

        :param pattern: Regex or literal text to search for.
        :param path: Directory path relative to the workspace root.
        :param glob: Optional glob filter applied before reading candidate files.
        :param case_sensitive: Whether matching should preserve case.
        :param regex: Whether pattern should be interpreted as a regex.
        :param files_with_matches: Whether to return only matching file paths.
        :param limit: Maximum number of returned matches.
        :param context: Reserved context-line count for future expansion.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = context
        _ = runtime_context
        if not str(pattern or "").strip():
            return {"status": "error", "message": "Pattern cannot be empty"}
        try:
            target = _resolve_workspace_path(self.workspace_root, path)
            matches = self._run_rg_grep(
                pattern, target, glob, case_sensitive, regex, files_with_matches
            )
            if matches is None:
                flags = 0 if case_sensitive else re.IGNORECASE
                matcher = re.compile(pattern if regex else re.escape(pattern), flags)
                matches = []
                for file_path in self._iter_files(target):
                    rel = os.path.relpath(str(file_path), self.workspace_root)
                    if glob and not (
                        fnmatch.fnmatch(rel, glob)
                        or fnmatch.fnmatch(file_path.name, glob)
                    ):
                        continue
                    try:
                        lines = file_path.read_text(
                            encoding="utf-8", errors="ignore"
                        ).splitlines()
                    except Exception:
                        continue
                    if files_with_matches:
                        if any(matcher.search(line) for line in lines):
                            matches.append({"path": rel})
                        continue
                    for line_no, text in enumerate(lines, 1):
                        if matcher.search(text):
                            matches.append({"path": rel, "line": line_no, "text": text})
            capped = matches[: max(1, int(limit))]
            return {
                "status": "success",
                "pattern": pattern,
                "path": path,
                "matches": capped,
                "match_count": len(capped),
                "truncated": len(matches) > len(capped),
                "context": {
                    "glob": glob,
                    "case_sensitive": case_sensitive,
                    "regex": regex,
                    "files_with_matches": files_with_matches,
                },
            }
        except re.error as e:
            return {
                "status": "error",
                "message": f"Invalid regex: {e}",
                "pattern": pattern,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "pattern": pattern}

    @tool(
        name="grep_files",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def grep_files(
        self,
        pattern: str,
        path: str = ".",
        glob: Optional[str] = None,
        case_sensitive: bool = False,
        regex: bool = True,
        files_with_matches: bool = False,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """
        Search workspace files for a regex or literal text pattern.

        :param pattern: Regex or literal text to search for.
        :param path: Directory path relative to the workspace root.
        :param glob: Optional glob filter applied before reading candidate files.
        :param case_sensitive: Whether matching should preserve case.
        :param regex: Whether pattern should be interpreted as a regex.
        :param files_with_matches: Whether to return only one entry per matching file.
        :param limit: Maximum number of returned matches.
        """
        result = self.grep_v2(
            pattern=pattern,
            path=path,
            glob=glob,
            case_sensitive=case_sensitive,
            regex=regex,
            files_with_matches=files_with_matches,
            limit=limit,
        )
        if result.get("status") == "success":
            result["num_matches"] = result.get("match_count", 0)
        return result

    @tool(
        name="read_file_range",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def read_file_range(
        self,
        path: str,
        offset: int = 0,
        limit: int = 200,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Read a bounded line range from one workspace file.

        :param path: File path relative to the workspace root.
        :param offset: Zero-based starting line offset.
        :param limit: Maximum number of lines to return.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.file_read_v2(
            path=path, offset=offset, limit=limit, runtime_context=runtime_context
        )
        if result.get("status") != "success":
            return result
        return {
            "status": "success",
            "path": path,
            "offset": result.get("offset", offset),
            "limit": result.get("limit", limit),
            "total_lines": result.get("total_lines", 0),
            "content": result.get("content", ""),
            "lines": result.get("lines", []),
            "has_more": result.get("has_more", False),
        }

    @tool(
        name="search",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def search(self, path: str, keyword: str) -> Dict[str, Any]:
        """
        Search for a keyword inside files within a directory tree.

        :param path: Directory path relative to the workspace root.
        :param keyword: Keyword to search for.
        """
        return self.grep_v2(pattern=keyword, path=path, regex=False, limit=15)

    @tool(
        name="list_tree",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
    )
    def list_tree(self, path: str = ".", depth: int = 3) -> Dict[str, Any]:
        """
        List directory structure in a tree format.

        :param path: Directory path relative to the workspace root.
        :param depth: Maximum depth to traverse.
        """
        try:
            resolved = _resolve_workspace_path(self.workspace_root, path)
            if not resolved.is_dir():
                return {
                    "status": "error",
                    "message": f"Path is not a directory: {path}",
                }
            normalized_depth = min(max(int(depth), 1), 10)
            lines = self._tree_lines(resolved, normalized_depth)
            return {
                "status": "success",
                "path": path,
                "depth": normalized_depth,
                "tree": "\n".join(lines),
                "lines": lines,
            }
        except Exception as e:
            return {"status": "error", "message": str(e), "path": path}

    @tool(
        name="http_request",
        permissions=ToolPermission(network=True),
        rule_scope_builder=_default_rule_scope,
    )
    def http_request(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        allow_redirects: bool = True,
        max_content_chars: int = 120_000,
    ) -> Dict[str, Any]:
        """
        Execute an HTTP request and return a structured response payload.

        :param method: HTTP method such as GET or POST.
        :param url: Absolute http or https URL.
        :param params: Optional query parameters.
        :param data: Optional form-like request body.
        :param json_data: Optional JSON request body.
        :param headers: Optional per-request headers.
        :param timeout: Optional timeout override in seconds.
        :param verify_tls: Whether TLS certificates should be verified.
        :param allow_redirects: Whether redirects should be followed automatically.
        :param max_content_chars: Maximum number of response characters to keep.
        """
        return self._request(
            method=method,
            url=url,
            params=params,
            data=data,
            json_data=json_data,
            headers=headers,
            timeout=timeout,
            verify_tls=verify_tls,
            allow_redirects=allow_redirects,
            max_content_chars=max_content_chars,
        )

    @tool(
        name="http_get",
        permissions=ToolPermission(network=True),
        rule_scope_builder=_default_rule_scope,
    )
    def http_get(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        allow_redirects: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute one HTTP GET request.

        :param url: Absolute URL to request.
        :param params: Optional query parameters.
        :param headers: Optional request headers.
        :param timeout: Optional timeout override in seconds.
        :param verify_tls: Whether TLS certificates should be verified.
        :param allow_redirects: Whether redirects should be followed automatically.
        """
        return self.http_request(
            method="GET",
            url=url,
            params=params,
            headers=headers,
            timeout=timeout,
            verify_tls=verify_tls,
            allow_redirects=allow_redirects,
        )

    @tool(
        name="http_post",
        permissions=ToolPermission(network=True),
        rule_scope_builder=_default_rule_scope,
    )
    def http_post(
        self,
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
        verify_tls: bool = True,
        allow_redirects: bool = True,
    ) -> Dict[str, Any]:
        """
        Execute one HTTP POST request.

        :param url: Absolute URL to request.
        :param data: Optional form-like request body.
        :param json_data: Optional JSON request body.
        :param headers: Optional request headers.
        :param timeout: Optional timeout override in seconds.
        :param verify_tls: Whether TLS certificates should be verified.
        :param allow_redirects: Whether redirects should be followed automatically.
        """
        return self.http_request(
            method="POST",
            url=url,
            data=data,
            json_data=json_data,
            headers=headers,
            timeout=timeout,
            verify_tls=verify_tls,
            allow_redirects=allow_redirects,
        )

    @tool(name="extract_web_text")
    def extract_web_text(self, html: str) -> Dict[str, Any]:
        """
        Extract readable text from raw HTML.

        :param html: Raw HTML string to process.
        """
        payload = self._extract_html_text(html)
        return {
            "status": payload.get("status", "success"),
            "title": payload.get("title", ""),
            "content": payload.get("text", ""),
        }

    @tool(
        name="web_fetch_v2",
        permissions=ToolPermission(network=True),
        rule_scope_builder=_default_rule_scope,
    )
    def web_fetch_v2(
        self,
        url: str,
        prompt: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Fetch one URL and extract concise text for coding workflows.

        :param url: Absolute URL to fetch.
        :param prompt: Optional task-specific extraction hint.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        response = self.http_get(url=url, allow_redirects=False)
        if response.get("status") == "error":
            return response
        if response.get("status_code") in {301, 302, 303, 307, 308}:
            headers = response.get("headers", {})
            redirect_url = headers.get("Location") or response.get("url")
            return {
                "status": "success",
                "redirect_url": redirect_url,
                "url": response.get("url", url),
            }
        extracted = self.extract_web_text(html=str(response.get("content", "")))
        text = str(extracted.get("content", ""))
        result = text
        if prompt.strip():
            keywords = [item.lower() for item in prompt.split() if item.strip()]
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            picked = [
                line
                for line in lines
                if any(token in line.lower() for token in keywords)
            ]
            if picked:
                result = "\n".join(picked[:6])
        auth_hint = ""
        if "github.com" in str(response.get("url", url)):
            auth_hint = "This host may require authentication or a raw-content URL."
        return {
            "status": "success",
            "url": response.get("url", url),
            "result": result,
            "title": extracted.get("title", ""),
            "auth_hint": auth_hint,
        }

    @tool(
        name="web_fetch",
        permissions=ToolPermission(network=True),
        rule_scope_builder=_default_rule_scope,
    )
    def web_fetch(
        self, url: str, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Fetch one web page and extract readable text.

        :param url: Absolute URL to fetch.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        payload = self.web_fetch_v2(url=url, prompt="", runtime_context=runtime_context)
        if payload.get("status") != "success":
            return payload
        return {
            "status": "success",
            "url": payload.get("url", url),
            "redirect_url": payload.get("redirect_url"),
            "title": payload.get("title", ""),
            "content": payload.get("result", ""),
            "auth_hint": payload.get("auth_hint", ""),
        }

    @tool(name="ask_user_choice", requires_user_interaction=True)
    def ask_user_choice(
        self,
        questions: List[Dict[str, Any]],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Emit a structured user-input request.

        :param questions: One to three structured user questions.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        return {"status": "needs_user_input", "questions": list(questions or [])}

    @tool(name="todo_write")
    def todo_write(
        self,
        todos: List[Dict[str, Any]],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Write lightweight todo items into runtime state metadata.

        :param todos: Todo item list.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        state = (runtime_context or {}).get("state")
        normalized = [dict(item) for item in list(todos or [])]
        if state is not None and hasattr(state, "metadata"):
            state.metadata["todos"] = normalized
        return {"status": "success", "count": len(normalized), "todos": normalized}

    @tool(name="tool_search", read_only=True)
    def tool_search(
        self, query: str, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Search the current tool registry by name or description.

        :param query: Case-insensitive substring to search for.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        registry = (runtime_context or {}).get("tool_registry")
        needle = str(query or "").lower()
        results: List[Dict[str, Any]] = []
        if registry is not None and hasattr(registry, "list_tools"):
            for name in registry.list_tools():
                desc = ""
                try:
                    desc = str(
                        (registry.describe_tool(name) or {}).get("description", "")
                    )
                except Exception:
                    desc = ""
                if needle in name.lower() or needle in desc.lower():
                    results.append({"name": name, "description": desc})
        return {"status": "success", "count": len(results), "results": results}

    @tool(
        name="enter_plan_mode",
        prompt="Use this tool proactively when you need to plan a non-trivial implementation before starting. This transitions into a read-only mode where you can analyze the codebase without making changes.",
    )
    def enter_plan_mode(
        self, reason: str = "", runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Switch runtime state into plan mode.

        :param reason: Optional reason for entering plan mode.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        state = (runtime_context or {}).get("state")
        if state is not None and hasattr(state, "metadata"):
            state.metadata["mode"] = "plan"
            state.metadata["plan_reason"] = reason
        return {"status": "success", "current_mode": "plan", "reason": reason}

    @tool(name="exit_plan_mode")
    def exit_plan_mode(
        self, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Switch runtime state out of plan mode.

        :param runtime_context: Optional runtime context injected by the executor.
        """
        state = (runtime_context or {}).get("state")
        if state is not None and hasattr(state, "metadata"):
            state.metadata["mode"] = "work"
        return {"status": "success", "current_mode": "work"}

    @tool(name="enter_worktree")
    def enter_worktree(
        self, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Record that the agent entered worktree mode.

        :param runtime_context: Optional runtime context injected by the executor.
        """
        state = (runtime_context or {}).get("state")
        if state is not None and hasattr(state, "metadata"):
            state.metadata["worktree_mode"] = True
        return {"status": "success", "current_mode": "worktree"}

    @tool(name="exit_worktree")
    def exit_worktree(
        self, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Record that the agent exited worktree mode.

        :param runtime_context: Optional runtime context injected by the executor.
        """
        state = (runtime_context or {}).get("state")
        if state is not None and hasattr(state, "metadata"):
            state.metadata["worktree_mode"] = False
        return {"status": "success", "current_mode": "workspace"}

    @tool(name="lsp_query", read_only=True)
    def lsp_query(
        self,
        operation: str,
        symbol: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Query an injected LSP backend.

        :param operation: LSP operation such as `definition` or `references`.
        :param symbol: Optional symbol or identifier hint.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        ops = (runtime_context or {}).get("ops") or {}
        lsp = ops.get("lsp")
        if lsp is None or not hasattr(lsp, "query"):
            return {"status": "error", "message": "LSP capability unavailable"}
        return lsp.query(operation=operation, symbol=symbol, **kwargs)

    @tool(
        name="task_create",
        prompt="Use this tool proactively when you're about to start a non-trivial implementation task. Creating tasks helps you track progress and organize complex work.",
    )
    def task_create(
        self,
        subject: str,
        description: str,
        active_form: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create a session-native task record.

        :param subject: Short task title.
        :param description: Longer task description.
        :param active_form: Optional active-form wording.
        :param metadata: Optional structured metadata.
        :param status: Initial task status.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        normalized_status = str(status or "pending").strip()
        if normalized_status not in TASK_STATUSES:
            return {
                "status": "error",
                "message": f"Unsupported status: {normalized_status}",
            }
        task_id = self._next_task_id()
        task = {
            "id": task_id,
            "subject": subject,
            "description": description,
            "status": normalized_status,
            "active_form": active_form,
            "blocks": [],
            "blocked_by": [],
            "notes": [],
            "metadata": dict(metadata or {}),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        self._session_tasks[task_id] = task
        return {"status": "success", "task": dict(task)}

    @tool(name="task_get", read_only=True)
    def task_get(
        self, task_id: str, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Fetch one session-native task by id.

        :param task_id: Task identifier.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        task = self._session_tasks.get(str(task_id))
        if task is None:
            return {"status": "error", "message": f"Task not found: {task_id}"}
        return {"status": "success", "task": dict(task)}

    @tool(name="task_list", read_only=True)
    def task_list(
        self,
        status: str = "",
        include_completed: bool = True,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        List session-native tasks.

        :param status: Optional status filter.
        :param include_completed: Whether completed tasks should remain in the result.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        tasks = list(self._session_tasks.values())
        if status:
            tasks = [task for task in tasks if task.get("status") == status]
        if not include_completed:
            tasks = [task for task in tasks if task.get("status") != "completed"]
        return {
            "status": "success",
            "tasks": [dict(task) for task in tasks],
            "count": len(tasks),
        }

    @tool(name="task_update")
    def task_update(
        self,
        task_id: str,
        status: str = "",
        add_blocks: Optional[List[str]] = None,
        remove_blocks: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Update a session-native task.

        :param task_id: Task identifier.
        :param status: Optional new task status.
        :param add_blocks: Optional task ids to add to the blocks list.
        :param remove_blocks: Optional task ids to remove from the blocks list.
        :param metadata: Optional metadata merge payload.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        task = self._session_tasks.get(str(task_id))
        if task is None:
            return {"status": "error", "message": f"Task not found: {task_id}"}
        if status:
            normalized_status = str(status).strip()
            if normalized_status not in TASK_STATUSES:
                return {
                    "status": "error",
                    "message": f"Unsupported status: {normalized_status}",
                }
            task["status"] = normalized_status
        blocks = list(task.get("blocks", []))
        for item in list(add_blocks or []):
            if item not in blocks:
                blocks.append(item)
        for item in list(remove_blocks or []):
            if item in blocks:
                blocks.remove(item)
        task["blocks"] = blocks
        if metadata:
            task["metadata"] = {**dict(task.get("metadata", {})), **dict(metadata)}
        task["updated_at"] = _utc_now()
        self._session_tasks[str(task_id)] = task
        return {"status": "success", "task": dict(task)}

    @tool(name="mcp_list_resources", read_only=True)
    def mcp_list_resources(
        self, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        List injected MCP resources.

        :param runtime_context: Optional runtime context injected by the executor.
        """
        return {
            "status": "success",
            "resources": dict((runtime_context or {}).get("mcp_resources") or {}),
        }

    @tool(name="mcp_read_resource", read_only=True)
    def mcp_read_resource(
        self, server: str, uri: str, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Read one MCP resource from injected snapshots.

        :param server: MCP server name.
        :param uri: Resource URI.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        resources = dict((runtime_context or {}).get("mcp_resources") or {})
        for item in list(resources.get(server) or []):
            if isinstance(item, dict) and str(item.get("uri", "")) == str(uri):
                return {"status": "success", "resource": item}
        return {"status": "error", "message": f"Resource not found: {server}:{uri}"}

    @tool(
        name="agent_spawn",
        prompt=(
            "Launch a new agent to handle a sub-task autonomously. "
            "The agent runs in an isolated context with its own tool set.\n\n"
            "Available agent types:\n"
            "- explore: Fast codebase search agent (Read, Glob, Grep). Use for finding files, "
            "searching code, or answering questions about the codebase.\n"
            "- plan: Read-only architecture planning agent. Use for designing implementation approaches.\n"
            "- general: General-purpose agent with full tool access. Use for complex multi-step tasks.\n\n"
            "The prompt should be self-contained — the agent won't see this conversation. "
            "Include all context the agent needs (file paths, what to look for, etc.)."
        ),
    )
    def agent_spawn(
        self,
        task: str = "",
        subagent_type: str = "explore",
        max_steps: int = 8,
        run_in_background: bool = False,
        runtime_context: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Spawn a sub-agent to handle a task autonomously.

        Creates a child Engine with the sub-agent's toolset and runs it.
        Returns the agent's final answer and step summary.

        :param task: The task prompt for the sub-agent.
        :param subagent_type: Agent type (explore, plan, general).
        :param max_steps: Maximum steps for the sub-agent.
        :param run_in_background: If True, run agent in background thread.
        :param runtime_context: Runtime context from the executor.
        """
        if not task:
            return {"status": "error", "message": "No task provided for sub-agent."}

        # Get the parent agent's LLM and protocol
        state_obj = (runtime_context or {}).get("state")
        llm = None
        model_parser = None
        model_protocol = None

        # Try to get LLM from the parent engine's agent
        engine = (runtime_context or {}).get("engine")
        if engine is None and runtime_context:
            # Walk up to find the engine
            tool_registry = runtime_context.get("tool_registry")
            if tool_registry and hasattr(tool_registry, "_engine"):
                engine = tool_registry._engine

        if engine is not None:
            parent_agent = getattr(engine, "agent", None)
            if parent_agent is not None:
                llm = getattr(parent_agent, "llm", None)
                model_parser = getattr(parent_agent, "model_parser", None)
                model_protocol = getattr(parent_agent, "model_protocol", None)

        if llm is None:
            return {"status": "error", "message": "No LLM available for sub-agent."}

        try:
            agent = self._create_sub_agent(
                subagent_type=subagent_type,
                llm=llm,
                max_steps=max_steps,
                model_parser=model_parser,
                model_protocol=model_protocol,
            )
        except ValueError as e:
            return {"status": "error", "message": str(e)}

        if run_in_background:
            return self._run_agent_background(agent, task)
        return self._run_agent_sync(agent, task)

    def _create_sub_agent(
        self,
        subagent_type: str,
        llm: Any,
        max_steps: int,
        model_parser: Any = None,
        model_protocol: Any = None,
    ) -> Any:
        """Create a sub-agent instance based on type."""
        subagent_type = subagent_type.lower().strip()

        if subagent_type == "explore":
            from qitos.kit.tool.internal.subagents import ExploreAgent
            return ExploreAgent(
                llm=llm,
                workspace_root=self.workspace_root,
                max_steps=max_steps,
                model_parser=model_parser,
                model_protocol=model_protocol,
            )
        elif subagent_type == "plan":
            from qitos.kit.tool.internal.subagents import PlanAgent
            return PlanAgent(
                llm=llm,
                workspace_root=self.workspace_root,
                max_steps=max_steps,
                model_parser=model_parser,
                model_protocol=model_protocol,
            )
        elif subagent_type in ("general", "general-purpose"):
            from qitos.kit.tool.internal.subagents import GeneralAgent
            return GeneralAgent(
                llm=llm,
                workspace_root=self.workspace_root,
                max_steps=max_steps,
                model_parser=model_parser,
                model_protocol=model_protocol,
            )
        else:
            raise ValueError(
                f"Unknown sub-agent type: '{subagent_type}'. "
                f"Available types: explore, plan, general"
            )

    def _run_agent_sync(self, agent: Any, task: str) -> Dict[str, Any]:
        """Run a sub-agent synchronously and return results."""
        from qitos.engine.engine import Engine
        from qitos.engine.states import ContextConfig, RuntimeBudget

        # Propagate permission pipeline and RBW enforcer from parent engine
        parent_pipeline = None
        parent_rbw = None
        if self._engine is not None and self._engine.executor is not None:
            parent_pipeline = getattr(self._engine.executor, "_pipeline", None)
            parent_rbw = getattr(self._engine.executor, "_rbw_enforcer", None)

        engine = Engine(
            agent=agent,
            budget=RuntimeBudget(max_steps=agent.max_steps),
            permission_pipeline=parent_pipeline,
            read_before_write_enforcer=parent_rbw,
            context_config=ContextConfig(
                tool_result_max_chars=50000,
                tool_result_per_message_max_chars=200000,
                reactive_compact=True,
            ),
        )
        result = engine.run(task)

        final_answer = ""
        if result.task_result is not None:
            final_answer = str(getattr(result.task_result, "final_output", "")) or ""
        if not final_answer:
            final_answer = str(getattr(result.state, "final_result", "")) or ""

        step_summaries = []
        for s in result.step_summaries:
            step_summaries.append({
                "step": s.step_id,
                "tool": s.tool_name,
                "status": s.status,
            })

        return {
            "status": "success",
            "spawned": True,
            "subagent_type": agent.name,
            "final_answer": final_answer[:8000],
            "step_count": result.step_count,
            "step_summaries": step_summaries,
            "total_tokens": result.total_tokens,
            "runtime_seconds": round(result.runtime_seconds, 2),
        }

    def _run_agent_background(self, agent: Any, task: str) -> Dict[str, Any]:
        """Run a sub-agent in a background thread."""
        import threading

        task_id = f"agent_{id(agent)}_{threading.get_ident()}"

        # Store in session tasks
        self._session_tasks[task_id] = {
            "status": "running",
            "agent_name": agent.name,
            "task": task[:200],
        }

        def _run():
            try:
                result_dict = self._run_agent_sync(agent, task)
                self._session_tasks[task_id] = {
                    **result_dict,
                    "status": "completed",
                }
            except Exception as exc:
                self._session_tasks[task_id] = {
                    "status": "error",
                    "error": str(exc),
                }

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

        return {
            "status": "success",
            "spawned": True,
            "background": True,
            "task_id": task_id,
            "message": f"Agent running in background. Use task_get with task_id='{task_id}' to check results.",
        }

    @tool(name="cron_create")
    def cron_create(
        self, runtime_context: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Stub cron-create tool.

        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        return {"status": "success", "created": True, "job": kwargs}

    @tool(name="cron_delete")
    def cron_delete(
        self, runtime_context: Optional[Dict[str, Any]] = None, **kwargs: Any
    ) -> Dict[str, Any]:
        """
        Stub cron-delete tool.

        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        return {"status": "success", "deleted": True, "request": kwargs}

    @tool(name="cron_list", read_only=True)
    def cron_list(
        self, runtime_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Stub cron-list tool.

        :param runtime_context: Optional runtime context injected by the executor.
        """
        _ = runtime_context
        return {"status": "success", "jobs": []}

    # ── Claude Code modern-name aliases ────────────────────────────────────────
    # These match Claude Code's exact tool names and signatures for compatibility.

    @tool(
        name="Read",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
        prompt=(
            "Reads a file from the local filesystem. You can access any file directly by using this tool.\n"
            "Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid.\n"
            "Usage:\n"
            "- The file_path parameter must be an absolute path, not a relative path\n"
            "- By default, it reads up to 2000 lines starting from the beginning of the file\n"
            "- You can optionally specify a line offset and limit, but it's recommended to read the whole file by not providing these parameters\n"
            "- When you already know which part of the file you need, only read that part. This can be important for larger files.\n"
            "- This tool can only read files, not directories. To read a directory, use an ls command via the Bash tool.\n"
            "- If you read a file that exists but has empty contents you will receive a system reminder warning."
        ),
    )
    def Read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
        *,
        pages: Optional[str] = None,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Read a file, image, PDF, or notebook. Returns content with line numbers.

        :param file_path: Absolute or relative path to the file.
        :param offset: Line number to start reading from (0-based).
        :param limit: Maximum number of lines to read.
        :param pages: Page range for PDF files (e.g., "1-5", "3").
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.file_read_v2(
            path=file_path,
            offset=offset,
            limit=limit,
            max_chars=200_000,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            return f"Error reading file: {result.get('error', 'unknown error')}"
        content = result.get("content", "")
        # Add line numbers like Claude Code
        lines = content.split("\n")
        numbered = []
        for i, line in enumerate(lines[offset : offset + limit], start=offset + 1):
            numbered.append(f"{i}\t{line}")
        return "\n".join(numbered)

    @tool(
        name="Edit",
        permissions=ToolPermission(filesystem_read=True, filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
        prompt=(
            "Performs exact string replacements in files.\n"
            "Usage:\n"
            "- You must use your `Read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.\n"
            "- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. Never include any part of the line number prefix in the old_string or new_string.\n"
            "- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.\n"
            "- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.\n"
            "- Use `replace_all` for replacing and renaming strings across the file."
        ),
    )
    def Edit(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Replace old_string with new_string in a file. old_string must be unique unless replace_all=True.

        :param file_path: Absolute or relative path to the file.
        :param old_string: Text to find and replace. Must appear exactly once unless replace_all=True.
        :param new_string: Replacement text.
        :param replace_all: Replace all occurrences of old_string.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.file_edit_v2(
            path=file_path,
            action="str_replace",
            old_text=old_string,
            new_text=new_string,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            return f"Error editing file: {result.get('error', 'unknown error')}"
        return result.get("content", "Edit applied successfully")

    @tool(
        name="Write",
        permissions=ToolPermission(filesystem_write=True),
        rule_scope_builder=_default_rule_scope,
        prompt=(
            "Writes a file to the local filesystem.\n"
            "Usage:\n"
            "- This tool will overwrite the existing file if there is one at the provided path.\n"
            "- If this is an existing file, you MUST use the Read tool first to read the file's contents. This tool will fail if you did not read the file first.\n"
            "- Prefer the Edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.\n"
            "- NEVER create documentation files (*.md) or README files unless explicitly requested by the User."
        ),
    )
    def Write(
        self,
        file_path: str,
        content: str,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Write content to a file, creating it if it doesn't exist.

        :param file_path: Absolute or relative path to the file.
        :param content: Content to write.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.write_file(
            path=file_path,
            content=content,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            return f"Error writing file: {result.get('error', 'unknown error')}"
        return f"Successfully wrote to {file_path}"

    @tool(
        name="Glob",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
        prompt=(
            "Fast file pattern matching tool that works with any codebase size.\n"
            "Supports glob patterns like \"**/*.js\" or \"src/**/*.ts\". Returns matching file paths sorted by modification time.\n"
            "Use this tool when you need to find files by name patterns. When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the Agent tool instead."
        ),
    )
    def Glob(
        self,
        pattern: str,
        path: str = ".",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Find files matching a glob pattern.

        :param pattern: Glob pattern (e.g., "**/*.py", "src/**/*.ts").
        :param path: Directory to search in.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.glob_v2(
            pattern=pattern,
            path=path,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            return f"Error: {result.get('error', 'unknown error')}"
        files = result.get("files", [])
        return "\n".join(files)

    @tool(
        name="Grep",
        permissions=ToolPermission(filesystem_read=True),
        read_only=True,
        rule_scope_builder=_default_rule_scope,
        prompt=(
            "A powerful search tool built on ripgrep.\n"
            "Usage:\n"
            "- ALWAYS use Grep for search tasks. NEVER invoke `grep` or `rg` as a Bash command. The Grep tool has been optimized for correct permissions and access.\n"
            "- Supports full regex syntax (e.g., \"log.*Error\", \"function\\\\s+\\\\w+\")\n"
            "- Filter files with glob parameter (e.g., \"*.js\", \"**/*.tsx\") or type parameter\n"
            "- Output modes: \"content\" shows matching lines, \"files_with_matches\" shows only file paths (default), \"count\" shows match counts\n"
            "- Use Agent tool for open-ended searches requiring multiple rounds"
        ),
    )
    def Grep(
        self,
        pattern: str,
        path: str = ".",
        glob: Optional[str] = None,
        type: Optional[str] = None,
        output_mode: str = "content",
        context: int = 0,
        head_limit: int = 100,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Search file contents using regex patterns.

        :param pattern: Regular expression pattern to search for.
        :param path: Directory or file to search in.
        :param glob: File pattern filter (e.g., "*.py").
        :param type: File type filter (js, py, rust, etc.).
        :param output_mode: "content", "files_with_matches", or "count".
        :param context: Number of lines of context before/after matches.
        :param head_limit: Maximum number of results.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        # Map Claude Code's output_mode to grep_v2 parameters
        files_with_matches = output_mode == "files_with_matches"
        result = self.grep_v2(
            pattern=pattern,
            path=path,
            glob=glob,
            case_sensitive=False,
            regex=True,
            files_with_matches=files_with_matches,
            limit=head_limit,
            context=context,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            return f"Error: {result.get('error', 'unknown error')}"
        if files_with_matches:
            matches = result.get("matches", [])
            return "\n".join(str(m) for m in matches)
        matches = result.get("matches", [])
        lines = []
        for m in matches:
            if isinstance(m, dict):
                lines.append(
                    f"{m.get('file', '')}:{m.get('line', '')}:{m.get('text', '')}"
                )
            else:
                lines.append(str(m))
        return "\n".join(lines)

    @tool(
        name="Bash",
        permissions=ToolPermission(command=True),
        supports_background=True,
        rule_scope_builder=_default_rule_scope,
        prompt=(
            "Executes a given bash command and returns its output.\n"
            "The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).\n"
            "IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:\n"
            " - File search: Use Glob (NOT find or ls)\n"
            " - Content search: Use Grep (NOT grep or rg)\n"
            " - Read files: Use Read (NOT cat/head/tail)\n"
            " - Edit files: Use Edit (NOT sed/awk)\n"
            " - Write files: Use Write (NOT echo >/cat <<EOF)\n"
            "If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists. Try to maintain your current working directory throughout the session by using absolute paths. You may specify an optional timeout in milliseconds. You can use `run_in_background` to run commands in the background.\n"
            "For git commands: Prefer to create a new commit rather than amending an existing commit. Before running destructive operations, consider whether there is a safer alternative. Never skip hooks (--no-verify) unless the user has explicitly asked for it.\n"
            "For git commit messages, use HEREDOC format: git commit -m \"$(cat <<'EOF'\\n  Commit message here.\\n  EOF\\n  )\""
        ),
    )
    def Bash(
        self,
        command: str,
        description: str = "",
        timeout: Optional[int] = None,
        run_in_background: bool = False,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Execute a shell command.

        :param command: Shell command to execute.
        :param description: Brief description of what the command does.
        :param timeout: Timeout in milliseconds (max 600000).
        :param run_in_background: Run command in background and return task ID.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.bash_v2(
            command=command,
            read_only=False,
            allow_destructive=False,
            run_in_background=run_in_background,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            error = result.get("error", "")
            returncode = result.get("returncode", 1)
            stdout = result.get("stdout", "")
            if stdout:
                return f"Exit code {returncode}:\n{stdout}\n{error}"
            return f"Error: {error}"
        stdout = result.get("stdout", "")
        returncode = result.get("returncode", 0)
        if returncode != 0:
            stderr = result.get("stderr", "")
            return f"Exit code {returncode}:\n{stdout}\n{stderr}"
        return stdout

    @tool(
        name="WebFetch",
        permissions=ToolPermission(network=True),
        rule_scope_builder=_default_rule_scope,
        prompt=(
            "Fetches content from a specified URL and processes it using an AI model. Takes a URL and a prompt as input. Fetches the URL content, converts HTML to markdown. Processes the content with the prompt using a small, fast model.\n"
            "Usage notes:\n"
            "- The URL must be a fully-formed valid URL. HTTP URLs will be automatically upgraded to HTTPS.\n"
            "- The prompt should describe what information you want to extract from the page.\n"
            "- This tool is read-only and does not modify any files.\n"
            "- Results may be summarized if the content is very large.\n"
            "- Includes a self-cleaning 15-minute cache for faster responses.\n"
            "- For GitHub URLs, prefer using the gh CLI via Bash instead."
        ),
    )
    def WebFetch(
        self,
        url: str,
        prompt: str = "",
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Fetch a URL and convert to markdown, optionally summarizing with AI.

        :param url: URL to fetch.
        :param prompt: Optional prompt for AI summarization of the content.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        result = self.web_fetch_v2(
            url=url,
            prompt=prompt,
            runtime_context=runtime_context,
        )
        if result.get("status") != "success":
            return f"Error fetching URL: {result.get('error', 'unknown error')}"
        return result.get("content", "")

    @tool(
        name="AskUserQuestion",
        requires_user_interaction=True,
        prompt=(
            "Use this tool when you need to ask the user questions during execution. This allows you to:\n"
            "1. Gather user preferences or requirements\n"
            "2. Clarify ambiguous instructions\n"
            "3. Get decisions on implementation choices as you work\n"
            "4. Offer choices to the user about what direction to take.\n"
            "Usage notes:\n"
            "- Users will always be able to select \"Other\" to provide custom text input\n"
            "- Use multiSelect: true to allow multiple answers to be selected for a question\n"
            "- If you recommend a specific option, make that the first option in the list and add \"(Recommended)\" at the end of the label"
        ),
    )
    def AskUserQuestion(
        self,
        questions: List[Dict[str, Any]],
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Ask the user one or more questions with optional choices.

        :param questions: List of question dicts with 'question', 'options', and optional 'preview'.
        :param runtime_context: Optional runtime context injected by the executor.
        """
        return self.ask_user_choice(
            questions=questions,
            runtime_context=runtime_context,
        )


__all__ = ["CodingToolSet", "TASK_STATUSES", "_resolve_workspace_path"]
