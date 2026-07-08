# SFNT/Font Format Guidance

## Carrier Structure
- SFNT files (TTF/OTF/TTC/WOFF) start with a table directory
- TTF magic: `\x00\x01\x00\x00`; OTF magic: `OTTO`; TTC magic: `ttcf`
- Table directory: 4-byte tag + 4-byte checksum + 4-byte offset + 4-byte length

## Key Structural Elements
- **Table directory**: sorted by tag, maps tag→offset+length+checksum
- **Required tables**: `head`, `hhea`, `maxp`, `post`, `name`, `cmap`, `OS/2`
- **`head` table**: `checkSumAdjustment` must make whole-file checksum = 0xB1B0AFBA
- **`loca` table**: glyph data offsets (short/long format, indexToLocFormat in head)
- **`glyf` table**: glyph outlines (simple/compound)

## Protected Fields (do NOT overwrite directly)
- Table checksums in directory — auto-recomputed from table content
- `head.checkSumAdjustment` — computed from whole-file checksum
- Table offsets in directory — recomputed when table sizes change
- `loca` offsets — recomputed from `glyf` table layout

## Common Mutation Strategies
1. **Table length mismatch**: Set table length smaller than actual → over-read
2. **Invalid glyph index**: `maxp.numGlyphs` smaller than actual glyph count
3. **Corrupted `loca`**: Offsets pointing outside `glyf` table
4. **Compound glyph recursion**: Self-referencing compound glyphs
5. **Invalid `cmap`**: Mapped glyph IDs exceeding numGlyphs

## Construction Pitfalls
- fontTools normalizes on save — verify mutation survives round-trip
- Tables must be 4-byte aligned; padding bytes added after each table
- `head.checkSumAdjustment` is a whole-file checksum — must be last field computed
- TTC (font collection) has additional `dsig` and offset table headers

## Format-Specific Sanity Checks
- Valid SFNT magic at offset 0
- Table directory checksums match actual table data
- `head.checkSumAdjustment` produces correct file checksum
- `loca` offsets within `glyf` table bounds
- `maxp.numGlyphs` consistent with `loca` entry count
