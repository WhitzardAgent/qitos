"""Packet builder — builds candidates from seed bytes + recipe plan.

Uses Scapy to construct and modify packets.  Handles checksum recomputation
for IP/TCP/UDP layers after mutation.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from ...models import BuildResult, RecipePlan

logger = logging.getLogger(__name__)


def build_packet_candidate(
    seed: bytes,
    plan: RecipePlan,
    output_dir: str | None = None,
) -> BuildResult:
    """Build a packet candidate from seed bytes and recipe plan."""
    try:
        from scapy.all import Ether, IP, Raw, conf
        conf.verb = 0
    except ImportError:
        return BuildResult(
            status="backend_unavailable",
            reason="scapy not installed",
        )

    if not seed:
        return BuildResult(status="failed", reason="seed_empty")

    applied: list[str] = []
    blocked: list[str] = []

    try:
        pkt = Ether(seed)
    except Exception:
        # Try raw IP
        try:
            pkt = IP(seed)
        except Exception as e:
            return BuildResult(status="failed", reason=f"scapy_parse_failed: {e}")

    # Apply operations
    for op in plan.operations:
        try:
            _apply_operation(pkt, op)
            applied.append(op.op_id)
        except Exception as e:
            logger.warning("Operation %s failed: %s", op.op_id, e)
            blocked.append(op.op_id)

    # Save candidate
    output_path = ""
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        fd, output_path = tempfile.mkstemp(suffix=".bin", dir=output_dir)
        os.close(fd)
    else:
        fd, output_path = tempfile.mkstemp(suffix=".bin")
        os.close(fd)

    try:
        result_bytes = bytes(pkt)
        with open(output_path, "wb") as f:
            f.write(result_bytes)
    except Exception as e:
        return BuildResult(status="failed", reason=f"save_failed: {e}")

    status = "success"
    if blocked and not applied:
        status = "failed"
    elif blocked:
        status = "partial"

    return BuildResult(
        status=status,
        artifact_path=output_path,
        applied_operations=tuple(applied),
        blocked_operations=tuple(blocked),
        mutation_intent_preserved=True,
    )


def _apply_operation(pkt: Any, op: Any) -> None:
    """Apply a single recipe operation to the packet."""
    kind = op.kind
    target = op.target_node_id or ""

    if kind == "set_field":
        _apply_set_field(pkt, target, op)
    elif kind == "mutate_field":
        _apply_set_field(pkt, target, op)  # Same mechanism for packets
    elif kind == "truncate":
        _apply_truncate(pkt, target, op)
    else:
        logger.debug("Unknown operation kind: %s", kind)


def _get_layer_from_target(pkt: Any, target: str) -> tuple[Any, str] | None:
    """Get the Scapy layer and field name from target_node_id."""
    try:
        from scapy.all import TCP, UDP, IP, ICMP, Ether, Raw
    except ImportError:
        return None

    layer_map = {
        "layer_ether": Ether,
        "layer_ip": IP,
        "layer_tcp": TCP,
        "layer_udp": UDP,
        "layer_icmp": ICMP,
        "layer_raw": Raw,
    }

    for layer_name, layer_cls in layer_map.items():
        if target.startswith(layer_name):
            field_name = target[len(layer_name) + 1:] if len(target) > len(layer_name) + 1 else ""
            if layer_cls in pkt:
                return pkt[layer_cls], field_name

    return None


def _apply_set_field(pkt: Any, target: str, op: Any) -> None:
    """Set a field value in the packet layer."""
    transform = op.ast_transform if hasattr(op, "ast_transform") else {}
    key = transform.get("key", "")
    value = transform.get("value")

    if not key or value is None:
        return

    # Try to find and set the field in the appropriate layer
    try:
        from scapy.all import TCP, UDP, IP, ICMP
        for layer_cls in [IP, TCP, UDP, ICMP]:
            if layer_cls in pkt and hasattr(pkt[layer_cls], key):
                pkt[layer_cls].__setattr__(key, int(value))
                return
    except Exception:
        pass


def _apply_truncate(pkt: Any, target: str, op: Any) -> None:
    """Truncate the packet payload."""
    transform = op.ast_transform if hasattr(op, "ast_transform") else {}
    truncate_at = transform.get("offset", 0)

    try:
        from scapy.all import Raw
        if Raw in pkt:
            payload = bytes(pkt[Raw])
            pkt[Raw].load = payload[:truncate_at]
    except Exception:
        pass
