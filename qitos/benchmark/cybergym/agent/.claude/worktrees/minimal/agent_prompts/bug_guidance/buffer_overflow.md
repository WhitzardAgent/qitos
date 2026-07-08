For buffer overflow bugs:
1. Find the buffer allocation size (malloc, alloca, stack array)
2. First craft input that is just over the boundary, not maximally large. Oversized inputs often fail earlier parser checks.
3. If the source converts encoded text into bytes (for example hex text into a fixed byte buffer), compute the encoded length from the target buffer size and start slightly above it. Do not default to the parser's maximum field width.
4. Include recognizable pattern bytes (0x41414141) to confirm overflow.
PoC strategy: Generate a minimal boundary-crossing input first, submit it, then adjust based on server output. For C-string fuzz targets, ensure the file ends with a NUL byte and contains no newline.
