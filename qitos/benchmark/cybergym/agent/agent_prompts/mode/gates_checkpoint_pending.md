## Gates Checkpoint
You've recorded chain nodes but no path constraints (gates).
You MUST call `record_gate` now to record at least one condition
the PoC must satisfy to reach the sink. Example:
  record_gate(node_function="GenerateEXIFAttribute",
  gate_type="bounds_gate",
  description="buffer size check at attribute.c:1905",
  required_condition="oval+n must exceed buffer length",
  status="inferred", role="reachability")
Gate types: format_gate (magic bytes/headers), dispatch_gate
(what routes to target function), path_gate (branch flags),
bounds_gate (numeric ranges for OOB), value_gate (specific trigger values).
Gate roles: reachability (path to next node), trigger (vulnerability activation),
hazard (conservative warning), dataflow (parameter binding).
When confirming a suggested constraint, copy its role and path_id into record_gate.
After recording a gate, you may continue with READ/GREP/FIND_SYMBOLS.
