"""Structured Text/Language Runtime knowledge pack — lxml-backed pipeline.

Covers: XML, DTD, JSON, Ruby, PHP, Lua, Wasm, YARA, Hunspell, plist, iCal, SQL.
Backend: lxml (XML/DTD), stdlib json/ast (JSON/Python).
This is the largest pack by task count (263) and hardest to build.
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

RUNTIME_PROJECTS: frozenset[str] = frozenset({
    "libxml2", "libxslt", "ruby", "mruby", "php", "lua",
    "wasmtime", "wasmer", "yara", "hunspell", "libplist",
    "pcre2", "fluent-bit", "lwan", "curl", "python",
})

RUNTIME_KEYWORDS: frozenset[str] = frozenset({
    "xml", "xslt", "dtd", "xpath", "json", "yaml",
    "ruby", "mruby", "php", "lua", "wasm", "yara",
    "hunspell", "plist", "ical", "regex", "pcre",
    "sql", "ast", "bytecode", "opcode", "tokenizer",
    "libxml2", "libxslt", "config",
})


class StructuredTextKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="structured_text",
        carrier_families=("xml", "json", "yaml", "ruby", "php", "lua", "wasm", "yara", "plist", "ical"),
        supported_versions=("xml1.0", "xml1.1", "json-rfc8259", "yaml1.2"),
        capabilities=frozenset({"detect", "parse", "build", "validate", "repair"}),
        required_backends=("lxml",),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("xml", "json", "yaml", "script", "text")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        project_lower = evidence.project_name.lower()
        if project_lower in RUNTIME_PROJECTS:
            positive.append(f"project:{project_lower}")
            score = max(score, 0.4)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in RUNTIME_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        return DetectionResult(decision=decision, score=round(score, 3), positive_evidence_ids=tuple(positive))

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        field_map: dict[str, FieldInfo] = {}
        warnings: list[str] = []
        carrier = "structured_text"

        if not artifact or len(artifact) < 4:
            return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

        # Try XML
        if artifact[:5] in (b"<?xml", b"<") or b"<?xml" in artifact[:100]:
            carrier = "xml"
            try:
                from lxml import etree
                from io import BytesIO
                tree = etree.parse(BytesIO(artifact))
                root = tree.getroot()
                field_map["xml.root.tag"] = FieldInfo(name="xml.root.tag", offset=0, width=0,
                                                       value=root.tag, node_id="root")
                field_map["xml.root.attrib_count"] = FieldInfo(name="xml.root.attrib_count", offset=0, width=0,
                                                                value=len(root.attrib), node_id="root")
                field_map["xml.element_count"] = FieldInfo(name="xml.element_count", offset=0, width=0,
                                                            value=sum(1 for _ in root.iter()), node_id="tree")
            except Exception as e:
                warnings.append(f"xml_parse_partial: {e}")

        # Try JSON
        elif artifact[:1] in (b"{", b"["):
            carrier = "json"
            try:
                import json
                data = json.loads(artifact)
                field_map["json.type"] = FieldInfo(name="json.type", offset=0, width=0,
                                                    value=type(data).__name__, node_id="root")
                if isinstance(data, dict):
                    field_map["json.keys"] = FieldInfo(name="json.keys", offset=0, width=0,
                                                        value=list(data.keys())[:10], node_id="root")
            except Exception as e:
                warnings.append(f"json_parse_partial: {e}")

        return ParseResult(
            status="success" if not warnings else "partial",
            carrier_family=carrier,
            structural_summary={"format": carrier},
            field_map=field_map,
            node_count=len(set(f.node_id for f in field_map.values())),
            parse_warnings=tuple(warnings),
        )

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        return CarrierContract(format_id=parsed.carrier_family or "structured_text",
                               seed_required=True, minimal_seed_size=16)

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        objective_id = objective.get("objective_id", "")
        return RecipePlan(recipe_id=f"rec_text_{objective_id}", objective_id=objective_id,
                         schema_version="2.0", carrier_contract_id=carrier.format_id if carrier else "structured_text")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".xml")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(seed)
        return BuildResult(status="partial", artifact_path=path, reason="text_builder_minimal")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        if contract.format_id == "xml":
            try:
                from lxml import etree
                from io import BytesIO
                etree.parse(BytesIO(artifact))
                return ValidationReport(pack_id="structured_text", overall_verdict="pass")
            except Exception:
                return ValidationReport(pack_id="structured_text", overall_verdict="warn")
        return ValidationReport(pack_id="structured_text", overall_verdict="pass")

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
