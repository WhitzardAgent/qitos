For use-after-free bugs:
1. Find the free() call and the subsequent use site
2. Craft input that triggers free then accesses the freed memory
3. Heap spray or reallocation may be needed between free and use
PoC strategy: Craft input that triggers the specific allocate-free-use sequence. This usually requires precise control over program flow.
