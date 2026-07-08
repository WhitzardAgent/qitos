# PDF Format Guidance

## Carrier Structure
- PDF files start with `%PDF-1.X` magic header (5+ bytes)
- End with `%%EOF` marker
- Core structure: objects → xref table → trailer → startxref offset

## Key Structural Elements
- **Objects**: numbered `N 0 obj ... endobj` containers for dicts, streams, arrays
- **XRef table**: cross-reference table mapping object IDs to byte offsets
- **Trailer**: dict with /Root, /Size, /Info references + startxref offset
- **Streams**: `/Length` must match actual stream byte count after `stream\n` and before `\nendstream`

## Protected Fields (do NOT overwrite directly)
- `/Length` on stream objects — auto-recomputed from stream content
- XRef offsets — recomputed when any object changes size
- `startxref` — points to xref table start, must be updated after xref changes

## Common Mutation Strategies
1. **Stream length mismatch**: Set `/Length` to value smaller than actual stream → buffer over-read
2. **Corrupted xref**: Invalid offsets cause parser to read arbitrary memory
3. **Object reference loops**: Circular /Parent or /Kids references
4. **Deep nesting**: Excessive /Pages tree depth or inline image nesting
5. **Invalid stream filters**: Unsupported /Filter values cause fallback decoder errors

## Construction Pitfalls
- pikepdf auto-repairs on save — verify your mutation survives round-trip
- Stream `/Length` must be exact or parser will read past boundary
- XRef must be consistent or reader falls back to linear scan (slow but safe)
- Always check that the target malformed field persists after build

## Format-Specific Sanity Checks
- `%PDF` magic present at offset 0
- `startxref` points to valid xref offset
- Object count matches xref entries
- Stream `/Length` values match actual stream sizes
