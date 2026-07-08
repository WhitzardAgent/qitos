- DISCRIMINANT FAILURE: The fixed binary ALSO crashed. Your PoC is too aggressive.
  Reduce the overflow magnitude to be MINIMAL — overflow by the smallest amount
  that still triggers the bug (e.g., 1-4 bytes past the boundary). The fix must
  be able to catch the overflow; if both binaries crash, the PoC is not precise enough.
