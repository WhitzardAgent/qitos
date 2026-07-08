# Packet Format Guidance

## Carrier Structure
- Packet harnesses consume raw bytes as protocol data
- Input contract: `buffer` (LLVMFuzzerTestOneInput), `packet` (dissector), `packet_stack` (layered), `apdu` (smartcard)
- Selector bytes at fixed offsets dispatch to different protocol dissectors

## Key Structural Elements
- **Selector fields**: First N bytes determine protocol type (e.g., DLT_PPP, Ethernet type)
- **Layer stack**: Ordered protocol layers (e.g., ethernet→ip→udp→rtps)
- **Checksums**: IP header checksum, UDP/TCP checksum (must be valid or zero for some harnesses)
- **Length fields**: IP total length, UDP length must match actual data

## Protected Fields (do NOT overwrite directly)
- Protocol checksums — recomputed from payload
- IP total length / UDP length — derived from actual packet size
- Selector bytes at dispatch offsets — must target the correct dissector

## Common Mutation Strategies
1. **Length field mismatch**: IP/UDP length > actual data → over-read
2. **Invalid protocol field**: Reserved bits or out-of-range values
3. **Layer boundary violation**: Inner protocol data shorter than outer length indicates
4. **Extension header loops**: IPv6 extension headers with infinite next-header chains
5. **Malformed TLV**: Type-Length-Value where length exceeds remaining data

## Transcript Harnesses
- Some harnesses expect multi-message sequences (APDU, Spinel, etc.)
- Protocol state machine: each message advances state
- Must provide complete transcript in correct order
- Use `protocol_transcript_plans` to determine required message sequence

## Construction Pitfalls
- Checksums must be valid for most dissectors — invalid checksums cause early rejection
- Selector bytes must target the correct dissector or harness rejects input
- Scapy may auto-fill missing fields — verify raw bytes match your intent
- For pcap-based harnesses: global header + per-packet headers wrap the actual packet data

## Format-Specific Sanity Checks
- IP header checksum valid (or harness explicitly skips check)
- Layer lengths consistent (outer length >= inner length)
- Selector bytes target the expected dissector
- For transcripts: message order and completeness
