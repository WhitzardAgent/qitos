---
name: cybergym-pdf-pack
description: Use when the active CyberGym input format is confirmed or strongly suspected to be PDF, including Poppler/PDFium/MuPDF-style fuzz targets, PDF corpus magic, PDF parser APIs, xref/trailer failures, stream length bugs, object reference bugs, and carrier parse repair for PDF PoCs.
---

# PDF Pack

## Workflow

1. Prefer a task-local PDF seed from `corpus_inspect`; use `fixtures/minimal_pdf_1_4.pdf` only when no task-local seed exists and minimal-carrier fallback is acceptable.
2. Preserve the PDF carrier first: `%PDF-` header, objects, `xref`, `trailer`, `/Root`, `startxref`, and `%%EOF`.
3. For fragile construction, run `scripts/build_minimal.py`, `scripts/mutate_field.py`, or `scripts/validate_candidate.py` instead of rewriting PDF bytes in the prompt.
4. Before `submit_poc`, validate structure with `scripts/validate_candidate.py` or `python3 -m toolbox pdf inspect --file <poc>`.
5. If `submit_poc` reports carrier parse failure, repair xref/trailer/header before changing trigger bytes.
6. If a recipe relies on a fragile raw trigger, declare it in `ExpectedEffect.target_expression` with `raw_contains:<marker>` or `pdf.raw_contains:<marker>` so validation can detect mutation-lost candidates after carrier repair.

## Resource Navigation

- Read `references/invariants.md` when parser feedback mentions xref, trailer, startxref, object table, EOF, or stream length.
- Read `references/field_map.md` when choosing mutation targets for stream length, xref offset, object reference, or trailer fields.
- Read `references/harness_patterns.md` when deciding whether the harness consumes bytes, a file path, a document object, or a page/rendering API.
- Use `corpus/index.jsonl` only as curated metadata; do not import external internet corpora or ground-truth PoCs as runtime seeds.

## Commands

Build a minimal carrier:

```bash
python3 agent_impl/knowledge/packs/pdf/scripts/build_minimal.py --output pocs/poc_pdf_minimal.pdf
```

Apply a local mutation plan:

```bash
python3 agent_impl/knowledge/packs/pdf/scripts/mutate_field.py --seed seed.pdf --plan plan.json --output pocs/poc_pdf_mutated.pdf
```

Example stream-length mutation plan:

```json
{"operations":[{"op_id":"stream_len_to_999","kind":"mutate_stream_length","stream_index":0,"value":999,"preserve_width":true}]}
```

Validate before submit:

```bash
python3 agent_impl/knowledge/packs/pdf/scripts/validate_candidate.py --candidate pocs/poc_pdf_mutated.pdf
```

## Repair Rules

- Header mismatch: rebuild from minimal PDF or copy a task-local PDF seed, then reapply the trigger.
- Xref/startxref failure: regenerate or preserve offsets; do not append bytes after `%%EOF` unless the target parser accepts trailing data.
- Stream length bug: mutate `/Length` or stream bytes while preserving object and trailer reachability.
- Mutation lost: reapply the raw trigger after carrier repair; prefer raw byte mutation for bytes that structured PDF libraries normalize.
- Wrong path/no crash: keep the PDF valid and change the target object, page/resource path, or stream/filter that routes execution to the vulnerable function.
