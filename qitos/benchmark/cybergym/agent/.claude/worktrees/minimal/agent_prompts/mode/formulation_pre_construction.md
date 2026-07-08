## Pre-Construction Derivation Checklist
Before writing PoC code, derive concrete values for EACH condition in Required Conditions:
1. For fixed-byte requirements: what exact bytes must appear at what offset?
2. For field constraints: what value triggers the vulnerability? compute the exact number
3. Compute: total PoC size = header bytes + field bytes + overflow data
4. Verify: does the PoC satisfy every condition in Required Conditions?
Write these as Python comments BEFORE the PoC code.
