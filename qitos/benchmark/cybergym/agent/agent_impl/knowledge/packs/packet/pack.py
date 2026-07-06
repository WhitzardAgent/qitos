"""Packet/TLV knowledge pack — full detect→parse→derive_contract→plan→build→validate→explain_repair pipeline.

Backed by Scapy.  Gracefully degrades when Scapy is not installed.

Detection is evidence-based:
- corpus magic → confirmed (Ethernet/IP headers)
- project_name in PACKET_PROJECTS → candidate
- harness input_contract "packet" → confirmed
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
from .builder import build_packet_candidate
from .parser import parse_packet
from .repairs import explain_packet_repairs
from .validator import validate_packet_candidate

logger = logging.getLogger(__name__)

PACKET_PROJECTS: frozenset[str] = frozenset({
    "wireshark", "tcpdump", "scapy", "libpcap",
    "openthread", "freeradius", "openvpn",
    "openssh", "curl", "wget", "nginx",
})

PACKET_KEYWORDS: frozenset[str] = frozenset({
    "packet", "dissector", "pcap", "protocol", "tlv",
    "spinel", "apdu", "ethernet", "udp", "tcp",
    "wireshark", "openthread", "ncp",
})

PACKET_HARNESS_PATTERNS: frozenset[str] = frozenset({
    "fuzz_packet", "fuzz_dissector", "fuzz_pcap",
    "fuzz_tlv", "fuzz_spinel", "fuzz_apdu",
    "packet_fuzzer", "dissect_fuzz",
})

# Harness input contracts that indicate packet domain
PACKET_INPUT_CONTRACTS: frozenset[str] = frozenset({
    "packet", "pcap", "dissector", "spinel", "apdu", "tlv",
})


class PacketKnowledgePack:
    """Executable knowledge pack for packet/TLV formats."""

    descriptor = PackDescriptor(
        pack_id="packet",
        carrier_families=("ethernet", "ip_packet", "pcap", "tlv", "spinel", "apdu"),
        supported_versions=("eth2", "802.3", "ipv4", "ipv6", "spinel", "apdu"),
        capabilities=frozenset({"detect", "parse", "build", "validate", "repair", "transcript"}),
        required_backends=("scapy",),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        """Evidence-based detection for packet/TLV domain."""
        positive: list[str] = []
        contradictory: list[str] = []
        missing: list[str] = []
        score = 0.0

        # Source-backed hints
        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("packet", "pcap", "protocol", "spinel", "apdu", "tlv")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        # Harness input_contract — strong evidence
        if evidence.harness_input_contract in PACKET_INPUT_CONTRACTS:
            positive.append(f"harness_input_contract:{evidence.harness_input_contract}")
            score = max(score, 0.8)

        # Harness carrier_stack
        for carrier in evidence.harness_carrier_stack:
            if carrier.lower() in PACKET_INPUT_CONTRACTS:
                positive.append(f"harness_carrier:{carrier}")
                score = max(score, 0.8)

        # Harness API
        for api in evidence.harness_api_calls:
            api_lower = api.lower()
            if any(pat in api_lower for pat in PACKET_HARNESS_PATTERNS):
                positive.append(f"harness_api:{api}")
                score = max(score, 0.8)

        # Project name (weak)
        project_lower = evidence.project_name.lower()
        if project_lower in PACKET_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        # Description keywords (weak)
        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in PACKET_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        # Decision
        if score >= 0.7:
            decision = "confirmed"
        elif score >= 0.2:
            decision = "candidate"
        elif positive:
            decision = "candidate"
        else:
            decision = "insufficient"

        if score < 0.7:
            if not evidence.harness_input_contract:
                missing.append("harness_input_contract")
            if not evidence.harness_api_calls:
                missing.append("harness_api_calls")

        return DetectionResult(
            decision=decision,
            score=round(score, 3),
            positive_evidence_ids=tuple(positive),
            contradictory_evidence_ids=tuple(contradictory),
            missing_evidence=tuple(missing),
        )

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        return parse_packet(artifact, context)

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

        # Checksums are derived
        for name in parsed.field_map:
            if "chksum" in name.lower() or "checksum" in name.lower():
                if name not in derived_fields:
                    derived_fields.append(name)

        # Selector fields (protocol, ports) are protected from random mutation
        for name in parsed.field_map:
            if any(sel in name for sel in ("proto", "dport", "sport", "type")):
                protected_fields.append(name)

        hints: list[str] = []
        if harness:
            ic = harness.get("input_contract", "")
            if ic:
                hints.append(f"harness_expects_{ic}")
            # Selector fields from harness
            selectors = harness.get("selector_fields", [])
            for sel in selectors:
                field = sel.get("field", "")
                if field:
                    hints.append(f"selector:{field}")

        return CarrierContract(
            format_id=parsed.carrier_family or "packet",
            seed_required=True,
            minimal_seed_size=64,
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
                op_id="op_packet_overflow",
                kind="mutate_field",
                target_node_id=target_node or "layer_ip",
                invalidated_derivations=("packet.*.chksum",),
                rollback_hint="restore original field value",
                evidence_id=f"objective:{objective_id}",
                ast_transform={"key": "len", "value": overflow_value},
            ))
            invariants.append(Invariant(
                invariant_id="inv_packet_length",
                kind="length",
                expression="IP total length must be consistent with actual data",
                protected=False,
            ))

        elif objective_kind in ("selector_error", "dissector_crash"):
            operations.append(RecipeOperation(
                op_id="op_packet_selector",
                kind="set_field",
                target_node_id=target_node or "layer_udp",
                invalidated_derivations=("packet.*.chksum",),
                rollback_hint="restore original selector value",
                evidence_id=f"objective:{objective_id}",
                ast_transform={"key": "dport", "value": objective.get("selector_value", 0)},
            ))

        elif objective_kind in ("tlv_error", "protocol_error"):
            operations.append(RecipeOperation(
                op_id="op_tlv_corrupt",
                kind="mutate_field",
                target_node_id=target_node or "payload",
                rollback_hint="restore original TLV bytes",
                evidence_id=f"objective:{objective_id}",
            ))

        else:
            if target_node:
                operations.append(RecipeOperation(
                    op_id=f"op_mutate_{target_node}",
                    kind="mutate_field",
                    target_node_id=target_node,
                    evidence_id=f"objective:{objective_id}",
                ))

        # Checksum recomputation is always needed after packet mutation
        if operations:
            invariants.append(Invariant(
                invariant_id="inv_checksum",
                kind="checksum",
                expression="IP/TCP/UDP checksums must be recomputed after mutation",
                protected=True,
            ))

        carrier_info: dict[str, Any] = {}
        if provenance:
            carrier_info = {
                "format": provenance.get("format", "packet"),
                "seed_path": provenance.get("seed_path", ""),
            }

        return RecipePlan(
            recipe_id=f"rec_packet_{objective_id}",
            schema_version="2.0",
            objective_id=objective_id,
            carrier_contract_id=carrier.format_id if carrier else "packet",
            seed_id=provenance.get("seed_path", "") if provenance else "",
            operations=tuple(operations),
            invariants=tuple(invariants),
            expected_effects=tuple(effects),
            carrier=carrier_info,
        )

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        return build_packet_candidate(seed, plan)

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
            fd, candidate_path = tempfile.mkstemp(suffix=".bin")
            import os
            with os.fdopen(fd, "wb") as f:
                f.write(artifact if isinstance(artifact, bytes) else artifact.encode())

        return validate_packet_candidate(candidate_path, contract, mutation_intent)

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return explain_packet_repairs(report)


def _infer_target_node(objective: dict[str, Any]) -> str:
    desc = (objective.get("description", "") or objective.get("kind", "")).lower()
    if "tcp" in desc:
        return "layer_tcp"
    if "udp" in desc:
        return "layer_udp"
    if "ip" in desc:
        return "layer_ip"
    if "ethernet" in desc or "ether" in desc:
        return "layer_ether"
    if "spinel" in desc or "tlv" in desc:
        return "payload"
    return ""
