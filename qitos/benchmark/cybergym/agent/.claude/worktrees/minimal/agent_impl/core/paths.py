"""PoC path normalization and extraction mixin."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..state import CyberGymState

from .constants import POC_OUTPUT_DIR, POC_PLACEHOLDER_CHARS


class PathMixin:
    """PoC path utilities — mostly @staticmethod."""

    def _extract_findings_from_read(self, state: CyberGymState, content: str) -> None:
        """Extract vulnerable function names from file read output."""
        func_pattern = r'(?:void|int|char|unsigned|long|static)\s+\*?\s*(\w+)\s*\('
        for match in re.finditer(func_pattern, content):
            func_name = match.group(1)
            if func_name not in state.vulnerable_functions and len(state.vulnerable_functions) < 20:
                desc_lower = state.vulnerability_description.lower()
                if func_name.lower() in desc_lower:
                    state.vulnerable_functions.append(func_name)

    def _extract_findings_from_search(self, state: CyberGymState, content: str) -> None:
        """Extract file paths from search/grep output."""
        file_pattern = r'^([^\s:]+\.[ch]|[^:\s]+\.py|[^:\s]+\.rs|[^:\s]+\.cpp|[^:\s]+\.cc):'
        for match in re.finditer(file_pattern, content, re.MULTILINE):
            filepath = self._normalize_repo_path(state, match.group(1))
            if filepath not in state.vulnerable_files and len(state.vulnerable_files) < 20:
                state.vulnerable_files.append(filepath)

        # Also check for "path" field in structured grep output
        if not state.vulnerable_files and '"path"' in content:
            try:
                data = json.loads(content)
                matches = data.get("matches") or []
                if isinstance(matches, list):
                    for match in matches:
                        if not isinstance(match, dict):
                            continue
                        path = self._normalize_repo_path(state, match.get("path", ""))
                        if path and path not in state.vulnerable_files:
                            state.vulnerable_files.append(path)
                        if len(state.vulnerable_files) >= 20:
                            break
                if not state.vulnerable_files:
                    path = self._normalize_repo_path(state, data.get("path", ""))
                    if path and path not in state.vulnerable_files:
                        state.vulnerable_files.append(path)
            except (json.JSONDecodeError, TypeError):
                pass

    @staticmethod
    def _normalize_repo_path(state: CyberGymState, path: str) -> str:
        """Normalize absolute paths into repo-relative paths when possible."""
        if not path:
            return ""

        normalized = str(path).strip().strip("'\"")
        if not normalized:
            return ""

        try:
            candidate = Path(normalized)
        except (TypeError, ValueError):
            return normalized

        repo_roots = []
        if getattr(state, "repo_dir", ""):
            repo_roots.append(Path(state.repo_dir))
        archive_root = state.repo_archive_root or state.metadata.get("repo_archive_root")
        if archive_root:
            repo_roots.append(Path(archive_root))

        for root in repo_roots:
            try:
                relative = candidate.resolve().relative_to(root.resolve())
                return str(relative)
            except (OSError, RuntimeError, ValueError):
                continue

        if normalized.startswith("./"):
            return normalized[2:]
        return normalized

    @staticmethod
    def _poc_output_dir_path(state: CyberGymState) -> Path:
        workspace_root = str(getattr(state, "workspace_root", "") or "").strip()
        base = Path(workspace_root) if workspace_root else Path(".")
        return base / POC_OUTPUT_DIR

    @staticmethod
    def _ensure_poc_output_dir(state: CyberGymState) -> None:
        try:
            PathMixin._poc_output_dir_path(state).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    @staticmethod
    def _has_placeholder_path_chars(path: str) -> bool:
        return any(ch in str(path or "") for ch in POC_PLACEHOLDER_CHARS)

    @staticmethod
    def _normalize_ready_poc_path(state: CyberGymState, raw_path: str) -> str:
        cleaned = PathMixin._clean_path_candidate(str(raw_path or ""))
        if not cleaned or PathMixin._has_placeholder_path_chars(cleaned):
            return ""
        if not PathMixin._is_poc_path_candidate(cleaned):
            return ""

        try:
            candidate = Path(cleaned)
        except (TypeError, ValueError):
            return ""

        poc_dir = PathMixin._poc_output_dir_path(state)
        workspace_root = Path(str(getattr(state, "workspace_root", "") or "."))
        if candidate.is_absolute():
            candidate_path = candidate
        else:
            candidate_path = workspace_root / candidate

        try:
            candidate_resolved = candidate_path.resolve()
            poc_dir_resolved = poc_dir.resolve()
            candidate_resolved.relative_to(poc_dir_resolved)
        except (OSError, RuntimeError, ValueError):
            return ""

        if not candidate_path.is_file() or candidate_path.stat().st_size <= 0:
            return ""

        try:
            return str(candidate_resolved.relative_to(workspace_root.resolve()))
        except (OSError, RuntimeError, ValueError):
            return str(Path(POC_OUTPUT_DIR) / candidate_path.name)

    @staticmethod
    def _extract_poc_paths_from_command(command: str) -> List[str]:
        """Best-effort extraction of PoC output paths from a shell command."""
        if not command:
            return []

        patterns = [
            # Python: open('/tmp/.../poc.bin', 'wb') or open('p3.bin', 'wb')
            r"open\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][^'\"]*[wa]",
            # Shell redirection: > /tmp/poc.bin or >> ./p3.bin
            r"(?:^|\s)(?:>{1,2})\s*(['\"]?)([^'\"\s;|&]+)\1",
            # dd of=poc.bin
            r"(?:^|\s)of=(['\"]?)([^'\"\s;|&]+)\1",
            # tee poc.bin
            r"(?:^|\s)tee\s+(['\"]?)([^'\"\s;|&]+)\1",
            # cp /tmp/poc.bin poc.bin, mv tmp.bin poc.bin
            r"(?:^|[;&|]\s*)(?:cp|mv)\s+(?:-\S+\s+)*\S+\s+(['\"]?)([^'\"\s;|&]+)\1",
            # Variant tables: variants.append(('p3.bin', data))
            r"['\"]([^'\"]+\.(?:bin|pcap|jxl|rar|gif|png|jpg|jpeg|webp|tif|tiff|bmp|zip|gz|xz|dat))['\"]\s*,",
        ]
        paths: List[str] = []
        seen: set[str] = set()
        for pattern in patterns:
            for match in re.finditer(pattern, command, flags=re.IGNORECASE):
                path = match.group(1)
                if len(match.groups()) >= 2:
                    path = match.group(2)
                path = PathMixin._clean_path_candidate(path)
                if (
                    not path
                    or PathMixin._has_placeholder_path_chars(path)
                    or not PathMixin._is_poc_path_candidate(path)
                    or not str(Path(path)).startswith(f"{POC_OUTPUT_DIR}/")
                ):
                    continue
                if path in seen:
                    continue
                seen.add(path)
                paths.append(path)
        return paths

    @staticmethod
    def _extract_poc_path_from_command(command: str) -> str:
        paths = PathMixin._extract_poc_paths_from_command(command)
        return paths[0] if paths else ""

    @staticmethod
    def _clean_path_candidate(path: str) -> str:
        path = path.strip().strip("'\"")
        # Drop grep-style suffixes such as file.c:123:content.
        path = re.sub(r":\d+(?::.*)?$", "", path)
        return path.rstrip(",);")

    @staticmethod
    def _is_poc_path_candidate(path: str) -> bool:
        if not path:
            return False
        name = Path(path).name.lower()
        if not name.startswith("poc"):
            suffix = Path(name).suffix.lower()
            if suffix not in {
                ".bin", ".pcap", ".jxl", ".rar", ".gif", ".png",
                ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp",
                ".zip", ".gz", ".xz", ".dat",
            }:
                return False
            stem = Path(name).stem.lower()
            return bool(re.match(r"p\d+(?:[_-].*)?$", stem) or re.match(r"v\d+(?:[_-].*)?$", stem))
        return True
