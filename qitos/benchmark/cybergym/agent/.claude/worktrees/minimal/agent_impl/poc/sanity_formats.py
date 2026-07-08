"""Format-specific PoC sanity checks.

Ported and adapted from agentic-poc/toolbox (github.com/HRsGIT/agentic-poc).
Only inspection/sanity logic is ported; mutation/building/CLI code is excluded.

All checks produce PoCSanityIssue entries. Only severity="fail" blocks submit.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .sanity import PoCSanityIssue


# ===========================================================================
# Font / SFNT / OTF / CFF2 — ported from agentic-poc/toolbox/font.py
# ===========================================================================

_SFNT_VERSIONS = {
    b"\x00\x01\x00\x00": "TrueType",
    b"OTTO": "OpenType/CFF",
    b"true": "TrueType",
    b"typ1": "Type1/SFNT",
}


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _sfnt_checksum(data: bytes) -> int:
    padded = data + b"\0" * ((4 - len(data) % 4) % 4)
    total = 0
    for offset in range(0, len(padded), 4):
        total = (total + struct.unpack(">I", padded[offset:offset + 4])[0]) & 0xFFFFFFFF
    return total


def _sfnt_table_checksum(tag: bytes, data: bytes) -> int:
    if tag == b"head" and len(data) >= 12:
        data = data[:8] + b"\0\0\0\0" + data[12:]
    return _sfnt_checksum(data)


def _tag_text(tag: bytes) -> str:
    return tag.decode("latin1", errors="replace")


def check_font(data: bytes, fmt: str, issues: list[PoCSanityIssue]) -> None:
    """Check font/SFNT/OTF/TTF/CFF2 carrier sanity."""
    from .sanity import PoCSanityIssue as _Issue

    if len(data) < 4:
        issues.append(_Issue(
            severity="fail", category="font_table",
            message="File too small for font header",
            evidence=f"size={len(data)}",
        ))
        return

    head = data[:4]

    # WOFF container
    if head == b"wOFF":
        _check_woff(data, issues)
        return
    if head == b"wOF2":
        # WOFF2 requires decompression; only basic check
        from .sanity import PoCSanityIssue as _Issue
        if len(data) < 44:
            issues.append(_Issue(
                severity="warn", category="font_table",
                message="WOFF2 file too small for header",
                evidence=f"size={len(data)}",
            ))
        return

    # SFNT container (TTF/OTF/TTC)
    _check_sfnt(data, issues)


def _check_sfnt(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check SFNT font structure."""
    from .sanity import PoCSanityIssue

    if len(data) < 12:
        issues.append(PoCSanityIssue(
            severity="fail", category="font_table",
            message="File too small for SFNT header",
            evidence=f"size={len(data)} need>=12",
        ))
        return

    sfnt_version = data[:4]
    if sfnt_version not in _SFNT_VERSIONS:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Invalid SFNT version bytes",
            evidence=f"version={sfnt_version.hex()}",
            repair_hint="Expected 00010000 (TrueType) or 4F54544F (OTTO).",
        ))
        return

    num_tables, search_range, entry_selector, range_shift = struct.unpack(">HHHH", data[4:12])

    # Validate search params consistency
    max_power = 1
    sel = 0
    while max_power * 2 <= num_tables:
        max_power *= 2
        sel += 1
    expected_sr = max_power * 16
    expected_es = sel
    expected_rs = num_tables * 16 - expected_sr

    if search_range != expected_sr or entry_selector != expected_es or range_shift != expected_rs:
        issues.append(PoCSanityIssue(
            severity="warn", category="font_table",
            message="SFNT search params inconsistent with numTables",
            evidence=f"numTables={num_tables} searchRange={search_range}(exp={expected_sr}) entrySelector={entry_selector}(exp={expected_es}) rangeShift={range_shift}(exp={expected_rs})",
            repair_hint="Recalculate: searchRange = (2^floor(log2(n))) * 16.",
        ))

    directory_end = 12 + num_tables * 16
    if directory_end > len(data):
        issues.append(PoCSanityIssue(
            severity="fail", category="font_table",
            message="SFNT table directory extends beyond file size",
            evidence=f"directory_end={directory_end} file_size={len(data)}",
            repair_hint="Reduce numTables or extend the file.",
        ))
        return

    # Check each table directory entry
    bad_ranges = []
    checksum_mismatches = []
    table_tags = []

    for index in range(num_tables):
        entry = data[12 + index * 16:28 + index * 16]
        if len(entry) < 16:
            bad_ranges.append(f"entry_{index}")
            continue

        tag, declared_checksum, offset, length = struct.unpack(">4sIII", entry)
        tag_str = _tag_text(tag)
        table_tags.append(tag_str)

        if offset + length > len(data):
            bad_ranges.append(f"{tag_str}(offset={offset}+length={length}>file_size={len(data)})")
        else:
            table_data = data[offset:offset + length]
            actual = _sfnt_table_checksum(tag, table_data)
            if actual != declared_checksum:
                checksum_mismatches.append(f"{tag_str}")

    if bad_ranges:
        issues.append(PoCSanityIssue(
            severity="fail", category="font_table",
            message=f"Table directory entries with invalid ranges: {bad_ranges[:5]}",
            evidence=f"bad_ranges_count={len(bad_ranges)}",
            repair_hint="Ensure all table offsets+lengths are within file bounds.",
        ))

    if checksum_mismatches:
        issues.append(PoCSanityIssue(
            severity="warn", category="font_table",
            message=f"Checksum mismatches: {checksum_mismatches[:5]}",
            evidence=f"mismatch_count={len(checksum_mismatches)}",
            repair_hint="Checksum mismatches are common in mutated fonts; the parser may still accept the file.",
        ))

    # Check outline type
    tag_set = set(table_tags)
    if "CFF2" in tag_set:
        _check_cff2_basic(data, table_tags, issues)
    elif "CFF " in tag_set:
        pass  # CFF1 — no basic check beyond table directory
    elif "glyf" not in tag_set and sfnt_version == b"OTTO":
        issues.append(PoCSanityIssue(
            severity="warn", category="font_table",
            message="OTTO font has neither CFF2, CFF, nor glyf table",
            evidence=f"tables={table_tags[:10]}",
        ))


def _check_cff2_basic(data: bytes, table_tags: list[str], issues: list[PoCSanityIssue]) -> None:
    """Basic CFF2 table structure check (stdlib-only, no fontTools required)."""
    from .sanity import PoCSanityIssue

    # Find CFF2 table offset/length from directory
    num_tables = struct.unpack(">H", data[4:6])[0]
    for index in range(num_tables):
        entry = data[12 + index * 16:28 + index * 16]
        tag, _checksum, offset, length = struct.unpack(">4sIII", entry)
        if tag == b"CFF2":
            if offset + length > len(data):
                issues.append(PoCSanityIssue(
                    severity="fail", category="font_table",
                    message="CFF2 table offset+length exceeds file size",
                    evidence=f"offset={offset} length={length} file_size={len(data)}",
                    repair_hint="Ensure CFF2 table data is within the file.",
                ))
                return

            cff2_data = data[offset:offset + length]
            if len(cff2_data) < 5:
                issues.append(PoCSanityIssue(
                    severity="fail", category="font_table",
                    message="CFF2 table too small for header",
                    evidence=f"cff2_length={len(cff2_data)}",
                ))
                return

            major = cff2_data[0]
            minor = cff2_data[1]
            header_size = cff2_data[2]
            top_dict_length = struct.unpack(">H", cff2_data[3:5])[0]

            if major != 2:
                issues.append(PoCSanityIssue(
                    severity="warn", category="font_table",
                    message=f"CFF2 header major version is {major}, expected 2",
                    evidence=f"major={major} minor={minor}",
                ))

            if header_size < 5:
                issues.append(PoCSanityIssue(
                    severity="warn", category="font_table",
                    message=f"CFF2 headerSize={header_size} seems too small",
                    evidence=f"headerSize={header_size}",
                ))

            if top_dict_length > len(cff2_data) - header_size:
                issues.append(PoCSanityIssue(
                    severity="warn", category="font_table",
                    message="CFF2 topDictLength exceeds remaining CFF2 data",
                    evidence=f"topDictLength={top_dict_length} remaining={len(cff2_data) - header_size}",
                ))

            return

    # CFF2 tag not found in directory (shouldn't happen if tag_set said it was there)
    issues.append(PoCSanityIssue(
        severity="warn", category="font_table",
        message="CFF2 listed in tags but not found in table directory",
    ))


def _check_woff(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check WOFF container header."""
    from .sanity import PoCSanityIssue

    if len(data) < 44:
        issues.append(PoCSanityIssue(
            severity="fail", category="font_table",
            message="WOFF file too small for header",
            evidence=f"size={len(data)} need>=44",
        ))
        return

    signature = data[:4]
    if signature != b"wOFF":
        return  # Not WOFF

    flavor = data[4:8]
    length = struct.unpack(">I", data[8:12])[0]
    num_tables = struct.unpack(">H", data[12:14])[0]

    if length > len(data):
        issues.append(PoCSanityIssue(
            severity="warn", category="font_table",
            message="WOFF declared length exceeds file size",
            evidence=f"declared={length} actual={len(data)}",
        ))

    # Check flavor points to known SFNT version
    if flavor not in _SFNT_VERSIONS:
        issues.append(PoCSanityIssue(
            severity="warn", category="font_table",
            message="WOFF flavor is not a recognized SFNT version",
            evidence=f"flavor={flavor.hex()}",
        ))

    # Check table directory fits
    directory_end = 44 + num_tables * 20  # WOFF entries are 20 bytes
    if directory_end > len(data):
        issues.append(PoCSanityIssue(
            severity="fail", category="font_table",
            message="WOFF table directory extends beyond file",
            evidence=f"directory_end={directory_end} file_size={len(data)}",
        ))


# ===========================================================================
# PNG — ported from agentic-poc/toolbox/formats/png.py
# ===========================================================================

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def check_png(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check PNG carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 8:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="File too small for PNG signature",
            evidence=f"size={len(data)}",
        ))
        return

    if data[:8] != _PNG_SIGNATURE:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Invalid PNG signature",
            evidence=f"got={data[:8].hex()} expected=89504e470d0a1a0a",
        ))
        return

    # Check IHDR chunk
    if len(data) < 24:  # 8 sig + 4 length + 4 type + 4 width + 4 height + ...
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="PNG too small for IHDR chunk",
        ))
        return

    # Parse chunks minimally
    offset = 8
    ihdr_found = False
    while offset + 12 <= len(data):  # min chunk: 4 length + 4 type + 0 data + 4 CRC
        chunk_length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        chunk_end = offset + 12 + chunk_length

        if chunk_type == b"IHDR":
            ihdr_found = True
            if chunk_length != 13:
                issues.append(PoCSanityIssue(
                    severity="warn", category="format",
                    message=f"IHDR chunk length is {chunk_length}, expected 13",
                ))

        if chunk_end > len(data):
            issues.append(PoCSanityIssue(
                severity="warn", category="format",
                message=f"Chunk {chunk_type!r} at offset {offset} extends beyond file",
                evidence=f"chunk_end={chunk_end} file_size={len(data)}",
            ))
            break

        if chunk_type == b"IEND":
            break

        offset = chunk_end

    if not ihdr_found:
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="No IHDR chunk found in PNG",
            repair_hint="A valid PNG must start with an IHDR chunk.",
        ))


# ===========================================================================
# JPEG — ported from agentic-poc/toolbox/formats/jpeg.py
# ===========================================================================


def check_jpeg(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check JPEG carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 3:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="File too small for JPEG",
        ))
        return

    if data[:2] != b"\xff\xd8":
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Missing JPEG SOI marker",
            evidence=f"got={data[:2].hex()} expected=ffd8",
        ))
        return

    # Scan markers
    offset = 2
    sos_found = False
    while offset + 2 <= len(data):
        if data[offset] != 0xFF:
            break
        marker = data[offset + 1]
        if marker == 0xDA:  # SOS — scan data follows
            sos_found = True
            break
        if marker == 0xD9:  # EOI
            break
        # Standalone markers (no payload)
        if marker in (0xD0, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0x01):
            offset += 2
            continue
        # Markers with payload
        if offset + 4 > len(data):
            break
        payload_length = struct.unpack(">H", data[offset + 2:offset + 4])[0]
        offset += 2 + payload_length

    if not sos_found:
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="No SOS marker found in JPEG — scan data may be missing",
            repair_hint="JPEG needs an SOS marker to trigger the scan parser.",
        ))


# ===========================================================================
# BMP — ported from agentic-poc/toolbox/formats/bmp.py
# ===========================================================================


def check_bmp(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check BMP carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 14:
        issues.append(PoCSanityIssue(
            severity="fail", category="format",
            message="File too small for BMP file header",
            evidence=f"size={len(data)} need>=14",
        ))
        return

    if data[:2] != b"BM":
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Invalid BMP magic bytes",
            evidence=f"got={data[:2].hex()}",
        ))
        return

    file_size = struct.unpack("<I", data[2:6])[0]
    pixel_offset = struct.unpack("<I", data[10:14])[0]

    if pixel_offset > len(data):
        issues.append(PoCSanityIssue(
            severity="warn", category="offset",
            message="BMP pixel data offset exceeds file size",
            evidence=f"pixel_offset={pixel_offset} file_size={len(data)}",
        ))

    if file_size > len(data):
        issues.append(PoCSanityIssue(
            severity="info", category="format",
            message="BMP declared file size exceeds actual size",
            evidence=f"declared={file_size} actual={len(data)}",
        ))


# ===========================================================================
# PDF — ported from agentic-poc/toolbox/formats/pdf.py
# ===========================================================================


def check_pdf(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check PDF carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 5:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="File too small for PDF header",
        ))
        return

    if not data[:5].startswith(b"%PDF-"):
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Missing PDF header (%PDF-)",
            evidence=f"got={data[:5]}",
        ))
        return

    # Check for EOF marker
    tail = data[-32:] if len(data) > 32 else data
    if b"%%EOF" not in tail and b"%%eof" not in tail.lower():
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="No %%EOF marker found at end of PDF",
            repair_hint="A valid PDF should end with %%EOF.",
        ))


# ===========================================================================
# ZIP — ported from agentic-poc/toolbox/formats/zipfmt.py
# ===========================================================================


def check_zip(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check ZIP carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 4:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="File too small for ZIP",
        ))
        return

    if data[:4] != b"PK\x03\x04":
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Missing ZIP local file header signature",
            evidence=f"got={data[:4].hex()} expected=504b0304",
        ))
        return

    # Scan local file headers
    offset = 0
    entry_count = 0
    while offset + 30 <= len(data):
        sig = data[offset:offset + 4]
        if sig != b"PK\x03\x04":
            break
        if offset + 30 > len(data):
            break
        fname_len = struct.unpack("<H", data[offset + 26:offset + 28])[0]
        extra_len = struct.unpack("<H", data[offset + 28:offset + 30])[0]
        comp_size = struct.unpack("<I", data[offset + 18:offset + 22])[0]
        uncomp_size = struct.unpack("<I", data[offset + 22:offset + 26])[0]
        data_offset = offset + 30 + fname_len + extra_len
        if data_offset + comp_size > len(data):
            issues.append(PoCSanityIssue(
                severity="warn", category="format",
                message=f"ZIP entry {entry_count} compressed data extends beyond file",
                evidence=f"entry={entry_count} data_end={data_offset + comp_size} file_size={len(data)}",
            ))
            break
        offset = data_offset + comp_size
        entry_count += 1

    # Look for central directory (PK\x01\x02)
    # Simple scan from the end
    eocd_pos = data.rfind(b"PK\x05\x06")
    if eocd_pos < 0:
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="No ZIP End of Central Directory record found",
            repair_hint="ZIP files need an EOCD record for the central directory.",
        ))


# ===========================================================================
# WAV / RIFF — ported from agentic-poc/toolbox/formats/wav.py
# ===========================================================================


def check_wav(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check WAV/RIFF carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 12:
        issues.append(PoCSanityIssue(
            severity="fail", category="format",
            message="File too small for WAV/RIFF header",
            evidence=f"size={len(data)} need>=12",
        ))
        return

    if data[:4] != b"RIFF":
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Missing RIFF header",
            evidence=f"got={data[:4]}",
        ))
        return

    riff_size = struct.unpack("<I", data[4:8])[0] + 8
    form_type = data[8:12]

    if form_type != b"WAVE":
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message=f"RIFF form type is {form_type!r}, expected WAVE",
        ))

    if riff_size > len(data):
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="RIFF declared size exceeds file size",
            evidence=f"declared={riff_size} actual={len(data)}",
        ))

    # Scan sub-chunks
    offset = 12
    while offset + 8 <= len(data):
        chunk_id = data[offset:offset + 4]
        chunk_size = struct.unpack("<I", data[offset + 4:offset + 8])[0]
        chunk_end = offset + 8 + chunk_size
        if chunk_end > len(data):
            issues.append(PoCSanityIssue(
                severity="warn", category="format",
                message=f"WAV sub-chunk {chunk_id!r} extends beyond file",
                evidence=f"chunk_end={chunk_end} file_size={len(data)}",
            ))
            break
        offset = chunk_end
        if offset % 2:  # RIFF chunks are word-aligned
            offset += 1


# ===========================================================================
# TIFF — basic IFD offset range check
# ===========================================================================


def check_tiff(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check TIFF carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 8:
        issues.append(PoCSanityIssue(
            severity="fail", category="format",
            message="File too small for TIFF header",
            evidence=f"size={len(data)} need>=8",
        ))
        return

    # Byte order mark
    bom = data[:2]
    if bom == b"II":
        endian = "<"
    elif bom == b"MM":
        endian = ">"
    else:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Invalid TIFF byte order mark",
            evidence=f"got={bom.hex()} expected=4949(II) or 4D4D(MM)",
        ))
        return

    magic = struct.unpack(f"{endian}H", data[2:4])[0]
    if magic != 42:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message=f"TIFF magic number is {magic}, expected 42",
            evidence=f"magic={magic}",
        ))
        return

    ifd_offset = struct.unpack(f"{endian}I", data[4:8])[0]
    if ifd_offset > len(data):
        issues.append(PoCSanityIssue(
            severity="fail", category="offset",
            message="TIFF first IFD offset exceeds file size",
            evidence=f"ifd_offset={ifd_offset} file_size={len(data)}",
            repair_hint="Ensure IFD offset points within the file.",
        ))
    elif ifd_offset < 8:
        issues.append(PoCSanityIssue(
            severity="warn", category="offset",
            message="TIFF first IFD offset is within header area",
            evidence=f"ifd_offset={ifd_offset}",
        ))


# ===========================================================================
# AV1 / OBU — basic header and length varint check
# ===========================================================================


def _read_obu_header(data: bytes, offset: int) -> tuple[int, int, int]:
    """Read an OBU header at offset. Returns (obu_type, header_size, payload_size).

    Returns (0, 0, 0) on failure.
    """
    if offset >= len(data):
        return 0, 0, 0

    first = data[offset]
    obu_type = (first >> 3) & 0x0F
    has_extension = bool(first & 0x04)
    has_size_field = bool(first & 0x02)

    header_size = 1
    if has_extension:
        header_size += 1

    if not has_size_field:
        # Payload extends to end of data (temporal delimiting)
        return obu_type, header_size, len(data) - offset - header_size

    # Read LEB128 varint for payload size
    payload_size = 0
    shift = 0
    pos = offset + header_size
    while pos < len(data) and shift < 32:
        byte = data[pos]
        payload_size |= (byte & 0x7F) << shift
        shift += 7
        pos += 1
        header_size += 1
        if not (byte & 0x80):
            break

    return obu_type, header_size, payload_size


def check_av1(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check AV1/OBU basic carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 2:
        issues.append(PoCSanityIssue(
            severity="fail", category="format",
            message="File too small for AV1 OBU header",
            evidence=f"size={len(data)}",
        ))
        return

    offset = 0
    obu_count = 0
    while offset < len(data) and obu_count < 20:
        obu_type, header_size, payload_size = _read_obu_header(data, offset)
        if header_size == 0:
            break

        total_size = header_size + payload_size
        if offset + total_size > len(data):
            issues.append(PoCSanityIssue(
                severity="warn", category="offset",
                message=f"OBU #{obu_count} (type={obu_type}) extends beyond file",
                evidence=f"obu_end={offset + total_size} file_size={len(data)}",
            ))
            break

        offset += total_size
        obu_count += 1

    if obu_count == 0:
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="No valid OBU headers found",
        ))


# ===========================================================================
# Zstandard — basic magic and frame check
# ===========================================================================

_ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"


def check_zstd(data: bytes, issues: list[PoCSanityIssue]) -> None:
    """Check Zstandard basic carrier sanity."""
    from .sanity import PoCSanityIssue

    if len(data) < 4:
        issues.append(PoCSanityIssue(
            severity="fail", category="format",
            message="File too small for Zstandard frame",
            evidence=f"size={len(data)}",
        ))
        return

    if data[:4] != _ZSTD_MAGIC:
        issues.append(PoCSanityIssue(
            severity="fail", category="magic",
            message="Missing Zstandard magic number",
            evidence=f"got={data[:4].hex()} expected=28b52ffd",
        ))
        return

    # Frame header descriptor (byte 4)
    if len(data) < 5:
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="Zstandard file too small for frame header",
        ))
        return

    fhd = data[4]
    single_segment = bool(fhd & 0x20)
    window_descriptor = not single_segment
    dict_id_flag = (fhd >> 0) & 0x03
    content_size_flag = (fhd >> 6) & 0x03

    # Minimal header size check
    min_header = 5  # magic + FHD
    if window_descriptor:
        min_header += 1
    if dict_id_flag > 0:
        min_header += (1 << (dict_id_flag - 1))
    if content_size_flag > 0:
        min_header += (1 << (content_size_flag - 1))

    if len(data) < min_header:
        issues.append(PoCSanityIssue(
            severity="warn", category="format",
            message="Zstandard frame header extends beyond file",
            evidence=f"min_header={min_header} file_size={len(data)}",
        ))
