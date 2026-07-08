
Binary format tip: Use `struct.pack('<I', size+1)` for the overflow field. Keep the rest of the carrier intact so it still parses past the header.
