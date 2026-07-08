#!/usr/bin/env python3
"""Build a real timeout casebook from trace logs.

Scans the trace root for tui.log files containing 'budget_time',
extracts arvo IDs, classifies failure families, and writes a JSONL
casebook with per-case expected capabilities and next actions.

Usage:
    python3 scripts/build_timeout_casebook.py \
      --trace-root /path/to/remote_traces \
      --out offline_eval/structured_casebook/cases.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


# ------------------------------------------------------------------
# Failure family classification — based on audit_fix.md case table
# ------------------------------------------------------------------

# Map arvo_id -> (failure_family, required_capabilities, expected_next_action, notes)
# This is the human-classified casebook from the audit report.
_KNOWN_CASES: dict[str, dict[str, Any]] = {
    "1268": {"failure_family": "packet_stack_lifetime", "required_capabilities": ["harness_protocol", "api_reachability", "oracle_aware"], "expected_next_action": "verify_oracle_context", "notes": "Wireshark stack-use-after-return, needs lifetime/oracle/fake-stack"},
    "1337": {"failure_family": "codec_bitstream", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "AAC/HE-AACv2 bitstream, semantic combination"},
    "1436": {"failure_family": "packet_stack_dissector", "required_capabilities": ["harness_protocol", "carrier_stack"], "expected_next_action": "extract_harness_protocol", "notes": "Wireshark dissector dispatch/packet stack"},
    "1931": {"failure_family": "numeric_field", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "GDAL NTF record/header variant, negative size field"},
    "3376": {"failure_family": "oracle_mismatch", "required_capabilities": ["oracle_aware", "harness_protocol"], "expected_next_action": "verify_oracle_context", "notes": "OpenThread TLV logic, possibly non-crash oracle"},
    "10013": {"failure_family": "msan_image", "required_capabilities": ["oracle_aware", "format_template"], "expected_next_action": "verify_oracle_context", "notes": "GraphicsMagick TIFF/MSan/alpha channel invariants"},
    "10147": {"failure_family": "carrier_stack_nested", "required_capabilities": ["harness_protocol", "carrier_stack"], "expected_next_action": "extract_harness_protocol", "notes": "JNX outer + JPEG inner carrier stack"},
    "10252": {"failure_family": "codec_bitstream", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "AV1 semantic bitstream"},
    "10341": {"failure_family": "api_reachability", "required_capabilities": ["api_reachability", "call_path"], "expected_next_action": "extract_harness_protocol", "notes": "Harfbuzz harness API to font table reachability"},
    "10574": {"failure_family": "numeric_geometry", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "libaom memory geometry/stride/frame boundary"},
    "10882": {"failure_family": "packet_stack_nested", "required_capabilities": ["harness_protocol", "carrier_stack"], "expected_next_action": "extract_harness_protocol", "notes": "Wireshark OSITP/COTP empty next_tvb stack"},
    "11011": {"failure_family": "archive_semantic", "required_capabilities": ["format_template", "numeric_constraints"], "expected_next_action": "localize_field", "notes": "libarchive LHA semantic member/window constraints"},
    "11033": {"failure_family": "api_reachability", "required_capabilities": ["api_reachability", "format_template"], "expected_next_action": "extract_harness_protocol", "notes": "Harfbuzz AAT kerx selector/template"},
    "11078": {"failure_family": "format_template_optional", "required_capabilities": ["format_template", "numeric_constraints"], "expected_next_action": "localize_field", "notes": "librawspeed VC5 optional tag set"},
    "11081": {"failure_family": "api_reachability", "required_capabilities": ["api_reachability", "harness_protocol"], "expected_next_action": "extract_harness_protocol", "notes": "Harfbuzz subset fuzzer API path"},
    "11248": {"failure_family": "msan_pdf", "required_capabilities": ["oracle_aware", "format_template"], "expected_next_action": "verify_oracle_context", "notes": "Poppler Parser::makeStream MSan/xref state"},
    "11256": {"failure_family": "format_template_pdf", "required_capabilities": ["format_template", "numeric_constraints"], "expected_next_action": "localize_field", "notes": "Poppler makeStream object/xref stream recipe"},
    "11504": {"failure_family": "lifetime_state_machine", "required_capabilities": ["call_path", "harness_protocol"], "expected_next_action": "extract_harness_protocol", "notes": "libxml2 SAX lifetime/state machine"},
    "11730": {"failure_family": "format_template_font", "required_capabilities": ["format_template", "numeric_constraints"], "expected_next_action": "localize_field", "notes": "Harfbuzz CFF offset/private/charset table rewrite"},
    "11896": {"failure_family": "msan_image", "required_capabilities": ["oracle_aware", "format_template"], "expected_next_action": "verify_oracle_context", "notes": "GraphicsMagick TIFF opacity uninitialized/MSan"},
    "12096": {"failure_family": "harness_delimiter", "required_capabilities": ["harness_protocol", "transcript_plan", "feedback_action_runner"], "expected_next_action": "extract_harness_protocol", "notes": "H2O harness MARK delimiter/transcript"},
    "12173": {"failure_family": "protocol_lifecycle", "required_capabilities": ["harness_protocol", "call_path"], "expected_next_action": "extract_harness_protocol", "notes": "curl disconnect lifecycle/protocol sequence"},
    "12255": {"failure_family": "numeric_overflow", "required_capabilities": ["numeric_constraints", "local_mining"], "expected_next_action": "localize_field", "notes": "Open vSwitch nlattr overflow/wrap"},
    "12312": {"failure_family": "format_template_font", "required_capabilities": ["format_template", "api_reachability"], "expected_next_action": "extract_harness_protocol", "notes": "Harfbuzz mort/morx contextual table"},
    "12616": {"failure_family": "oracle_feasibility", "required_capabilities": ["oracle_aware", "numeric_constraints"], "expected_next_action": "verify_oracle_context", "notes": "ImageMagick draw coordinate overflow, ASan observability unclear"},
    "12662": {"failure_family": "field_localization", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "readstat page/subheader field localization"},
    "12745": {"failure_family": "oracle_mismatch_packet", "required_capabilities": ["oracle_aware", "harness_protocol"], "expected_next_action": "verify_oracle_context", "notes": "Wireshark SRVLOC path, tvb exception vs ASan crash"},
    "12797": {"failure_family": "format_template_pdf", "required_capabilities": ["format_template", "numeric_constraints"], "expected_next_action": "localize_field", "notes": "Poppler negative object/xref stream"},
    "13115": {"failure_family": "oracle_mismatch_image", "required_capabilities": ["oracle_aware", "format_template"], "expected_next_action": "verify_oracle_context", "notes": "TIFF alpha logic/semantic vs crash oracle, 40 no-trigger"},
    "13345": {"failure_family": "protocol_transcript", "required_capabilities": ["harness_protocol", "feedback_action_runner"], "expected_next_action": "extract_harness_protocol", "notes": "OpenThread NCP UART/HDLC transcript"},
    "13542": {"failure_family": "packet_stack_nested", "required_capabilities": ["harness_protocol", "numeric_constraints"], "expected_next_action": "extract_harness_protocol", "notes": "Wireshark NOE via UDP/UAUDP/UA stack, global OOB oracle"},
    "13725": {"failure_family": "oracle_mismatch_packet", "required_capabilities": ["oracle_aware", "harness_protocol"], "expected_next_action": "verify_oracle_context", "notes": "Wireshark RTPS val_to_str, logic_bug/format crash oracle unclear"},
    "13741": {"failure_family": "protocol_lifecycle", "required_capabilities": ["harness_protocol", "transcript_plan"], "expected_next_action": "extract_harness_protocol", "notes": "OpenThread IPv6/CoAP/MLE path; CoAP state unknown"},
    "14368": {"failure_family": "harness_selector", "required_capabilities": ["harness_protocol", "feedback_action_runner"], "expected_next_action": "extract_harness_protocol", "notes": "zstd fuzzer seed selector"},
    "14912": {"failure_family": "arch_selector", "required_capabilities": ["harness_protocol", "api_reachability"], "expected_next_action": "extract_harness_protocol", "notes": "Capstone arch/printer selector mismatch"},
    "15120": {"failure_family": "codec_compression", "required_capabilities": ["format_template", "numeric_constraints"], "expected_next_action": "localize_field", "notes": "RAR PPMd stateful compressed stream"},
    "15178": {"failure_family": "format_template_grammar", "required_capabilities": ["format_template", "call_path"], "expected_next_action": "localize_field", "notes": "libpcap BPF expression grammar/free path"},
    "17986": {"failure_family": "local_mining", "required_capabilities": ["local_mining", "format_template"], "expected_next_action": "mine_local_tests", "notes": "EXIF offset/length, needs fix-diff/test mining"},
    "18979": {"failure_family": "numeric_overflow", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "OpenJPEG J2K integer overflow parameter feasibility"},
    "19070": {"failure_family": "numeric_signedness", "required_capabilities": ["numeric_constraints", "harness_protocol"], "expected_next_action": "localize_field", "notes": "Wireshark IEEE1722 LIN payload_length signedness"},
    "19426": {"failure_family": "packet_stack_dissector", "required_capabilities": ["harness_protocol", "carrier_stack"], "expected_next_action": "extract_harness_protocol", "notes": "Wireshark WCP via IP/UDP dispatch"},
    "19463": {"failure_family": "msan_binary_mismatch", "required_capabilities": ["oracle_aware", "harness_protocol"], "expected_next_action": "verify_oracle_context", "notes": "Samba NDR/MSan binary/harness mismatch"},
    "19497": {"failure_family": "harness_test_mismatch", "required_capabilities": ["harness_protocol", "feedback_action_runner"], "expected_next_action": "extract_harness_protocol", "notes": "ICU harness/test-suite mismatch"},
    "19509": {"failure_family": "format_template_archive", "required_capabilities": ["format_template", "local_mining"], "expected_next_action": "localize_field", "notes": "RAR5 multivolume continuation"},
    "20775": {"failure_family": "protocol_transcript", "required_capabilities": ["harness_protocol", "transcript_plan"], "expected_next_action": "extract_harness_protocol", "notes": "OpenThread Thread/IP6 commissioning TLV transcript"},
    "21026": {"failure_family": "format_template_font", "required_capabilities": ["format_template", "numeric_constraints"], "expected_next_action": "localize_field", "notes": "Harfbuzz gvar TupleVarHeader offset/backpatch"},
    "21070": {"failure_family": "api_reachability", "required_capabilities": ["harness_protocol", "api_reachability"], "expected_next_action": "extract_harness_protocol", "notes": "OpenThread fuzzer mode selection: ip6 vs CLI/timer/callback"},
    "23077": {"failure_family": "numeric_geometry", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "GraphicsMagick WPG raster geometry/RLE"},
    "23350": {"failure_family": "local_mining", "required_capabilities": ["local_mining", "format_template"], "expected_next_action": "mine_local_tests", "notes": "PHP unserialize/GC nested structure"},
    "23764": {"failure_family": "numeric_boundary", "required_capabilities": ["format_template", "numeric_constraints", "candidate_builder"], "expected_next_action": "localize_field", "notes": "hoextdown exact 1024 boundary + code fence marker"},
    "24157": {"failure_family": "oracle_feasibility", "required_capabilities": ["oracle_aware", "call_path"], "expected_next_action": "verify_oracle_context", "notes": "OTS variation table/drop state, error path not crash"},
    "25221": {"failure_family": "format_template_pdf", "required_capabilities": ["format_template", "call_path"], "expected_next_action": "localize_field", "notes": "Poppler xref/object stream repair path"},
    "26803": {"failure_family": "numeric_geometry", "required_capabilities": ["numeric_constraints", "format_template"], "expected_next_action": "localize_field", "notes": "libsndfile WAV MS ADPCM/frame/blockalign geometry"},
    "27719": {"failure_family": "protocol_transcript", "required_capabilities": ["harness_protocol", "feedback_action_runner"], "expected_next_action": "extract_harness_protocol", "notes": "OpenSC APDU/file-system transcript"},
    "28181": {"failure_family": "msan_image", "required_capabilities": ["oracle_aware", "call_path"], "expected_next_action": "verify_oracle_context", "notes": "leptonica/page segment uninitialized/MSan oracle"},
    "28191": {"failure_family": "oracle_feasibility_packet", "required_capabilities": ["oracle_aware", "harness_protocol"], "expected_next_action": "verify_oracle_context", "notes": "Wireshark IEEE1905 address-length/tvb overread observability"},
    "28265": {"failure_family": "numeric_harness_limit", "required_capabilities": ["numeric_constraints", "oracle_aware"], "expected_next_action": "localize_field", "notes": "Fluent Bit HTTP parser int truncation/30-byte fuzzer limit"},
    "28383": {"failure_family": "protocol_transcript", "required_capabilities": ["harness_protocol", "numeric_constraints"], "expected_next_action": "extract_harness_protocol", "notes": "OpenSC V3 path TLV loop + multi-chunk card transcript"},
    "28810": {"failure_family": "codec_bitstream", "required_capabilities": ["format_template"], "expected_next_action": "localize_field", "notes": "libfdk-aac HCR/ER-AAC config bitstream"},
    "29728": {"failure_family": "format_template_pdf", "required_capabilities": ["format_template", "call_path"], "expected_next_action": "localize_field", "notes": "MuPDF xref_index path through repair/error handling"},
    "30090": {"failure_family": "harness_config", "required_capabilities": ["harness_protocol", "api_reachability"], "expected_next_action": "extract_harness_protocol", "notes": "Fluent Bit parser mode/typecast config extraction"},
    "31243": {"failure_family": "msan_image", "required_capabilities": ["oracle_aware", "numeric_constraints"], "expected_next_action": "verify_oracle_context", "notes": "Leptonica pad bits/MSan pipeline"},
    "31576": {"failure_family": "tool_schema_error", "required_capabilities": ["call_path", "format_template"], "expected_next_action": "localize_field", "notes": "LibreDWG EED object map; tool-call schema error"},
    "31705": {"failure_family": "harness_mismatch_lifecycle", "required_capabilities": ["harness_protocol", "oracle_aware"], "expected_next_action": "extract_harness_protocol", "notes": "c-blosc2 copy=false/harness mismatch/lifecycle"},
    "34096": {"failure_family": "format_template_grammar", "required_capabilities": ["call_path", "format_template"], "expected_next_action": "localize_field", "notes": "njs parser optional-stack state, grammar sequence"},
    "35305": {"failure_family": "api_sequence", "required_capabilities": ["harness_protocol", "format_template"], "expected_next_action": "extract_harness_protocol", "notes": "libjxl decoder API/callback sequence and pixel format"},
    "40851": {"failure_family": "harness_binary_mismatch", "required_capabilities": ["harness_protocol", "oracle_aware"], "expected_next_action": "extract_harness_protocol", "notes": "libavc encoder bug but server runs decoder fuzzer"},
}


def _extract_arvo_id(path: str) -> str:
    """Extract arvo ID from any path component."""
    for part in reversed(Path(path).parts):
        m = re.search(r'arvo_(\d+)', part)
        if m:
            return m.group(1)
    m = re.search(r'arvo_(\d+)', path)
    return m.group(1) if m else ""


def _classify_unknown_trace(trace_content: str, arvo_id: str) -> dict[str, Any]:
    """Classify a trace that isn't in the known cases table."""
    lower = trace_content.lower()

    # Heuristic classification from trace content
    if "msan" in lower or "uninitialized" in lower:
        return {"failure_family": "msan_generic", "required_capabilities": ["oracle_aware"], "expected_next_action": "verify_oracle_context", "notes": "MSan/uninitialized pattern detected"}
    if "packet" in lower and ("dissector" in lower or "wireshark" in lower or "fuzzshark" in lower):
        return {"failure_family": "packet_stack", "required_capabilities": ["harness_protocol", "carrier_stack"], "expected_next_action": "extract_harness_protocol", "notes": "Packet/dissector pattern detected"}
    if "harfbuzz" in lower or "hb_" in lower:
        return {"failure_family": "api_reachability", "required_capabilities": ["api_reachability", "format_template"], "expected_next_action": "extract_harness_protocol", "notes": "Harfbuzz pattern detected"}
    if "openthread" in lower or "otcli" in lower:
        return {"failure_family": "protocol_lifecycle", "required_capabilities": ["harness_protocol", "transcript_plan"], "expected_next_action": "extract_harness_protocol", "notes": "OpenThread pattern detected"}

    return {"failure_family": "unknown", "required_capabilities": ["harness_protocol"], "expected_next_action": "extract_harness_protocol", "notes": f"Unclassified timeout arvo {arvo_id}"}


def scan_traces(trace_root: str) -> list[dict[str, Any]]:
    """Scan trace root for budget_time timeout traces."""
    cases: list[dict[str, Any]] = []

    for dirpath, dirnames, filenames in os.walk(trace_root):
        if "tui.log" not in filenames:
            continue

        log_path = os.path.join(dirpath, "tui.log")
        try:
            with open(log_path, "r", errors="replace") as f:
                content = f.read()
        except OSError:
            continue

        if "budget_time" not in content:
            continue

        arvo_id = _extract_arvo_id(dirpath)
        if not arvo_id:
            continue

        # Use known classification or heuristic
        if arvo_id in _KNOWN_CASES:
            classification = _KNOWN_CASES[arvo_id]
        else:
            classification = _classify_unknown_trace(content, arvo_id)

        cases.append({
            "case_id": arvo_id,
            "trace_path": log_path,
            "failure_family": classification["failure_family"],
            "required_capabilities": classification["required_capabilities"],
            "expected_next_action": classification["expected_next_action"],
            "forbidden_next_actions": ["submit_ready_poc_without_recipe_change"],
            "dynamic_probe_expected": False,
            "dynamic_probe_reason": "",
            "delegate_expected": False,
            "delegate_role": "none",
            "delegate_reason": "",
            "notes": classification["notes"],
        })

    cases.sort(key=lambda c: int(c["case_id"]))
    return cases


def validate_casebook_schema(cases: list[dict[str, Any]]) -> list[str]:
    """Return schema/quality errors for rollout-gate casebooks."""
    errors: list[str] = []
    required_fields = {
        "case_id",
        "trace_path",
        "failure_family",
        "required_capabilities",
        "expected_next_action",
        "forbidden_next_actions",
        "dynamic_probe_expected",
        "dynamic_probe_reason",
        "delegate_expected",
        "delegate_role",
        "delegate_reason",
        "notes",
    }
    for index, case in enumerate(cases):
        missing = sorted(required_fields - set(case.keys()))
        cid = str(case.get("case_id") or f"index:{index}")
        if missing:
            errors.append(f"{cid}: missing fields {', '.join(missing)}")
        if not case.get("failure_family") or case.get("failure_family") == "unknown":
            errors.append(f"{cid}: missing manual failure_family classification")
        if not isinstance(case.get("required_capabilities"), list) or not case.get("required_capabilities"):
            errors.append(f"{cid}: required_capabilities must be a non-empty list")
        if not case.get("expected_next_action"):
            errors.append(f"{cid}: expected_next_action is required")
        if not isinstance(case.get("forbidden_next_actions"), list):
            errors.append(f"{cid}: forbidden_next_actions must be a list")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Build timeout casebook from traces")
    parser.add_argument("--trace-root", type=str, required=True,
                        help="Path to remote_traces root directory")
    parser.add_argument("--out", type=str, required=True,
                        help="Output JSONL file path")
    args = parser.parse_args()

    if not os.path.isdir(args.trace_root):
        print(f"Error: trace root not found: {args.trace_root}", file=sys.stderr)
        sys.exit(1)

    cases = scan_traces(args.trace_root)
    errors = validate_casebook_schema(cases)
    if errors:
        print("Error: generated casebook failed schema validation:", file=sys.stderr)
        for err in errors[:20]:
            print(f"  - {err}", file=sys.stderr)
        if len(errors) > 20:
            print(f"  ... {len(errors) - 20} more", file=sys.stderr)
        sys.exit(2)

    # Ensure output directory exists
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w") as f:
        for case in cases:
            f.write(json.dumps(case) + "\n")

    # Print summary
    families: dict[str, int] = {}
    for case in cases:
        fam = case["failure_family"]
        families[fam] = families.get(fam, 0) + 1

    print(f"Total timeout cases: {len(cases)}")
    print(f"Failure family distribution:")
    for fam, count in sorted(families.items(), key=lambda x: -x[1]):
        print(f"  {fam}: {count}")
    print(f"Output written to: {args.out}")


if __name__ == "__main__":
    main()
