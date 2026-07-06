"""PDF parser — uses pikepdf to extract structural fields and node map.

Extracts: object count, xref structure, trailer keys, stream objects,
page tree.  Produces a field_map with named structural nodes that
the builder and validator use for targeted mutation and verification.
"""

from __future__ import annotations

import logging
from typing import Any

from ...models import FieldInfo, ParseResult

logger = logging.getLogger(__name__)


def parse_pdf(artifact: bytes, context: dict[str, Any] | None = None) -> ParseResult:
    """Parse a PDF artifact using pikepdf.

    Returns a ParseResult with field_map keyed by structured names like:
      - pdf.xref.offset
      - pdf.trailer.root
      - pdf.object.N.type
      - pdf.object.N.stream.length
      - pdf.object.N.stream.offset
    """
    try:
        import pikepdf
    except ImportError:
        return ParseResult(status="backend_unavailable")

    if not artifact or len(artifact) < 16:
        return ParseResult(status="failed", parse_warnings=("artifact_too_short",))

    try:
        from io import BytesIO
        pdf = pikepdf.Pdf.open(BytesIO(artifact))
    except pikepdf.PasswordError:
        return ParseResult(
            status="partial",
            carrier_family="pdf",
            parse_warnings=("password_protected",),
        )
    except pikepdf.PdfError as e:
        return ParseResult(
            status="failed",
            carrier_family="pdf",
            parse_warnings=(f"pikepdf_error: {e}",),
        )

    field_map: dict[str, FieldInfo] = {}
    warnings: list[str] = []
    evidence_ids: list[str] = []

    # Version
    version = ""
    try:
        version = str(pdf.pdf_version) if pdf.pdf_version else ""
    except Exception:
        pass

    # Object count
    obj_count = 0
    try:
        obj_count = len(pdf.objects)
    except Exception:
        warnings.append("could_not_count_objects")

    # Xref offset — the byte offset of the xref table or xref stream
    xref_offset = 0
    try:
        # pikepdf exposes the xref section offset via internal _accessor
        # For robustness, search the artifact for startxref
        text = artifact
        startxref_marker = b"startxref"
        idx = text.rfind(startxref_marker)
        if idx >= 0:
            # Read the number after startxref
            after = text[idx + len(startxref_marker):idx + len(startxref_marker) + 32]
            after = after.strip()
            end = 0
            while end < len(after) and (after[end:end+1].isdigit()):
                end += 1
            if end > 0:
                xref_offset = int(after[:end])
    except Exception:
        warnings.append("xref_offset_parse_failed")

    field_map["pdf.xref.offset"] = FieldInfo(
        name="pdf.xref.offset",
        offset=xref_offset,
        width=0,  # variable-width offset field
        value=xref_offset,
        node_id="xref",
        derived=True,
    )
    evidence_ids.append("pdf.xref.offset")

    # Trailer / Root
    try:
        root = pdf.Root
        if root is not None:
            root_obj = root.object if hasattr(root, "object") else root
            root_ref = str(root_obj) if root_obj else ""
            field_map["pdf.trailer.root"] = FieldInfo(
                name="pdf.trailer.root",
                offset=0,
                width=0,
                value=root_ref,
                node_id="trailer",
                protected=True,
            )
            evidence_ids.append("pdf.trailer.root")
    except Exception:
        warnings.append("trailer_root_unavailable")

    # Page count
    try:
        page_count = len(pdf.pages)
        field_map["pdf.pages.count"] = FieldInfo(
            name="pdf.pages.count",
            offset=0,
            width=0,
            value=page_count,
            node_id="pages",
        )
        evidence_ids.append("pdf.pages.count")
    except Exception:
        warnings.append("page_count_unavailable")

    # Stream objects — extract offset and length for mutation targets
    try:
        for obj_num in list(pdf.objects)[:100]:  # limit to first 100 objects
            obj = pdf.objects[obj_num]
            obj_id = f"pdf.object.{obj_num}"

            # Type
            obj_type = ""
            try:
                if hasattr(obj, "Type"):
                    obj_type = str(obj.Type)
                elif hasattr(obj, "get") and obj.get("/Type"):
                    obj_type = str(obj.get("/Type"))
            except Exception:
                pass

            if obj_type:
                field_map[f"{obj_id}.type"] = FieldInfo(
                    name=f"{obj_id}.type",
                    offset=0,
                    width=0,
                    value=obj_type,
                    node_id=f"obj_{obj_num}",
                )

            # Stream
            try:
                raw_stream = obj.get_raw_stream() if hasattr(obj, "get_raw_stream") else None
                if raw_stream is not None:
                    stream_len = len(raw_stream)
                    # Stream length from /Length key
                    length_val = 0
                    try:
                        length_val = int(obj.get("/Length", 0))
                    except (TypeError, ValueError):
                        pass

                    field_map[f"{obj_id}.stream.length"] = FieldInfo(
                        name=f"{obj_id}.stream.length",
                        offset=0,
                        width=0,
                        value=length_val,
                        node_id=f"obj_{obj_num}_stream",
                        derived=True,
                    )
                    field_map[f"{obj_id}.stream.actual_length"] = FieldInfo(
                        name=f"{obj_id}.stream.actual_length",
                        offset=0,
                        width=0,
                        value=stream_len,
                        node_id=f"obj_{obj_num}_stream",
                    )
                    evidence_ids.append(f"{obj_id}.stream")
            except Exception:
                pass

    except Exception as e:
        warnings.append(f"object_scan_failed: {e}")

    pdf.close()

    return ParseResult(
        status="success",
        carrier_family="pdf",
        version=version,
        structural_summary={
            "object_count": obj_count,
            "xref_offset": xref_offset,
            "has_streams": any("stream" in k for k in field_map),
            "has_xref_stream": _has_xref_stream(artifact),
        },
        field_map=field_map,
        node_count=len(set(f.node_id for f in field_map.values())),
        parse_warnings=tuple(warnings),
        evidence_ids=tuple(evidence_ids),
    )


def _has_xref_stream(artifact: bytes) -> bool:
    """Check if PDF uses an xref stream (PDF 1.5+) instead of classic xref table."""
    try:
        # Look for /Type /XRef in the artifact
        return b"/Type" in artifact and b"/XRef" in artifact
    except Exception:
        return False
