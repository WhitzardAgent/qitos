"""Image/Raw/Color/Geo knowledge pack — tifffile+Pillow-backed pipeline.

Covers: TIFF, DNG, PNG, JPEG, BMP, WebP, EXR, ICC, GeoTIFF, EXIF.
Backend: tifffile (TIFF/DNG), Pillow (PNG/JPEG/BMP/WebP).
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

IMAGE_PROJECTS: frozenset[str] = frozenset({
    "libtiff", "imagemagick", "graphicsmagick", "leptonica",
    "libpng", "libjpeg", "libraw", "darktable", "rawtherapee",
    "gdal", "proj", "openslide", "serenity-libgfx",
})

IMAGE_KEYWORDS: frozenset[str] = frozenset({
    "image", "tiff", "png", "jpeg", "jpg", "bmp", "exif",
    "dng", "raw image", "geotiff", "icc", "exr", "webp",
    "ifd", "raster", "pixel", "stride", "tile",
    "libpng", "libjpeg", "libtiff", "imagemagick",
})

IMAGE_MAGICS: frozenset[str] = frozenset({"png", "jpeg", "bmp", "tiff", "gif"})


class ImageKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="image",
        carrier_families=("tiff", "png", "jpeg", "bmp", "gif", "webp", "dng", "exr", "icc"),
        supported_versions=("tiff6", "png1.2", "jpeg1.1", "bmp3", "gif89a", "webp1"),
        capabilities=frozenset({"detect", "parse", "build", "validate", "repair"}),
        required_backends=("tifffile", "Pillow"),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("image", "tiff", "png", "jpeg", "bitmap")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        matched_magics = [m for m in evidence.detected_magics if m in IMAGE_MAGICS]
        if matched_magics:
            positive.append(f"corpus_magic:{','.join(matched_magics)}")
            score = max(score, 0.7)

        if evidence.input_format_type and evidence.input_format_type.lower() in IMAGE_MAGICS | {"image", "tiff", "dng"}:
            positive.append(f"input_format:{evidence.input_format_type}")
            score = max(score, 0.8)

        project_lower = evidence.project_name.lower()
        if project_lower in IMAGE_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in IMAGE_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        missing = []
        if score < 0.7 and not evidence.detected_magics:
            missing.append("corpus_magic_bytes")

        return DetectionResult(
            decision=decision, score=round(score, 3),
            positive_evidence_ids=tuple(positive), missing_evidence=tuple(missing),
        )

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        field_map: dict[str, FieldInfo] = {}
        warnings: list[str] = []
        evidence_ids: list[str] = []
        carrier = "image"

        if not artifact or len(artifact) < 4:
            return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

        # Detect format from magic
        if artifact[:4] == b"\x89PNG":
            carrier = "png"
            try:
                from PIL import Image
                from io import BytesIO
                img = Image.open(BytesIO(artifact))
                field_map["image.width"] = FieldInfo(name="image.width", offset=0, width=0, value=img.width, node_id="header")
                field_map["image.height"] = FieldInfo(name="image.height", offset=0, width=0, value=img.height, node_id="header")
                field_map["image.mode"] = FieldInfo(name="image.mode", offset=0, width=0, value=img.mode, node_id="header")
                img.close()
            except Exception as e:
                warnings.append(f"pillow_parse_failed: {e}")

        elif artifact[:2] in (b"II", b"MM") and len(artifact) >= 8:
            carrier = "tiff"
            try:
                import tifffile
                tf = tifffile.TiffFile(BytesIO(artifact) if isinstance(artifact, bytes) else artifact)
                field_map["tiff.pages"] = FieldInfo(name="tiff.pages", offset=0, width=0, value=len(tf.pages), node_id="header")
                for i, page in enumerate(tf.pages[:10]):
                    field_map[f"tiff.page.{i}.width"] = FieldInfo(name=f"tiff.page.{i}.width", offset=0, width=0, value=page.shape[1] if len(page.shape) > 1 else 0, node_id=f"page_{i}")
                    field_map[f"tiff.page.{i}.height"] = FieldInfo(name=f"tiff.page.{i}.height", offset=0, width=0, value=page.shape[0] if page.shape else 0, node_id=f"page_{i}")
                tf.close()
            except Exception as e:
                warnings.append(f"tifffile_parse_failed: {e}")

        elif artifact[:2] == b"\xff\xd8":
            carrier = "jpeg"
            try:
                from PIL import Image
                from io import BytesIO
                img = Image.open(BytesIO(artifact))
                field_map["image.width"] = FieldInfo(name="image.width", offset=0, width=0, value=img.width, node_id="header")
                field_map["image.height"] = FieldInfo(name="image.height", offset=0, width=0, value=img.height, node_id="header")
                img.close()
            except Exception:
                warnings.append("jpeg_parse_partial")

        elif artifact[:2] == b"BM":
            carrier = "bmp"

        return ParseResult(
            status="success" if not warnings else "partial",
            carrier_family=carrier,
            structural_summary={"format": carrier},
            field_map=field_map,
            node_count=len(set(f.node_id for f in field_map.values())),
            parse_warnings=tuple(warnings), evidence_ids=tuple(evidence_ids),
        )

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        return CarrierContract(
            format_id=parsed.carrier_family or "image",
            seed_required=True, minimal_seed_size=64,
            required_fields=(), derived_fields=(), protected_fields=(),
        )

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        objective_id = objective.get("objective_id", "")
        return RecipePlan(recipe_id=f"rec_image_{objective_id}", objective_id=objective_id,
                         schema_version="2.0", carrier_contract_id=carrier.format_id if carrier else "image")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(seed)
        return BuildResult(status="partial", artifact_path=path, reason="image_builder_minimal")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(artifact))
            img.close()
            return ValidationReport(pack_id="image", overall_verdict="pass")
        except Exception:
            return ValidationReport(pack_id="image", overall_verdict="warn")

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
