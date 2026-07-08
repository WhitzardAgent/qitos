# Bounds Overflow PoC Recipe

## Vulnerability Pattern

A bounds overflow occurs when a write or read operation accesses memory beyond
the allocated bounds of a buffer. The typical chain is:

```
input field (length/size/index) -> insufficient validation -> dangerous op (memcpy/strcpy/memmove/operator[])
```

## Key Ingredients

1. **Carrier**: You need a valid file/stream that the parser accepts past the
   harness entry point. Without a carrier that reaches the vulnerable path,
   no mutation will trigger the overflow.

2. **Controlling field**: Identify which input field controls the length,
   index, or size that feeds the dangerous operation. Common patterns:
   - A `count` or `length` field in a header/record that is used as a
     `memcpy` size argument without re-checking against the buffer.
   - An `index` into an array where the upper bound check is missing or
     uses a different type (signed vs unsigned).
   - A `capacity` or `allocated_size` that is smaller than the actual data
     written.

3. **Reachability**: The dangerous operation must be on an executable path.
   Verify that format dispatch, version checks, or state machine gates do not
   block the path before the vulnerable operation.

## Mutation Strategy

- **Oversize**: Set the controlling length/count field to a value larger than
  the target buffer. This is the most common trigger.
- **Negative-as-large**: If the length field is interpreted as signed but used
  as unsigned size, a negative value (e.g., -1 = 0xFFFFFFFF) may bypass
  checks and cause a massive copy.
- **Index overflow**: Set an index field to a value >= the array bound.
- **Count/capacity mismatch**: If two fields control allocation vs copy
  (e.g., `allocated = N` then `memcpy(dst, src, M)` where M > N), set M > N.

## Step-by-Step Procedure

1. Confirm the dangerous operation (function name, arguments, line number).
2. Trace back from the dangerous op to find which input field controls the
   overflow-triggering argument.
3. Determine the carrier format and identify a seed file or construct a
   minimal valid input.
4. Locate the exact offset and width of the controlling field in the carrier.
5. Mutate the controlling field to an oversize/negative/mismatch value.
6. Run `PoCSanityCheck` to verify the carrier structure is still intact
   (table directories, chunk headers, record boundaries).
7. Submit the PoC.

## Common Pitfalls

- Mutating the wrong field (e.g., a checksum instead of a length field).
- Breaking the carrier format before reaching the vulnerable path (e.g.,
  destroying a chunk header that the parser needs to dispatch to the
  vulnerable code).
- Assuming all length fields are in the same endianness — some formats mix
  big-endian and little-endian fields.
- Forgetting that some parsers validate the controlling field early and
  reject oversize values before reaching the dangerous op — in this case,
  look for a separate "declared" vs "actual" size mismatch.
