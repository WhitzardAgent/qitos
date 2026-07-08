"""Harness protocol extraction — identifies fuzzer input contract from
harness source code patterns.

Extracts:
- Record delimiter patterns (e.g., --MARK--)
- Selector fields (first bytes seed, last byte linktype)
- API callback sequences
- Packet stack requirements
- Configured dissectors
- FuzzedDataProvider consumption patterns
- Dissector registration and dispatch
- Command/stdin protocol patterns
- APDU/smart-card transcript patterns

Only uses source text analysis; no network or execution.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any


def extract_harness_protocol(
    *,
    harness_files: list[str],
    fuzzer_binary_name: str = "",
    source_root: str = "",
) -> dict[str, Any]:
    """Extract fuzzer input contract from harness source files.

    Returns a structured protocol dict with input_contract, delimiters,
    selectors, API sequence, and required wrappers.

    Fix E: Extended schema with carrier_stack, selectors, delimiters,
    transcript_steps, sanity_checks, and source provenance.
    """
    result: dict[str, Any] = {
        "protocol_id": "",
        "endpoint_scope": "file",
        "input_contract": "buffer",
        "record_delimiters": [],
        "selector_fields": [],
        "api_sequence": [],
        "configured_target": "",
        "required_wrappers": [],
        "carrier_stack": [],
        "transcript_steps": [],
        "sanity_checks": [],
        "source": [],
        "evidence": [],
        "repair_action": "",
    }

    all_text = ""
    file_evidence: list[dict[str, Any]] = []
    source_lines: list[dict[str, Any]] = []

    for hf in harness_files:
        hpath = Path(source_root) / hf if source_root else Path(hf)
        if not hpath.is_file():
            continue
        try:
            text = hpath.read_text(errors="replace")
        except OSError:
            continue
        all_text += text + "\n"
        file_evidence.append({"file": hf, "lines": text.count("\n") + 1})

    if not all_text:
        result["input_contract"] = "unknown"
        result["endpoint_scope"] = "unknown"
        return result

    # Pattern detection — order matters: more specific first
    _detect_delimiter_patterns(all_text, file_evidence, result, source_lines)
    _detect_first_bytes_selector(all_text, file_evidence, result, source_lines)
    _detect_last_byte_selector(all_text, file_evidence, result, source_lines)
    _detect_fuzzed_data_provider(all_text, file_evidence, result, source_lines)
    _detect_api_callback_sequence(all_text, file_evidence, result, source_lines)
    _detect_configured_dissector(all_text, fuzzer_binary_name, file_evidence, result, source_lines)
    _detect_dissector_registration(all_text, file_evidence, result, source_lines)
    _detect_packet_patterns(all_text, file_evidence, result, source_lines)
    _detect_command_parser(all_text, file_evidence, result, source_lines)
    _detect_apdu_transcript(all_text, file_evidence, result, source_lines)

    # Derive endpoint_scope from input_contract
    scope_map = {
        "buffer": "file",
        "multi_record": "packet",
        "api_sequence": "callback",
        "packet_stack": "packet",
        "packet": "packet",
        "command": "stdin",
        "apdu": "apdu",
    }
    result["endpoint_scope"] = scope_map.get(result["input_contract"], "file")

    # Generate protocol_id
    material = f"{result['input_contract']}|{'|'.join(result['record_delimiters'])}|{'|'.join(s.get('field','') for s in result['selector_fields'])}"
    result["protocol_id"] = f"hp_{hashlib.blake2s(material.encode(), digest_size=6).hexdigest()}"

    # Generate repair action
    if result["record_delimiters"]:
        result["repair_action"] = f"encode PoC as multi-record stream separated by {result['record_delimiters'][0]}"
    elif result["api_sequence"]:
        result["repair_action"] = "generate API callback sequence, not raw file bytes"
    elif result["required_wrappers"]:
        result["repair_action"] = f"wrap PoC in protocol layers: {', '.join(result['required_wrappers'])}"
    elif result["selector_fields"]:
        result["repair_action"] = f"encode selector field {result['selector_fields'][0].get('field', '')} correctly"
    elif result["transcript_steps"]:
        result["repair_action"] = "generate multi-step APDU/command transcript, not a single buffer"

    result["evidence"] = file_evidence[:3]
    result["source"] = source_lines[:8]

    return result


# ------------------------------------------------------------------
# Pattern detectors
# ------------------------------------------------------------------

def _detect_delimiter_patterns(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Detect record delimiter patterns in harness source."""
    patterns = [
        (r'--MARK--', "--MARK--"),
        (r'\\n--MARK--\\n', "\\n--MARK--\\n"),
        (r'\bMARK\b', "MARK"),
        (r'Split\s*\(\s*\w+\s*,\s*"([^"]+)"', "split_delimiter"),
        (r'fuzz_split(?:_input)?\s*\(', "fuzz_split"),
        # Fix E: memmem/strstr delimiter detection
        (r'memmem\s*\(\s*\w+\s*,\s*\w+\s*,\s*"([^"]+)"\s*,', "memmem_delimiter"),
        (r'strstr\s*\(\s*\w+\s*,\s*"([^"]+)"\s*\)', "strstr_delimiter"),
    ]

    for pattern, label in patterns:
        match = re.search(pattern, text)
        if match:
            delimiter = match.group(1) if label in ("split_delimiter", "memmem_delimiter", "strstr_delimiter") else label
            result["record_delimiters"].append(delimiter)
            result["input_contract"] = "multi_record"
            line = text[:match.start()].count("\n") + 1
            source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})
            break  # One delimiter is enough


def _detect_first_bytes_selector(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Detect first-bytes-as-seed patterns."""
    patterns = [
        (r'FUZZ_seed\s*\(\s*(\w+)\s*\)', "FUZZ_seed", "u32le"),
        (r'readLE32\s*\(\s*(\w+)\s*\)', "readLE32", "u32le"),
        (r'readBE32\s*\(\s*(\w+)\s*\)', "readBE32", "u32be"),
        (r'(?:data|buf|input)\s*\[\s*0\s*\]\s*\|\s*(?:data|buf|input)\s*\[\s*1\s*\]', "first_2_bytes", "u16"),
        (r'memcmp\s*\(\s*(\w+)\s*,\s*(\w+)\s*,\s*(\d+)\s*\)', "magic_check", "bytes"),
        # Fix E: data[0] single byte selector
        (r'(?:data|buf|input)\s*\[\s*0\s*\]', "first_byte_selector", "u8"),
        # Fix E: ConsumeIntegral selector (from FuzzedDataProvider)
        (r'ConsumeIntegral\w*\s*\(\s*\)', "fuzzed_selector", "integral"),
    ]

    for pattern, meaning, encoding in patterns:
        match = re.search(pattern, text)
        if match:
            if "32" in encoding:
                field = "input[0:4]"
            elif "16" in encoding or encoding == "u16":
                field = "input[0:2]"
            else:
                field = "input[0]"
            result["selector_fields"].append({
                "field": field,
                "meaning": meaning,
                "encoding": encoding,
            })
            line = text[:match.start()].count("\n") + 1
            source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})
            break


def _detect_last_byte_selector(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Detect last-byte-as-selector patterns (e.g., linktype)."""
    patterns = [
        (r'(?:data|buf|input)\s*\[\s*(?:size|len|length)\s*-\s*1\s*\]', "linktype"),
        (r'(?:data|buf|input)\s*\[\s*(?:size|len)\s*-\s*1\s*\]', "last_byte_selector"),
    ]

    for pattern, meaning in patterns:
        match = re.search(pattern, text)
        if match:
            result["selector_fields"].append({
                "field": "input[-1]",
                "meaning": meaning,
                "encoding": "u8",
            })
            line = text[:match.start()].count("\n") + 1
            source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})
            break


def _detect_fuzzed_data_provider(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Fix E: Detect FuzzedDataProvider consumption patterns.

    FuzzedDataProvider-based harnesses consume structured data via method calls
    like ConsumeIntegral, ConsumeBool, ConsumeBytes, ConsumeRandomLengthString,
    ConsumeRemainingBytes.
    """
    fdp_patterns = [
        (r'FuzzedDataProvider\s+\w+\s*\(', "FuzzedDataProvider_init"),
        (r'ConsumeIntegral(?:InRange)?\s*<\w+>\s*\(\s*\d+\s*,\s*\d+\s*\)', "ConsumeIntegral"),
        (r'ConsumeBool\s*\(\s*\)', "ConsumeBool"),
        (r'ConsumeBytes\s*<\w+>\s*\(\s*\d+\s*\)', "ConsumeBytes"),
        (r'ConsumeRandomLengthString\s*\(\s*\d+\s*\)', "ConsumeRandomLengthString"),
        (r'ConsumeRemainingBytes\s*<\w+>\s*\(\s*\)', "ConsumeRemainingBytes"),
    ]

    found_fdp = False
    for pattern, meaning in fdp_patterns:
        matches = list(re.finditer(pattern, text))
        if matches:
            if meaning == "FuzzedDataProvider_init":
                found_fdp = True
            match = matches[0]
            line = text[:match.start()].count("\n") + 1
            result["selector_fields"].append({
                "field": f"fdp:{meaning}",
                "meaning": meaning,
                "encoding": "fuzzed_data_provider",
            })
            source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})

    if found_fdp or any(s.get("encoding") == "fuzzed_data_provider" for s in result["selector_fields"]):
        if result["input_contract"] == "buffer":
            result["input_contract"] = "api_sequence"
            result["endpoint_scope"] = "callback"


def _detect_api_callback_sequence(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Detect API callback sequence patterns."""
    api_calls = re.findall(
        r'\b(\w+(?:Create|Init|Set\w+|Process\w+|Handle\w+|Decode\w+|Parse\w+|Callback|Destroy|Free|Close))\s*\(',
        text,
    )
    # Filter to known API patterns
    known_apis = {
        "JxlDecoderCreate", "JxlDecoderSetImageOutCallback", "JxlDecoderProcessInput",
        "JxlDecoderDestroy",
        "SCardConnect", "SCardTransmit", "SCardDisconnect",
        "APDU", "SC_transmit_apdu",
        # Fix E: additional API patterns
        "curl_easy_init", "curl_easy_setopt", "curl_easy_perform", "curl_easy_cleanup",
        "opj_create_decompress", "opj_read_header", "opj_decode", "opj_destroy_decompress",
        "OTInstanceInit", "otInstanceFinalize",
    }

    found_apis = [a for a in api_calls if a in known_apis]
    if not found_apis:
        # Also check for generic callback patterns
        callback_matches = re.findall(r'\b(\w+[Cc]allback\w*)\s*\(', text)
        found_apis = callback_matches[:4]

    if found_apis:
        result["api_sequence"] = found_apis[:5]
        result["input_contract"] = "api_sequence"
        for api in found_apis[:3]:
            match = re.search(re.escape(api) + r'\s*\(', text)
            if match:
                line = text[:match.start()].count("\n") + 1
                source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": api})

        # Build transcript steps from API sequence
        result["transcript_steps"] = [
            {"name": api, "input_slice": f"api_call:{api}"}
            for api in found_apis[:5]
        ]


def _detect_configured_dissector(
    text: str,
    fuzzer_binary_name: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Detect configured dissector or fuzzshark patterns."""
    # Check for fuzzshark dissector configuration
    dissector_match = re.search(r'configured\s+(?:for|dissector)\s*:\s*(\w+)', text, re.IGNORECASE)
    if dissector_match:
        result["configured_target"] = f"dissector:{dissector_match.group(1)}"
        result["carrier_stack"] = _infer_wrapper_stack(dissector_match.group(1))
        result["required_wrappers"] = result["carrier_stack"]
        if not result["input_contract"] or result["input_contract"] == "buffer":
            result["input_contract"] = "packet_stack"
        line = text[:dissector_match.start()].count("\n") + 1
        source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": dissector_match.group(0)[:80]})

    # Check binary name for fuzzshark hints
    if fuzzer_binary_name:
        fuzz_match = re.search(r'fuzzshark(?:_(\w+))?', fuzzer_binary_name, re.IGNORECASE)
        if fuzz_match:
            proto = fuzz_match.group(1) or ""
            if proto:
                result["configured_target"] = f"dissector:{proto}"
                result["carrier_stack"] = _infer_wrapper_stack(proto)
                result["required_wrappers"] = result["carrier_stack"]
                if not result["input_contract"] or result["input_contract"] == "buffer":
                    result["input_contract"] = "packet_stack"
                source_lines.append({"file": fuzzer_binary_name, "line": 0, "expr": f"binary:{fuzzer_binary_name}"})

    # Fix E: Check for Wireshark dissector table patterns
    ws_patterns = [
        (r'find_dissector\s*\(\s*"(\w+)"\s*\)', "find_dissector"),
        (r'dissector_try_heuristic\s*\(\s*"(\w+)"\s*', "dissector_try_heuristic"),
        (r'register_dissector\s*\(\s*"(\w+)"\s*', "register_dissector"),
        (r'dissector_add_uint\s*\(\s*"(\w+)"\s*,', "dissector_add_uint"),
    ]
    for pattern, kind in ws_patterns:
        match = re.search(pattern, text)
        if match:
            result["configured_target"] = f"{kind}:{match.group(1)}"
            if not result["carrier_stack"]:
                result["carrier_stack"] = _infer_wrapper_stack(match.group(1))
            if result["input_contract"] == "buffer":
                result["input_contract"] = "packet_stack"
            line = text[:match.start()].count("\n") + 1
            source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})
            break


def _detect_dissector_registration(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Fix E: Detect dissector registration patterns specific to Wireshark."""
    reg_patterns = [
        (r'proto_reg_handoff\s*\(\s*\)', "handoff_registration"),
        (r'proto_register_\w+\s*\(\s*\)', "proto_registration"),
        (r'heur_dissector_add\s*\(\s*"(\w+)"\s*,', "heuristic_dissector"),
    ]
    for pattern, kind in reg_patterns:
        match = re.search(pattern, text)
        if match:
            if result["input_contract"] == "buffer":
                result["input_contract"] = "packet_stack"
                result["endpoint_scope"] = "packet"
            line = text[:match.start()].count("\n") + 1
            source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})
            break


def _detect_packet_patterns(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Detect packet/protocol fuzzer patterns."""
    packet_keywords = {
        "HandleDatagram": "packet",
        "HandlePacket": "packet",
        "ip6-send": "packet_stack",
        "send_frame": "packet",
        "process_frame": "packet",
        # Fix E: more packet patterns
        "tvb_new_subset": "packet_stack",
        "call_dissector": "packet_stack",
        "dissect_": "packet_stack",
    }

    for keyword, contract in packet_keywords.items():
        if keyword in text:
            if result["input_contract"] == "buffer":
                result["input_contract"] = contract
            match = re.search(re.escape(keyword), text)
            if match:
                line = text[:match.start()].count("\n") + 1
                source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": keyword})
            break

    # Infer carrier stack from packet-related keywords
    if result["input_contract"] in ("packet_stack", "packet") and not result["carrier_stack"]:
        carrier_parts = []
        for proto in ["ethernet", "ip", "udp", "tcp", "rtps", "sctp"]:
            if proto in text.lower():
                carrier_parts.append(proto)
        if carrier_parts:
            result["carrier_stack"] = carrier_parts


def _detect_command_parser(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Fix E: Detect command/stdin text protocol patterns.

    Some harnesses consume text commands from input (OpenThread CLI,
    Samba NDR, etc.).
    """
    command_patterns = [
        (r'ProcessCommand\s*\(\s*"(\w+)"', "cli_command"),
        (r'stdin.*getline|fgets.*stdin', "stdin_read"),
        (r'ParseCommand\s*\(\s*(\w+)', "parse_command"),
        (r'cli_\w+\s*\(', "cli_handler"),
        (r'otCliAppendResult\s*\(', "openthread_cli"),
    ]

    for pattern, kind in command_patterns:
        match = re.search(pattern, text)
        if match:
            if result["input_contract"] == "buffer":
                result["input_contract"] = "command"
                result["endpoint_scope"] = "stdin"
            line = text[:match.start()].count("\n") + 1
            source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})
            break


def _detect_apdu_transcript(
    text: str,
    evidence: list[dict[str, Any]],
    result: dict[str, Any],
    source_lines: list[dict[str, Any]],
) -> None:
    """Fix E: Detect APDU/smart-card transcript patterns.

    OpenSC and similar use multi-step APDU command sequences.
    """
    apdu_patterns = [
        (r'SC_transmit_apdu\s*\(', "apdu_transmit"),
        (r'APDU\s*\(\s*(\w+)', "apdu_construct"),
        (r'SCardConnect\s*\(', "smartcard_connect"),
        (r'SCardTransmit\s*\(', "scard_transmit"),
    ]

    found_steps: list[dict[str, str]] = []
    for pattern, kind in apdu_patterns:
        for match in re.finditer(pattern, text):
            found_steps.append({"name": kind, "input_slice": f"apdu:{match.group(0)[:30]}"})

    if found_steps:
        result["transcript_steps"] = found_steps[:6]
        if result["input_contract"] == "buffer":
            result["input_contract"] = "apdu"
            result["endpoint_scope"] = "apdu"
        for pattern, kind in apdu_patterns:
            for match in list(re.finditer(pattern, text))[:2]:
                line = text[:match.start()].count("\n") + 1
                source_lines.append({"file": evidence[0]["file"] if evidence else "", "line": line, "expr": match.group(0)[:80]})

    # Sanity checks from transcript requirements
    if result["transcript_steps"]:
        result["sanity_checks"] = [
            {"kind": "length", "description": "APDU must have valid CLA/INS/P1/P2/Lc/Data/Le structure"},
        ]


def _infer_wrapper_stack(dissector: str) -> list[str]:
    """Infer the wrapper protocol stack from a dissector name."""
    stacks: dict[str, list[str]] = {
        "ip": ["ethernet", "ip"],
        "udp": ["ethernet", "ip", "udp"],
        "tcp": ["ethernet", "ip", "tcp"],
        "ieee1722": ["ethernet", "ip", "udp", "ieee1722"],
        "cotp": ["ethernet", "ip", "cotp"],
        "sctp": ["ethernet", "ip", "sctp"],
        "http": ["ethernet", "ip", "tcp", "http"],
        "http2": ["ethernet", "ip", "tcp", "http2"],
        "rtps": ["ethernet", "ip", "udp", "rtps"],
        "srvloc": ["ethernet", "ip", "udp", "srvloc"],
        "wcp": ["ethernet", "ip", "udp", "wcp"],
        "noe": ["ethernet", "ip", "udp", "noe"],
        "ositp": ["ethernet", "ip", "ositp"],
        "hdls": ["ethernet", "ip", "udp", "hdls"],
    }
    return stacks.get(dissector.lower(), ["ethernet", "ip", dissector.lower()])
