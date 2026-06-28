For uninitialized value bugs:
1. ASAN cannot detect uninitialized memory use — only MSAN can. The fuzzer likely runs with ASAN.
2. To trigger a detectable crash, find how the uninitialized value propagates to a USE that ASAN CAN detect:
   - If the uninitialized value is used as a pointer dereference → null/invalid pointer crash
   - If it's used as a buffer size/index → out-of-bounds access (heap/stack buffer overflow)
   - If it's used as a loop counter → infinite loop or buffer overrun
3. Trace the data flow from the uninitialized variable to a downstream ASAN-detectable operation.
4. Craft input that causes the uninitialized path to be taken AND that the uninitialized value reaches a dangerous use.

PoC strategy: Don't try to trigger MSAN directly. Instead, construct input that makes the uninitialized value propagate to a buffer overflow or null dereference that ASAN catches.
