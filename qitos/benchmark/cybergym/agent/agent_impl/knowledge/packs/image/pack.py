"""Image/Raw/Color/Geo knowledge pack — tifffile+Pillow-backed pipeline.

Covers: TIFF, DNG, PNG, JPEG, BMP, WebP, EXR, ICC, GeoTIFF, EXIF.
Backend: tifffile (TIFF/DNG), Pillow (PNG/JPEG/BMP/WebP).
"""

from __future__ import annotations

import logging
import os
import struct
import tempfile
from io import BytesIO
from typing import Any

from ...evidence import EvidenceView
from ...models import (
    BuildResult, CarrierContract, DetectionResult, ExpectedEffect,
    Invariant, PackDescriptor, ParseResult, RecipeOperation, RecipePlan,
    RepairAction, ValidationFinding, ValidationReport, FieldInfo,
)
from ..raw_marker import validate_raw_marker_intent
from .mutator import apply_image_operations

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
        objective_kind = str(objective.get("kind") or "").lower()
        format_id = carrier.format_id if carrier else str((provenance or {}).get("format") or "image")
        operations: list[RecipeOperation] = []
        invariants: list[Invariant] = []
        effects: list[ExpectedEffect] = []

        if objective_kind in {"dimension_overflow", "stride_overflow", "integer_overflow", "numeric"}:
            tag = int(objective.get("tag") or 256)
            value = _coerce_int(objective.get("overflow_value"), 0xFFFF)
            operations.append(RecipeOperation(
                op_id=f"op_tiff_tag_{tag}_overflow",
                kind="mutate_tiff_tag",
                target_node_id=f"tiff.tag.{tag}",
                invalidated_derivations=("tiff.strip_byte_counts", "tiff.decoder_allocation"),
                rollback_hint=f"restore TIFF tag {tag} to the seed value",
                evidence_id=f"objective:{objective_id}",
                ast_transform={"tag": tag, "value": value},
            ))
            invariants.append(Invariant(
                invariant_id="inv_tiff_ifd_reachable",
                kind="offset",
                expression="first IFD offset must remain inside the TIFF payload",
                protected=True,
            ))
            effects.append(ExpectedEffect(
                effect_id="eff_tiff_dimension_overflow",
                target_expression=f"tiff.tag.{tag}",
                desired_relation=f"value == {value}",
                expected_runtime_probe="image_decoder_allocation_or_stride_overflow",
            ))

        elif objective_kind in {"raw_marker", "metadata_corruption", "trigger_marker"}:
            marker = str(objective.get("marker") or "%TIFF_TRIGGER%")
            operations.append(RecipeOperation(
                op_id="op_append_image_raw_marker",
                kind="append_raw_marker",
                target_node_id="image.trailing_metadata",
                rollback_hint="remove appended raw marker",
                evidence_id=f"objective:{objective_id}",
                ast_transform={"marker": marker},
            ))
            effects.append(ExpectedEffect(
                effect_id="eff_image_raw_marker",
                target_expression=f"tiff.raw_contains:{marker}",
                desired_relation="mutation_preserved",
                expected_runtime_probe="marker_reaches_image_metadata_path",
            ))

        if objective_kind in {"exif_wrapper", "exif_metadata"} or str(format_id).lower() == "exif":
            operations.append(RecipeOperation(
                op_id="op_wrap_exif_app1",
                kind="wrap_exif_app1",
                target_node_id="jpeg.app1",
                rollback_hint="use the original TIFF seed without JPEG APP1 wrapper",
                evidence_id=f"objective:{objective_id}",
            ))
            invariants.append(Invariant(
                invariant_id="inv_exif_app1",
                kind="offset",
                expression="JPEG APP1 payload must start with Exif\\0\\0 and contain a valid TIFF header",
                protected=True,
            ))

        carrier_info = {
            "format": str(format_id or "image"),
            "seed_path": (provenance or {}).get("seed_path", "") if provenance else "",
            "seed_policy": (provenance or {}).get("seed_policy", "task_local_or_minimal_tiff") if provenance else "task_local_or_minimal_tiff",
        }
        return RecipePlan(
            recipe_id=f"rec_image_{objective_id}",
            objective_id=objective_id,
            schema_version="2.0",
            carrier_contract_id=str(format_id or "image"),
            seed_id=carrier_info["seed_path"],
            operations=tuple(operations),
            invariants=tuple(invariants),
            expected_effects=tuple(effects),
            carrier=carrier_info,
            knowledge_revision=self.descriptor.knowledge_revision,
        )

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        if not plan.operations:
            fd, path = tempfile.mkstemp(suffix=".bin")
            os.close(fd)
            with open(path, "wb") as f:
                f.write(seed)
            return BuildResult(status="partial", artifact_path=path, reason="image_builder_no_operations")

        output, applied, blocked = apply_image_operations(seed, plan)
        suffix = ".jpg" if output.startswith(b"\xff\xd8") else ".tiff"
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(path, "wb") as f:
            f.write(output)
        if applied:
            status = "success" if not blocked else "partial"
            return BuildResult(
                status=status,
                artifact_path=path,
                applied_operations=applied,
                blocked_operations=blocked,
                mutation_intent_preserved=True,
                reason="" if status == "success" else "some_image_operations_blocked",
            )
        return BuildResult(
            status="partial",
            artifact_path=path,
            blocked_operations=blocked,
            mutation_intent_preserved=False,
            reason="no_image_operations_applied",
        )

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        candidate_path = ""
        if isinstance(artifact, str) and len(artifact) < 512 and "/" in artifact:
            candidate_path = artifact
            try:
                data = open(candidate_path, "rb").read()
            except OSError:
                return ValidationReport(
                    candidate_path=candidate_path,
                    pack_id="image",
                    findings=(ValidationFinding(
                        validator_id="image.byte_safety.readable",
                        layer="byte_safety",
                        verdict="fail",
                        strength="authoritative",
                        evidence_ref="cannot_read_candidate",
                        repair_actions=("regenerate_candidate",),
                    ),),
                    overall_verdict="fail",
                    blocks_submit=True,
                )
        else:
            data = artifact if isinstance(artifact, bytes) else str(artifact).encode()

        findings = _validate_image_bytes(data, contract)
        if mutation_intent:
            findings.extend(validate_raw_marker_intent(
                data,
                mutation_intent,
                validator_id="image.mutation.raw_marker",
                repair_actions=(
                    "reapply_mutation_after_ifd_repair",
                    "use_raw_byte_mutation",
                    "check_recipe_target_expression",
                ),
            ))

        return _build_validation_report(candidate_path, findings)

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        repairs: list[RepairAction] = []
        for finding in report.findings:
            if finding.verdict == "pass":
                continue
            vid = finding.validator_id
            if vid == "image.tiff.ifd_offset":
                repairs.append(RepairAction(
                    action_id="repair_tiff_ifd_offset",
                    kind="fix_field",
                    target_node_id="tiff.header",
                    description="Repair the first TIFF IFD offset so it points inside the file",
                    evidence_ref=finding.evidence_ref,
                ))
            elif vid in {"image.tiff.header", "image.tiff.byte_order", "image.tiff.magic"}:
                repairs.append(RepairAction(
                    action_id="repair_tiff_header",
                    kind="fix_field",
                    target_node_id="tiff.header",
                    description="Restore TIFF byte order, magic, and minimum header fields",
                    evidence_ref=finding.evidence_ref,
                ))
            elif vid in {"image.exif.app1", "image.exif.tiff_offset", "image.exif.tiff_header"}:
                repairs.append(RepairAction(
                    action_id="repair_exif_app1_wrapper",
                    kind="restore",
                    target_node_id="jpeg.app1",
                    description="Restore JPEG APP1 Exif wrapper and inner TIFF header/offsets",
                    evidence_ref=finding.evidence_ref,
                ))
            elif vid == "image.mutation.raw_marker":
                repairs.append(RepairAction(
                    action_id="repair_reapply_image_raw_marker",
                    kind="fix_field",
                    target_node_id=None,
                    description="Reapply the declared raw trigger after image metadata repair",
                    evidence_ref=finding.evidence_ref,
                ))
        return tuple(dict.fromkeys(repairs))


def _validate_image_bytes(data: bytes, contract: CarrierContract) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    if not data:
        return [ValidationFinding(
            validator_id="image.byte_safety.empty",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref="file_is_empty",
            repair_actions=("regenerate_candidate",),
        )]

    findings.append(ValidationFinding(
        validator_id="image.byte_safety.size",
        layer="byte_safety",
        verdict="pass",
        strength="authoritative",
        evidence_ref=f"file_size_{len(data)}",
    ))

    fmt = _detect_image_format(data, contract)
    if fmt == "exif":
        findings.extend(_validate_exif_app1(data))
    elif fmt == "tiff":
        findings.extend(_validate_tiff_bytes(data))
    elif fmt in {"png", "jpeg", "bmp"}:
        findings.append(ValidationFinding(
            validator_id=f"image.{fmt}.magic",
            layer="structural_parse",
            verdict="pass",
            strength="strong",
            evidence_ref=f"{fmt}_magic_present",
        ))
        if fmt == "jpeg" and b"Exif\x00\x00" in data:
            findings.extend(_validate_exif_app1(data))
    else:
        findings.append(ValidationFinding(
            validator_id="image.magic",
            layer="byte_safety",
            verdict="warn",
            strength="supporting",
            evidence_ref="unknown_or_unsupported_image_magic",
            repair_actions=("use_task_local_image_seed",),
        ))

    findings.append(ValidationFinding(
        validator_id="image.harness",
        layer="harness_acceptance",
        verdict="unknown",
        strength="heuristic",
        evidence_ref="harness_not_available",
    ))
    return findings


def _coerce_int(value: Any, default: int) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 0)
        except ValueError:
            return default
    return default


def _detect_image_format(data: bytes, contract: CarrierContract) -> str:
    fmt = (contract.format_id or "").lower()
    if fmt in {"tiff", "png", "jpeg", "jpg", "bmp", "exif", "dng"}:
        if fmt in {"exif", "dng"}:
            return "exif" if fmt == "exif" else "tiff"
        return "jpeg" if fmt == "jpg" else fmt
    if data.startswith((b"II\x2a\x00", b"MM\x00\x2a")):
        return "tiff"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8"):
        return "jpeg"
    if data.startswith(b"BM"):
        return "bmp"
    return "image"


def _validate_exif_app1(data: bytes) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    if not data.startswith(b"\xff\xd8"):
        return [ValidationFinding(
            validator_id="image.exif.app1",
            layer="byte_safety",
            verdict="fail",
            strength="strong",
            evidence_ref="missing_jpeg_soi_for_exif_app1",
            repair_actions=("wrap_tiff_as_exif_app1",),
        )]

    app1_payload = _find_exif_app1_payload(data)
    if app1_payload is None:
        return [ValidationFinding(
            validator_id="image.exif.app1",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref="missing_exif_app1_segment",
            repair_actions=("wrap_tiff_as_exif_app1",),
        )]

    findings.append(ValidationFinding(
        validator_id="image.exif.app1",
        layer="invariant_check",
        verdict="pass",
        strength="strong",
        evidence_ref=f"exif_app1_payload_size_{len(app1_payload)}",
    ))

    if not app1_payload.startswith(b"Exif\x00\x00"):
        findings.append(ValidationFinding(
            validator_id="image.exif.app1",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref="app1_payload_missing_exif_prefix",
            repair_actions=("restore_exif_prefix",),
        ))
        return findings

    tiff = app1_payload[6:]
    if len(tiff) < 8:
        findings.append(ValidationFinding(
            validator_id="image.exif.tiff_header",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"embedded_tiff_size_{len(tiff)}_need_8",
            repair_actions=("repair_exif_tiff_header",),
        ))
        return findings

    bom = tiff[:2]
    endian = "<" if bom == b"II" else (">" if bom == b"MM" else "")
    if not endian:
        findings.append(ValidationFinding(
            validator_id="image.exif.tiff_header",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"invalid_embedded_tiff_bom_{bom.hex()}",
            repair_actions=("repair_exif_tiff_header",),
        ))
        return findings

    magic = struct.unpack(f"{endian}H", tiff[2:4])[0]
    if magic != 42:
        findings.append(ValidationFinding(
            validator_id="image.exif.tiff_header",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"embedded_tiff_magic_{magic}_expected_42",
            repair_actions=("repair_exif_tiff_header",),
        ))
        return findings

    ifd_offset = struct.unpack(f"{endian}I", tiff[4:8])[0]
    if ifd_offset > len(tiff):
        findings.append(ValidationFinding(
            validator_id="image.exif.tiff_offset",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"ifd_offset_{ifd_offset}_exceeds_embedded_tiff_size_{len(tiff)}",
            repair_actions=("repair_exif_ifd_offset",),
        ))
    elif ifd_offset < 8:
        findings.append(ValidationFinding(
            validator_id="image.exif.tiff_offset",
            layer="invariant_check",
            verdict="warn",
            strength="supporting",
            evidence_ref=f"ifd_offset_{ifd_offset}_inside_embedded_header",
            repair_actions=("repair_exif_ifd_offset",),
        ))
    else:
        findings.append(ValidationFinding(
            validator_id="image.exif.tiff_offset",
            layer="invariant_check",
            verdict="pass",
            strength="strong",
            evidence_ref=f"ifd_offset_{ifd_offset}",
        ))
    return findings


def _find_exif_app1_payload(data: bytes) -> bytes | None:
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker == 0xD9:
            return None
        if marker == 0xDA:
            return None
        if 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        segment_len = struct.unpack(">H", data[pos + 2:pos + 4])[0]
        if segment_len < 2 or pos + 2 + segment_len > len(data):
            return None
        payload = data[pos + 4:pos + 2 + segment_len]
        if marker == 0xE1:
            return payload
        pos += 2 + segment_len
    return None


def _validate_tiff_bytes(data: bytes) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    if len(data) < 8:
        return [ValidationFinding(
            validator_id="image.tiff.header",
            layer="byte_safety",
            verdict="fail",
            strength="authoritative",
            evidence_ref=f"size_{len(data)}_need_8",
            repair_actions=("use_task_local_tiff_seed", "rebuild_tiff_header"),
        )]

    bom = data[:2]
    if bom == b"II":
        endian = "<"
    elif bom == b"MM":
        endian = ">"
    else:
        return [ValidationFinding(
            validator_id="image.tiff.byte_order",
            layer="byte_safety",
            verdict="fail",
            strength="strong",
            evidence_ref=f"invalid_bom_{bom.hex()}",
            repair_actions=("fix_tiff_byte_order",),
        )]

    magic = struct.unpack(f"{endian}H", data[2:4])[0]
    if magic != 42:
        findings.append(ValidationFinding(
            validator_id="image.tiff.magic",
            layer="byte_safety",
            verdict="fail",
            strength="strong",
            evidence_ref=f"magic_{magic}_expected_42",
            repair_actions=("fix_tiff_magic",),
        ))
        return findings

    findings.append(ValidationFinding(
        validator_id="image.tiff.magic",
        layer="byte_safety",
        verdict="pass",
        strength="strong",
        evidence_ref="tiff_magic_present",
    ))

    ifd_offset = struct.unpack(f"{endian}I", data[4:8])[0]
    if ifd_offset > len(data):
        findings.append(ValidationFinding(
            validator_id="image.tiff.ifd_offset",
            layer="invariant_check",
            verdict="fail",
            strength="strong",
            evidence_ref=f"ifd_offset_{ifd_offset}_exceeds_size_{len(data)}",
            repair_actions=("repair_ifd_offset", "use_task_local_tiff_seed"),
        ))
    elif ifd_offset < 8:
        findings.append(ValidationFinding(
            validator_id="image.tiff.ifd_offset",
            layer="invariant_check",
            verdict="warn",
            strength="supporting",
            evidence_ref=f"ifd_offset_{ifd_offset}_inside_header",
            repair_actions=("repair_ifd_offset",),
        ))
    else:
        findings.append(ValidationFinding(
            validator_id="image.tiff.ifd_offset",
            layer="invariant_check",
            verdict="pass",
            strength="strong",
            evidence_ref=f"ifd_offset_{ifd_offset}",
        ))
    return findings


def _build_validation_report(
    candidate_path: str,
    findings: list[ValidationFinding],
) -> ValidationReport:
    has_fail = any(f.verdict == "fail" for f in findings)
    has_warn = any(f.verdict == "warn" for f in findings)
    overall = "fail" if has_fail else ("warn" if has_warn else "pass")
    blocks = any(
        f.verdict == "fail" and f.strength in ("authoritative", "strong")
        for f in findings
    )
    return ValidationReport(
        candidate_path=candidate_path,
        pack_id="image",
        findings=tuple(findings),
        overall_verdict=overall,
        blocks_submit=blocks,
    )
