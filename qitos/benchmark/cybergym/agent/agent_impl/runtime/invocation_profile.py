"""Conservative target invocation profile derivation for dynamic tools."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any, Literal

from .staged_binary import StagedBinaryCapability


InvocationMode = Literal["argv_file", "stdin", "unknown"]


@dataclass(frozen=True)
class InvocationProfile:
    """How a generated candidate should be passed to the staged binary."""

    binary_path: str
    mode: InvocationMode
    fixed_args: tuple[str, ...] = ()
    candidate_arg_index: int | None = None
    cwd: str = "/"
    library_path: str | None = None
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["digest"] = self.digest()
        return payload

    def digest(self) -> str:
        material = "|".join(
            [
                self.binary_path,
                self.mode,
                "\0".join(self.fixed_args),
                "" if self.candidate_arg_index is None else str(self.candidate_arg_index),
                self.cwd,
                self.library_path or "",
            ]
        )
        return hashlib.blake2s(material.encode("utf-8", errors="replace"), digest_size=8).hexdigest()


def build_invocation_profile(
    state: Any,
    capability: StagedBinaryCapability | dict[str, Any],
) -> InvocationProfile:
    """Derive the safest known invocation profile from current state.

    Unknown is a valid result.  Dynamic tools must refuse to execute an unknown
    profile instead of guessing argv/stdin and misclassifying usage errors.
    """

    cap = _coerce_capability(capability)
    binary_path = str(cap.binary_path or "")
    if not cap.available or not binary_path:
        return InvocationProfile(
            binary_path=binary_path,
            mode="unknown",
            library_path=cap.library_path,
            confidence=0.0,
            reason=cap.reason or "staged_binary_unavailable",
        )

    fmt = getattr(state, "input_format", None)
    consumption = getattr(fmt, "consumption", None) if fmt else None

    input_path = str(getattr(fmt, "input_path", "") or "").strip().lower() if fmt else ""
    if input_path in {"stdin", "standard_input"}:
        return _profile(binary_path, "stdin", cap, 0.80, f"input_format.input_path={input_path}")
    if input_path in {"file_argv", "argv_file", "file", "path"}:
        return _profile(binary_path, "argv_file", cap, 0.75, f"input_format.input_path={input_path}")

    endpoint_scope = str(getattr(consumption, "endpoint_scope", "") or "").strip().lower() if consumption else ""
    if endpoint_scope in {"stdin", "command"}:
        return _profile(binary_path, "stdin", cap, 0.72, f"harness endpoint_scope={endpoint_scope}")
    if endpoint_scope in {"file", "buffer", "packet", "callback", "apdu"}:
        return _profile(binary_path, "argv_file", cap, 0.65, f"harness endpoint_scope={endpoint_scope}")

    protocols = list(getattr(state, "harness_protocols", []) or [])
    for proto in protocols:
        if not isinstance(proto, dict):
            continue
        proto_scope = str(proto.get("endpoint_scope") or "").strip().lower()
        proto_contract = str(proto.get("input_contract") or "").strip().lower()
        if proto_scope in {"stdin", "command"} or proto_contract == "command":
            return _profile(binary_path, "stdin", cap, 0.68, f"harness_protocol={proto_contract or proto_scope}")
        if proto_scope in {"file", "packet", "callback", "apdu"} or proto_contract in {"buffer", "multi_record", "packet_stack", "apdu"}:
            return _profile(binary_path, "argv_file", cap, 0.62, f"harness_protocol={proto_contract or proto_scope}")

    selected = _selected_harness_candidate(state)
    entry_function = str(getattr(selected, "entry_function", "") or "")
    if entry_function == "LLVMFuzzerTestOneInput":
        return _profile(binary_path, "argv_file", cap, 0.70, "libFuzzer entry LLVMFuzzerTestOneInput")

    harness_info = str(getattr(state, "harness_info", "") or "").lower()
    if "stdin" in harness_info:
        return _profile(binary_path, "stdin", cap, 0.45, "harness_info mentions stdin")
    if "@@" in harness_info or "argv" in harness_info:
        return _profile(binary_path, "argv_file", cap, 0.45, "harness_info mentions argv/file placeholder")

    return InvocationProfile(
        binary_path=binary_path,
        mode="unknown",
        library_path=cap.library_path,
        confidence=0.0,
        reason="invocation_unresolved",
    )


def _profile(
    binary_path: str,
    mode: InvocationMode,
    cap: StagedBinaryCapability,
    confidence: float,
    reason: str,
) -> InvocationProfile:
    candidate_arg_index = 0 if mode == "argv_file" else None
    return InvocationProfile(
        binary_path=binary_path,
        mode=mode,
        fixed_args=(),
        candidate_arg_index=candidate_arg_index,
        cwd="/",
        library_path=cap.library_path,
        confidence=confidence,
        reason=reason,
    )


def _coerce_capability(capability: StagedBinaryCapability | dict[str, Any]) -> StagedBinaryCapability:
    if isinstance(capability, StagedBinaryCapability):
        return capability
    return StagedBinaryCapability(
        available=bool(capability.get("available")),
        binary_path=capability.get("binary_path"),
        binary_candidates=tuple(str(p) for p in capability.get("binary_candidates", ()) or ()),
        library_path=capability.get("library_path"),
        gdb_available=bool(capability.get("gdb_available")),
        reason=str(capability.get("reason") or ""),
        source=str(capability.get("source") or "unavailable"),
    )


def _selected_harness_candidate(state: Any) -> Any:
    resolution = getattr(state, "harness_resolution", None)
    selected_id = str(getattr(resolution, "selected_candidate_id", "") or "")
    candidates = list(getattr(state, "harness_candidates", []) or [])
    if selected_id:
        for candidate in candidates:
            if getattr(candidate, "candidate_id", "") == selected_id:
                return candidate
    return candidates[0] if candidates else None
