# SEGV / Dispatch / Null Pointer PoC Recipe

## Vulnerability Pattern

A SEGV (segmentation fault) from null pointer dereference or bad dispatch
occurs when code accesses memory through an invalid pointer. Typical chains:

```
null pointer: function returns NULL on failure -> caller dereferences without check
bad dispatch: index/tag from input -> table lookup -> corrupted/missing entry -> call through bad pointer
corrupted pointer: input controls pointer arithmetic -> out-of-bounds access
```

## Key Ingredients

1. **Null pointer source**: Identify what can return NULL:
   - `malloc()` / `calloc()` on allocation failure (rare in harness context)
   - Lookup function returning NULL (e.g., `find_entry(id)` returns NULL when
     `id` is not found)
   - Factory function returning NULL on error
   - Virtual method table with missing override

2. **Dereference point**: Identify where the null/corrupted pointer is used:
   - `ptr->field` dereference
   - `(*callback)(args)` function pointer call
   - Array index: `table[index]` where `index` is out of bounds
   - Pointer arithmetic: `base + offset` where `offset` is corrupted

3. **Dispatch pattern**: For tag/index-based dispatch:
   - A switch or if-else that handles some tags but not others
   - A function pointer table where some entries are NULL or uninitialized
   - A vtable lookup that depends on a corrupted object header
   - A callback registration that is missing for certain paths

## Mutation Strategy

- **Trigger NULL return**: Set an input field (ID, key, name, tag) to a value
  that causes a lookup to return NULL, then ensure the code continues to
  dereference the result.
- **Missing dispatch entry**: Use a tag/index value that falls through to a
  default or unhandled case where a function pointer is NULL.
- **Corrupted vtable**: If the input controls an object's type tag, set it
  to an unexpected value so the vtable lookup produces a bad pointer.
- **Out-of-bounds index**: Set an array index to a value outside the table,
  potentially reading a NULL or garbage function pointer.

## Step-by-Step Procedure

1. Classify the SEGV type: null deref, bad dispatch, or corrupted pointer.
2. For null deref: find what returns NULL and what input triggers it.
3. For bad dispatch: find the dispatch table/switch and which entries are
   missing or NULL.
4. Trace the controlling input field back to the carrier.
5. Construct a carrier that sets the field to trigger the null/bad-dispatch.
6. Verify the code path continues to the dereference without early exit.
7. Run `PoCSanityCheck` — verify carrier structure.
8. Submit the PoC.

## Common Pitfalls

- Null deref that is caught by an error handler (e.g., `if (!ptr) return -1;`)
  — the code must NOT have a null check before the dereference.
- Dispatch tables that use bounds-checked indices — the index must bypass
  the check (e.g., wrong type comparison, off-by-one).
- Corrupted pointers that crash in a non-reproducible way depending on
  memory layout — prefer null-pointer triggers for reliability.
- Virtual dispatch in C++: the vtable pointer may be read from a base class
  that has been freed or never properly constructed.
