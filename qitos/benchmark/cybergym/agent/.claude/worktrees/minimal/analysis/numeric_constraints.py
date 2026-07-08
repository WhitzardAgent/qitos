"""Numeric constraint extraction — finds overflow/underflow/boundary patterns.

Detects numeric constraints from source code that are relevant to PoC
trigger conditions: size_t→int truncation, signed/unsigned compare,
length underflow, count*stride overflow, exact boundary, and array
index bounds.

Ported patterns from tree-sitter-analyzer constraint analysis.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def extract_numeric_constraints(
    *,
    source_files: list[str],
    repo_root: str = "",
    suspect_functions: list[str] | None = None,
    crash_type: str = "",
) -> list[dict[str, Any]]:
    """Extract numeric constraints from source files.

    Returns a list of constraint dicts, each with:
      constraint_id, ranked_path_id, kind, formula, input_fields,
      candidate_values, source
    """
    constraints: list[dict[str, Any]] = []
    suspect_set = set(suspect_functions or [])
    counter = 0
    repo = Path(repo_root) if repo_root else Path(".")

    for rel in source_files:
        abs_path = repo / rel
        if not abs_path.is_file():
            continue
        try:
            text = abs_path.read_text(errors="replace")
        except OSError:
            continue

        lines = text.splitlines()

        for i, line in enumerate(lines):
            line_num = i + 1

            # Only analyze lines in/near suspect functions
            if suspect_set:
                # Check if this line is within a suspect function's body
                # Simple heuristic: check if any suspect function name
                # appears within ~80 lines before
                context_start = max(0, i - 80)
                context = "\n".join(lines[context_start:i + 1])
                if not any(fn in context for fn in suspect_set):
                    continue

            # Pattern 1: size_t → int truncation
            for match in re.finditer(
                r'\bint\s+(\w+)\s*=\s*(?:\(int\))?\s*(\w+)',
                line,
            ):
                var_name = match.group(1)
                source_var = match.group(2)
                if source_var in ("0", "1"):
                    continue
                counter += 1
                constraints.append({
                    "constraint_id": f"nc_{counter:04d}",
                    "ranked_path_id": "",
                    "kind": "overflow",
                    "formula": f"{var_name} = (int){source_var}  // truncation",
                    "input_fields": [
                        {"name": source_var, "offset": None, "width": 4, "endian": "native"},
                    ],
                    "candidate_values": [
                        {"field": source_var, "value": "0x7fffffff + 1", "reason": "exceeds INT_MAX after truncation"},
                    ],
                    "source": [{"file": rel, "line": line_num, "expr": match.group(0)}],
                })

            # Pattern 2: signed/unsigned comparison
            for match in re.finditer(
                r'\b(\w+)\s*([<>]=?|==|!=)\s*(\d+|0x[\da-fA-F]+)',
                line,
            ):
                var_name = match.group(1)
                op = match.group(2)
                value_str = match.group(3)
                # Check if the variable might be unsigned
                var_context = "\n".join(lines[max(0, i - 10):i + 1])
                is_unsigned = bool(re.search(
                    r'\b(?:unsigned|size_t|uint\d+_t|u_int\d+_t)\b',
                    var_context,
                ))
                value = int(value_str, 0)
                if is_unsigned and value < 0:
                    counter += 1
                    constraints.append({
                        "constraint_id": f"nc_{counter:04d}",
                        "ranked_path_id": "",
                        "kind": "signedness",
                        "formula": f"{var_name} {op} {value}  // unsigned vs signed compare",
                        "input_fields": [
                            {"name": var_name, "offset": None, "width": 4, "endian": "native"},
                        ],
                        "candidate_values": [
                            {"field": var_name, "value": hex(0xFFFFFFFF), "reason": "unsigned wrap-around"},
                        ],
                        "source": [{"file": rel, "line": line_num, "expr": match.group(0)}],
                    })

            # Pattern 3: length - header_len underflow
            for match in re.finditer(
                r'(\w+)\s*[+-]\s*(\d+)\s*([<>]=?|==)\s*(\w+)',
                line,
            ):
                left_var = match.group(1)
                offset = int(match.group(2))
                op = match.group(3)
                right_var = match.group(4)
                if "-" in match.group(0) and offset > 0:
                    counter += 1
                    constraints.append({
                        "constraint_id": f"nc_{counter:04d}",
                        "ranked_path_id": "",
                        "kind": "underflow",
                        "formula": f"{left_var} - {offset} {op} {right_var}  // underflow if {left_var} < {offset}",
                        "input_fields": [
                            {"name": left_var, "offset": None, "width": 2, "endian": "be"},
                        ],
                        "candidate_values": [
                            {"field": left_var, "value": str(offset - 1), "reason": "triggers underflow"},
                        ],
                        "source": [{"file": rel, "line": line_num, "expr": match.group(0)}],
                    })

            # Pattern 4: count * stride overflow
            for match in re.finditer(
                r'(\w+)\s*\*\s*(?:sizeof\((\w+)\)|(\d+))',
                line,
            ):
                count_var = match.group(1)
                type_name = match.group(2) or ""
                stride = match.group(3) or "sizeof(struct)"
                counter += 1
                constraints.append({
                    "constraint_id": f"nc_{counter:04d}",
                    "ranked_path_id": "",
                    "kind": "overflow",
                    "formula": f"{count_var} * {stride}  // count*stride overflow",
                    "input_fields": [
                        {"name": count_var, "offset": None, "width": 2, "endian": "be"},
                    ],
                    "candidate_values": [
                        {"field": count_var, "value": "0xFFFF", "reason": "oversize count"},
                    ],
                    "source": [{"file": rel, "line": line_num, "expr": match.group(0)}],
                })

            # Pattern 5: exact boundary (1024, 512, page_size)
            for match in re.finditer(
                r'(\w+)\s*([<>]=?|==)\s*(1024|512|4096|2048|256|128|0x[0-9a-fA-F]+)\b',
                line,
            ):
                var_name = match.group(1)
                op = match.group(2)
                boundary = match.group(3)
                counter += 1
                constraints.append({
                    "constraint_id": f"nc_{counter:04d}",
                    "ranked_path_id": "",
                    "kind": "boundary",
                    "formula": f"{var_name} {op} {boundary}",
                    "input_fields": [
                        {"name": var_name, "offset": None, "width": 4, "endian": "native"},
                    ],
                    "candidate_values": [
                        {"field": var_name, "value": str(int(boundary, 0)), "reason": f"exact boundary {boundary}"},
                    ],
                    "source": [{"file": rel, "line": line_num, "expr": match.group(0)}],
                })

            # Pattern 6: array index >= count or < 0
            for match in re.finditer(
                r'(\w+)\s*\[\s*(\w+)\s*\]',
                line,
            ):
                arr_name = match.group(1)
                idx_var = match.group(2)
                # Check nearby for bounds check
                context = "\n".join(lines[max(0, i - 5):i + 6])
                has_bounds_check = bool(re.search(
                    rf'\b{idx_var}\b\s*[<>]=?\s*\d+|if\s*\(\s*{idx_var}',
                    context,
                ))
                if not has_bounds_check:
                    counter += 1
                    constraints.append({
                        "constraint_id": f"nc_{counter:04d}",
                        "ranked_path_id": "",
                        "kind": "overflow",
                        "formula": f"{arr_name}[{idx_var}]  // unbounded index",
                        "input_fields": [
                            {"name": idx_var, "offset": None, "width": 2, "endian": "native"},
                        ],
                        "candidate_values": [
                            {"field": idx_var, "value": "0xFFFF", "reason": "out-of-bounds index"},
                        ],
                        "source": [{"file": rel, "line": line_num, "expr": match.group(0)}],
                    })

    # Deduplicate by formula
    seen_formulas: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in constraints:
        key = c["formula"]
        if key not in seen_formulas:
            seen_formulas.add(key)
            unique.append(c)

    return unique[:30]
