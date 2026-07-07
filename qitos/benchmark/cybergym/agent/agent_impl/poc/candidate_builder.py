"""Candidate builder — compiles a PoC recipe into an actual candidate file.

Fix D: Bridges the gap between the recipe/rewriter layer and the agent's
candidate generation flow.  When a recipe has no open gaps and has a seed,
this module uses the structured rewriter to produce a PoC file.

The candidate builder is called from the agent's candidate_required branch,
before falling back to hand-crafted BASH/WRITE PoC generation.
"""

from __future__ import annotations

import os
import hashlib
import struct
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ...state import CyberGymState


def build_candidate_from_recipe(state: CyberGymState) -> dict[str, Any]:
    """Build a PoC candidate from the compiled recipe.

    Returns a dict with:
      status: "success" | "blocked" | "partial"
      candidate_path: path to the generated PoC file
      recipe_id: the recipe that was used
      applied_mutations: list of mutations that were applied
      blocked_operations: list of operations that couldn't be applied
      reason: explanation if blocked
    """
    from .recipe import empty_poc_recipe

    recipe = {}
    if hasattr(state, "get_poc_recipe"):
        recipe = state.get_poc_recipe()

    if not recipe or not recipe.get("recipe_id"):
        return {
            "status": "blocked",
            "candidate_path": "",
            "recipe_id": "",
            "applied_mutations": [],
            "blocked_operations": [],
            "reason": "no recipe available",
        }

    open_gaps = recipe.get("open_gaps", [])
    if open_gaps:
        return {
            "status": "blocked",
            "candidate_path": "",
            "recipe_id": recipe.get("recipe_id", ""),
            "applied_mutations": [],
            "blocked_operations": [],
            "reason": f"recipe has {len(open_gaps)} open gap(s): {', '.join(str(g)[:60] for g in open_gaps[:3])}",
        }

    carrier = recipe.get("carrier", {}) or {}
    seed_path = carrier.get("seed_path", "")
    carrier_format = carrier.get("format", "")
    mutations = recipe.get("trigger_mutations", [])
    rewrite = recipe.get("rewrite", {}) or {}
    for pack in list(recipe.get("domain_packs", []) or []):
        if not isinstance(pack, dict) or pack.get("status") != "ready":
            continue
        pack_plan = pack.get("rewrite_plan", {}) or {}
        if pack_plan:
            rewrite = {
                **rewrite,
                "operations": list(rewrite.get("operations", []) or []) + list(pack_plan.get("operations", []) or []),
                "invariants": list(dict.fromkeys(list(rewrite.get("invariants", []) or []) + list(pack_plan.get("invariants", []) or []))),
            }
            break
    rewrite_plan = {
        "operations": rewrite.get("operations", []),
        "invariants": rewrite.get("invariants", []),
    }

    # If no seed, try to generate a minimal seed from format template
    if not seed_path or not Path(seed_path).is_file():
        seed_data = _generate_minimal_seed(carrier_format, carrier)
        if seed_data is None:
            return {
                "status": "blocked",
                "candidate_path": "",
                "recipe_id": recipe.get("recipe_id", ""),
                "applied_mutations": [],
                "blocked_operations": [],
                "reason": f"no seed file and no template for format '{carrier_format}'",
            }
        # Write generated seeds next to generated candidates so traces can
        # reproduce exactly what was submitted.
        seed_path = str(_poc_output_dir(state) / f"seed_{_safe_id(recipe.get('recipe_id', 'recipe'))}.bin")
        Path(seed_path).parent.mkdir(parents=True, exist_ok=True)
        Path(seed_path).write_bytes(seed_data)

    # Apply structured rewrite
    out_path = str(_candidate_output_path(state, recipe))

    # Try pack.build() for confirmed format (format-aware mutation + backpatch)
    pack_mode = getattr(state, "pack_mode", {}) or {}
    if pack_mode.get("mode") == "confirmed" and pack_mode.get("pack_id"):
        try:
            from ..knowledge.registry import get_knowledge_registry
            from ..knowledge.recipe_ir import recipe_to_dict
            pack = get_knowledge_registry().get_pack(pack_mode["pack_id"])
            if pack and "build" in pack.descriptor.capabilities:
                plan_dict = (state.metadata or {}).get("pack_recipe_plan")
                if plan_dict and seed_path and Path(seed_path).is_file():
                    seed_data = Path(seed_path).read_bytes()
                    build_result = pack.build(seed_data, plan_dict)
                    if build_result.status == "success" and build_result.artifact_path:
                        import shutil
                        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(build_result.artifact_path, out_path)
                        fingerprint = _file_fingerprint(out_path)
                        recipe_id = str(recipe.get("recipe_id", "") or "")
                        objective_id = ""
                        objectives = list(recipe.get("objectives", []) or [])
                        if objectives and isinstance(objectives[0], dict):
                            objective_id = str(objectives[0].get("objective_id", "") or "")
                        return {
                            "status": "success",
                            "candidate_path": out_path,
                            "candidate_id": f"cand_{_safe_id(recipe_id)}_{fingerprint.rsplit(':', 1)[-1][:12] if fingerprint else 'nofp'}",
                            "family_id": f"recipe:{recipe_id}",
                            "objective_id": objective_id,
                            "rewrite_id": "",
                            "content_fingerprint": fingerprint,
                            "generation_method": "pack_build",
                            "recipe_id": recipe_id,
                            "applied_mutations": list(build_result.applied_operations),
                            "blocked_operations": list(build_result.blocked_operations),
                            "reason": "",
                        }
        except Exception:
            pass  # Fall through to generic structured rewrite

    # Default: generic structured rewrite

    from ..core.structured_rewriter import apply_structured_rewrite
    rewrite_result = apply_structured_rewrite(
        seed_path=seed_path,
        out_path=out_path,
        rewrite_plan=rewrite_plan,
        mutations=mutations,
    )

    if rewrite_result.get("status") == "blocked":
        return {
            "status": "blocked",
            "candidate_path": "",
            "recipe_id": recipe.get("recipe_id", ""),
            "applied_mutations": [],
            "blocked_operations": rewrite_result.get("blocked_reason", ""),
            "reason": f"rewrite blocked: {rewrite_result.get('blocked_reason', '')}",
        }

    fingerprint = _file_fingerprint(out_path)
    recipe_id = str(recipe.get("recipe_id", "") or "")
    objective_id = ""
    objectives = list(recipe.get("objectives", []) or [])
    if objectives and isinstance(objectives[0], dict):
        objective_id = str(objectives[0].get("objective_id", "") or "")
    rewrite_id = str((recipe.get("rewrite", {}) or {}).get("rewrite_id", "") or "")

    return {
        "status": "success" if rewrite_result.get("status") == "success" else "partial",
        "candidate_path": out_path,
        "candidate_id": f"cand_{_safe_id(recipe_id)}_{fingerprint.rsplit(':', 1)[-1][:12] if fingerprint else 'nofp'}",
        "family_id": f"recipe:{recipe_id}",
        "objective_id": objective_id,
        "rewrite_id": rewrite_id,
        "content_fingerprint": fingerprint,
        "generation_method": "recipe",
        "recipe_id": recipe_id,
        "applied_mutations": rewrite_result.get("applied_operations", []),
        "blocked_operations": [],
        "reason": "",
    }


def _workspace_root(state: CyberGymState) -> Path:
    root = str(getattr(state, "workspace_root", "") or "").strip()
    return Path(root) if root else Path(".")


def _poc_output_dir(state: CyberGymState) -> Path:
    from ..core.constants import POC_OUTPUT_DIR

    return _workspace_root(state) / POC_OUTPUT_DIR


def _safe_id(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "_" for ch in str(value or ""))
    return cleaned[:80] or "x"


def _candidate_output_path(state: CyberGymState, recipe: dict[str, Any]) -> Path:
    recipe_id = _safe_id(str(recipe.get("recipe_id", "") or "recipe"))
    out_dir = _poc_output_dir(state)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"poc_{recipe_id}.bin"


def _file_fingerprint(path: str) -> str:
    candidate = Path(path)
    if not candidate.is_file():
        return ""
    return "sha256:" + hashlib.sha256(candidate.read_bytes()).hexdigest()


def _generate_minimal_seed(format_name: str, carrier: dict[str, Any]) -> bytes | None:
    """Generate a minimal valid seed for a given format.

    Returns None if the format is not supported for template generation.
    """
    generators = {
        "tiff": _generate_tiff_seed,
        "wav": _generate_wav_seed,
        "pdf": _generate_pdf_seed,
        "elf": _generate_elf_seed,
        "zip": _generate_zip_seed,
        "packet": _generate_packet_seed,
        "tlv": _generate_tlv_seed,
    }

    generator = generators.get(format_name.lower())
    if generator:
        return generator(carrier)

    return None


def _generate_tiff_seed(carrier: dict[str, Any]) -> bytes:
    """Generate a minimal TIFF seed file."""
    # TIFF header: byte order (II=little), magic 42, IFD offset
    width = 8
    height = 8
    bpp = 8

    # Byte order mark (little-endian)
    header = struct.pack("<2sHI", b"II", 42, 8)

    # IFD: 10 entries
    entries = [
        # Tag, Type, Count, Value
        (256, 3, 1, width),     # ImageWidth
        (257, 3, 1, height),    # ImageLength
        (258, 3, 1, bpp),       # BitsPerSample
        (259, 3, 1, 1),         # Compression (no compression)
        (262, 3, 1, 1),         # PhotometricInterpretation
        (273, 4, 1, 0),         # StripOffsets (will be set)
        (277, 3, 1, 1),         # SamplesPerPixel
        (278, 3, 1, height),    # RowsPerStrip
        (279, 4, 1, width * height),  # StripByteCounts
        (282, 5, 1, 72),        # XResolution
    ]

    ifd = bytearray()
    ifd += struct.pack("<H", len(entries))
    for tag, typ, count, value in entries:
        ifd += struct.pack("<HHII", tag, typ, count, value)
    ifd += struct.pack("<I", 0)  # Next IFD offset = 0

    # Pixel data
    pixels = bytes([0x80] * (width * height))

    # Assemble
    strip_offset = 8 + len(ifd)
    # Patch StripOffsets entry (entry index 5)
    # The IFD entries start at offset 2 in the ifd bytes (after entry count)
    # Entry 5 is at offset 2 + 5*12 = 62, value is at +8 bytes within entry
    struct.pack_into("<I", ifd, 2 + 5 * 12 + 8, strip_offset)

    return header + bytes(ifd) + pixels


def _generate_wav_seed(carrier: dict[str, Any]) -> bytes:
    """Generate a minimal WAV/RIFF seed file."""
    sample_rate = 8000
    num_channels = 1
    bits_per_sample = 16
    num_samples = 100

    # RIFF header
    data_size = num_samples * num_channels * (bits_per_sample // 8)
    fmt_chunk_size = 16
    riff_size = 4 + (8 + fmt_chunk_size) + (8 + data_size)

    riff = struct.pack("<4sI4s", b"RIFF", riff_size, b"WAVE")

    # fmt chunk
    fmt_chunk = struct.pack("<4sIHHIIHH",
        b"fmt ", fmt_chunk_size,
        1,  # PCM format
        num_channels,
        sample_rate,
        sample_rate * num_channels * (bits_per_sample // 8),  # byte rate
        num_channels * (bits_per_sample // 8),  # block align
        bits_per_sample,
    )

    # data chunk
    data_header = struct.pack("<4sI", b"data", data_size)
    samples = bytes([0x00, 0x40] * num_samples)  # quiet sine-like

    return riff + fmt_chunk + data_header + samples


def _generate_pdf_seed(carrier: dict[str, Any]) -> bytes:
    """Generate a minimal PDF seed file."""
    # Minimal valid PDF with one page
    pdf = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer
<< /Size 4 /Root 1 0 R >>
startxref
190
%%EOF
"""
    return pdf


def _generate_elf_seed(carrier: dict[str, Any]) -> bytes:
    """Generate a minimal ELF seed file."""
    # 64-bit ELF header
    elf = bytearray(64)
    # Magic
    elf[0:4] = b"\x7fELF"
    # Class: 64-bit
    elf[4] = 2
    # Data: little-endian
    elf[5] = 1
    # Version: 1
    elf[6] = 1
    # OS/ABI: System V
    elf[7] = 0
    # Type: ET_EXEC
    struct.pack_into("<H", elf, 16, 2)
    # Machine: EM_X86_64
    struct.pack_into("<H", elf, 18, 62)
    # Version
    struct.pack_into("<I", elf, 20, 1)
    # Entry point, phoff, shoff, flags, ehsize, phentsize, phnum, etc.
    struct.pack_into("<Q", elf, 24, 0)  # entry
    struct.pack_into("<Q", elf, 32, 64)  # phoff
    struct.pack_into("<Q", elf, 40, 0)  # shoff
    struct.pack_into("<I", elf, 48, 0)  # flags
    struct.pack_into("<H", elf, 52, 64)  # ehsize
    struct.pack_into("<H", elf, 54, 56)  # phentsize
    struct.pack_into("<H", elf, 56, 0)  # phnum
    struct.pack_into("<H", elf, 58, 64)  # shentsize
    struct.pack_into("<H", elf, 60, 0)  # shnum
    struct.pack_into("<H", elf, 62, 0)  # shstrndx
    return bytes(elf)


def _generate_zip_seed(carrier: dict[str, Any]) -> bytes:
    """Generate a minimal ZIP seed file."""
    import zlib

    # Minimal ZIP with one empty file
    filename = b"test.txt"
    content = b"A" * 16
    crc = zlib.crc32(content) & 0xFFFFFFFF

    # Local file header
    local_header = struct.pack("<4sHHHHHIIIHH",
        b"PK\x03\x04",  # signature
        20,  # version needed
        0,   # flags
        0,   # compression (stored)
        0,   # mod time
        0,   # mod date
        crc,  # crc32
        len(content),  # compressed size
        len(content),  # uncompressed size
        len(filename),  # filename length
        0,   # extra field length
    )

    # Central directory
    cd_header = struct.pack("<4sHHHHHHIIIHHHHHII",
        b"PK\x01\x02",  # signature
        20,  # version made by
        20,  # version needed
        0,   # flags
        0,   # compression
        0,   # mod time
        0,   # mod date
        crc,  # crc32
        len(content),  # compressed size
        len(content),  # uncompressed size
        len(filename),  # filename length
        0,   # extra field length
        0,   # file comment length
        0,   # disk number start
        0,   # internal file attributes
        0,   # external file attributes
        0,   # relative offset of local header
    )

    # End of central directory
    cd_offset = len(local_header) + len(filename) + len(content)
    cd_size = len(cd_header) + len(filename)
    eocd = struct.pack("<4sHHHHIIH",
        b"PK\x05\x06",  # signature
        0,   # disk number
        0,   # disk number with CD
        1,   # number of entries on disk
        1,   # total number of entries
        cd_size,  # size of CD
        cd_offset,  # offset of CD
        0,   # comment length
    )

    return local_header + filename + content + cd_header + filename + eocd


def _generate_packet_seed(carrier: dict[str, Any]) -> bytes:
    """Generate a generic packet/dissector seed.

    It is deliberately conservative: an Ethernet+IPv4+UDP-like prefix plus a
    small payload gives packet-stack harnesses enough structure to reach common
    dispatch paths while staying format-agnostic for Wireshark-style cases.
    """
    eth = b"\x02\x00\x00\x00\x00\x01\x02\x00\x00\x00\x00\x02\x08\x00"
    ip = b"\x45\x00\x00\x30\x00\x00\x40\x00\x40\x11\x00\x00\x7f\x00\x00\x01\x7f\x00\x00\x01"
    udp = b"\x30\x39\x30\x39\x00\x1c\x00\x00"
    payload = b"CYBERGYM-PACKET-SEED\x00\x01\x02\x03"
    return eth + ip + udp + payload


def _generate_tlv_seed(carrier: dict[str, Any]) -> bytes:
    """Generate a simple TLV/protocol seed for lifecycle harnesses."""
    records = [
        (0x01, b"\x00"),          # init/version selector
        (0x02, b"CYBERGYM"),      # payload
        (0x03, b"\x00\x10"),      # length/boundary-ish field
    ]
    out = bytearray()
    for tag, value in records:
        out += bytes([tag, len(value) & 0xFF])
        out += value
    return bytes(out)
