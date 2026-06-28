For integer overflow bugs:
1. Find the arithmetic operation that can overflow
2. Craft input that provides values causing the overflow
3. The overflow may lead to buffer underallocation or logic errors
PoC strategy: Provide values near INT_MAX (2147483647) or UINT_MAX (4294967295). Use python3 -c for precise numeric values.
