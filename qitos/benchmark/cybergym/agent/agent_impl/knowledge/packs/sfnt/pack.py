"""SFNT/Font knowledge pack — full detect→parse→derive_contract→plan→build→validate→explain_repair pipeline.

Backed by fontTools.ttLib.  Gracefully degrades when fontTools is not installed.

Detection is evidence-based:
- corpus magic (ttf/otf/woff) → confirmed
- project_name in FONT_PROJECTS → candidate
- description keywords → candidate
- harness API → confirmed
"""

from __future__ import annotations

import logging
from typing import Any

from ...evidence import EvidenceView
from ...models import (
    BuildResult,
    CarrierContract,
    DetectionResult,
    ExpectedEffect,
    Invariant,
    PackDescriptor,
    ParseResult,
    RecipeOperation,
    RecipePlan,
    RepairAction,
    ValidationReport,
)
from .builder import build_sfnt_candidate
from .parser import parse_sfnt
from .repairs import explain_sfnt_repairs
from .validator import validate_sfnt_candidate

logger = logging.getLogger(__name__)

FONT_PROJECTS: frozenset[str] = frozenset({
    "freetype", "fonttools", "harfbuzz", "libass", "pango",
    "stb_truetype", "libpng-font", "librsvg-font",
    "google-font", "noto-fonts", " chromium-font",
    "firefox-font", "webkit-font",
})

FONT_KEYWORDS: frozenset[str] = frozenset({
    "font", "sfnt", "truetype", "opentype", "ttf", "otf",
    "woff", "cff", "gvar", "glyf", "cmap", "hinting",
    "freetype", "harfbuzz", "type1", "type 1",
})

FONT_HARNESS_PATTERNS: frozenset[str] = frozenset({
    "fuzz_font", "fuzz_ttf", "fuzz_sfnt", "fuzz_otf",
    "fuzz_cff", "fuzz_glyf", "fuzz_gvar", "font_fuzzer",
})


class SfntKnowledgePack:
    """Executable knowledge pack for SFNT/Font formats."""

    descriptor = PackDescriptor(
        pack_id="sfnt",
        carrier_families=("ttf", "otf", "ttc", "woff", "woff2"),
        supported_versions=("1.0", "OTTO", "true", "ttcf"),
        capabilities=frozenset({"detect", "parse", "build", "validate", "repair"}),
        required_backends=("fontTools",),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        """Evidence-based detection for SFNT/Font domain."""
        positive: list[str] = []
        contradictory: list[str] = []
        missing: list[str] = []
        score = 0.0

        # Source-backed hints
        for hint in evidence.source_backed_hints:
            if "font" in hint.lower() or "sfnt" in hint.lower():
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        # Corpus magic bytes
        font_magics = {"ttf", "otf", "woff"}
        matched_magics = [m for m in evidence.detected_magics if m in font_magics]
        if matched_magics:
            positive.append(f"corpus_magic:{','.join(matched_magics)}")
            score = max(score, 0.7)

        # Harness API
        for api in evidence.harness_api_calls:
            api_lower = api.lower()
            if any(pat in api_lower for pat in FONT_HARNESS_PATTERNS):
                positive.append(f"harness_api:{api}")
                score = max(score, 0.8)

        # input_format_type
        if evidence.input_format_type and evidence.input_format_type.lower() in ("font", "sfnt", "ttf", "otf"):
            positive.append(f"input_format:{evidence.input_format_type}")
            score = max(score, 0.8)

        # Project name (weak)
        project_lower = evidence.project_name.lower()
        if project_lower in FONT_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        # Description keywords (weak)
        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in FONT_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        # Contradictory
        if evidence.detected_magics and not matched_magics:
            other = [m for m in evidence.detected_magics if m not in font_magics]
            if other and score < 0.7:
                contradictory.append(f"corpus_magic:{','.join(other)}")

        # Decision
        if score >= 0.7:
            decision = "confirmed"
        elif score >= 0.2:
            decision = "candidate"
        elif positive:
            decision = "candidate"
        else:
            decision = "insufficient"

        if score < 0.7 and not evidence.detected_magics:
            missing.append("corpus_magic_bytes")
        if score < 0.5 and not evidence.harness_api_calls:
            missing.append("harness_api_calls")

        return DetectionResult(
            decision=decision,
            score=round(score, 3),
            positive_evidence_ids=tuple(positive),
            contradictory_evidence_ids=tuple(contradictory),
            missing_evidence=tuple(missing),
        )

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        return parse_sfnt(artifact, context)

    def derive_contract(
        self,
        parsed: ParseResult,
        harness: dict[str, Any] | None = None,
    ) -> CarrierContract:
        derived_fields: list[str] = []
        protected_fields: list[str] = []

        for name in parsed.field_map:
            f = parsed.field_map[name]
            if f.derived:
                derived_fields.append(name)
            if f.protected:
                protected_fields.append(name)

        # head.checkSumAdjustment is always derived and protected
        if "sfnt.head.checkSumAdjustment" not in protected_fields:
            protected_fields.append("sfnt.head.checkSumAdjustment")

        hints: list[str] = []
        if harness:
            ic = harness.get("input_contract", "")
            if ic:
                hints.append(f"harness_expects_{ic}")

        return CarrierContract(
            format_id=parsed.carrier_family or "sfnt",
            seed_required=True,
            minimal_seed_size=256,
            required_fields=tuple(
                k for k in parsed.field_map
                if not parsed.field_map[k].derived and not parsed.field_map[k].protected
            )[:20],
            derived_fields=tuple(derived_fields),
            protected_fields=tuple(protected_fields),
            harness_acceptance_hints=tuple(hints),
        )

    def plan(
        self,
        objective: dict[str, Any],
        provenance: dict[str, Any] | None = None,
        carrier: CarrierContract | None = None,
    ) -> RecipePlan:
        objective_id = objective.get("objective_id", "")
        objective_kind = objective.get("kind", "")

        operations: list[RecipeOperation] = []
        invariants: list[Invariant] = []
        effects: list[ExpectedEffect] = []

        target_node = _infer_target_node(objective)

        if objective_kind in ("overflow", "heap_overflow", "buffer_overflow"):
            overflow_value = objective.get("overflow_value", 0xFFFF)
            operations.append(RecipeOperation(
                op_id="op_font_overflow",
                kind="mutate_field",
                target_node_id=target_node or "table_head",
                invalidated_derivations=("sfnt.table.*.checksum", "sfnt.head.checkSumAdjustment"),
                rollback_hint="restore original field value",
                evidence_id=f"objective:{objective_id}",
                ast_transform={"key": "numGlyphs", "value": overflow_value},
            ))
            invariants.append(Invariant(
                invariant_id="inv_font_length",
                kind="length",
                expression="Table lengths must match actual data",
                protected=False,
            ))

        elif objective_kind in ("checksum_error", "corruption"):
            operations.append(RecipeOperation(
                op_id="op_font_checksum_corrupt",
                kind="set_field",
                target_node_id=target_node or "table_head",
                invalidated_derivations=("sfnt.head.checkSumAdjustment",),
                rollback_hint="restore original checksum",
                evidence_id=f"objective:{objective_id}",
                ast_transform={"key": "checkSumAdjustment", "value": 0},
            ))

        else:
            if target_node:
                operations.append(RecipeOperation(
                    op_id=f"op_mutate_{target_node}",
                    kind="mutate_field",
                    target_node_id=target_node,
                    evidence_id=f"objective:{objective_id}",
                ))

        carrier_info: dict[str, Any] = {}
        if provenance:
            carrier_info = {
                "format": provenance.get("format", "sfnt"),
                "seed_path": provenance.get("seed_path", ""),
            }

        return RecipePlan(
            recipe_id=f"rec_sfnt_{objective_id}",
            schema_version="2.0",
            objective_id=objective_id,
            carrier_contract_id=carrier.format_id if carrier else "sfnt",
            seed_id=provenance.get("seed_path", "") if provenance else "",
            operations=tuple(operations),
            invariants=tuple(invariants),
            expected_effects=tuple(effects),
            carrier=carrier_info,
        )

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        return build_sfnt_candidate(seed, plan)

    def validate(
        self,
        artifact: bytes,
        contract: CarrierContract,
        mutation_intent: ExpectedEffect | None = None,
    ) -> ValidationReport:
        import tempfile
        candidate_path = ""

        if isinstance(artifact, str) and len(artifact) < 512 and "/" in artifact:
            candidate_path = artifact
        else:
            fd, candidate_path = tempfile.mkstemp(suffix=".ttf")
            import os
            with os.fdopen(fd, "wb") as f:
                f.write(artifact if isinstance(artifact, bytes) else artifact.encode())

        return validate_sfnt_candidate(candidate_path, contract, mutation_intent)

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return explain_sfnt_repairs(report)


def _infer_target_node(objective: dict[str, Any]) -> str:
    desc = (objective.get("description", "") or objective.get("kind", "")).lower()
    if "cff" in desc:
        return "table_cff"
    if "gvar" in desc:
        return "table_gvar"
    if "glyf" in desc:
        return "table_glyf"
    if "cmap" in desc:
        return "table_cmap"
    if "head" in desc:
        return "table_head"
    return ""
