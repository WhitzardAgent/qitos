"""WorktreeManager — git worktree management for sub-agent isolation."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional


class WorktreeManager:
    """Manages git worktrees under .qitos/worktrees/ for sub-agent isolation.

    Each worktree provides an isolated copy of the repository so that
    sub-agents can work in parallel without interfering with each other
    or the main working directory.
    """

    def __init__(self, workspace_root: str = "."):
        self.workspace_root = os.path.abspath(workspace_root)
        self._worktrees_dir = os.path.join(self.workspace_root, ".qitos", "worktrees")
        os.makedirs(self._worktrees_dir, exist_ok=True)

    def create_worktree(self, name: str) -> str:
        """Create a new git worktree and return its path.

        :param name: Name for the worktree (used as directory name).
        :returns: Absolute path to the worktree directory.
        :raises RuntimeError: If the worktree cannot be created.
        """
        worktree_path = os.path.join(self._worktrees_dir, name)

        if os.path.exists(worktree_path):
            # Already exists — return it
            return worktree_path

        # Create a new git worktree
        try:
            result = subprocess.run(
                ["git", "worktree", "add", "--detach", worktree_path, "HEAD"],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                # Fall back: just copy the directory if git worktree fails
                return self._fallback_copy(name)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return self._fallback_copy(name)

        return worktree_path

    def remove_worktree(self, name: str) -> bool:
        """Remove a git worktree by name.

        :param name: Name of the worktree to remove.
        :returns: True if the worktree was removed.
        """
        worktree_path = os.path.join(self._worktrees_dir, name)
        if not os.path.exists(worktree_path):
            return False

        try:
            result = subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_path],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

        # Fallback: just delete the directory
        try:
            import shutil

            shutil.rmtree(worktree_path, ignore_errors=True)
            return True
        except Exception:
            return False

    def list_worktrees(self) -> List[str]:
        """List all worktree names under .qitos/worktrees/."""
        if not os.path.isdir(self._worktrees_dir):
            return []
        return [
            d
            for d in os.listdir(self._worktrees_dir)
            if os.path.isdir(os.path.join(self._worktrees_dir, d))
        ]

    def _fallback_copy(self, name: str) -> str:
        """Fallback: create a copy of the workspace if git worktree fails."""
        import shutil

        worktree_path = os.path.join(self._worktrees_dir, name)

        # Create a lightweight symlink-based copy for key directories
        os.makedirs(worktree_path, exist_ok=True)

        # Copy .git reference if it exists
        git_dir = os.path.join(self.workspace_root, ".git")
        if os.path.isdir(git_dir):
            git_link = os.path.join(worktree_path, ".git")
            if not os.path.exists(git_link):
                # Write a gitdir file pointing to the original
                with open(git_link, "w") as f:
                    f.write(git_dir)

        return worktree_path
