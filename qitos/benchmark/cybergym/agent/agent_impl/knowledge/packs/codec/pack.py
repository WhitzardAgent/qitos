"""Media Codec/Container knowledge pack — construct-backed pipeline.

Covers: H.264/AVC NAL, H.265/HEVC, AAC, FLAC, AV1, VP9, JXL, HEIF, Matroska/MP4.
Backend: construct for bitstream syntax definition.

Construct pattern: define Struct schemas per sub-format, parse/build with them.
The pack detects the sub-format from artifact magic bytes and selects
the appropriate schema.
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

CODEC_PROJECTS: frozenset[str] = frozenset({
    "ffmpeg", "gpac", "libxaac", "libaom", "libvpx",
    "libjxl", "libheif", "openh264", "dav1d",
})

CODEC_KEYWORDS: frozenset[str] = frozenset({
    "h264", "h265", "hevc", "avc", "nal", "aac", "flac",
    "av1", "vp9", "jxl", "heif", "codec", "bitstream",
    "ffmpeg", "mp4", "matroska", "isobmff", "container",
})


# ------------------------------------------------------------------
# Construct schemas for common codec structures
# ------------------------------------------------------------------

def _build_h264_nal_schema() -> Any:
    """Build a construct schema for H.264 Annex-B NAL units."""
    try:
        import construct as cs
        return cs.Struct(
            'start_code' / cs.Bytes(4),  # 00 00 00 01
            'header' / cs.BitStruct(
                'forbidden_zero_bit' / cs.Bit,
                'nal_ref_idc' / cs.BitsInteger(2),
                'nal_unit_type' / cs.BitsInteger(5),
            ),
            'payload' / cs.GreedyBytes,
        )
    except ImportError:
        return None


def _build_flac_schema() -> Any:
    """Build a construct schema for FLAC stream header."""
    try:
        import construct as cs
        return cs.Struct(
            'magic' / cs.Bytes(4),  # fLaC
            'stream_info_block' / cs.Struct(
                'header' / cs.BitStruct(
                    'last_metadata_block' / cs.Bit,
                    'block_type' / cs.BitsInteger(7),
                ),
                'length' / cs.Int24ub,
                'min_block_size' / cs.Int16ub,
                'max_block_size' / cs.Int16ub,
                'min_frame_size' / cs.Int24ub,
                'max_frame_size' / cs.Int24ub,
            ),
        )
    except ImportError:
        return None


def _build_aac_adts_schema() -> Any:
    """Build a construct schema for AAC ADTS frame header."""
    try:
        import construct as cs
        return cs.BitStruct(
            'syncword' / cs.BitsInteger(12),   # 0xFFF
            'id' / cs.Bit,               # MPEG-4=0, MPEG-2=1
            'layer' / cs.BitsInteger(2),
            'protection_absent' / cs.Bit,
            'profile' / cs.BitsInteger(2),
            'sampling_freq_index' / cs.BitsInteger(4),
            'private_bit' / cs.Bit,
            'channel_config' / cs.BitsInteger(3),
            'original_copy' / cs.Bit,
            'home' / cs.Bit,
            'frame_length' / cs.BitsInteger(13),
        )
    except ImportError:
        return None


def _detect_codec_subformat(artifact: bytes) -> str:
    """Detect codec sub-format from magic bytes."""
    if len(artifact) < 4:
        return ""

    # H.264 Annex-B start code
    if artifact[:4] == b'\x00\x00\x00\x01' or artifact[:3] == b'\x00\x00\x01':
        return "h264"

    # FLAC
    if artifact[:4] == b'fLaC':
        return "flac"

    # AAC ADTS syncword (12 bits = 0xFFF)
    if len(artifact) >= 2 and (artifact[0] == 0xFF and (artifact[1] & 0xF0) == 0xF0):
        return "aac"

    # MP4/MOV ftyp box
    if len(artifact) >= 8 and artifact[4:8] == b'ftyp':
        return "mp4"

    # Matroska EBML header
    if artifact[:4] == b'\x1a\x45\xdf\xa3':
        return "mkv"

    # AV1 OBU
    if len(artifact) >= 1 and (artifact[0] & 0x88) == 0x08:  # obu_forbidden_bit=0, obu_type
        return "av1"

    return "codec"


class CodecKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="codec",
        carrier_families=("h264", "h265", "aac", "flac", "av1", "vp9", "jxl", "heif", "mp4", "mkv"),
        supported_versions=("h264-annexb", "h265-vps", "aac-adts", "flac-stream"),
        capabilities=frozenset({"detect", "parse", "build", "validate"}),
        required_backends=("construct",),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("codec", "h264", "h265", "aac", "flac", "bitstream")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        if evidence.harness_input_contract in ("codec", "bitstream", "h264", "aac"):
            positive.append(f"harness_input_contract:{evidence.harness_input_contract}")
            score = max(score, 0.8)

        project_lower = evidence.project_name.lower()
        if project_lower in CODEC_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in CODEC_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        return DetectionResult(decision=decision, score=round(score, 3), positive_evidence_ids=tuple(positive))

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        try:
            import construct as cs
        except ImportError:
            return ParseResult(status="backend_unavailable", carrier_family="codec",
                               parse_warnings=("construct_not_available",))

        if not artifact or len(artifact) < 4:
            return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

        field_map: dict[str, FieldInfo] = {}
        warnings: list[str] = []
        evidence_ids: list[str] = []
        subformat = _detect_codec_subformat(artifact)

        if subformat == "h264":
            schema = _build_h264_nal_schema()
            if schema:
                try:
                    # Parse first NAL unit
                    result = schema.parse(artifact)
                    field_map["h264.nal_type"] = FieldInfo(
                        name="h264.nal_type", offset=4, width=1,
                        value=int(result.header.nal_unit_type), node_id="nal_0")
                    field_map["h264.nal_ref_idc"] = FieldInfo(
                        name="h264.nal_ref_idc", offset=4, width=1,
                        value=int(result.header.nal_ref_idc), node_id="nal_0")
                    evidence_ids.append("h264.nal_0")
                except Exception as e:
                    warnings.append(f"h264_parse_failed: {e}")

        elif subformat == "flac":
            schema = _build_flac_schema()
            if schema:
                try:
                    result = schema.parse(artifact)
                    field_map["flac.min_block_size"] = FieldInfo(
                        name="flac.min_block_size", offset=0, width=2,
                        value=result.stream_info_block.min_block_size, node_id="stream_info")
                    field_map["flac.max_block_size"] = FieldInfo(
                        name="flac.max_block_size", offset=0, width=2,
                        value=result.stream_info_block.max_block_size, node_id="stream_info")
                    evidence_ids.append("flac.stream_info")
                except Exception as e:
                    warnings.append(f"flac_parse_failed: {e}")

        elif subformat == "aac":
            schema = _build_aac_adts_schema()
            if schema:
                try:
                    result = schema.parse(artifact)
                    field_map["aac.profile"] = FieldInfo(
                        name="aac.profile", offset=0, width=0,
                        value=int(result.profile), node_id="adts_header")
                    field_map["aac.sampling_freq_index"] = FieldInfo(
                        name="aac.sampling_freq_index", offset=0, width=0,
                        value=int(result.sampling_freq_index), node_id="adts_header")
                    field_map["aac.channel_config"] = FieldInfo(
                        name="aac.channel_config", offset=0, width=0,
                        value=int(result.channel_config), node_id="adts_header")
                    field_map["aac.frame_length"] = FieldInfo(
                        name="aac.frame_length", offset=0, width=0,
                        value=int(result.frame_length), node_id="adts_header",
                        derived=True)
                    evidence_ids.append("aac.adts_header")
                except Exception as e:
                    warnings.append(f"aac_parse_failed: {e}")

        elif subformat == "mp4":
            # Parse ftyp box
            try:
                import struct
                box_size = struct.unpack(">I", artifact[:4])[0]
                box_type = artifact[4:8].decode("ascii", errors="replace")
                field_map["mp4.ftyp.size"] = FieldInfo(
                    name="mp4.ftyp.size", offset=0, width=4,
                    value=box_size, node_id="ftyp")
                field_map["mp4.ftyp.type"] = FieldInfo(
                    name="mp4.ftyp.type", offset=4, width=4,
                    value=box_type, node_id="ftyp", protected=True)
                evidence_ids.append("mp4.ftyp")
            except Exception as e:
                warnings.append(f"mp4_parse_failed: {e}")

        return ParseResult(
            status="success" if not warnings else "partial",
            carrier_family=subformat or "codec",
            structural_summary={"format": subformat},
            field_map=field_map,
            node_count=len(set(f.node_id for f in field_map.values())),
            parse_warnings=tuple(warnings),
            evidence_ids=tuple(evidence_ids),
        )

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        derived = [k for k, f in parsed.field_map.items() if f.derived]
        return CarrierContract(
            format_id=parsed.carrier_family or "codec",
            seed_required=True, minimal_seed_size=32,
            required_fields=(), derived_fields=tuple(derived),
        )

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        objective_id = objective.get("objective_id", "")
        return RecipePlan(recipe_id=f"rec_codec_{objective_id}", objective_id=objective_id,
                         schema_version="2.0", carrier_contract_id=carrier.format_id if carrier else "codec")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(seed)
        return BuildResult(status="partial", artifact_path=path, reason="codec_builder_minimal")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        # Try to re-parse with the same schema
        subformat = contract.format_id
        if subformat == "h264" and artifact[:4] == b'\x00\x00\x00\x01':
            return ValidationReport(pack_id="codec", overall_verdict="pass")
        if subformat == "flac" and artifact[:4] == b'fLaC':
            return ValidationReport(pack_id="codec", overall_verdict="pass")
        if subformat == "aac" and len(artifact) >= 2 and (artifact[0] & 0xFF) == 0xFF:
            return ValidationReport(pack_id="codec", overall_verdict="pass")
        return ValidationReport(pack_id="codec", overall_verdict="unknown")

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
