#!/usr/bin/env python3
"""Apply Image/TIFF recipe operations to a seed candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[6]))

from cybergym_agent.agent_impl.knowledge.recipe_ir import recipe_from_dict
from cybergym_agent.agent_impl.knowledge.packs.image.mutator import apply_image_operations


def main() -> int:
    parser = argparse.ArgumentParser(description="Mutate Image/TIFF fields from a recipe plan")
    parser.add_argument("--seed", required=True, help="Input TIFF/image seed")
    parser.add_argument("--plan", required=True, help="RecipePlan JSON or operation-list JSON")
    parser.add_argument("--output", required=True, help="Output candidate path")
    args = parser.parse_args()

    seed_path = Path(args.seed)
    if not seed_path.is_file():
        print(json.dumps({
            "status": "error",
            "reason": "seed_not_found",
            "seed": str(seed_path),
        }, sort_keys=True))
        return 1

    try:
        plan_data = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({
            "status": "error",
            "reason": "plan_read_failed",
            "error": str(exc),
        }, sort_keys=True))
        return 1

    if "operations" not in plan_data and isinstance(plan_data, list):
        plan_data = {"operations": plan_data}
    plan = recipe_from_dict(plan_data)
    if plan is None:
        print(json.dumps({
            "status": "error",
            "reason": "invalid_plan",
        }, sort_keys=True))
        return 1

    seed = seed_path.read_bytes()
    output, applied, blocked = apply_image_operations(seed, plan)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(output)

    status = "success" if applied and not blocked else ("partial" if applied else "blocked")
    print(json.dumps({
        "status": status,
        "seed": str(seed_path),
        "output": str(out),
        "input_size": len(seed),
        "output_size": len(output),
        "applied_operations": list(applied),
        "blocked_operations": list(blocked),
        "sha256": hashlib.sha256(output).hexdigest(),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
