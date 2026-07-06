"""Unknown domain resolver — deterministic degradation for tasks without confirmed packs.

Per CAPABILITY_MATURITY_95_PLAN.md Section V: "95% target cannot depend on
knowing all formats in advance."

This resolver provides a systematic degradation path for the ~118 uninferred
tasks (7.8%) where no confirmed pack exists. It uses harness contracts,
corpus inspection, and runtime probes to build task-local knowledge.

Task-local schema/knowledge is written to runtime artifact
(.agent/runtime_evidence/), NOT committed to source.  Only cross-case-validated
knowledge gets promoted to a formal pack.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .evidence import EvidenceView

logger = logging.getLogger(__name__)


@dataclass
class UnknownDomainResult:
    """Result of unknown domain resolution."""
    status: str = "unresolved"  # unresolved, partial_schema, resolved
    carrier_format: str = "unknown-record-format"
    harness_status: str = "unknown"  # confirmed, partial, unknown
    parser_accept: str = "unknown"   # hit, miss, unknown
    dispatch_selector: str = ""      # e.g. "input[0:2] little-endian, observed=3"
    record_length_field: str = ""    # e.g. "input[4:8]"
    checksum_algorithm: str = ""     # e.g. "crc32", "sum16", "unresolved"
    next_action: str = ""
    task_local_schema: dict[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()


class UnknownDomainResolver:
    """Deterministic degradation path for tasks where no confirmed pack exists.

    Strategy:
    1. Parse harness contract and invocation
    2. Replay/rank local seeds
    3. Extract parser reads/guards/selectors from AST (via api_reachability)
    4. Build partial input provenance and generic structural model
    5. Submit minimal seed to get oracle feedback
    6. Use run/GDB/coverage to locate frontier
    7. Narrow target field via observed comparison/value
    8. Generate task-local schema/recipe (data, no arbitrary code execution)
    9. Validation + submit loop
    """

    def resolve(self, evidence: EvidenceView, state: Any = None) -> UnknownDomainResult:
        """Resolve an unknown domain task.

        Returns an UnknownDomainResult with the best-effort degradation path.
        """
        result = UnknownDomainResult()

        # Step 1: Parse harness contract
        if evidence.harness_input_contract:
            result.harness_status = "confirmed"
            result.evidence_ids = result.evidence_ids + ("harness_input_contract",)
        elif evidence.harness_protocols:
            result.harness_status = "partial"
            result.evidence_ids = result.evidence_ids + ("harness_protocols",)

        # Step 2: Check corpus
        if evidence.corpus_files:
            result.parser_accept = "hit"
            result.evidence_ids = result.evidence_ids + ("corpus_files",)

            # Read dispatch selector from first corpus file
            if state is not None:
                result.dispatch_selector = _extract_dispatch_selector(state, evidence)

        # Step 3: Check api_reachability for parser guards
        if state is not None:
            metadata = getattr(state, "metadata", {}) or {}
            api_reach = metadata.get("api_reachability", {})
            if api_reach:
                result.record_length_field = _infer_length_field(api_reach)
                result.checksum_algorithm = _infer_checksum_algorithm(api_reach)

        # Step 4: Determine next action
        if result.harness_status == "confirmed" and result.parser_accept == "hit":
            if result.dispatch_selector:
                result.next_action = "modify selector only; preserve seed; run frontier probe"
                result.status = "partial_schema"
            else:
                result.next_action = "extract dispatch selector from harness source"
                result.status = "unresolved"
        elif result.harness_status == "confirmed":
            result.next_action = "submit minimal seed to get oracle feedback"
            result.status = "unresolved"
        elif result.parser_accept == "hit":
            result.next_action = "confirm harness input contract"
            result.status = "unresolved"
        else:
            result.next_action = "inspect corpus and harness to determine format"
            result.status = "unresolved"

        # Build task-local schema
        result.task_local_schema = {
            "carrier_format": result.carrier_format,
            "harness_status": result.harness_status,
            "dispatch_selector": result.dispatch_selector,
            "record_length_field": result.record_length_field,
            "checksum_algorithm": result.checksum_algorithm,
            "next_action": result.next_action,
        }

        return result


def _extract_dispatch_selector(state: Any, evidence: EvidenceView) -> str:
    """Extract dispatch selector from harness protocols and corpus."""
    if not evidence.harness_protocols:
        return ""

    proto = evidence.harness_protocols[0]
    selectors = proto.get("selector_fields", [])
    if selectors:
        parts = []
        for sel in selectors[:3]:
            field_name = sel.get("field", "")
            observed = sel.get("observed_value", "")
            if field_name:
                part = f"field={field_name}"
                if observed:
                    part += f" observed={observed}"
                parts.append(part)
        return "; ".join(parts)

    # Fallback: infer from input_contract
    contract = evidence.harness_input_contract
    if contract:
        return f"contract={contract}"

    return ""


def _infer_length_field(api_reachability: dict[str, Any]) -> str:
    """Infer length field location from API reachability data."""
    # Look for length-related API calls
    for harness_api in api_reachability.get("harness_apis", []):
        for api in harness_api.get("reachable_apis", []):
            api_lower = str(api).lower()
            if "length" in api_lower or "size" in api_lower or "count" in api_lower:
                return f"api_hint:{api}"
    return ""


def _infer_checksum_algorithm(api_reachability: dict[str, Any]) -> str:
    """Infer checksum algorithm from API reachability data."""
    for harness_api in api_reachability.get("harness_apis", []):
        for api in harness_api.get("reachable_apis", []):
            api_lower = str(api).lower()
            if "crc32" in api_lower:
                return "crc32"
            if "checksum" in api_lower:
                return "checksum"
            if "adler" in api_lower:
                return "adler32"
    return "unresolved"
