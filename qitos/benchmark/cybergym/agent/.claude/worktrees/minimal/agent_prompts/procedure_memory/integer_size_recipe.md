# Integer Overflow / Size Computation PoC Recipe

## Vulnerability Pattern

An integer overflow occurs when an arithmetic operation produces a result that
wraps around the integer type's representable range. The dangerous pattern is:

```
arithmetic expression (overflow/wrap) -> result feeds allocation/copy/loop consumer
```

The overflow alone is not the crash — the consumer that uses the wrapped
value (e.g., allocating too-small buffer, then copying too-much data) is
what triggers the observable crash.

## Key Ingredients

1. **Arithmetic expression**: Identify the computation that overflows:
   - `size = a * b` where `a * b > MAX_INT` (e.g., width * height * bpp)
   - `offset = base + delta` where sum wraps
   - `length = end - start` where subtraction underflows for unsigned types
   - `count = old_count + increment` where addition wraps

2. **Consumer**: Identify what uses the wrapped result:
   - `malloc(wrapped_small)` then `memcpy(dst, src, actual_large)`
   - Loop `for (i = 0; i < wrapped_small; ...)` that accesses beyond bounds
   - `realloc(ptr, wrapped_small)` then writing `actual_large` bytes
   - Comparison `if (wrapped >= threshold)` that takes wrong branch

3. **Input fields controlling the operands**: Which input fields feed the
   arithmetic expression? Often these are header fields (width, height,
   count, element_size, bpp, channels, depth).

## Mutation Strategy

- **Multiply overflow**: Set operand fields so that `a * b > 2^31` (for
  int32) or `> 2^63` (for int64). Example: width=65536, height=65536,
  bpp=4 => 17179869184 which wraps to a small number in 32-bit.
- **Addition wrap**: Set `base` near MAX and `delta` large enough to wrap.
- **Subtraction underflow (unsigned)**: Set `start > end` for unsigned
  subtraction, producing a very large value.
- **Sign confusion**: If a signed value is used where unsigned is expected,
  a negative value becomes very large (e.g., -1 -> 0xFFFFFFFF).

## Step-by-Step Procedure

1. Identify the arithmetic expression and its consumer.
2. Trace the operands back to input fields.
3. Determine the integer type and range (signed 32-bit, unsigned 64-bit, etc.).
4. Calculate input values that cause the expression to wrap to a "small" value
   (for allocation) while the actual data size is large.
5. Construct a carrier with the calculated field values.
6. Ensure the carrier still reaches the consumer after the overflow.
7. Run `PoCSanityCheck` — verify the carrier is parseable.
8. Submit the PoC.

## Common Pitfalls

- Only triggering the overflow without reaching the consumer — the overflow
  is harmless if the wrapped value is never used for allocation/copy.
- The compiler may optimize away the overflow (undefined behavior in C for
  signed overflow). Look for patterns where the program explicitly checks
  the result but the check is also affected by the overflow.
- Operand fields may be validated independently but not their product —
  `width <= MAX_WIDTH` and `height <= MAX_HEIGHT` but `width * height` is
  not checked.
- Endianness: multi-byte size fields may be read as wrong-endian, producing
  unexpected values.
