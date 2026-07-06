"""ELF/Executable/Debug knowledge pack — lief/pyelftools-backed pipeline.

Covers: ELF, PE, Mach-O, DWARF, BFD, COFF, archive (.a) formats.
Backend: lief (preferred) or pyelftools (ELF-only fallback).
"""

from __future__ import annotations

import logging
from typing import Any

from ...evidence import EvidenceView
from ...models import (
    BuildResult, CarrierContract, DetectionResult, ExpectedEffect,
    Invariant, PackDescriptor, ParseResult, RecipeOperation, RecipePlan,
    RepairAction, ValidationReport, FieldInfo,
)

logger = logging.getLogger(__name__)

ELF_PROJECTS: frozenset[str] = frozenset({
    "binutils", "libdwarf", "upx", "elfutils", "libbpf",
    "radare2", "capstone", "readelf", "objdump", "dwarfdump",
    "libbfd", "gdb", "lldb", "patchelf", "prelink",
})

ELF_KEYWORDS: frozenset[str] = frozenset({
    "elf", "dwarf", "ecoff", "bfd", "coff", "pe",
    "section", "segment", "symbol", "relocation",
    "executable", "object file", "shared library",
    "debug info", "eh_frame", "dwarf", "mach-o",
})


class ElfKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="elf",
        carrier_families=("elf", "pe", "mach-o", "coff", "archive"),
        supported_versions=("elf32", "elf64", "pe32", "pe64", "mach-o"),
        capabilities=frozenset({"detect", "parse", "build", "validate", "repair"}),
        required_backends=("lief",),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("elf", "pe", "executable", "binary")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        if "elf" in evidence.detected_magics:
            positive.append("corpus_magic:elf")
            score = max(score, 0.7)

        if evidence.input_format_type and evidence.input_format_type.lower() in ("elf", "pe", "executable", "binary"):
            positive.append(f"input_format:{evidence.input_format_type}")
            score = max(score, 0.8)

        project_lower = evidence.project_name.lower()
        if project_lower in ELF_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in ELF_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        missing = []
        if score < 0.7 and not evidence.detected_magics:
            missing.append("corpus_magic_bytes")

        return DetectionResult(
            decision=decision, score=round(score, 3),
            positive_evidence_ids=tuple(positive),
            missing_evidence=tuple(missing),
        )

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        try:
            import lief
        except ImportError:
            return ParseResult(status="backend_unavailable")

        if not artifact or len(artifact) < 16:
            return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

        field_map: dict[str, FieldInfo] = {}
        warnings: list[str] = []
        evidence_ids: list[str] = []

        try:
            binary = lief.parse(artifact)
            if binary is None:
                return ParseResult(status="failed", carrier_family="elf",
                                   parse_warnings=("lief_cannot_parse",))

            carrier = "elf"
            if binary.format == lief.Binary.FORMATS.ELF:
                carrier = "elf"
            elif binary.format == lief.Binary.FORMATS.PE:
                carrier = "pe"
            elif binary.format == lief.Binary.FORMATS.MACHO:
                carrier = "mach-o"

            # Header fields
            if hasattr(binary, "header"):
                header = binary.header
                if hasattr(header, "entrypoint"):
                    field_map["elf.header.entrypoint"] = FieldInfo(
                        name="elf.header.entrypoint", offset=0, width=0,
                        value=header.entrypoint, node_id="header")

            # Sections
            for i, section in enumerate(binary.sections[:50]):
                name = section.name if hasattr(section, "name") else f"section_{i}"
                sec_name = f"elf.section.{name}"
                field_map[f"{sec_name}.offset"] = FieldInfo(
                    name=f"{sec_name}.offset", offset=0, width=0,
                    value=section.offset if hasattr(section, "offset") else 0,
                    node_id=f"section_{name}", derived=True)
                field_map[f"{sec_name}.size"] = FieldInfo(
                    name=f"{sec_name}.size", offset=0, width=0,
                    value=section.size if hasattr(section, "size") else 0,
                    node_id=f"section_{name}", derived=True)
                evidence_ids.append(sec_name)

            # Symbols (limited)
            if hasattr(binary, "symbols"):
                sym_count = len(list(binary.symbols))
                field_map["elf.symbols.count"] = FieldInfo(
                    name="elf.symbols.count", offset=0, width=0,
                    value=sym_count, node_id="symbols")

        except Exception as e:
            warnings.append(f"lief_parse_partial: {e}")

        return ParseResult(
            status="success" if not warnings else "partial",
            carrier_family=carrier,
            structural_summary={"format": carrier},
            field_map=field_map,
            node_count=len(set(f.node_id for f in field_map.values())),
            parse_warnings=tuple(warnings),
            evidence_ids=tuple(evidence_ids),
        )

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        derived = [k for k, f in parsed.field_map.items() if f.derived]
        protected = [k for k, f in parsed.field_map.items() if f.protected]
        return CarrierContract(
            format_id=parsed.carrier_family or "elf",
            seed_required=True, minimal_seed_size=128,
            required_fields=(), derived_fields=tuple(derived),
            protected_fields=tuple(protected),
        )

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        objective_id = objective.get("objective_id", "")
        return RecipePlan(recipe_id=f"rec_elf_{objective_id}", objective_id=objective_id,
                         schema_version="2.0", carrier_contract_id=carrier.format_id if carrier else "elf")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        try:
            import lief
        except ImportError:
            return BuildResult(status="backend_unavailable", reason="lief not installed")
        # Minimal: write seed bytes as-is (lief builder is complex)
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(seed)
        return BuildResult(status="partial", artifact_path=path, reason="elf_builder_minimal")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        try:
            import lief
        except ImportError:
            return ValidationReport(pack_id="elf", overall_verdict="unknown")
        # Parse check only
        binary = lief.parse(artifact)
        if binary is not None:
            return ValidationReport(pack_id="elf", overall_verdict="pass")
        return ValidationReport(pack_id="elf", overall_verdict="fail", blocks_submit=True)

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
