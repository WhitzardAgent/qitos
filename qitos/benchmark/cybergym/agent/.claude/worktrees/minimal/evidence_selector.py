from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


FAMILY_ORDER = [
    "minimal_truncation",
    "seed_mutation",
    "header_length_mismatch",
    "offset_pointer_mismatch",
]

PARSER_HINTS = (
    "parse",
    "parser",
    "decode",
    "reader",
    "scan",
    "lexer",
    "lex",
    "token",
)

HEADER_SUFFIXES = {".h", ".hh", ".hpp", ".hxx", ".inc"}
_BUILD_NAMES = {"cmakelists.txt", "makefile", "meson.build", "cargo.toml", "go.mod", "build.sh"}
_SAMPLE_SUFFIXES = {".png", ".jpg", ".gif", ".pdf", ".xml", ".json", ".yaml", ".yml", ".bin"}
_NOISE_SEGMENTS = ("vendor/", "third_party/", "generated/", "node_modules/", ".git/")


def _uniq(items: List[str]) -> List[str]:
    seen: set[str] = set()
    ordered: List[str] = []
    for item in items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _score_path(relative: str, *, task_spec: Dict[str, Any]) -> float:
    lowered = relative.lower()
    score = 0.0
    for path in task_spec.get("source_files_mentioned", []) or []:
        if str(path).strip().lower() == lowered:
            score += 5.0
    for symbol in task_spec.get("symbols_mentioned", []) or []:
        symbol_value = str(symbol or "").strip().lower()
        if symbol_value and symbol_value in lowered:
            score += 2.5
    for hint in task_spec.get("input_vector_hints", []) or []:
        hint_value = str(hint or "").strip().lower()
        if hint_value and hint_value in lowered:
            score += 1.5
    if "fuzz" in lowered or "fuzzer" in lowered:
        score += 2.0
    if any(segment in lowered for segment in _NOISE_SEGMENTS):
        score -= 3.0
    return score


def _looks_like_sample_path(lowered: str) -> bool:
    return any(token in lowered for token in ("sample", "seed", "corpus", "test", "fuzz"))


def bootstrap_evidence_index(
    repo_root: str,
    description: str,
    task_spec: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root)
    task_spec = dict(task_spec or {})
    parser_candidates: List[str] = []
    header_candidates: List[str] = []
    build_paths: List[str] = []
    fuzz_target_paths: List[str] = []
    sample_paths: List[str] = []
    ranked_candidates: List[tuple[float, str]] = []

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = str(path.relative_to(root))
        lowered = relative.lower()
        if any(hint in lowered for hint in PARSER_HINTS):
            parser_candidates.append(relative)
        if path.suffix.lower() in HEADER_SUFFIXES:
            header_candidates.append(relative)
        if path.name.lower() in _BUILD_NAMES:
            build_paths.append(relative)
        if "fuzz" in lowered or "fuzzer" in lowered:
            fuzz_target_paths.append(relative)
        if path.suffix.lower() in _SAMPLE_SUFFIXES and _looks_like_sample_path(lowered):
            sample_paths.append(relative)
        ranked_candidates.append((_score_path(relative, task_spec=task_spec), relative))

    parser_paths = sorted(set(parser_candidates))
    seed_paths = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".omf", ".cram", ".pdf", ".rar", ".bin"}
    )
    field_paths = sorted(set(header_candidates))
    ranked_paths = [
        path
        for score, path in sorted(ranked_candidates, key=lambda item: (-item[0], item[1]))
        if score > 0
    ][:20]
    language_hints = sorted(
        {
            relative.rsplit(".", 1)[-1]
            for _score, relative in ranked_candidates
            if "." in relative
        }
    )[:8]
    repo_profile_summary = (
        f"parsers={len(parser_paths)} fuzz_targets={len(set(fuzz_target_paths))} "
        f"samples={len(set(sample_paths))} builds={len(set(build_paths))}"
    )
    return {
        "description": description,
        "parser_paths": parser_paths[:8],
        "seed_paths": seed_paths[:8],
        "field_paths": field_paths[:8],
        "build_paths": sorted(set(build_paths))[:8],
        "fuzz_target_paths": sorted(set(fuzz_target_paths))[:8],
        "sample_paths": sorted(set(sample_paths))[:8],
        "language_hints": language_hints,
        "ranked_paths": ranked_paths,
        "repo_profile_summary": repo_profile_summary,
    }


def initial_families_for_task(
    description: str,
    evidence_index: Dict[str, Any],
) -> List[Dict[str, object]]:
    lower = description.lower()
    ordered = []
    if "truncat" in lower or "1 byte" in lower:
        ordered.append("minimal_truncation")
    if evidence_index.get("seed_paths"):
        ordered.append("seed_mutation")
    if any(token in lower for token in ("length", "size", "span", "count", "typesize")):
        ordered.append("header_length_mismatch")
    if any(token in lower for token in ("offset", "pointer", "index", "start")):
        ordered.append("offset_pointer_mismatch")
    for family in FAMILY_ORDER:
        if family not in ordered:
            ordered.append(family)
    return [
        {
            "family_id": f"bootstrap-{idx}-{family}",
            "family_name": family,
            "parent_family_id": "",
            "state": "new",
            "hypothesis": description,
            "generation_axes": [],
        }
        for idx, family in enumerate(ordered[:4])
    ]


def select_family_evidence(
    family_name: str,
    evidence_index: Dict[str, Any],
) -> Dict[str, object]:
    paths: List[str] = []
    ranked_paths = list(evidence_index.get("ranked_paths", []) or [])
    if family_name == "minimal_truncation":
        paths.extend(ranked_paths[:1])
        paths.extend(evidence_index.get("seed_paths", [])[:2])
        paths.extend(evidence_index.get("parser_paths", [])[:2])
    elif family_name == "seed_mutation":
        paths.extend(ranked_paths[:1])
        paths.extend(evidence_index.get("sample_paths", [])[:2])
        paths.extend(evidence_index.get("seed_paths", [])[:3])
        paths.extend(evidence_index.get("parser_paths", [])[:1])
    elif family_name == "header_length_mismatch":
        paths.extend(ranked_paths[:1])
        paths.extend(evidence_index.get("field_paths", [])[:2])
        paths.extend(evidence_index.get("parser_paths", [])[:2])
    elif family_name == "offset_pointer_mismatch":
        paths.extend(ranked_paths[:1])
        paths.extend(evidence_index.get("parser_paths", [])[:2])
        paths.extend(evidence_index.get("field_paths", [])[:2])
    else:
        raise ValueError(f"unknown family_name: {family_name}")
    return {"family_name": family_name, "paths": _uniq(paths)[:4]}
