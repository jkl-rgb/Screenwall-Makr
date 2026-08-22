"""Dependency-free PDF preview of a panel flat pattern.

Renders the same path model the G-code exporter uses
(`gcode_export.extract_paths`) onto a US-letter page as stroked vector
paths, one color per DXF layer, with reference layers dashed. Arcs are
approximated with cubic Beziers (max 90 degrees per segment). Pure
stdlib: the only "compression" dependency is zlib (FlateDecode).
"""
from __future__ import annotations

import math
import zlib

from gcode_export import extract_paths

# Print-friendly stroke colors on white, keyed by DXF layer.
LAYER_STYLE = {
    # layer: (rgb, line_width_pt, dashed)
    "finished_face": ((0.62, 0.62, 0.62), 0.5, True),
    "bend": ((0.05, 0.55, 0.25), 0.6, True),
    "holes": ((0.93, 0.55, 0.05), 0.7, False),
    "fastening": ((0.10, 0.35, 0.90), 0.8, False),
    "text": ((0.72, 0.10, 0.72), 0.7, False),
    "cut": ((0.85, 0.10, 0.10), 1.2, False),
}
DRAW_ORDER = ["finished_face", "bend", "holes", "fastening", "text", "cut"]
DEFAULT_STYLE = ((0.25, 0.25, 0.25), 0.7, False)

PAGE_W, PAGE_H = 612.0, 792.0  # US letter portrait, points
MARGIN = 36.0
TITLE_ZONE = 42.0   # reserved strip at the top of the page
LEGEND_ZONE = 26.0  # reserved strip at the bottom


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _pdf_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _arc_beziers(p0, p1, center, ccw):
    """Approximate an arc with cubic Beziers, <= 90 degrees each.

    Yields (c1, c2, end) control/end points starting from p0.
    """
    cx, cy = center
    r = math.hypot(p0[0] - cx, p0[1] - cy)
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    a1 = math.atan2(p1[1] - cy, p1[0] - cx)
    if ccw:
        sweep = (a1 - a0) % (2.0 * math.pi)
    else:
        sweep = -((a0 - a1) % (2.0 * math.pi))
    if abs(sweep) < 1e-12:
        return
    n = max(1, math.ceil(abs(sweep) / (math.pi / 2.0)))
    step = sweep / n
    a = a0
    for _ in range(n):
        b = a + step
        k = (4.0 / 3.0) * math.tan(step / 4.0)
        sa, ca = math.sin(a), math.cos(a)
        sb, cb = math.sin(b), math.cos(b)
        start = (cx + r * ca, cy + r * sa)
        end = (cx + r * cb, cy + r * sb)
        c1 = (start[0] - k * r * sa, start[1] + k * r * ca)
        c2 = (end[0] + k * r * sb, end[1] - k * r * cb)
        yield c1, c2, end
        a = b


def _paths_bbox(paths):
    xs, ys = [], []
    for p in paths:
        for seg in p["segments"]:
            if seg[0] == "line":
                pts = (seg[1], seg[2])
            else:
                _, p0, p1, c, _ = seg
                r = math.hypot(p0[0] - c[0], p0[1] - c[1])
                pts = (p0, p1, (c[0] - r, c[1] - r), (c[0] + r, c[1] + r))
            for x, y in pts:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs), max(ys)


def _content_stream(paths, title: str) -> bytes:
    minx, miny, maxx, maxy = _paths_bbox(paths)
    bw = max(maxx - minx, 1e-9)
    bh = max(maxy - miny, 1e-9)
    avail_w = PAGE_W - 2 * MARGIN
    avail_h = PAGE_H - 2 * MARGIN - TITLE_ZONE - LEGEND_ZONE
    scale = min(avail_w / bw, avail_h / bh)
    ox = MARGIN + (avail_w - bw * scale) / 2.0
    oy = MARGIN + LEGEND_ZONE + (avail_h - bh * scale) / 2.0

    def X(x):
        return _fmt(ox + (x - minx) * scale)

    def Y(y):
        return _fmt(oy + (y - miny) * scale)

    by_layer: dict[str, list[dict]] = {}
    for p in paths:
        by_layer.setdefault(p["layer"], []).append(p)

    out: list[str] = []
    # Title block
    out.append("BT /F1 12 Tf")
    out.append(f"{_fmt(MARGIN)} {_fmt(PAGE_H - MARGIN + 6)} Td ({_pdf_escape(title)}) Tj")
    out.append("ET")

    for layer in DRAW_ORDER + sorted(set(by_layer) - set(DRAW_ORDER)):
        if layer not in by_layer:
            continue
        (r, g, b), width, dashed = LAYER_STYLE.get(layer, DEFAULT_STYLE)
        out.append(f"{_fmt(r)} {_fmt(g)} {_fmt(b)} RG {_fmt(width)} w")
        out.append("[3 2] 0 d" if dashed else "[] 0 d")
        for p in by_layer[layer]:
            first = p["segments"][0][1]
            out.append(f"{X(first[0])} {Y(first[1])} m")
            for seg in p["segments"]:
                if seg[0] == "line":
                    _, _, p1 = seg
                    out.append(f"{X(p1[0])} {Y(p1[1])} l")
                else:
                    _, p0, p1, c, ccw = seg
                    for c1, c2, end in _arc_beziers(p0, p1, c, ccw):
                        out.append(
                            f"{X(c1[0])} {Y(c1[1])} {X(c2[0])} {Y(c2[1])} "
                            f"{X(end[0])} {Y(end[1])} c"
                        )
            out.append("h S" if p["closed"] else "S")

    # Legend along the bottom, colored to match the strokes
    lx = MARGIN
    for layer in DRAW_ORDER:
        if layer not in by_layer:
            continue
        (r, g, b), _, _ = LAYER_STYLE.get(layer, DEFAULT_STYLE)
        out.append(f"BT /F1 9 Tf {_fmt(r)} {_fmt(g)} {_fmt(b)} rg")
        out.append(f"{_fmt(lx)} {_fmt(MARGIN - 14)} Td ({_pdf_escape(layer)}) Tj ET")
        lx += 9 * 0.55 * len(layer) + 18  # approx Helvetica advance + gap
    return "\n".join(out).encode("ascii")


def paths_to_pdf(paths, title: str) -> bytes:
    """Assemble a single-page PDF with the stroked paths."""
    stream = zlib.compress(_content_stream(paths, title))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_fmt(PAGE_W)} {_fmt(PAGE_H)}] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ).encode("ascii"),
        (f"<< /Length {len(stream)} /Filter /FlateDecode >>\nstream\n").encode("ascii")
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    buf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(buf))
        buf += f"{i} 0 obj\n".encode("ascii") + body + b"\nendobj\n"
    xref_at = len(buf)
    buf += f"xref\n0 {len(objects) + 1}\n".encode("ascii")
    buf += b"0000000000 65535 f \n"
    for off in offsets:
        buf += f"{off:010d} 00000 n \n".encode("ascii")
    buf += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("ascii")
    return bytes(buf)


def doc_to_pdf(doc, title: str) -> bytes:
    """Render a built panel document (ezdxf) to a one-page preview PDF."""
    return paths_to_pdf(extract_paths(doc), title)
