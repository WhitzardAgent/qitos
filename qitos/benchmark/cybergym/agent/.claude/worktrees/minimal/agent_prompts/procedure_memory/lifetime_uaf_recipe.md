# Lifetime / Use-After-Free PoC Recipe

## Vulnerability Pattern

A use-after-free (UAF) or lifetime bug occurs when a pointer is dereferenced
after the object it points to has been freed, released, or invalidated.
The typical chain involves two events:

```
invalidation event: free/release/unref/erase/realloc on an object
  -> later use event: dereference/read/copy/call on the same pointer or alias
```

This is a **two-event** vulnerability like uninit, but the "origin" is an
explicit invalidation rather than a missing initialization.

## Key Ingredients

1. **Invalidation event**: Identify what frees/invalidates the object:
   - `free(ptr)` / `delete ptr` / `realloc(ptr, 0)`
   - `release()` / `unref()` / `erase()` on a refcounted object
   - `clear()` / `reset()` on a container that owns the buffer
   - `realloc()` that moves the allocation, invalidating old pointers

2. **Use event**: Identify the later dereference:
   - Read from `ptr->field` after `free(ptr)`
   - Call through a function pointer stored in the freed object
   - Copy from the freed buffer
   - Use an alias (another pointer to the same freed memory)

3. **Sequence**: The invalidation must happen before the use, and both must
   execute in the same run. The use must not be guarded by a null check
   or ownership check that would prevent access after free.

4. **Paired endpoints**: Often the invalidation and use are in different
  functions. The static analysis may identify them as a "paired endpoint"
  — one is the causal site (invalidation) and the other is the crash site
  (use).

## Mutation Strategy

- **Trigger invalidation then use**: Construct input that causes the
  program to execute the invalidation path, then continue to the use path.
- **Duplicate free sequence**: For double-free, call the same free path
  twice. This may require triggering a re-entry loop or processing two
  records that share a resource.
- **Realloc pattern**: If `realloc` is the invalidation, the new size must
  differ from the old size to force a move. Set a size field to trigger
  growth or shrinkage.
- **Object lifetime gap**: After invalidation, the freed memory must not
  be overwritten before the use. This is often automatic in single-threaded
  harness runs.

## Step-by-Step Procedure

1. Identify the invalidation (causal site) and use (crash site) endpoints.
   Check if static analysis provides a "paired_endpoint" linking them.
2. Determine the input condition that triggers the invalidation.
3. Determine the input condition that reaches the use after invalidation.
4. Verify the sequence: invalidation must execute before the use.
5. Construct a carrier that triggers both events in order.
6. Run `PoCSanityCheck` to verify the carrier structure.
7. Submit the PoC.

## Common Pitfalls

- Missing the invalidation point — you find the use but not what frees the
  object. Look for `free`/`release`/`erase` in the same or calling functions.
- The invalidation and use are in different code paths that cannot both
  execute in a single run — look for shared state that survives across
  function calls (global/static variables, object fields).
- `realloc` invalidation is subtle: the pointer is updated in the realloc
  call, but aliases (saved pointers) are not.
- Reference counting: `unref` may only free when count reaches zero — the
  PoC must trigger enough `unref` calls to reach zero, then a `use`.
