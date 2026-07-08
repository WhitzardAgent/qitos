"""Crypto/Smartcard knowledge pack — cryptography-backed pipeline.

Covers: APDU, DER/ASN.1, X.509, TLS, PKCS#15, OpenPGP, NDR/RPC, TPM, SSH.
Backend: cryptography (DER/ASN.1/X.509).
"""

from __future__ import annotations

import logging
from typing import Any

from ...evidence import EvidenceView
from ...models import (
    BuildResult, CarrierContract, DetectionResult, ExpectedEffect,
    PackDescriptor, ParseResult, RecipePlan, RepairAction, ValidationReport,
)

logger = logging.getLogger(__name__)

CRYPTO_PROJECTS: frozenset[str] = frozenset({
    "opensc", "wolfssl", "samba", "tpm2-tss", "openssh",
    "gnupg", "libgcrypt", "openssl", "boringssl",
})

CRYPTO_KEYWORDS: frozenset[str] = frozenset({
    "apdu", "smartcard", "der", "asn1", "x509", "certificate",
    "tls", "ssl", "pkcs", "openpgp", "ndr", "rpc",
    "tpm", "ssh", "wolfssl", "opensc", "crypto",
    "cipher", "signature", "key exchange",
})


class CryptoKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="crypto",
        carrier_families=("apdu", "der", "asn1", "x509", "tls", "pkcs", "openpgp", "ndr", "tpm", "ssh"),
        supported_versions=("apdu-iso7816", "der-ber", "x509v3", "tls1.3"),
        capabilities=frozenset({"detect", "parse", "build", "validate"}),
        required_backends=("cryptography",),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("crypto", "tls", "apdu", "certificate")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        if evidence.harness_input_contract in ("apdu", "tls", "crypto"):
            positive.append(f"harness_input_contract:{evidence.harness_input_contract}")
            score = max(score, 0.8)

        project_lower = evidence.project_name.lower()
        if project_lower in CRYPTO_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in CRYPTO_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        return DetectionResult(decision=decision, score=round(score, 3), positive_evidence_ids=tuple(positive))

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        return ParseResult(status="backend_unavailable", carrier_family="crypto",
                           parse_warnings=("crypto_parse_not_implemented",))

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        return CarrierContract(format_id="crypto", seed_required=True, minimal_seed_size=16)

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        return RecipePlan(recipe_id=f"rec_crypto_{objective.get('objective_id', '')}",
                         objective_id=objective.get("objective_id", ""), schema_version="2.0")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        return BuildResult(status="backend_unavailable", reason="crypto builder not implemented")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        return ValidationReport(pack_id="crypto", overall_verdict="unknown")

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
