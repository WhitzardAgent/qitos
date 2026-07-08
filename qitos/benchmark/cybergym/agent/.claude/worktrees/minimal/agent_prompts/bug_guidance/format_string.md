For format string bugs:
1. Find where user input is passed as format string to printf-family
2. Craft input containing format specifiers like %s%s%s or %n
3. This causes reads/writes to arbitrary memory
PoC strategy: Use write with format specifiers as the PoC content.
