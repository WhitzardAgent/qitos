"""Seed corpus discovery and preparation for fuzzing targets.

Extracted from CyberGymAgent static methods so the logic is testable and
reusable without pulling in the full agent class.
"""

from __future__ import annotations

import os
import re
import zipfile
from collections import Counter
from pathlib import Path
from typing import Dict, List


# Source/text extensions that are NEVER fuzzer input samples.
NON_SAMPLE_EXT = frozenset({
    ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hh", ".inc", ".py", ".pyc",
    ".md", ".txt", ".rst", ".html", ".htm", ".js", ".ts", ".css", ".sh",
    ".cmake", ".in", ".am", ".ac", ".m4", ".mk", ".yml", ".yaml", ".cfg",
    ".ini", ".toml", ".go", ".rs", ".java", ".kt", ".rb", ".pl", ".php",
    ".po", ".pot", ".map", ".def", ".sym", ".ld", ".s", ".asm", ".o", ".a",
    ".lo", ".la", ".so", ".dll", ".dylib", ".gitignore", ".gitattributes",
    ".cs", ".swift", ".lua", ".tcl", ".bat", ".ps1", ".dox", ".1", ".3",
    ".json", ".tests", ".test", ".mak", ".supp", ".dist", ".svg", ".diff",
    ".patch", ".log", ".csv", ".tsv", ".expected", ".out", ".err", ".ref",
    ".dat.txt", ".am.in", ".cmake.in", ".gperf", ".vcxproj", ".sln",
})

# Text formats that are themselves fuzzer INPUTS (not source/build/config).
# Targets like libxslt/libxml2/JS/SQL/regex consume text, so their useful
# mutation seeds are text files (.xml/.xsl/.js/...) that the binary-only
# filter would otherwise drop. These override the NON_SAMPLE_EXT exclusion
# and are kept even though they are not binary.
TEXT_INPUT_EXT = frozenset({
    ".xml", ".xsl", ".xslt", ".html", ".htm", ".xhtml", ".svg", ".js",
    ".mjs", ".json", ".css", ".sql", ".csv", ".tsv", ".ps", ".eps",
    ".rtf", ".vtt", ".srt", ".wkt", ".gml", ".kml", ".geojson", ".dtd",
})


def parse_harness_info(workspace_root: str) -> str:
    """Read submit.sh and extract harness info (binary path, arguments)."""
    submit_sh = os.path.join(workspace_root, "submit.sh")
    if not os.path.isfile(submit_sh):
        return ""
    try:
        # submit.sh is retained for audit and parsed separately.  A generous
        # bound avoids losing declarations that follow generated headers.
        content = Path(submit_sh).read_text(errors="replace")[:65536]
        return f"submit.sh content:\n{content}"
    except Exception:
        return ""


def discover_corpus_files(repo_dir: str) -> List[str]:
    """Find fuzzing corpus and sample input files in the repo."""
    corpus_files = []
    repo_path = Path(repo_dir)
    seen = set()
    sample_path_keywords = (
        "corpus", "seed", "sample", "samples", "testcase",
        "fuzz", "oss-fuzz", "test", "testdata", "test_input",
        "test_data", "input", "examples", "crash", "poc",
    )

    # Search for corpus directories (expanded patterns)
    corpus_dir_patterns = [
        "fuzzing/corpus", "corpus", "testcases", "seeds",
        "seed_corpus", "fuzz/corpus", "test_corpus",
        "test/data", "testdata", "test_input", "test/input",
        "testcases", "examples/input", "samples", "input",
    ]
    for pattern in corpus_dir_patterns:
        corpus_dir = repo_path / pattern
        if corpus_dir.is_dir():
            for f in corpus_dir.iterdir():
                if (
                    f.is_file()
                    and f.stat().st_size < 1_000_000
                    and not is_git_lfs_pointer(f)
                    and str(f) not in seen
                ):  # < 1MB
                    rel = str(f.relative_to(repo_path))
                    corpus_files.append(rel)
                    seen.add(str(f))

    # Search for sample input files by extension
    sample_extensions = {
        ".png", ".jpg", ".jpeg", ".heic", ".heif",
        ".pdf", ".zip", ".gz", ".tar", ".bz2",
        ".bin", ".raw", ".dat", ".img",
        ".mng", ".gif", ".bmp", ".tiff", ".webp",
        ".input", ".poc", ".crash",
    }
    for f in repo_path.rglob("*"):
        if f.is_file() and f.suffix.lower() in sample_extensions:
            if str(f) in seen:
                continue
            lowered = str(f.relative_to(repo_path)).lower()
            # Accept files in corpus-like directories OR small files anywhere
            in_corpus_dir = any(token in lowered for token in sample_path_keywords)
            is_small = f.stat().st_size < 100_000  # < 100KB
            if not in_corpus_dir and not is_small:
                continue
            if (
                f.stat().st_size < 1_000_000
                and not is_git_lfs_pointer(f)
            ):  # < 1MB
                try:
                    rel = str(f.relative_to(repo_path))
                    corpus_files.append(rel)
                    seen.add(str(f))
                except ValueError:
                    pass

    return corpus_files[:30]  # Cap at 30 files


def prepare_seed_corpus(task_root: str, repo_dir: str) -> List[str]:
    """Find seed-corpus zips/dirs near the task and extract them.

    oss-fuzz ships `<fuzzer>_seed_corpus.zip` of VALID inputs BESIDE the
    source tree (at repo-vul/), i.e. OUTSIDE repo_dir (= repo-vul/<project>),
    so the repo_dir-only discovery misses it. Scan the task root + repo_dir's
    parent, extract any seed/corpus zip into <task_root>/seeds/, and return
    workspace-relative paths to individual seed files (smallest first) so the
    agent can copy+mutate a real input rather than hand-craft raw bytes.
    """
    if not task_root or not os.path.isdir(task_root):
        return []
    roots: List[str] = []
    for r in (task_root, os.path.dirname(repo_dir or ""), repo_dir):
        if r and os.path.isdir(r) and r not in roots:
            roots.append(r)
    out_base = os.path.join(task_root, "seeds")
    seed_paths: List[str] = []
    seen_zip: set = set()
    for root in roots:
        try:
            entries = sorted(os.listdir(root))
        except OSError:
            continue
        for name in entries:
            low = name.lower()
            if not low.endswith(".zip"):
                continue
            if not any(tok in low for tok in ("seed_corpus", "corpus", "seed")):
                continue
            full = os.path.join(root, name)
            if full in seen_zip or not os.path.isfile(full):
                continue
            seen_zip.add(full)
            try:
                if os.path.getsize(full) > 20_000_000:
                    continue
                dest = os.path.join(
                    out_base, re.sub(r"[^A-Za-z0-9_.-]", "_", name[:-4])
                )
                if not os.path.isdir(dest):
                    os.makedirs(dest, exist_ok=True)
                    with zipfile.ZipFile(full) as zf:
                        members = [m for m in zf.namelist() if not m.endswith("/")][:200]
                        for m in members:
                            try:
                                zf.extract(m, dest)
                            except Exception:
                                continue
                for dp, _dirs, files in os.walk(dest):
                    for f in files:
                        fp = os.path.join(dp, f)
                        try:
                            sz = os.path.getsize(fp)
                        except OSError:
                            continue
                        if 0 < sz < 2_000_000:
                            seed_paths.append(os.path.relpath(fp, task_root))
            except Exception:
                continue
    seed_paths = sorted(
        set(seed_paths),
        key=lambda p: (
            os.path.getsize(os.path.join(task_root, p))
            if os.path.exists(os.path.join(task_root, p))
            else 1 << 30
        ),
    )
    return seed_paths[:20]


def file_looks_binary(path: str) -> bool:
    """True if the file content is binary (a real fuzzer input), not ASCII
    text (a spec/config/build file). Real fonts/images/CAD/etc. contain NUL
    bytes or a high fraction of non-text bytes in their header."""
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(1024)
    except OSError:
        return False
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    # printable-text bytes: tab/newline/CR + printable ASCII range
    text_bytes = set(range(0x20, 0x7F)) | {0x09, 0x0A, 0x0D, 0x0C}
    nonprint = sum(1 for b in chunk if b not in text_bytes)
    return (nonprint / len(chunk)) > 0.20


def discover_repo_seed_samples(repo_dir: str) -> List[str]:
    """Find REAL format sample files already in the repo to use as mutation seeds.

    Most oss-fuzz projects ship complex valid inputs in `test/`, `tests/`,
    `examples/`, `data/`, `fonts/`, `fixtures/` etc. (e.g. harfbuzz has 1000+
    real .ttf/.otf, libredwg has 100+ real .dwg). These are NOT in a
    `seed_corpus.zip`, have format-specific extensions the old corpus scan
    ignored, and live under `test/` (a path token the old scan missed) — so
    the agent never saw them and hand-crafted tiny invalid files that never
    reach the bug. Surface them so poc_strategy -> corpus_mutate and the
    agent mutates a real input. Returns ABSOLUTE paths (under repo_dir, which
    is inside the workspace, so the agent can READ/cp them).
    """
    if not repo_dir or not os.path.isdir(repo_dir):
        return []
    sample_dir_tokens = (
        "test", "sample", "example", "data", "font", "corpus", "seed",
        "fixture", "asset", "demo", "input", "regress", "case",
    )

    cands: List[tuple] = []  # (path, size, ext)
    scanned_dirs = 0
    for dp, dirs, files in os.walk(repo_dir):
        # prune VCS / build dirs
        dirs[:] = [d for d in dirs if d not in (".git", "build", ".github", "node_modules", "__pycache__")]
        low = dp.lower()
        if not any(tok in low for tok in sample_dir_tokens):
            continue
        scanned_dirs += 1
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            is_text_input = ext in TEXT_INPUT_EXT
            if not ext:
                continue
            # Skip source/build/config unless it is a known text INPUT format.
            if ext in NON_SAMPLE_EXT and not is_text_input:
                continue
            fp = os.path.join(dp, f)
            try:
                sz = os.path.getsize(fp)
            except OSError:
                continue
            # Keep real binary inputs, OR text-format inputs (xml/xsl/js/...)
            # whose value as a seed does not depend on being binary.
            if 32 < sz < 2_000_000 and (
                file_looks_binary(fp) or is_text_input
            ):
                cands.append((fp, sz, ext))
        if len(cands) > 3000 or scanned_dirs > 4000:
            break
    if not cands:
        return []
    # Lock onto the dominant input format: the most common sample extensions
    # are almost always the fuzzer's input type (fonts, images, CAD, ...).
    ext_counts = Counter(e for _, _, e in cands)
    top_exts = {e for e, _ in ext_counts.most_common(3)}
    sel = [c for c in cands if c[2] in top_exts]
    sel.sort(key=lambda c: c[1])  # smallest first: easier to reason about + mutate
    out: List[str] = []
    per_ext: Dict[str, int] = {}
    for fp, _sz, ext in sel:
        if per_ext.get(ext, 0) >= 8:
            continue
        per_ext[ext] = per_ext.get(ext, 0) + 1
        out.append(fp)
        if len(out) >= 16:
            break
    return out


def is_git_lfs_pointer(path: Path) -> bool:
    try:
        if path.stat().st_size > 1024:
            return False
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return (
        "version https://git-lfs.github.com/spec/v1" in content
        and "\noid sha256:" in content
    )
