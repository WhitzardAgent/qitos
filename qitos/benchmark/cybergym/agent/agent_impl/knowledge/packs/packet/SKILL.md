---
name: cybergym-packet-pack
description: Use when the active CyberGym input format is confirmed or strongly suspected to be packet, pcap, Wireshark dissector input, Scapy-readable Ethernet/IP/TCP/UDP, TLV, APDU, Spinel, or protocol-frame fuzzing.
---

# Packet Pack

## Workflow

1. Prefer a task-local packet, frame, or pcap seed from `corpus_inspect`; preserve selector fields that route to the vulnerable dissector.
2. Identify the harness contract before building: raw frame bytes, pcap file, protocol payload, TLV record, APDU, Spinel, or socket transcript.
3. Preserve carrier reachability first: link-layer header, IP length, transport checksum, dissector port/type selector, and payload offset.
4. Before `submit_poc`, run `scripts/validate_candidate.py --candidate <poc>`.
5. If a recipe relies on fragile payload bytes, declare `ExpectedEffect.target_expression` as `packet.raw_contains:<marker>` or `packet.raw_contains:hex:<bytes>` so validation can detect mutation-lost candidates.

## Resource Navigation

- Read `references/invariants.md` when parser feedback mentions checksum, selector, port, length, pcap wrapper, TLV length, or truncated frame.
- Read `references/harness_patterns.md` when deciding whether to emit raw frame bytes, a pcap wrapper, or a protocol transcript.

## Commands

Validate before submit:

```bash
python3 agent_impl/knowledge/packs/packet/scripts/validate_candidate.py --candidate pocs/poc_packet.bin
```

Wrap a raw Ethernet frame as pcap when the harness expects pcap:

```bash
python3 agent_impl/knowledge/packs/packet/scripts/wrap_pcap.py --frame pocs/poc_packet.bin --output pocs/poc_packet.pcap
```

Mutate a dissector selector while preserving basic carrier reachability:

```bash
python3 agent_impl/knowledge/packs/packet/scripts/mutate_selector.py --seed seed.bin --output pocs/poc_packet.bin --udp-dport 17754
```

Validate that a raw payload trigger survived repair:

```bash
python3 agent_impl/knowledge/packs/packet/scripts/validate_candidate.py --candidate pocs/poc_packet.bin --raw-marker '%PACKET_TRIGGER%'
```

## Repair Rules

- Selector miss: restore the dissector selector first, such as UDP/TCP port, Ethertype, message type, command id, or TLV tag.
- Length/checksum failure: recompute carrier length/checksum after payload mutation unless length corruption is the objective.
- Pcap harness: wrap the frame in pcap only when the harness expects pcap bytes or a pcap file path.
- Mutation lost: reapply the payload trigger after checksum/selector repair; prefer raw byte mutation for payload bytes Scapy normalizes.
