- PARTIAL HIT: Vulnerable binary crashes but fix-side precision is UNVERIFIED.
  The PoC must be PRECISE enough that the fix can prevent the crash.
  Reduce overflow magnitude to minimal (1-4 bytes past boundary),
  target the exact vulnerable field/offset, and ensure only the vulnerable
  code path is exercised. Study the patch diff if available to understand
  what the fix checks. If both binaries crash, the PoC will be rejected.
