from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from typing import Optional

import ezdxf


MATERIAL_RULES = {
    0.0625: {
        "k": 0.38,
        "r": 0.0625,
        "gap": 0.0528,
        "relief_width": 0.04,
        "relief_depth": 0.04,
        "corner_relief_size": 0.16,
    },
    0.0800: {
        "k": 0.50,
        "r": 0.0800,
        "gap": 0.0528,
        "relief_width": 0.04,
        "relief_depth": 0.04,
        "corner_relief_size": 0.16,
    },
    0.1250: {
        "k": 0.42,
        "r": 0.1875,
        "gap": 0.0528,
        "relief_width": 0.04,
        "relief_depth": 0.04,
        "corner_relief_size": 0.20,
    },
    0.1875: {
        "k": 0.44,
        "r": 0.3750,
        "gap": 0.0528,
        "relief_width": 0.04,
        "relief_depth": 0.04,
        "corner_relief_size": 0.24,
    },
}


@dataclass
class PanelSpec:
    panel_id: str
    face_width: float
    face_height: float
    thickness: float
    flange_type: str
    flange1_depth: float
    flange2_depth: Optional[float]
    hole_dia: float
    pitch: float
    pattern: str
    fastening_pair: str = "none"
    k_factor_override: Optional[float] = None
    bend_radius_override: Optional[float] = None
    gap_override: Optional[float] = None
    corner_relief_size: float = 0.16
    relief_width: float = 0.04
    relief_depth: float = 0.04
    stagger_angle: float = 60.0
    margin: float = 1.25


def _to_float(value, default=None):
    if value in (None, ""):
        return default
    return float(value)


def _thickness_to_float(value: str) -> float:
    raw = str(value).strip()
    gauges = {"16 ga": 0.0625, "14 ga": 0.0800, "11 ga": 0.1250}
    key = raw.lower()
    if key in gauges:
        return gauges[key]
    return float(raw)


def parse_csv(path):
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV is missing a header row.")
        reader.fieldnames = [str(h).strip().lower() for h in reader.fieldnames]

        for i, row in enumerate(reader, start=2):
            row = {str(k).strip().lower(): v for k, v in row.items() if k is not None}

            panel_id = (row.get("panel_id") or "").strip()
            if not panel_id:
                raise ValueError(f"Row {i}: missing panel_id")

            flange_type = (row.get("flange_type") or "").strip().upper()
            if flange_type not in {"L", "J"}:
                raise ValueError(f"Row {i}: flange_type must be L or J")

            pattern = (row.get("pattern") or "").strip().lower()
            if pattern not in {"straight", "staggered"}:
                raise ValueError(f"Row {i}: pattern must be straight or staggered")

            out.append(
                PanelSpec(
                    panel_id=panel_id,
                    face_width=float(row.get("width")),
                    face_height=float(row.get("height")),
                    thickness=_thickness_to_float(row.get("thickness")),
                    flange_type=flange_type,
                    flange1_depth=float(row.get("flange1_depth")),
                    flange2_depth=_to_float(row.get("flange2_depth")),
                    hole_dia=float(row.get("hole_diameter")),
                    pitch=float(row.get("hole_pitch")),
                    pattern=pattern,
                    fastening_pair=(row.get("fastening_pair") or "none").strip().lower(),
                    k_factor_override=_to_float(row.get("k_factor_override")),
                    bend_radius_override=_to_float(row.get("bend_radius_override")),
                    gap_override=_to_float(row.get("gap_override")),
                    corner_relief_size=_to_float(row.get("corner_relief_size"), 0.16),
                    relief_width=_to_float(row.get("relief_width"), 0.04),
                    relief_depth=_to_float(row.get("relief_depth"), 0.04),
                    stagger_angle=_to_float(row.get("stagger_angle"), 60.0),
                    margin=_to_float(row.get("margin"), 1.25),
                )
            )
    return out


def bend_deduction(thickness: float, radius: float, k_factor: float) -> float:
    bend_allowance = (math.pi / 2.0) * (radius + k_factor * thickness)
    setback = radius + thickness
    return 2.0 * setback - bend_allowance


def get_rules(spec: PanelSpec):
    base = MATERIAL_RULES.get(round(spec.thickness, 4))
    if base is None:
        nearest = min(MATERIAL_RULES.keys(), key=lambda t: abs(t - spec.thickness))
        base = MATERIAL_RULES[nearest]
    return {
        "k": spec.k_factor_override if spec.k_factor_override is not None else base["k"],
        "r": spec.bend_radius_override if spec.bend_radius_override is not None else base["r"],
        "gap": spec.gap_override if spec.gap_override is not None else base["gap"],
        "corner_relief_size": spec.corner_relief_size if spec.corner_relief_size is not None else base["corner_relief_size"],
        "relief_width": spec.relief_width if spec.relief_width is not None else base["relief_width"],
        "relief_depth": spec.relief_depth if spec.relief_depth is not None else base["relief_depth"],
    }


def _flange_flat(depth: float, bd: float) -> float:
    return max(depth - bd, 0.0)


def flat_size(spec: PanelSpec):
    rules = get_rules(spec)
    k = rules["k"]
    r = rules["r"]
    bd = bend_deduction(spec.thickness, r, k)
    f1 = _flange_flat(spec.flange1_depth, bd)
    f2 = _flange_flat(spec.flange2_depth or 0.0, bd)

    if spec.flange_type == "L":
        return (
            spec.face_width + 2.0 * f1,
            spec.face_height + 2.0 * f1,
        )

    return (
        spec.face_width + 2.0 * (f1 + f2),
        spec.face_height + 2.0 * (f1 + f2),
    )


def _blank_outline(w: float, h: float, spec: PanelSpec, f1: float, f2: float):
    """
    Generate the flat blank outline with L-shaped corner reliefs for L or J flanges.
    
    For L flanges: L-shaped corner notches at all 4 corners, each notch is f1 x f1
    For J flanges: L-shaped corner notches at all 4 corners, each notch is f2 x f2
    
    The L-shaped relief allows both flanges to fold without interference at corners.
    """
    if spec.flange_type == "L":
        notch = f1
        return [
            (-notch, h - notch),
            (-notch, notch),
            (0.0, notch),
            (0.0, 0.0),
            (w, 0.0),
            (w, notch),
            (w + notch, notch),
            (w + notch, h - notch),
            (w, h - notch),
            (w, h + notch),
            (0.0, h + notch),
            (0.0, h - notch),
        ]
    else:
        # J: corner notches at all 4 corners, each notch is f2 x f2
        notch = f2
        return [
            (-notch, h - notch),
            (-notch, notch),
            (0.0, notch),
            (0.0, 0.0),
            (w, 0.0),
            (w, notch),
            (w + notch, notch),
            (w + notch, h - notch),
            (w, h - notch),
            (w, h + notch),
            (0.0, h + notch),
            (0.0, h - notch),
        ]


def _add_rect(msp, x0, y0, x1, y1, layer: str):
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": layer},
    )


def _hole_centers(face_x, face_y, face_w, face_h, hole_dia, pitch, pattern, stagger_angle, margin):
    centers = []
    radius = hole_dia / 2.0
    start_x = face_x + margin + radius
    start_y = face_y + margin + radius
    max_x = face_x + face_w - margin - radius
    max_y = face_y + face_h - margin - radius

    if pattern == "straight":
        y = start_y
        while y <= max_y + 1e-9:
            x = start_x
            while x <= max_x + 1e-9:
                centers.append((x, y))
                x += pitch
            y += pitch
        return centers

    alpha = math.radians(stagger_angle)
    row_step = pitch * math.sin(alpha)
    offset = pitch * math.cos(alpha)
    row = 0
    y = start_y
    while y <= max_y + 1e-9:
        x_offset = 0.0 if row % 2 == 0 else offset
        x = start_x + x_offset
        while x <= max_x + 1e-9:
            centers.append((x, y))
            x += pitch
        y += row_step
        row += 1

    return centers


def _select_fastening_holes(edge_centers, pitch):
    if not edge_centers:
        return []
    step = max(1, int(round(12.0 / pitch)))
    return [edge_centers[i] for i in range(0, len(edge_centers), step)]


def _add_slot(msp, cx, cy, length, width, orientation, layer: str):
    if orientation == "vertical":
        dx = width / 2.0
        dy = length / 2.0
    else:
        dx = length / 2.0
        dy = width / 2.0
    _add_rect(msp, cx - dx, cy - dy, cx + dx, cy + dy, layer)


def _fastening_slots(spec: PanelSpec, face_x: float, face_y: float, face_w: float, face_h: float, f1: float, f2: float):
    if spec.fastening_pair.strip().lower() in {"", "none"}:
        return []

    face_hole_centers = _hole_centers(
        face_x,
        face_y,
        face_w,
        face_h,
        spec.hole_dia,
        spec.pitch,
        spec.pattern,
        spec.stagger_angle,
        spec.margin,
    )
    radius = spec.hole_dia / 2.0
    tolerance = max(0.01, spec.pitch * 0.25)

    if spec.flange_type == "L":
        edge_x = face_x + face_w - spec.margin - radius
        edge_centers = [c for c in face_hole_centers if abs(c[0] - edge_x) <= tolerance]
        edge_centers.sort(key=lambda c: c[1])
        slot_centers = _select_fastening_holes(edge_centers, spec.pitch)
        return [("vertical", (face_x + face_w + f1 / 2.0, y)) for _, y in slot_centers]

    edge_y = face_y + face_h - spec.margin - radius
    edge_centers = [c for c in face_hole_centers if abs(c[1] - edge_y) <= tolerance]
    edge_centers.sort(key=lambda c: c[0])
    slot_centers = _select_fastening_holes(edge_centers, spec.pitch)
    return [("horizontal", (x, face_y + face_h + f2 / 2.0)) for x, _ in slot_centers]


def generate_panel_dxf(spec: PanelSpec, outdir: str):
    w, h = flat_size(spec)
    rules = get_rules(spec)
    k = rules["k"]
    r = rules["r"]
    gap = rules["gap"]
    bd = bend_deduction(spec.thickness, r, k)
    f1 = _flange_flat(spec.flange1_depth, bd)
    f2 = _flange_flat(spec.flange2_depth or 0.0, bd)

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    msp = doc.modelspace()

    for name, color in [("cut", 1), ("holes", 2), ("fastening", 5), ("bend", 3), ("bend_extent", 4)]:
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)

    cut_pts = _blank_outline(w, h, spec, f1, f2)
    msp.add_lwpolyline(cut_pts, close=True, dxfattribs={"layer": "cut"})

    face_x = (w - spec.face_width) / 2.0
    face_y = (h - spec.face_height) / 2.0
    _add_rect(msp, face_x, face_y, face_x + spec.face_width, face_y + spec.face_height, "bend")

    if gap > 0:
        gx0 = face_x + gap
        gy0 = face_y + gap
        gx1 = face_x + spec.face_width - gap
        gy1 = face_y + spec.face_height - gap
        if gx1 > gx0 and gy1 > gy0:
            _add_rect(msp, gx0, gy0, gx1, gy1, "bend_extent")

    if spec.flange_type == "J" and f2 > 0:
        outer_x = f2
        outer_y = f2
        _add_rect(msp, outer_x, outer_y, w - outer_x, h - outer_y, "bend")

        if gap > 0:
            jx0 = outer_x + gap
            jy0 = outer_y + gap
            jx1 = w - outer_x - gap
            jy1 = h - outer_y - gap
            if jx1 > jx0 and jy1 > jy0:
                _add_rect(msp, jx0, jy0, jx1, jy1, "bend_extent")

    for x, y in _hole_centers(
        face_x, face_y, spec.face_width, spec.face_height, spec.hole_dia, spec.pitch, spec.pattern, spec.stagger_angle, spec.margin
    ):
        msp.add_circle((x, y), spec.hole_dia / 2.0, dxfattribs={"layer": "holes"})

    for orientation, (cx, cy) in _fastening_slots(spec, face_x, face_y, spec.face_width, spec.face_height, f1, f2):
        _add_slot(msp, cx, cy, 0.75, 0.25, orientation, "fastening")

    os.makedirs(outdir, exist_ok=True)
    doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))


def nest_panels(panels, sw, sh):
    return []


def write_nesting_dxf(sheets, outdir):
    return None
