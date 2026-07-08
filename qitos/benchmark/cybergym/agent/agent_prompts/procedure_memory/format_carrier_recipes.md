# Format Carrier Recipes

## What is a Carrier?

A "carrier" is the input file that the harness/fuzzer feeds to the target
program. For CyberGym Level 1, you must construct a raw input file (not a
script or command-line arguments) that:
1. Passes the harness entry point.
2. Reaches the vulnerable code path.
3. Contains the mutation that triggers the bug.

The carrier's format must be valid enough to be accepted by the parser up to
the point where the vulnerability is triggered. It does NOT need to be a
fully valid file — it only needs to survive parsing up to the critical point.

## Carrier Strategy Selection

### corpus_mutate
- **When**: A corpus/seed directory exists with sample files for the target
  format.
- **How**: Copy a seed file, then mutate specific bytes at known offsets.
- **Advantage**: The seed already satisfies format gates (magic, headers,
  table directories). Small mutations are less likely to break the carrier
  before reaching the vulnerable path.
- **Key rule**: Preserve the carrier skeleton (magic, outer headers, table
  directory, chunk structure). Only mutate the specific field that controls
  the vulnerability.

### binary_python
- **When**: No suitable seed exists, or the format is simple enough to
  construct from scratch.
- **How**: Use Python to write raw bytes: `struct.pack('<I', value)` for
  little-endian 32-bit integers, etc.
- **Advantage**: Full control over every byte.
- **Key rule**: Build a minimal valid format from scratch, then add the
  mutation. Use format specifications to ensure minimum validity.

### text / hex
- **When**: The target is a text parser (config, source, markup) or a simple
  hex-based format.
- **How**: Write the text directly or construct hex bytes.
- **Key rule**: Ensure encoding (UTF-8, ASCII, line endings) matches what
  the parser expects.

## Format-Specific Carrier Notes

### Font (SFNT/OTF/TTF/CFF2)
- Magic: `\x00\x01\x00\x00` (TrueType), `OTTO` (OpenType/CFF), `ttcf` (TTC)
- Table directory: must be valid (numTables, searchRange, entrySelector,
  rangeShift consistent; all table offsets/lengths within file bounds)
- Target table (e.g., CFF2, glyf, head) must exist and its offset must
  point within the file
- Mutate only the target table's payload, not the table directory

### Image (PNG/JPEG/BMP)
- PNG: 8-byte signature + IHDR chunk must be valid; other chunks optional
- JPEG: SOI marker (0xFFD8) required; SOS marker triggers scan
- BMP: File header + DIB header must be consistent; pixel data offset must
  be within file

### Document (PDF/ZIP)
- PDF: %PDF-1.x header; cross-reference table must point within file
- ZIP: Local file headers + central directory; EOCD must be findable

### Audio (WAV/RIFF)
- RIFF header + fmt chunk must be valid; data chunk contains samples

## Carrier Sanity Checklist

Before submitting, verify:
1. Magic bytes match the expected format.
2. Header fields are self-consistent (declared sizes, counts, offsets).
3. Table directory / chunk offsets point within the file.
4. The mutation target field is at the correct offset and width.
5. If using a seed, the delta is localized to the target field (check with
   `PoCSanityCheck` or `hex_view`).
