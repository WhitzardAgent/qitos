"""Harness / corpus / PoC-strategy detection mixin."""

from __future__ import annotations

import os
import hashlib
import re as _re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

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
    def _discover_fuzzer_targets(repo_dir: str) -> List[Dict[str, Any]]:
        """Return every deterministic fuzzer target found in build evidence."""
        if not repo_dir or not os.path.isdir(repo_dir):
            return []
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

        search_files = sorted(set(search_files), key=lambda item: str(item.relative_to(repo_path)))
        found: Dict[str, Dict[str, Any]] = {}
        for fpath in search_files[:200]:
            try:
                content = fpath.read_text(errors="replace")[:65536]
            except (OSError, ValueError):
                continue
            for m in _re.finditer(
                r'(\w{3,}_fuzzer(?:_\w+)*)(?!\.(?:c|cc|cpp|cxx|m|mm|rs)\b)\b',
                content,
                _re.IGNORECASE,
            ):
                name = m.group(1)
                record = found.setdefault(name, {
                    "name": name,
                    "sources": [],
                    "source_paths": [],
                })
                rel = str(fpath.relative_to(repo_path))
                if rel not in record["sources"]:
                    record["sources"].append(rel)
                line_start = content.rfind("\n", 0, m.start()) + 1
                line_end = content.find("\n", m.end())
                context = content[line_start:line_end if line_end >= 0 else len(content)]
                # CMake targets are commonly split over several lines.  Limit
                # expansion to the enclosing parenthesized command so adjacent
                # targets cannot donate their source files.
                open_pos = max(
                    content.rfind("add_executable(", 0, m.start()),
                    content.rfind("add_fuzzer(", 0, m.start()),
                )
                previous_close = content.rfind(")", 0, m.start())
                if open_pos > previous_close:
                    close_pos = content.find(")", m.end())
                    if close_pos >= 0:
                        context = content[open_pos:close_pos + 1]
                for source_match in _re.finditer(
                    r'([\w./+-]+\.(?:c|cc|cpp|cxx|m|mm|rs))\b', context,
                    _re.IGNORECASE,
                ):
                    source_path = source_match.group(1).lstrip("./")
                    if source_path not in record["source_paths"]:
                        record["source_paths"].append(source_path)
        return [found[name] for name in sorted(found, key=str.lower)]

    @staticmethod
    def _discover_fuzzer_target(repo_dir: str) -> str:
        """Compatibility view: return a target only when discovery is unambiguous."""
        targets = HarnessMixin._discover_fuzzer_targets(repo_dir)
        return str(targets[0]["name"]) if len(targets) == 1 else ""

    @staticmethod
    def _extract_submit_harness_targets(content: str) -> List[str]:
        """Conservatively extract executable/declared fuzzer targets from submit.sh."""
        if not content:
            return []
        variables: Dict[str, str] = {}
        targets: List[str] = []

        def add(value: str) -> None:
            name = Path(value.strip("'\"")).name
            if _re.fullmatch(r'[A-Za-z0-9_.+-]{3,}_fuzzer(?:_[A-Za-z0-9_.+-]+)*', name) and name not in targets:
                targets.append(name)

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assignment = _re.match(
                r'(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*["\']?([^"\'\s;]+)',
                stripped,
            )
            if assignment:
                variables[assignment.group(1)] = assignment.group(2)
                if "fuzz" in assignment.group(1).lower() or "target" in assignment.group(1).lower():
                    add(assignment.group(2))
            if _re.match(r'^(?:curl|wget)\b', stripped):
                continue
            field_match = _re.search(
                r'\b(?:fuzzer_target|fuzz_target|target_binary)\b\s*[:=]\s*["\']?([^"\'\s,;]+)',
                stripped,
                _re.IGNORECASE,
            )
            if field_match:
                add(field_match.group(1))
            try:
                tokens = shlex.split(stripped, comments=True)
            except ValueError:
                tokens = stripped.split()
            for token in tokens:
                if token.startswith("$"):
                    token = variables.get(token.lstrip("${}"), "")
                add(token)
        return targets

    @staticmethod
    def _harness_name_family(value: str) -> str:
        tokens = [
            token for token in _re.split(r'[^a-z0-9]+', Path(value).stem.lower())
            if token and token not in {
                "fuzz", "fuzzer", "harness", "jpg", "jpeg", "png", "gif",
                "bmp", "pdf", "wav", "audio", "sfnt", "font", "ttf", "otf",
                "zip", "archive", "pcap", "heic", "heif", "webp", "avi",
                "mkv", "mp4", "video", "xml", "elf", "structure", "aware",
            }
        ]
        return "_".join(tokens)

    @staticmethod
    def _build_harness_candidates(
        index: Dict[str, Any],
        target_records: List[Dict[str, Any]],
        submit_targets: List[str],
    ) -> List[Any]:
        from ..state import HarnessCandidate

        candidates: List[HarnessCandidate] = []
        entries = list(index.get("harness_entries") or [])
        for entry in entries:
            path = str(entry.get("path") or "")
            entry_name = str(entry.get("entry_function") or "")
            line = int(entry.get("line") or 0)
            binaries: List[str] = []
            evidence = [f"source entry {entry_name} at {path}:{line}"]
            path_family = HarnessMixin._harness_name_family(path)
            for record in target_records:
                name = str(record.get("name") or "")
                source_paths = [str(item).lstrip("./") for item in record.get("source_paths") or []]
                source_match = any(path.endswith(item) or item.endswith(path) for item in source_paths)
                family_match = bool(path_family) and path_family == HarnessMixin._harness_name_family(name)
                if source_match or family_match:
                    binaries.append(name)
                    reason = "build source mapping" if source_match else "normalized target/source name"
                    evidence.append(f"{reason}: {name}")
            candidates.append(HarnessCandidate(
                candidate_id=str(entry.get("node_id") or f"{path}::{entry_name}::{line}"),
                binary_names=sorted(set(binaries), key=str.lower),
                source_path=path,
                entry_function=entry_name,
                line=line,
                evidence=evidence,
                direct_calls=list(entry.get("calls") or []),
            ))

        all_targets = sorted({
            str(record.get("name") or "") for record in target_records
            if str(record.get("name") or "")
        } | set(submit_targets), key=str.lower)
        if len(candidates) == 1:
            candidates[0].binary_names = sorted(
                set(candidates[0].binary_names) | set(all_targets), key=str.lower,
            )
            if all_targets:
                candidates[0].evidence.append("only indexed harness source for discovered targets")
        return candidates

    @staticmethod
    def _vulnerability_symbols(state: CyberGymState, index: Dict[str, Any]) -> List[str]:
        definitions = {
            str(fn.get("name") or "")
            for info in (index.get("files") or {}).values()
            for fn in info.get("functions", [])
        }
        ordered: List[str] = []
        for name in list(state.vulnerable_functions or []):
            if name in definitions and name not in ordered:
                ordered.append(name)
        evidence_texts = [state.patch_diff or "", state.error_txt or "", state.vulnerability_description or ""]
        for text in evidence_texts:
            for name in sorted(definitions):
                if name and name != "LLVMFuzzerTestOneInput" and _re.search(rf'\b{_re.escape(name)}\b', text):
                    if name not in ordered:
                        ordered.append(name)
        for name in list(state.symbols_mentioned or []):
            if name in definitions and name not in ordered:
                ordered.append(name)
        return ordered[:20]

    @staticmethod
    def _resolve_harness_candidates(state: CyberGymState, index: Dict[str, Any]) -> None:
        from ..state import HarnessResolution
        from .repo_index import trace_harness_reachability

        candidates = list(state.harness_candidates or [])
        for candidate in candidates:
            candidate.status = "discovered"
            candidate.reachable_symbols = []
        resolution = HarnessResolution()
        if not candidates:
            resolution.status = "ambiguous" if state.submit_harness_targets else "unresolved"
            resolution.next_action = "Locate the source harness entry and map it to the submitted target."
            state.harness_resolution = resolution
            state.harness_entry_confirmed = False
            state.metadata["harness_entry_confirmed"] = False
            return

        submit_matches = [
            candidate for candidate in candidates
            if set(candidate.binary_names) & set(state.submit_harness_targets)
        ]
        submit_choice = submit_matches[0] if len(submit_matches) == 1 else None
        if submit_choice:
            resolution.selected_binary = next(
                target for target in state.submit_harness_targets
                if target in submit_choice.binary_names
            )
            resolution.reasons.append(
                f"submit target {resolution.selected_binary} maps uniquely to {submit_choice.source_path}"
            )
        elif len(submit_matches) > 1:
            resolution.conflicts.append("submit target maps to multiple harness sources")

        vulnerable_symbols = HarnessMixin._vulnerability_symbols(state, index)
        verified: List[Any] = []
        unknown: List[Any] = []
        for candidate in candidates:
            reach = trace_harness_reachability(index, candidate.candidate_id, vulnerable_symbols)
            candidate.reachable_symbols = list(reach.get("symbols") or [])
            if reach.get("status") == "verified":
                verified.append(candidate)
                fact = "verified path to " + ", ".join(candidate.reachable_symbols)
                if fact not in candidate.evidence:
                    candidate.evidence.append(fact)
            elif reach.get("status") == "unknown":
                unknown.append(candidate)
                fact = "indirect or ambiguous call path remains unresolved"
                if fact not in candidate.evidence:
                    candidate.evidence.append(fact)

        reach_choice = verified[0] if len(verified) == 1 else None
        submit_is_verified = bool(submit_choice and any(
            item.candidate_id == submit_choice.candidate_id for item in verified
        ))
        if submit_choice and verified and not submit_is_verified:
            resolution.status = "conflicted"
            resolution.conflicts.append("submit target and vulnerability reachability select different harnesses")
            submit_choice.status = "conflicted"
            for item in verified:
                item.status = "conflicted"
        elif submit_choice and submit_is_verified:
            resolution.selected_candidate_id = submit_choice.candidate_id
            resolution.status = "reachability_verified"
            resolution.reasons.append(
                "selected submit harness has a source-backed path to "
                + ", ".join(submit_choice.reachable_symbols)
            )
            submit_choice.status = "reachability_verified"
            if len(verified) > 1:
                resolution.reasons.append("submit target disambiguates other reachable harnesses")
        elif not submit_choice and reach_choice:
            resolution.selected_candidate_id = reach_choice.candidate_id
            resolution.status = "reachability_verified"
            resolution.reasons.append(
                "unique source-backed path reaches " + ", ".join(reach_choice.reachable_symbols)
            )
            reach_choice.status = "reachability_verified"
        elif submit_choice:
            resolution.selected_candidate_id = submit_choice.candidate_id
            resolution.status = "selected"
            submit_choice.status = "selected"
            resolution.next_action = (
                "Resolve the selected harness's indirect or ambiguous calls to the vulnerability function."
                if any(item.candidate_id == submit_choice.candidate_id for item in unknown)
                else "Trace the selected harness to a vulnerability function."
            )
        else:
            resolution.status = "ambiguous"
            if len(verified) > 1:
                resolution.conflicts.append("multiple harnesses reach vulnerability symbols")
            resolution.next_action = (
                "Resolve indirect calls or identify which harness reaches the vulnerability function."
                if unknown else "Identify a unique submit target or source-backed path to the vulnerability."
            )

        if resolution.selected_candidate_id and not resolution.selected_binary:
            chosen = next(
                (item for item in candidates
                 if item.candidate_id == resolution.selected_candidate_id),
                None,
            )
            if chosen and len(chosen.binary_names) == 1:
                resolution.selected_binary = chosen.binary_names[0]

        state.harness_resolution = resolution
        state.harness_entry_confirmed = resolution.status == "reachability_verified"
        state.metadata["harness_entry_confirmed"] = state.harness_entry_confirmed

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
        from ..state import HarnessConsumptionEvidence, HarnessConsumptionModel, InputFormatModel
        from .harness_analyzer import analyze_harness_consumption

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
                fmt.field_provenance["format_type"] = "vulnerability description"
                fmt.field_confidence["format_type"] = 0.45
                break

        # Detect format from fuzzer binary name (stronger signal than description)
        # The fuzzer name is extracted from repo build scripts during state_init
        resolution = getattr(state, "harness_resolution", None)
        selected = None
        if getattr(resolution, "selected_candidate_id", ""):
            selected = next(
                (item for item in state.harness_candidates
                 if item.candidate_id == resolution.selected_candidate_id),
                None,
            )
        fuzzer_target = ""
        if (
            getattr(resolution, "status", "") in {"selected", "reachability_verified"}
            and getattr(resolution, "selected_candidate_id", "")
        ):
            fuzzer_target = str(getattr(resolution, "selected_binary", "") or "")
        if fuzzer_target:
            for pattern, fmt_type, magic in _FUZZER_NAME_FORMAT_MAP:
                if _re.search(pattern, fuzzer_target):
                    fmt.format_type = fmt_type
                    fmt.field_provenance["format_type"] = f"selected harness target {fuzzer_target}"
                    fmt.field_confidence["format_type"] = 0.9
                    if magic:
                        fmt.magic_bytes = magic
                        fmt.field_provenance["magic_bytes"] = f"selected harness target {fuzzer_target}"
                        fmt.field_confidence["magic_bytes"] = 0.8
                    fmt.mutation_strategy = "corpus_mutate"
                    break

        # Infer delivery only from the selected entry.  Raw upload scripts
        # often contain words such as "file" and "fuzzer" and are not
        # execution evidence by themselves.
        harness_lower = str(state.harness_info or "").lower()
        if selected and selected.entry_function == "LLVMFuzzerTestOneInput":
            fmt.input_path = "buffer"
            fmt.field_provenance["input_path"] = selected.source_path
            fmt.field_confidence["input_path"] = 0.9
        elif selected and selected.entry_function == "main":
            if "stdin" in harness_lower or "pipe" in harness_lower:
                fmt.input_path = "stdin"
            elif "$1" in harness_lower or "${1}" in harness_lower or " -f " in harness_lower:
                fmt.input_path = "file_argv"
        if fmt.input_path:
            fmt.field_provenance.setdefault("input_path", "selected main invocation in submit.sh")
            fmt.field_confidence.setdefault("input_path", 0.7)

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
                            fmt.field_provenance["magic_bytes"] = f"corpus sample {item}"
                            fmt.field_confidence["magic_bytes"] = 0.95
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
        if selected:
            fmt.entry_point = selected.entry_function
            fmt.field_provenance["entry_point"] = selected.source_path
            fmt.field_confidence["entry_point"] = (
                1.0 if resolution.status == "reachability_verified" else 0.75
            )

            repo_root = Path(str(state.repo_dir or state.workspace_root or ""))
            source_file = repo_root / selected.source_path
            cache_key = ""
            try:
                digest = hashlib.blake2s(source_file.read_bytes(), digest_size=8).hexdigest()
                cache_key = f"{selected.candidate_id}|{selected.source_path}|{selected.entry_function}|{digest}"
            except OSError:
                cache_key = f"{selected.candidate_id}|{selected.source_path}|{selected.entry_function}|missing"

            prior_key = str(getattr(state, "metadata", {}).get("_harness_consumption_cache_key", "") or "")
            prior_model = getattr(getattr(state, "input_format", None), "consumption", None)
            if cache_key and cache_key == prior_key and prior_model is not None:
                fmt.consumption = prior_model
            else:
                try:
                    fmt.consumption = analyze_harness_consumption(
                        repo_root,
                        selected.source_path,
                        selected.entry_function,
                    )
                except Exception as exc:
                    fmt.consumption = HarnessConsumptionModel(
                        status="partial",
                        evidence=[
                            HarnessConsumptionEvidence(
                                "gap",
                                f"harness analyzer failed: {type(exc).__name__}: {str(exc)[:120]}",
                                selected.source_path,
                                selected.line,
                                .0,
                            )
                        ],
                    )
                if cache_key:
                    state.metadata["_harness_consumption_cache_key"] = cache_key
                    revisions = dict(state.metadata.get("_vnext_context_revisions") or {})
                    revisions["harness"] = int(revisions.get("harness", 0) or 0) + 1
                    state.metadata["_vnext_context_revisions"] = revisions

            if fmt.consumption.first_hops:
                selected.direct_calls = list(fmt.consumption.first_hops)
            if fmt.consumption.magic_bytes:
                fmt.magic_bytes = fmt.consumption.magic_bytes
                fmt.field_provenance["magic_bytes"] = "selected harness source"
                fmt.field_confidence["magic_bytes"] = 0.98
            if "temp_file" in (fmt.consumption.patterns or []):
                fmt.input_path = "temp_file"
                fmt.field_provenance["input_path"] = "selected harness source"
                fmt.field_confidence["input_path"] = 0.95
            elif fmt.consumption.pattern in {"direct_data_size", "struct_split", "magic_header", "multi_api"}:
                fmt.input_path = "buffer"
                fmt.field_provenance["input_path"] = "selected harness source"
                fmt.field_confidence["input_path"] = max(
                    fmt.field_confidence.get("input_path", 0.0), 0.94,
                )
        fmt.confirmed = getattr(resolution, "status", "") == "reachability_verified"

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
