"""PDF format helper: minimal carrier generation and structure inspection."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List


def minimal() -> bytes:
    """Generate a minimal valid PDF (1-page empty document)."""
    return b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>
endobj
xref
0 4
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
trailer
<< /Size 4 /Root 1 0 R >>
startxref
190
%%%%EOF
"""


def inspect(path: str) -> Dict[str, Any]:
    """Parse PDF file structure, returning object and xref details as a dict."""
    data = Path(path).read_bytes()
    result: Dict[str, Any] = {
        "format": "pdf",
        "size": len(data),
        "valid_signature": data[:5] == b'%PDF-',
    }

    if not result["valid_signature"]:
        result["error"] = "Invalid PDF signature"
        return result

    # Extract version
    version_line = data.split(b'\n')[0].decode("latin-1", errors="replace").strip()
    result["version"] = version_line

    # Find objects
    text = data.decode("latin-1", errors="replace")
    objects: List[Dict[str, Any]] = []
    import re
    for m in re.finditer(r'(\d+)\s+(\d+)\s+obj', text):
        obj_num = int(m.group(1))
        gen = int(m.group(2))
        offset = m.start()
        objects.append({"number": obj_num, "generation": gen, "offset": offset})

    result["object_count"] = len(objects)
    result["objects"] = objects

    # Check xref and trailer
    xref_pos = text.rfind("startxref")
    if xref_pos >= 0:
        result["startxref_offset"] = xref_pos
    eof_pos = text.rfind("%%EOF")
    result["has_eof_marker"] = eof_pos >= 0

    return result
