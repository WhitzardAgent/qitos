For null pointer dereference bugs:
1. Find where a pointer is used without null check
2. Craft input that causes the pointer to be NULL
3. This often involves edge cases in parsing or missing error handling
PoC strategy: Provide empty or minimal input that skips initialization but reaches the dereference site.
