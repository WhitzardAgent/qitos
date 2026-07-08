- Use `record_chain_node` to record each function in the
  entry-to-sink call chain as you discover it.
  Example: record_chain_node(function="GenerateEXIFAttribute",
  location="attribute.c:1548", role="guard",
  description="EXIF IFD parser with overflow in BYTE case",
  status="confirmed")
- Use `record_gate` to record each condition the PoC must satisfy
  or that blocks the path.
  Example: record_gate(node_function="GenerateEXIFAttribute",
  gate_type="bounds_gate",
  description="oval+n > length check at line 1905",
  required_condition="oval+n must wrap on 32-bit overflow",
  status="inferred", role="reachability")
- Gate types: format_gate (magic bytes), path_gate (branch condition),
  dispatch_gate (routing to sub-parser), bounds_gate (size/offset check),
  value_gate (specific value requirement)
- Gate roles: reachability (path to next node), trigger (vulnerability activation),
  hazard (conservative warning), dataflow (parameter binding).
  When confirming a suggested constraint, copy its role and path_id.
- Use `sink` to propose a new sink candidate
  if you discover a vulnerable function not in the current list,
  or to upgrade an existing low-confidence candidate.
  If upgrading a static ranked path, include candidate_role and ranked_path_id.
  Use path_anchor only for an intermediate route node, not as a final crash target.
