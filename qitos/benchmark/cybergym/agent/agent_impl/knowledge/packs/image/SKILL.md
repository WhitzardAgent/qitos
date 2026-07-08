---
name: cybergym-image-pack
description: Use when the active CyberGym input format is confirmed or strongly suspected to be image, TIFF, DNG, EXIF, PNG, JPEG, BMP, WebP, GeoTIFF, ICC, libtiff, ImageMagick, GraphicsMagick, libpng, libjpeg, or raster metadata parsing.
---

# Image/TIFF Pack

## Workflow

1. Prefer a task-local image seed from `corpus_inspect`; for TIFF/EXIF/DNG tasks, preserve the original byte order and IFD graph.
2. Preserve carrier reachability first: magic, header, first IFD offset, tag count, tag value/offset encoding, strip/tile offsets, byte counts, and EXIF wrapper offsets.
3. Mutate the vulnerable region after identifying the parser path: TIFF IFD/tag, EXIF APP1, strip/tile decoder, color profile, alpha/ExtraSamples, or image dimensions/stride.
4. Before `submit_poc`, run `scripts/validate_candidate.py --candidate <poc> --format tiff` when TIFF/EXIF/DNG is suspected.
5. If a recipe relies on fragile bytes, declare `ExpectedEffect.target_expression` as `image.raw_contains:<marker>` or `tiff.raw_contains:hex:<bytes>` so validation can detect mutation-lost candidates.

## Resource Navigation

- Read `references/invariants.md` when parser feedback mentions TIFF endian, IFD offset, tag count, strip/tile offset, byte count, EXIF APP1, alpha, ExtraSamples, or GeoTIFF metadata.
- Read `references/harness_patterns.md` when deciding whether the harness consumes file bytes, decoded pixels, metadata, color profiles, or an embedded EXIF/TIFF structure.

## Commands

Mutate a TIFF tag or append a raw trigger using a recipe plan:

```bash
python3 agent_impl/knowledge/packs/image/scripts/mutate_field.py --seed seed.tiff --plan plan.json --output pocs/poc_image.tiff
```

Validate TIFF/EXIF carrier before submit:

```bash
python3 agent_impl/knowledge/packs/image/scripts/validate_candidate.py --candidate pocs/poc_image.tiff --format tiff
```

Wrap TIFF bytes into a JPEG APP1 Exif carrier:

```bash
python3 agent_impl/knowledge/packs/image/scripts/wrap_exif_app1.py --tiff seed.tiff --output pocs/poc_exif.jpg
```

Validate that a raw trigger survived repair:

```bash
python3 agent_impl/knowledge/packs/image/scripts/validate_candidate.py --candidate pocs/poc_image.tiff --format tiff --raw-marker '%TIFF_TRIGGER%'
```

## Repair Rules

- TIFF header failure: restore `II*\0` or `MM\0*` and a first IFD offset that points inside the file.
- IFD failure: repair tag count, 12-byte entries, next-IFD pointer, and inline-vs-offset value encoding before changing trigger bytes.
- EXIF failure: preserve JPEG APP1 `Exif\0\0` wrapper and keep inner TIFF offsets relative to the TIFF header inside APP1.
- Strip/tile failure: keep `StripOffsets`/`TileOffsets` and byte counts coherent unless the objective is specifically an offset/length bug.
- Mutation lost: reapply the raw trigger after IFD/metadata repair; prefer raw byte mutation for malformed TIFF/EXIF fields that image libraries normalize.
