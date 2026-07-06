"""Archive/Compression knowledge pack — stdlib zipfile-backed pipeline.

Covers: ZIP, RAR5, Zstd, LZ4/XZ/LZMA, Blosc2, Arrow IPC, BAM/CRAM/VCF, 7z.
Backend: stdlib zipfile (ZIP), construct (others when available).
"""

from __future__ import annotations

import logging
from typing import Any

from ...evidence import EvidenceView
from ...models import (
    BuildResult, CarrierContract, DetectionResult, ExpectedEffect,
    PackDescriptor, ParseResult, RecipePlan, RepairAction, ValidationReport,
    FieldInfo,
)

logger = logging.getLogger(__name__)

ARCHIVE_PROJECTS: frozenset[str] = frozenset({
    "libarchive", "c-blosc2", "htslib", "miniz", "zstd",
    "lz4", "xz", "7zip", "arrow",
})

ARCHIVE_KEYWORDS: frozenset[str] = frozenset({
    "archive", "zip", "rar", "zstd", "lz4", "lzma", "xz",
    "blosc", "arrow", "bam", "cram", "vcf", "7z",
    "compress", "decompress", "inflate", "deflate",
})


class ArchiveKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="archive",
        carrier_families=("zip", "rar", "zstd", "lz4", "lzma", "xz", "blosc2", "arrow", "bam", "7z"),
        supported_versions=("zip6.3", "rar5", "zstd-1.5"),
        capabilities=frozenset({"detect", "parse", "build", "validate"}),
        required_backends=(),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        if "zip" in evidence.detected_magics:
            positive.append("corpus_magic:zip")
            score = max(score, 0.7)

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("archive", "zip", "compress")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        project_lower = evidence.project_name.lower()
        if project_lower in ARCHIVE_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in ARCHIVE_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        return DetectionResult(decision=decision, score=round(score, 3), positive_evidence_ids=tuple(positive))

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        field_map: dict[str, FieldInfo] = {}
        warnings: list[str] = []
        carrier = "archive"

        if not artifact or len(artifact) < 4:
            return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

        # ZIP
        if artifact[:4] == b"PK\x03\x04":
            carrier = "zip"
            try:
                import zipfile
                from io import BytesIO
                zf = zipfile.ZipFile(BytesIO(artifact))
                field_map["zip.file_count"] = FieldInfo(name="zip.file_count", offset=0, width=0,
                                                         value=len(zf.namelist()), node_id="central")
                for i, info in enumerate(zf.infolist()[:10]):
                    field_map[f"zip.entry.{i}.name"] = FieldInfo(name=f"zip.entry.{i}.name", offset=0, width=0,
                                                                   value=info.filename, node_id=f"entry_{i}")
                    field_map[f"zip.entry.{i}.size"] = FieldInfo(name=f"zip.entry.{i}.size", offset=0, width=0,
                                                                   value=info.file_size, node_id=f"entry_{i}")
                zf.close()
            except Exception as e:
                warnings.append(f"zip_parse_partial: {e}")

        # Zstd
        elif artifact[:4] == b"\x28\xb5\x2f\xfd":
            carrier = "zstd"

        return ParseResult(
            status="success" if not warnings else "partial",
            carrier_family=carrier,
            structural_summary={"format": carrier},
            field_map=field_map,
            node_count=len(set(f.node_id for f in field_map.values())),
            parse_warnings=tuple(warnings),
        )

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        return CarrierContract(format_id=parsed.carrier_family or "archive",
                               seed_required=True, minimal_seed_size=64)

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        return RecipePlan(recipe_id=f"rec_archive_{objective.get('objective_id', '')}",
                         objective_id=objective.get("objective_id", ""), schema_version="2.0")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(seed)
        return BuildResult(status="partial", artifact_path=path, reason="archive_builder_minimal")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        if contract.format_id == "zip":
            try:
                import zipfile
                from io import BytesIO
                zf = zipfile.ZipFile(BytesIO(artifact))
                zf.close()
                return ValidationReport(pack_id="archive", overall_verdict="pass")
            except Exception:
                return ValidationReport(pack_id="archive", overall_verdict="warn")
        return ValidationReport(pack_id="archive", overall_verdict="unknown")

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
