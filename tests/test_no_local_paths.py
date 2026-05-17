from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _policy_files() -> list[Path]:
    roots = [ROOT / "README.md", ROOT / "README.zh.md", ROOT / "examples", ROOT / "docs"]
    files: list[Path] = []
    for root in roots:
        if root.is_file():
            files.append(root)
            continue
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix in {".py", ".md", ".mdx", ".txt"}
        )
    return files


def test_docs_and_examples_do_not_commit_local_absolute_paths() -> None:
    forbidden = ("/Users/", "/home/", "/private/")
    offenders: list[str] = []
    for path in _policy_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(item in text for item in forbidden):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_docs_and_examples_do_not_commit_real_api_keys() -> None:
    key_pattern = re.compile(r"sk-[A-Za-z0-9_=-]{12,}")
    offenders: list[str] = []
    for path in _policy_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        if key_pattern.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
