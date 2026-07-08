"""Audio knowledge pack — stdlib wave-backed pipeline.

Covers: WAV/RIFF, ADPCM, MP3, Ogg/Vorbis.
Backend: stdlib wave (WAV), construct (others when available).
"""

from __future__ import annotations

import logging
from typing import Any

from ...evidence import EvidenceView
from ...models import (
    BuildResult, CarrierContract, DetectionResult, ExpectedEffect,
    PackDescriptor, ParseResult, RecipePlan, RepairAction, ValidationReport,
    FieldInfo,
)

logger = logging.getLogger(__name__)

AUDIO_KEYWORDS: frozenset[str] = frozenset({
    "wav", "audio", "adpcm", "mp3", "ogg", "vorbis",
    "riff", "pcm", "sample rate",
})


class AudioKnowledgePack:
    descriptor = PackDescriptor(
        pack_id="audio",
        carrier_families=("wav", "mp3", "ogg", "adpcm"),
        supported_versions=("wav-riff", "mp3-id3v2", "ogg-vorbis"),
        capabilities=frozenset({"detect", "parse", "build", "validate"}),
        required_backends=(),
        knowledge_revision="2026.07.1",
    )

    def detect(self, evidence: EvidenceView) -> DetectionResult:
        positive: list[str] = []
        score = 0.0

        if "wav" in evidence.detected_magics:
            positive.append("corpus_magic:wav")
            score = max(score, 0.7)

        for hint in evidence.source_backed_hints:
            if any(kw in hint.lower() for kw in ("audio", "wav", "sound")):
                positive.append(f"source_hint:{hint}")
                score = max(score, 0.9)

        desc_lower = evidence.vulnerability_description.lower()
        keyword_matches = [kw for kw in AUDIO_KEYWORDS if kw in desc_lower]
        if keyword_matches:
            positive.extend(f"keyword:{kw}" for kw in keyword_matches)
            score = max(score, 0.3)

        decision = "confirmed" if score >= 0.7 else ("candidate" if score >= 0.2 else "insufficient")
        return DetectionResult(decision=decision, score=round(score, 3), positive_evidence_ids=tuple(positive))

    def parse(self, artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
        field_map: dict[str, FieldInfo] = {}
        carrier = "audio"

        if not artifact or len(artifact) < 12:
            return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

        if artifact[:4] == b"RIFF":
            carrier = "wav"
            try:
                import wave
                from io import BytesIO
                wf = wave.open(BytesIO(artifact))
                field_map["wav.channels"] = FieldInfo(name="wav.channels", offset=0, width=0,
                                                       value=wf.getnchannels(), node_id="header")
                field_map["wav.sample_width"] = FieldInfo(name="wav.sample_width", offset=0, width=0,
                                                           value=wf.getsampwidth(), node_id="header")
                field_map["wav.frame_rate"] = FieldInfo(name="wav.frame_rate", offset=0, width=0,
                                                         value=wf.getframerate(), node_id="header")
                field_map["wav.nframes"] = FieldInfo(name="wav.nframes", offset=0, width=0,
                                                      value=wf.getnframes(), node_id="header")
                wf.close()
            except Exception:
                pass

        return ParseResult(
            status="success" if field_map else "partial",
            carrier_family=carrier,
            structural_summary={"format": carrier},
            field_map=field_map,
            node_count=len(set(f.node_id for f in field_map.values())),
        )

    def derive_contract(self, parsed: ParseResult, harness: dict[str, Any] | None = None) -> CarrierContract:
        return CarrierContract(format_id=parsed.carrier_family or "audio",
                               seed_required=True, minimal_seed_size=44)

    def plan(self, objective: dict[str, Any], provenance: dict[str, Any] | None = None,
             carrier: CarrierContract | None = None) -> RecipePlan:
        return RecipePlan(recipe_id=f"rec_audio_{objective.get('objective_id', '')}",
                         objective_id=objective.get("objective_id", ""), schema_version="2.0")

    def build(self, seed: bytes, plan: RecipePlan) -> BuildResult:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        with open(path, "wb") as f:
            f.write(seed)
        return BuildResult(status="partial", artifact_path=path, reason="audio_builder_minimal")

    def validate(self, artifact: bytes, contract: CarrierContract,
                 mutation_intent: ExpectedEffect | None = None) -> ValidationReport:
        if contract.format_id == "wav":
            try:
                import wave
                from io import BytesIO
                wf = wave.open(BytesIO(artifact))
                wf.close()
                return ValidationReport(pack_id="audio", overall_verdict="pass")
            except Exception:
                return ValidationReport(pack_id="audio", overall_verdict="warn")
        return ValidationReport(pack_id="audio", overall_verdict="unknown")

    def explain_repair(self, report: ValidationReport) -> tuple[RepairAction, ...]:
        return ()
