---
name: cybergym-sfnt-pack
description: Use when the active CyberGym input format is confirmed or strongly suspected to be SFNT, TrueType, OpenType, TTC, WOFF, CFF/CFF2, FreeType, Harfbuzz, or font table parsing.
---

# SFNT Pack

## Workflow

1. Prefer a task-local font seed from `corpus_inspect`; do not synthesize a large generic font corpus at runtime.
2. Preserve the SFNT carrier first: magic, table directory, table offsets, table lengths, 4-byte alignment, table checksums, and `head.checkSumAdjustment`.
3. Mutate the vulnerable table after identifying the parser path: `cmap`, `glyf`, `gvar`, `CFF `, `CFF2`, `GSUB`, `GPOS`, `morx`, `kerx`, or `head`.
4. Before `submit_poc`, run `scripts/validate_candidate.py --candidate <poc>`.
5. If a recipe relies on fragile bytes, declare `ExpectedEffect.target_expression` as `sfnt.raw_contains:<marker>` or `sfnt.raw_contains:hex:<bytes>` so validation can detect mutation-lost candidates.

## Resource Navigation

- Read `references/invariants.md` when parser feedback mentions table directory, offset, length, checksum, alignment, or `checkSumAdjustment`.
- Read `references/harness_patterns.md` when deciding whether the harness consumes bytes, a file path, a face object, a glyph, a shaping buffer, or a table blob.

## Commands

Validate before submit:

```bash
python3 agent_impl/knowledge/packs/sfnt/scripts/validate_candidate.py --candidate pocs/poc_font.ttf
```

Repair offset-table search params and checksums:

```bash
python3 agent_impl/knowledge/packs/sfnt/scripts/repair_directory.py --seed seed.ttf --output pocs/poc_font_repaired.ttf --search-params --checksums --head-adjustment
```

Validate that a raw trigger survived repair:

```bash
python3 agent_impl/knowledge/packs/sfnt/scripts/validate_candidate.py --candidate pocs/poc_font.ttf --raw-marker '%SFNT_TRIGGER%'
```

## Repair Rules

- Table directory failure: realign offsets and lengths before changing trigger bytes.
- Checksum failure: recompute table checksums and `head.checkSumAdjustment` unless the checksum corruption is the intended trigger.
- CFF/CFF2 failure: preserve outer SFNT enough to route into the CFF parser, then mutate INDEX/count/offset bytes.
- Mutation lost: reapply the raw trigger after table/checksum repair; prefer raw byte mutation for fields that fontTools normalizes.
