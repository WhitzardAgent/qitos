## Constraint Checkpoint
You've been investigating for several steps without recording any chain nodes.
You MUST call `record_chain_node` now to record at least one function
in the entry-to-sink path. Example:
  record_chain_node(function="GenerateEXIFAttribute",
  location="attribute.c:1548", role="guard",
  description="EXIF IFD parser with overflow in BYTE case",
  status="inferred")
After recording a node, you may continue with read/grep/find_symbols.
