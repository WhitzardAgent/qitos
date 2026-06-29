"""Harness / corpus / PoC-strategy detection mixin."""

from __future__ import annotations

import os
import re as _re
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..state import CyberGymState, InputFormatModel


# Mapping from fuzzer binary name patterns to (format_type, magic_bytes).
# Covers the most common oss-fuzz naming conventions.
_FUZZER_NAME_FORMAT_MAP: List[tuple] = [
    (r'(?i)(?:coder_|codec_)?(?:jpg|jpeg)[_]fuzzer', "jpeg", "FF D8 FF"),
    (r'(?i)(?:coder_|codec_)?png[_]fuzzer', "png", "89 50 4E 47"),
    (r'(?i)(?:coder_|codec_)?gif[_]fuzzer', "gif", "47 49 46 38"),
    (r'(?i)(?:coder_|codec_)?bmp[_]fuzzer', "bmp", "42 4D"),
    (r'(?i)(?:coder_|codec_)?pdf[_]fuzzer', "pdf", "25 50 44 46"),
    (r'(?i)(?:coder_|codec_)?(?:wav|audio)[_]fuzzer', "wav", "52 49 46 46"),
    (r'(?i)(?:coder_|codec_)?(?:sfnt|font|ttf|otf)[_]fuzzer', "font", "00 01 00 00"),
    (r'(?i)(?:coder_|codec_)?(?:zip|archive)[_]fuzzer', "zip", "50 4B 03 04"),
    (r'(?i)(?:coder_|codec_)?pcap[_]fuzzer', "pcap", "D4 C3 B2 A1"),
    (r'(?i)(?:coder_|codec_)?(?:heic|heif)[_]fuzzer', "heic", ""),
    (r'(?i)(?:coder_|codec_)?webp[_]fuzzer', "webp", "52 49 46 46"),
    (r'(?i)(?:coder_|codec_)?(?:avi|mkv|mp4|video)[_]fuzzer', "video", ""),
    (r'(?i)(?:coder_|codec_)?xml[_]fuzzer', "xml", ""),
    (r'(?i)(?:coder_|codec_)?elf[_]fuzzer', "elf", "7F 45 4C 46"),
]


class HarnessMixin:
    """Static methods for detecting PoC strategy, input format, and corpus usage."""

    @staticmethod
    def _discover_fuzzer_target(repo_dir: str) -> str:
        """Scan repo build scripts for fuzzer binary target names.

        Searches oss-fuzz build scripts, Makefiles, and CMakeLists for
        fuzzer binary names like 'coder_JPG_fuzzer', 'png_fuzzer', etc.
        Returns the first matching fuzzer name, or empty string.
        """
        if not repo_dir or not os.path.isdir(repo_dir):
            return ""
        repo_path = Path(repo_dir)

        # Files most likely to contain fuzzer target names
        search_files: List[Path] = []
        for pattern in [
            "**/oss-fuzz-build.sh", "**/*build*.sh", "**/Makefile*",
            "**/CMakeLists.txt", "**/fuzzing/*.cc", "**/fuzzing/*.cpp",
        ]:
            try:
                search_files.extend(repo_path.glob(pattern))
            except (OSError, ValueError):
                continue

        # Limit search to first 10 files to bound I/O
        fuzzer_names: List[str] = []
        for fpath in search_files[:10]:
            try:
                content = fpath.read_text(errors="replace")[:10000]
            except (OSError, ValueError):
                continue
            # Match fuzzer binary names: word_fuzzer patterns
            for m in _re.finditer(r'(\w{3,}_fuzzer)\b', content):
                name = m.group(1)
                if name not in fuzzer_names:
                    fuzzer_names.append(name)
            if len(fuzzer_names) >= 10:
                break

        # Return the first name that matches a known format pattern
        for name in fuzzer_names:
            for pattern, fmt_type, magic in _FUZZER_NAME_FORMAT_MAP:
                if _re.search(pattern, name):
                    return name
        # Return the first generic fuzzer name if no format match
        return fuzzer_names[0] if fuzzer_names else ""

    @staticmethod
    def _detect_poc_strategy(state: CyberGymState) -> str:
        """Auto-detect PoC generation strategy based on bug type and corpus availability."""
        desc_lower = state.vulnerability_description.lower()

        # Text-oriented bug classes should not be forced into corpus mutation even if
        # the repository happens to contain binary samples.
        text_bug_types = {"format_string", "command_injection", "xss", "sql_injection"}
        text_indicators = [
            "format string", "injection", "xss", "sql",
            "command injection", "regex", "input validation",
        ]
        if state.bug_type in text_bug_types or any(ind in desc_lower for ind in text_indicators):
            return "text"

        # If corpus files are available and they look like actual fuzz/sample inputs,
        # prefer seed mutation over inventing a file from scratch.
        if state.corpus_files and HarnessMixin._should_use_corpus_mutation(state):
            return "corpus_mutate"

        # Binary format bugs -> Python struct.pack or hex
        binary_indicators = [
            "image", "png", "jpg", "jpeg", "heic", "heif", "gif", "bmp", "mng",
            "video", "mp4", "avi", "mkv",
            "archive", "zip", "tar", "gz", "bz2", "7z",
            "audio", "mp3", "wav", "ogg", "flac",
            "pdf", "doc", "elf", "pe",
            "heap-buffer-overflow", "stack-buffer-overflow",
            "heap-use-after-free",
        ]
        if any(ind in desc_lower for ind in binary_indicators):
            # Small/fixed-size payloads can use hex directly
            small_indicators = ["byte", "offset", "magic", "header", "chunk", "field"]
            if any(si in desc_lower for si in small_indicators):
                return "hex"
            return "binary_python"

        # Default: text (safe fallback)
        return "text"

    @staticmethod
    def _build_input_format_model(state: CyberGymState) -> InputFormatModel:
        """Build an InputFormatModel from harness info, corpus, and description.

        This is a best-effort initial model — it gets confirmed later when
        source code reveals the entry function (e.g., LLVMFuzzerTestOneInput).
        """
        from ..state import InputFormatModel

        fmt = InputFormatModel()
        desc_lower = state.vulnerability_description.lower()

        # Detect format type from description keywords
        format_map = [
            (["png"], "png"),
            (["jpeg", "jpg"], "jpeg"),
            (["gif"], "gif"),
            (["bmp"], "bmp"),
            (["heic", "heif"], "heic"),
            (["pdf"], "pdf"),
            (["zip", "archive"], "zip"),
            (["wav", "audio"], "wav"),
            (["mp4", "video", "avi", "mkv"], "video"),
            (["elf", "binary"], "elf"),
            (["font", "otf", "ttf", "sfnt", "woff"], "font"),
            (["xml", "html", "svg"], "xml"),
        ]
        for keywords, fmt_type in format_map:
            if any(kw in desc_lower for kw in keywords):
                fmt.format_type = fmt_type
                break

        # Detect format from fuzzer binary name (stronger signal than description)
        # The fuzzer name is extracted from repo build scripts during state_init
        fuzzer_target = str(
            state.metadata.get("fuzzer_target", "")
            if hasattr(state, "metadata") else ""
        )
        if fuzzer_target:
            for pattern, fmt_type, magic in _FUZZER_NAME_FORMAT_MAP:
                if _re.search(pattern, fuzzer_target):
                    fmt.format_type = fmt_type
                    if magic:
                        fmt.magic_bytes = magic
                    fmt.mutation_strategy = "corpus_mutate"
                    break

        # Detect input path from harness_info
        harness_lower = str(state.harness_info or "").lower()
        if "stdin" in harness_lower or "pipe" in harness_lower:
            fmt.input_path = "stdin"
        elif "-f " in harness_lower or "file" in harness_lower:
            fmt.input_path = "file_argv"
        elif "fuzzer" in harness_lower or "fuzz" in harness_lower:
            fmt.input_path = "buffer"

        # Detect magic bytes from corpus files
        magic_map = [
            (b'\x89PNG', "89 50 4E 47"),
            (b'PK\x03\x04', "50 4B 03 04"),
            (b'\x7fELF', "7F 45 4C 46"),
            (b'\xff\xd8\xff', "FF D8 FF"),
            (b'GIF8', "47 49 46 38"),
            (b'%PDF', "25 50 44 46"),
            (b'RIFF', "52 49 46 46"),
            (b'\x1f\x8b', "1F 8B"),
        ]
        workspace = str(state.workspace_root or state.repo_dir or "")
        for item in list(state.corpus_files)[:5]:
            try:
                full_path = Path(workspace) / item if workspace else Path(item)
                if full_path.is_file():
                    with open(full_path, 'rb') as f:
                        header = f.read(8)
                    for sig, hex_str in magic_map:
                        if header.startswith(sig):
                            fmt.magic_bytes = hex_str
                            if not fmt.format_type:
                                # Infer format from magic
                                for kw, ft in format_map:
                                    if ft in hex_str.lower() or any(k in hex_str.lower() for k in kw):
                                        fmt.format_type = ft
                                        break
                            break
                    if fmt.magic_bytes:
                        break
            except (OSError, ValueError):
                continue

        # Set sample paths and mutation strategy
        fmt.sample_paths = list(state.corpus_files[:5])
        fmt.mutation_strategy = state.poc_strategy

        return fmt

    @staticmethod
    def _should_use_corpus_mutation(state: CyberGymState) -> bool:
        corpus_keywords = ("corpus", "seed", "sample", "testcase", "oss-fuzz", "fuzz",
                           "test", "testdata", "input", "crash", "poc")
        # Check path keywords
        for item in state.corpus_files:
            lowered = item.lower()
            if any(token in lowered for token in corpus_keywords):
                return True
        # Check magic bytes of first few corpus files
        binary_signatures = [
            b'\x89PNG',       # PNG
            b'PK\x03\x04',   # ZIP
            b'\x7fELF',      # ELF
            b'\xff\xd8\xff',  # JPEG
            b'GIF8',          # GIF
            b'%PDF',          # PDF
            b'RIFF',          # WAV/AVI
            b'\x1f\x8b',     # GZIP
        ]
        workspace = str(state.workspace_root or state.repo_dir or "")
        for item in list(state.corpus_files)[:10]:
            try:
                full_path = Path(workspace) / item if workspace else Path(item)
                if full_path.is_file():
                    with open(full_path, 'rb') as f:
                        header = f.read(8)
                    if any(header.startswith(sig) for sig in binary_signatures):
                        return True
            except (OSError, ValueError):
                continue
        return False
