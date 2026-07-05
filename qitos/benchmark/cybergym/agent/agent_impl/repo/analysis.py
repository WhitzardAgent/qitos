"""Repository summary helpers for CyberGym."""

from __future__ import annotations

from pathlib import Path


class RepoAnalysisMixin:
    """Repository analysis helpers used during state initialization."""

    @staticmethod
    def _build_repo_index(repo_dir: str) -> str:
        try:
            repo_path = Path(repo_dir)
            top_entries = sorted(
                [p for p in repo_path.iterdir() if not p.name.startswith(".")],
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
            files = [p for p in repo_path.rglob("*") if p.is_file()]

            top_level_lines = []
            for entry in top_entries[:12]:
                suffix = "/" if entry.is_dir() else ""
                top_level_lines.append(f"- {entry.name}{suffix}")

            dir_counts = []
            for entry in top_entries:
                if not entry.is_dir():
                    continue
                file_count = sum(1 for item in entry.rglob("*") if item.is_file())
                dir_counts.append((file_count, entry.name))
            dir_counts.sort(reverse=True)

            interesting = []
            path_tokens = (
                "fuzz", "oss-fuzz", "corpus", "sample", "seed", "test",
                "src", "lib", "coders", "parser", "decode", "readelf",
            )
            for path in files:
                rel = str(path.relative_to(repo_path))
                lowered = rel.lower()
                if any(token in lowered for token in path_tokens):
                    interesting.append(rel)
                if len(interesting) >= 15:
                    break

            lines = [
                f"Source root: {repo_path.name}",
                f"Total files: {len(files)}",
                "Top-level entries:",
                *top_level_lines,
            ]
            if dir_counts:
                lines.append("Largest top-level directories:")
                for count, name in dir_counts[:8]:
                    lines.append(f"- {name}/ ({count} files)")
            if interesting:
                lines.append("Interesting paths:")
                for rel in interesting[:15]:
                    lines.append(f"- {rel}")
            # P28: raised cap from 1800 to 3000 — large repos (1000+ files)
            # lose critical directory structure under 1800 chars.
            return "\n".join(lines)[:3000]
        except Exception:
            return ""
