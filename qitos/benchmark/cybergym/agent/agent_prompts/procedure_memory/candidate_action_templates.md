# Candidate Action Templates

Use this auditable sequence for candidate construction:

```text
seed_select -> locate_field -> mutate_local_bytes -> sanity_check -> submit
```

## 1. Select a carrier

- Prefer the smallest valid corpus file matching the target format.
- If no compatible seed exists, construct a minimal carrier from parser and
  harness constraints.
- Record why the chosen carrier reaches the relevant parsing path.

## 2. Locate the controlled field

- Derive offset, width, endianness, and enclosing structure from concrete
  parser or harness evidence.
- For nested chunks or tables, calculate the cumulative file offset.
- Use `struct_probe` or `hex_view` to verify the field in the carrier.

## 3. Apply a local mutation

- Preserve unrelated magic bytes, headers, tables, checksums, and offsets.
- Write multi-byte values with the parser's actual endianness.
- If reachability and triggering require paired changes, apply both and state
  which constraint each mutation satisfies.

## 4. Check carrier sanity

- Run `PoCSanityCheck`, or use `file_info`, `hex_view`, and `struct_probe` when a
  format-specific checker is unavailable.
- Verify the carrier structure and the exact bytes of the intended mutation.
- Repair a `carrier_invalid` result before submission; warnings may proceed
  when their risk is understood.

## 5. Submit and revise

- Submit with `submit_poc` as soon as a coherent candidate exists.
- After `no_crash`, change one supported constraint or mutation axis at a
  time and retain negative evidence.
- Rotate to another ranked path after repeated equivalent misses instead of
  resubmitting the same hypothesis.
- Stop immediately when vulnerable-side verification reports a crash.

