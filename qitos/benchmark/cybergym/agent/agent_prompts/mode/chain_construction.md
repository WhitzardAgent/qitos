
## Chain Construction Mode

You are in **chain construction** mode. Your primary goal is to discover and record the complete entry-to-sink call chain, including all constraints (gates) along the path.

### MANDATORY: Record a gate after every read of chain node code

When you read a function that is part of the call chain, you MUST call `record_gate` before moving on. A read without a corresponding `record_gate` is wasted — the constraint will not appear in the board and cannot guide PoC construction.

**Workflow: read code → identify condition → call record_gate → proceed**

Example sequence:
1. read `coder_fuzzer.cc` → see `image.read(blob)` → `record_gate(node_order=0, gate_type="format_gate", description="Input must be a valid image that Magick::Image::read() accepts", status="confirmed")`
2. read `attribute.c:1553` → see `memcmp(info, "Exif", 4)` → `record_gate(node_order=1, gate_type="format_gate", description="EXIF profile must start with 'Exif' magic", status="confirmed")`
3. read more of `attribute.c` → see `if (offset >= length)` → `record_gate(node_order=1, gate_type="bounds_gate", description="IFD offset must be < length", status="confirmed")`

### Gate Types (with real patterns to look for)
- **format_gate**: `memcmp()`, magic byte checks, header validation, format signature detection
- **dispatch_gate**: `switch(tag)`, `if (tag == X)`, function pointer tables, coder selection
- **path_gate**: Early returns, `if (count > max) return`, error checks that skip processing
- **bounds_gate**: `if (offset + len > buf_size)`, `if (p + n > end)`, buffer size checks
- **value_gate**: Specific values that trigger a path (overflow conditions, sentinel values)

### Chain Building Strategy
1. **Find the harness entry**: Look for `LLVMFuzzerTestOneInput` or `main` in fuzz target files
2. **Read the entry function** → immediately record a format_gate for the input validation you see
3. **Trace the call chain**: From entry, find which functions are called until you reach the vulnerable sink
4. **Identify the sink**: The vulnerable function from the description is usually the sink
5. **Read the sink function** → record gates for the missing validations that cause the vulnerability
6. **For each intermediate function** → read and record dispatch/path gates

### Allowed Actions
You may use write/bash to construct early PoC candidates at any time. However, each PoC should target a specific gate you have recorded.

### Exit Criteria
Before focusing fully on PoC iteration, you should have:
- At least one **entry** node with at least one **confirmed gate**
- At least one **sink** node identified
- Gates covering the critical path from entry to sink
