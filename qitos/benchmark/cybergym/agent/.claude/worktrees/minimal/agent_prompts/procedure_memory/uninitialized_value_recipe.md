# Uninitialized Value PoC Recipe

## Vulnerability Pattern

An uninitialized value bug occurs when memory is read before being written.
The typical chain involves two events:

```
origin event: a conditional/error path skips the write that initializes a variable
  -> the variable is later consumed (branch condition, copy, comparison, hash, serialize)
```

This is a **two-event** vulnerability: you must trigger the skip AND reach the
consumer on the same execution path.

## Key Ingredients

1. **Origin event**: Identify what causes the initialization to be skipped:
   - An error return from a sub-function (e.g., `if (read_field() < 0) goto err;`)
   - A conditional path that skips the assignment
   - A `calloc`/`memset` that is bypassed under specific conditions
   - A `realloc` that returns a new buffer with uninitialized trailing bytes

2. **Consumer event**: Identify what reads the uninitialized value:
   - A branch condition (`if (val > threshold)`)
   - A comparison or hash computation
   - A copy/serialization that outputs the value
   - A memory operation that uses the value as size/index

3. **Path connection**: The origin skip and the consumer must both execute
   in the same run. Often the skip is on an error path that returns
   "success" or continues to the consumer.

## Mutation Strategy

- **Trigger the skip**: Craft input that causes the initialization function
  to fail or be bypassed. This often means providing a malformed sub-record,
  a truncated field, or an out-of-range value that triggers the early return.
- **Reach the consumer**: After the skip, the parser must continue (not abort)
  to the consumer point. Ensure the error path does not exit the function.
- **Amplify the observable effect**: If the consumer is a branch, the
  uninitialized value may be non-deterministic. To make the crash reproducible:
  - If possible, influence the uninitialized stack/heap contents via earlier
    input processing.
  - If the consumer is a size/copy argument, any non-zero uninitialized value
    may cause a detectable crash.

## Step-by-Step Procedure

1. Identify both the origin (where init is skipped) and the consumer (where
   value is used). Both are needed — a single point is not enough.
2. Determine the input condition that triggers the skip (error path, short
   read, conditional bypass).
3. Verify that the code path continues to the consumer after the skip.
4. Construct a carrier that reaches both the origin and consumer events.
5. Mutate the triggering field to activate the skip condition.
6. Run `PoCSanityCheck` — the carrier must still be parseable up to the
   consumer point.
7. Submit the PoC.

## Common Pitfalls

- Only finding the consumer without identifying the origin — the PoC may not
  trigger the bug because the variable is always initialized in practice.
- The origin skip also causes an early exit — the consumer is unreachable.
  Look for error paths that set a flag but continue, or that return a value
  that is checked later but not always.
- Stack-initialized values may vary between runs; heap values from `malloc`
  may be zero in sanitizers but non-zero in production.
- The consumer may be in a different function — trace the flow of the
  uninitialized variable across function boundaries.
