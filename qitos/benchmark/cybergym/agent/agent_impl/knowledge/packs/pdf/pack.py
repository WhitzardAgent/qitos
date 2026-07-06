"""PDF knowledge pack — full detect→parse→derive_contract→plan→build→validate→explain_repair pipeline.

Backed by pikepdf for parsing and building.  Gracefully degrades to
backend_unavailable when pikepdf is not installed.

Detection is evidence-based:
- project_name in PDF_PROJECTS → candidate (score ≤ 0.5)
- corpus magic %PDF → confirmed (score ≥ 0.7)
- description keywords (pdf, xref, stream, poppler, mupdf) → candidate (score ≤ 0.4)
- harness API (e.g. FuzzTargetPDF) → confirmed (score ≥ 0.8)
- source-backed format hint → confirmed (score ≥ 0.9)
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
from .builder import build_pdf_candidate
from .parser import parse_pdf
from .repairs import explain_pdf_repairs
from .validator import validate_pdf_candidate

logger = logging.getLogger(__name__)

# Known PDF-processing projects — these produce candidate-level evidence
PDF_PROJECTS: frozenset[str] = frozenset({
    "mupdf", "poppler", "qpdf", "pdfium", "chromium-pdf",
    "ghostscript", "cairo", "libpng", "imagemagick-pdf",
    "graphicsmagick-pdf", "cups-filters", "wkhtmltopdf",
    "xpdf", "pdftoppm", "pdf2svg", "inkscape",
    "libreoffice-pdf", "calligra-pdf", "sioyek",
    "sumatrapdf", "zathura", "apvlv",
})

# Description keywords that suggest PDF domain (candidate only)
PDF_KEYWORDS: frozenset[str] = frozenset({
    "pdf", "xref", "postscript", "ps document",
    "poppler", "mupdf", "qpdf", "pdfium",
    "jpeg2000", "jbig2",  # common PDF compression
})

# Harness API patterns that confirm PDF domain
PDF_HARNESS_PATTERNS: frozenset[str] = frozenset({
    "fuzz_target_pdf", "llfuzzpdf", "fuzz_pdf",
    "pdf_fuzzer", "pdf_fuzz", "pdf_parse",
    "fuzz_xref", "fuzz_stream", "fuzz_cmap",
})


class PdfKnowledgePack:
    """Executable knowledge pack for PDF format.

    Implements the KnowledgePack protocol: detect, parse, derive_contract,
    plan, build, validate, explain_repair.
    """

    descriptor = PackDescriptor(
        pack_id="pdf",
        carrier_families=("pdf", "ps"),
        supported_versions=("1.0", "1.1", "1.2", "1.3", "1.4", "1.5", "1.6", "1.7", "2.0"),
        capabilities=frozenset({"detect", "parse", "build", "validate", "repair"}),
        required_backends=("pikepdf",),
        knowledge_revision="2026.07.1",
    )

    # ------------------------------------------------------------------
    # detect
    # ------------------------------------------------------------------

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        """Evidence-based detection for PDF domain.

        Evidence hierarchy:
        1. source_backed_hints with format=pdf → confirmed (0.9)
        2. detected_magics contains "pdf" → confirmed (0.7)
        3. harness API matches PDF pattern → confirmed (0.8)
        4. input_format_type == "pdf" → confirmed (0.8)
        5. project_name in PDF_PROJECTS → candidate (0.4)
        6. description keywords → candidate (0.3)
        """
        positive: list[str] = []
        contradictory: list[str] = []
        missing: list[str] = []
        score = 0.0
        decision: str = "insufficient"

        # 1. Source-backed hints (authoritative)
        for hint in evidence.source_backed_hints:
            if "format=pdf" in hint or "carrier=pdf" in hint:
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        # 2. Corpus magic bytes (strong)
        if "pdf" in evidence.detected_magics:
            positive.append("corpus_magic:pdf")
            score = max(score, 0.7)

        # 3. Harness API patterns (strong)
        for api in evidence.harness_api_calls:
            api_lower = api.lower()
            if any(pat in api_lower for pat in PDF_HARNESS_PATTERNS):
                positive.append(f"harness_api:{api}")
                score = max(score, 0.8)

        # 4. input_format_type (strong)
        if evidence.input_format_type and evidence.input_format_type.lower() == "pdf":
            positive.append("input_format:pdf")
            score = max(score, 0.8)

        # 5. Project name (weak → candidate only)
        project_lower = evidence.project_name.lower()
        if project_lower in PDF_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        # 6. Description keywords (weak → candidate only)
        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in PDF_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        # Contradictory evidence
        if evidence.detected_magics and "pdf" not in evidence.detected_magics:
            other_fmts = [m for m in evidence.detected_magics if m != "pdf"]
            if other_fmts and score < 0.7:
                contradictory.append(f"corpus_magic:{','.join(other_fmts)}")

        # Determine decision
        if score >= 0.7:
            decision = "confirmed"
        elif score >= 0.2:
            decision = "candidate"
        elif positive:
            decision = "candidate"
        else:
            decision = "insufficient"

        # Missing evidence
        if score < 0.7 and not evidence.detected_magics and not evidence.corpus_files:
            missing.append("corpus_magic_bytes")
        if score < 0.7 and not evidence.harness_api_calls:
            missing.append("harness_api_calls")
        if score < 0.5 and not evidence.source_backed_hints:
            missing.append("source_backed_format_hint")

        return DetectionResult(
            decision=decision,
            score=round(score, 3),
            positive_evidence_ids=tuple(positive),
            contradictory_evidence_ids=tuple(contradictory),
            missing_evidence=tuple(missing),
        )

    # ------------------------------------------------------------------
    # parse
    # ------------------------------------------------------------------

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        """Parse a PDF artifact using pikepdf."""
        return parse_pdf(artifact, context)

    # ------------------------------------------------------------------
    # derive_contract
    # ------------------------------------------------------------------

    def derive_contract(
        self,
        parsed: ParseResult,
        harness: dict[str, Any] | None = None,
    ) -> CarrierContract:
        """Derive carrier contract from parsed PDF structure.

        Key contract points:
        - /Root must be preserved (protected)
        - Stream /Length is derived (auto-recomputed)
        - Xref offsets are derived (auto-recomputed)
        - Minimal seed ~256 bytes (1-page PDF with empty page)
        """
        derived_fields = ["pdf.xref.offset"]
        protected_fields = ["pdf.trailer.root"]

        # Add stream lengths as derived
        for name in parsed.field_map:
            if ".stream.length" in name and not name.endswith(".actual_length"):
                derived_fields.append(name)

        # Harness acceptance hints
        hints: list[str] = []
        if harness:
            input_contract = harness.get("input_contract", "")
            if input_contract:
                hints.append(f"harness_expects_{input_contract}")

        # Format version
        version = parsed.version or "1.7"
        format_id = f"pdf-{version}"

        # Minimal seed size estimate
        min_size = 256  # minimal valid PDF

        return CarrierContract(
            format_id=format_id,
            seed_required=True,
            minimal_seed_size=min_size,
            required_fields=tuple(
                k for k in parsed.field_map
                if not parsed.field_map[k].derived and not parsed.field_map[k].protected
            )[:20],
            derived_fields=tuple(derived_fields),
            protected_fields=tuple(protected_fields),
            harness_acceptance_hints=tuple(hints),
        )

    # ------------------------------------------------------------------
    # plan
    # ------------------------------------------------------------------

    def plan(
        self,
        objective: dict[str, Any],
        provenance: dict[str, Any] | None = None,
        carrier: CarrierContract | None = None,
    ) -> RecipePlan:
        """Generate a recipe plan from objective + provenance + carrier.

        Maps objective kinds to PDF-specific operations:
        - overflow → mutate_field (set /Length to oversized value)
        - stream_corruption → mutate_stream (corrupt stream bytes)
        - xref_inconsistency → set_field (modify xref offset)
        """
        objective_id = objective.get("objective_id", "")
        objective_kind = objective.get("kind", "")

        operations: list[RecipeOperation] = []
        invariants: list[Invariant] = []
        effects: list[ExpectedEffect] = []
        evidence_ids: list[str] = []

        target_function = objective.get("target_function", "")
        target_node = _infer_target_node(objective)

        if objective_kind in ("overflow", "heap_overflow", "buffer_overflow"):
            # Overflow: set /Length to oversized value
            ops = _plan_overflow_operations(objective, target_node)
            operations.extend(ops)
            invariants.append(Invariant(
                invariant_id="inv_stream_length",
                kind="length",
                expression="/Length must be consistent with actual stream size",
                protected=False,
            ))
            effects.append(ExpectedEffect(
                effect_id="eff_overflow",
                target_expression="pdf.stream.decoded_length",
                desired_relation="decoded_length > allocation_size",
                expected_runtime_probe="heap_buffer_overflow",
            ))

        elif objective_kind in ("stream_corruption", "use_after_free"):
            # Corrupt stream content
            ops = _plan_stream_corruption_operations(objective, target_node)
            operations.extend(ops)
            effects.append(ExpectedEffect(
                effect_id="eff_corruption",
                target_expression="pdf.stream.content",
                desired_relation="malformed_stream_triggers_parse_error",
                expected_runtime_probe="crash_or_asan_report",
            ))

        elif objective_kind in ("xref_inconsistency", "reference_error"):
            ops = _plan_xref_operations(objective, target_node)
            operations.extend(ops)
            invariants.append(Invariant(
                invariant_id="inv_xref",
                kind="offset",
                expression="xref offsets must point to valid objects",
                protected=False,
            ))

        elif objective_kind in ("integer_overflow", "numeric"):
            # Numeric constraint — set a field to overflow value
            ops = _plan_numeric_operations(objective, target_node)
            operations.extend(ops)

        else:
            # Generic: try to find the target object and mutate it
            if target_node:
                operations.append(RecipeOperation(
                    op_id=f"op_mutate_{target_node}",
                    kind="mutate_field",
                    target_node_id=target_node,
                    evidence_id=f"objective:{objective_id}",
                ))

        # Add carrier info from provenance
        carrier_info: dict[str, Any] = {}
        if provenance:
            carrier_info = {
                "format": provenance.get("format", "pdf"),
                "seed_path": provenance.get("seed_path", ""),
                "seed_policy": provenance.get("seed_policy", "minimal_template_ok"),
            }

        return RecipePlan(
            recipe_id=f"rec_pdf_{objective_id}",
            schema_version="2.0",
            objective_id=objective_id,
            carrier_contract_id=carrier.format_id if carrier else "pdf",
            seed_id=provenance.get("seed_path", "") if provenance else "",
            operations=tuple(operations),
            invariants=tuple(invariants),
            expected_effects=tuple(effects),
            evidence_ids=tuple(evidence_ids),
            carrier=carrier_info,
        )

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        """Build a PDF candidate from seed + plan."""
        return build_pdf_candidate(seed, plan)

    # ------------------------------------------------------------------
    # validate
    # ------------------------------------------------------------------

    def validate(
        self,
        artifact: bytes,
        contract: CarrierContract,
        mutation_intent: ExpectedEffect | None = None,
    ) -> ValidationReport:
        """Five-layer validation of a PDF candidate.

        artifact can be bytes or a file path string.
        """
        # Write bytes to temp file if needed
        import tempfile
        candidate_path = ""

        if isinstance(artifact, (str, bytes)):
            if isinstance(artifact, str) and len(artifact) < 512 and "/" in artifact:
                # Likely a file path
                candidate_path = artifact
            else:
                # Raw bytes — write to temp file
                fd, candidate_path = tempfile.mkstemp(suffix=".pdf")
                import os
                with os.fdopen(fd, "wb") as f:
                    f.write(artifact if isinstance(artifact, bytes) else artifact.encode())

        return validate_pdf_candidate(candidate_path, contract, mutation_intent)

    # ------------------------------------------------------------------
    # explain_repair
    # ------------------------------------------------------------------

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        """Generate repair actions from validation findings."""
        return explain_pdf_repairs(report)


# ------------------------------------------------------------------
# Internal helpers for plan()
# ------------------------------------------------------------------

def _infer_target_node(objective: dict[str, Any]) -> str:
    """Infer the target PDF object node from objective metadata."""
    target_func = objective.get("target_function", "")
    desc = objective.get("description", "") or objective.get("kind", "")

    # Try to match known PDF functions to object numbers
    # This is heuristic — the real mapping comes from api_reachability
    if "xref" in desc.lower():
        return "xref"
    if "stream" in desc.lower():
        return "obj_stream"
    if "trailer" in desc.lower():
        return "trailer"

    return ""


def _plan_overflow_operations(
    objective: dict[str, Any],
    target_node: str,
) -> list[RecipeOperation]:
    """Plan operations for overflow-type objectives."""
    ops: list[RecipeOperation] = []

    # Set /Length to an oversized value
    overflow_value = objective.get("overflow_value", 0xFFFF)
    if isinstance(overflow_value, str):
        try:
            overflow_value = int(overflow_value, 0)
        except ValueError:
            overflow_value = 0xFFFF

    ops.append(RecipeOperation(
        op_id="op_set_length_overflow",
        kind="mutate_field",
        target_node_id=target_node or "obj_stream",
        invalidated_derivations=("pdf.*.stream.length",),
        rollback_hint="restore original /Length value",
        evidence_id=f"objective:{objective.get('objective_id', '')}",
        ast_transform={"key": "/Length", "value": overflow_value},
    ))

    return ops


def _plan_stream_corruption_operations(
    objective: dict[str, Any],
    target_node: str,
) -> list[RecipeOperation]:
    """Plan operations for stream corruption objectives."""
    ops: list[RecipeOperation] = []

    # Mutate stream bytes at the target offset
    mutation_offset = objective.get("mutation_offset", 0)
    mutation_bytes = objective.get("mutation_bytes", b"\xff\xff\xff\xff")

    ops.append(RecipeOperation(
        op_id="op_mutate_stream_bytes",
        kind="mutate_stream",
        target_node_id=target_node or "obj_stream",
        write_spans=((mutation_offset, mutation_offset + len(mutation_bytes)),),
        rollback_hint="restore original stream bytes",
        evidence_id=f"objective:{objective.get('objective_id', '')}",
        ast_transform={"offset": mutation_offset, "bytes": mutation_bytes},
    ))

    return ops


def _plan_xref_operations(
    objective: dict[str, Any],
    target_node: str,
) -> list[RecipeOperation]:
    """Plan operations for xref inconsistency objectives."""
    ops: list[RecipeOperation] = []

    ops.append(RecipeOperation(
        op_id="op_mutate_xref_offset",
        kind="set_field",
        target_node_id=target_node or "xref",
        invalidated_derivations=("pdf.xref.offset",),
        rollback_hint="restore original xref offset",
        evidence_id=f"objective:{objective.get('objective_id', '')}",
        ast_transform={"key": "startxref", "value": 0},
    ))

    return ops


def _plan_numeric_operations(
    objective: dict[str, Any],
    target_node: str,
) -> list[RecipeOperation]:
    """Plan operations for numeric/integer overflow objectives."""
    ops: list[RecipeOperation] = []

    target_field = objective.get("target_field", "/Length")
    overflow_value = objective.get("overflow_value", 0x7FFFFFFF)

    ops.append(RecipeOperation(
        op_id="op_numeric_overflow",
        kind="mutate_field",
        target_node_id=target_node or "obj_stream",
        invalidated_derivations=("pdf.*.stream.length",),
        rollback_hint=f"restore original {target_field} value",
        evidence_id=f"objective:{objective.get('objective_id', '')}",
        ast_transform={"key": target_field, "value": overflow_value},
    ))

    return ops
