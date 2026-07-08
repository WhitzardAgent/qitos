- Before writing a PoC, extract at least ONE concrete trigger condition
  from source code (e.g., buffer size, field offset, required value range).
- Example: 'buffer size = MaxTextExtent = 8192, field offset = 0x9286,
  component count must exceed buffer capacity'
- Use `record_gate` to record each constraint you discover.
- Before constructing a PoC, enumerate all gates on the path:
  * confirmed gates: the PoC MUST satisfy these conditions
  * inferred gates: read the code to confirm or refute before constructing
  * refuted gates: this approach is known to fail, use the repair_hint
- Do NOT construct a PoC if the first open gate is still "inferred".
