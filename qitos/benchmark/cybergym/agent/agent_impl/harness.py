"""Harness / corpus / PoC-strategy detection mixin."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..state import CyberGymState, InputFormatModel


class HarnessMixin:
    """Static methods for detecting PoC strategy, input format, and corpus usage."""

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
