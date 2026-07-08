"""3D/CAD knowledge pack — construct-backed pipeline.

Covers: DWG, DXF, OBJ, STL, FBX, Collada, glTF.
Backend: construct for DWG internal format parsing.

DWG is proprietary; construct schemas are reverse-engineered from libredwg
source and the Open Design Alliance specification.  STL/OBJ are simpler
formats that construct handles directly.
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

CAD_PROJECTS: frozenset[str] = frozenset({"libredwg", "assimp", "opencascade", "freecad"})
CAD_KEYWORDS: frozenset[str] = frozenset({"dwg", "dxf", "3d model", "obj", "stl", "assimp", "libredwg", "cad"})


def _build_stl_binary_schema() -> Any:
    """Build a construct schema for binary STL files."""
    try:
        import construct as cs
        Triangle = cs.Struct(
            'normal' / cs.Array(3, cs.Float32l),
            'vert_a' / cs.Array(3, cs.Float32l),
            'vert_b' / cs.Array(3, cs.Float32l),
            'vert_c' / cs.Array(3, cs.Float32l),
            'attribute_byte_count' / cs.Int16ub,
        )
        return cs.Struct(
            'header' / cs.Bytes(80),
            'num_triangles' / cs.Int32ul,
            'triangles' / cs.Array(cs.this.num_triangles, Triangle),
        )
    except ImportError:
        return None


def _build_dwg_header_schema() -> Any:
    """Build a construct schema for DWG file header (version detection only).

    DWG header starts with version string: AC1015 (R2000), AC1018 (R2004), etc.
    """
    try:
        import construct as cs
        return cs.Struct(
            'version_string' / cs.Bytes(6),  # e.g. b'AC1015'
            'null_byte' / cs.Byte,            # should be 0x00
            'unknown' / cs.Bytes(5),
        )
    except ImportError:
        return None


def _detect_cad_subformat(artifact: bytes) -> str:
    """Detect CAD sub-format from magic bytes."""
    if len(artifact) < 6:
        return ""

    # DWG: starts with AC1xxx
    if artifact[:3] == b'AC1':
        return "dwg"

    # DXF: text-based, starts with 0\nSECTION\n
    if artifact[:2] == b'0\n' or artifact[:1] == b' ':
        return "dxf"

    # Binary STL: header(80) + triangle count(4)
    if len(artifact) >= 84:
        import struct
        num_triangles = struct.unpack("<I", artifact[80:84])[0]
        expected_size = 84 + num_triangles * 50  # 50 bytes per triangle
        if expected_size == len(artifact) and num_triangles < 10_000_000:
            return "stl"

    # OBJ: text-based, starts with # or v or mtllib
    if artifact[:1] in (b'#', b'v') or b'mtllib' in artifact[:100]:
        return "obj"

    return "cad"


class CadKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="cad",
        carrier_families=("dwg", "dxf", "obj", "stl", "fbx", "gltf"),
        supported_versions=("dwg-r2018", "dxf-ascii", "stl-binary"),
        capabilities=frozenset({"detect", "parse", "build", "validate"}),
        required_backends=("construct",),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("dwg", "dxf", "cad", "3d")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        project_lower = evidence.project_name.lower()
        if project_lower in CAD_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in CAD_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        return DetectionResult(decision=decision, score=round(score, 3), positive_evidence_ids=tuple(positive))

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        try:
            import construct as cs
        except ImportError:
            return ParseResult(status="backend_unavailable", carrier_family="cad",
                               parse_warnings=("construct_not_available",))

        if not artifact or len(artifact) < 6:
            return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

        field_map: dict[str, FieldInfo] = {}
        warnings: list[str] = []
        evidence_ids: list[str] = []
        subformat = _detect_cad_subformat(artifact)

        if subformat == "dwg":
            schema = _build_dwg_header_schema()
            if schema:
                try:
                    result = schema.parse(artifact)
                    field_map["dwg.version_string"] = FieldInfo(
                        name="dwg.version_string", offset=0, width=6,
                        value=result.version_string.decode("ascii", errors="replace"),
                        node_id="header", protected=True)
                    evidence_ids.append("dwg.header")
                except Exception as e:
                    warnings.append(f"dwg_parse_failed: {e}")

        elif subformat == "stl":
            schema = _build_stl_binary_schema()
            if schema:
                try:
                    result = schema.parse(artifact)
                    field_map["stl.num_triangles"] = FieldInfo(
                        name="stl.num_triangles", offset=80, width=4,
                        value=result.num_triangles, node_id="header",
                        derived=True)
                    evidence_ids.append("stl.header")
                except Exception as e:
                    warnings.append(f"stl_parse_failed: {e}")

        return ParseResult(
            status="success" if not warnings else "partial",
            carrier_family=subformat or "cad",
            structural_summary={"format": subformat},
            field_map=field_map,
            node_count=len(set(f.node_id for f in field_map.values())),
            parse_warnings=tuple(warnings),
            evidence_ids=tuple(evidence_ids),
        )

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        return CarrierContract(format_id=parsed.carrier_family or "cad",
                               seed_required=True, minimal_seed_size=32)

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        objective_id = objective.get("objective_id", "")
        return RecipePlan(recipe_id=f"rec_cad_{objective_id}", objective_id=objective_id,
                         schema_version="2.0", carrier_contract_id=carrier.format_id if carrier else "cad")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(seed)
        return BuildResult(status="partial", artifact_path=path, reason="cad_builder_minimal")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        subformat = contract.format_id
        if subformat == "stl" and len(artifact) >= 84:
            return ValidationReport(pack_id="cad", overall_verdict="pass")
        if subformat == "dwg" and artifact[:3] == b'AC1':
            return ValidationReport(pack_id="cad", overall_verdict="pass")
        return ValidationReport(pack_id="cad", overall_verdict="unknown")

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
