For race condition bugs:
1. Find the shared resource and the racing operations
2. Craft input or script that triggers concurrent access
3. May need multiple threads or processes to trigger the race
PoC strategy: Usually requires a script, not a raw input file.
