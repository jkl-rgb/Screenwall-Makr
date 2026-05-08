from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from typing import Optional

import ezdxf

MATERIAL_RULES = {
    0.0625: {
        "3003": {"k": 0.38, "r": 0.0625},
        "5052": {"k": 0.41, "r": 0.0625},
        "default": {"k": 0.38, "r": 0.0625},
    },
    0.0800: {
        "3003": {"k": 0.40, "r": 0.0800},
        "5052": {"k": 0.42, "r": 0.0800},
        "default": {"k": 0.40, "r": 0.0800},
    },
    0.1250: {
        "3003": {"k": 0.42, "r": 0.1250},
        "5052": {"k": 0.44, "r": 0.1875},
        "default": {"k": 0.42, "r": 0.1250},
    },
    0.1875: {
        "3003": {"k": 0.44, "r": 0.1875},
        "5052": {"k": 0.46, "r": 0.2813},
        "default": {"k": 0.44, "r": 0.1875},
    },
}

GAUGE_MAP = {"16 ga": 0.0625, "14 ga": 0.0800, "11 ga": 0.1250}
FLANGE_CODES = {"L4S", "J4S", "L2TB", "J2TB", "L2LR", "J2LR", "MIX"}


@dataclass
class SideDef:
    active: bool
    ftype: str    # "L" or "J"
    f1: float     # flat leg length (bend-deducted)
    f2: float     # flat return lip length (bend-deducted, J only)


@dataclass
class PanelSpec:
    panel_id: str
    face_width: float
    face_height: float
    thickness: float
    flange_code: str
    flange_type: str
    flange1_depth: float
    flange2_depth: Optional[float]
    hole_dia: float
    pitch: float
    pattern: str
    fastening_pair: str = "none"
    material: str = "aluminum"
    alloy: str = "3003"
    k_factor_override: Optional[float] = None
    bend_radius_override: Optional[float] = None
    gap_override: Optional[float] = None
    corner_relief_size: float = 0.16
    relief_width: float = 0.04
    relief_depth: float = 0.04
    stagger_angle: float = 60.0
    margin: float = 1.25
    top_type: str = "L"
    top_f1: float = 0.0
    top_f2: float = 0.0
    bottom_type: str = "L"
    bottom_f1: float = 0.0
    bottom_f2: float = 0.0
    left_type: str = "L"
    left_f1: float = 0.0
    left_f2: float = 0.0
    right_type: str = "L"
    right_f1: float = 0.0
    right_f2: float = 0.0


def _to_float(v, default=0.0):
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _thickness_to_float(v: str) -> float:
    raw = str(v).strip().lower()
    if raw in GAUGE_MAP:
        return GAUGE_MAP[raw]
    return float(raw)


def parse_csv(path: str) -> list[PanelSpec]:
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

            raw_code = (row.get("flange_code") or row.get("flange_type") or "").strip().upper()
            if raw_code == "L":
                raw_code = "L4S"
            elif raw_code == "J":
                raw_code = "J4S"
            if raw_code not in FLANGE_CODES:
                raise ValueError(f"Row {i}: flange_code must be one of {FLANGE_CODES}")

            pattern = (row.get("pattern") or "").strip().lower()
            if pattern not in {"straight", "staggered"}:
                raise ValueError(f"Row {i}: pattern must be straight or staggered")

            ftype = "J" if raw_code.startswith("J") else "L"

            out.append(PanelSpec(
                panel_id=panel_id,
                face_width=float(row["width"]),
                face_height=float(row["height"]),
                thickness=_thickness_to_float(row["thickness"]),
                flange_code=raw_code,
                flange_type=ftype,
                flange1_depth=_to_float(row.get("flange1_depth"), 0.0),
                flange2_depth=_to_float(row.get("flange2_depth"), None) or None,
                hole_dia=float(row["hole_diameter"]),
                pitch=float(row["hole_pitch"]),
                pattern=pattern,
                fastening_pair=(row.get("fastening_pair") or "none").strip().lower(),
                material=(row.get("material") or "aluminum").strip().lower(),
                alloy=(row.get("alloy") or "3003").strip(),
                k_factor_override=_to_float(row.get("k_factor_override"), None) or None,
                bend_radius_override=_to_float(row.get("bend_radius_override"), None) or None,
                gap_override=_to_float(row.get("gap_override"), None) or None,
                corner_relief_size=_to_float(row.get("corner_relief_size"), 0.16),
                relief_width=_to_float(row.get("relief_width"), 0.04),
                relief_depth=_to_float(row.get("relief_depth"), 0.04),
                stagger_angle=_to_float(row.get("stagger_angle"), 60.0),
                margin=_to_float(row.get("margin"), 1.25),
                top_type=(row.get("top_type") or "L").strip().upper(),
                top_f1=_to_float(row.get("top_f1"), 0.0),
                top_f2=_to_float(row.get("top_f2"), 0.0),
                bottom_type=(row.get("bottom_type") or "L").strip().upper(),
                bottom_f1=_to_float(row.get("bottom_f1"), 0.0),
                bottom_f2=_to_float(row.get("bottom_f2"), 0.0),
                left_type=(row.get("left_type") or "L").strip().upper(),
                left_f1=_to_float(row.get("left_f1"), 0.0),
                left_f2=_to_float(row.get("left_f2"), 0.0),
                right_type=(row.get("right_type") or "L").strip().upper(),
                right_f1=_to_float(row.get("right_f1"), 0.0),
                right_f2=_to_float(row.get("right_f2"), 0.0),
            ))
    return out


def get_rules(spec: PanelSpec) -> dict:
    t = round(spec.thickness, 4)
    nearest = min(MATERIAL_RULES.keys(), key=lambda x: abs(x - t))
    alloy_key = spec.alloy if spec.alloy in MATERIAL_RULES[nearest] else "default"
    base = MATERIAL_RULES[nearest][alloy_key]
    return {
        "k":   spec.k_factor_override   if spec.k_factor_override   is not None else base["k"],
        "r":   spec.bend_radius_override if spec.bend_radius_override is not None else base["r"],
        "gap": spec.gap_override         if spec.gap_override         is not None else 0.0528,
    }


def _setback(r: float, t: float) -> float:
    return r + t


def _flat_leg(nominal: float, r: float, k: float, t: float) -> float:
    return max(nominal - _setback(r, t), 0.0)


def resolve_sides(spec: PanelSpec) -> dict[str, SideDef]:
    rules = get_rules(spec)
    k, r, t = rules["k"], rules["r"], spec.thickness

    def _sd(active, ftype, nom_f1, nom_f2=0.0) -> SideDef:
        f1 = _flat_leg(nom_f1, r, k, t) if active else 0.0
        f2 = (_flat_leg(nom_f2, r, k, t) if ftype == "J" else 0.0) if active else 0.0
        return SideDef(active=active, ftype=ftype, f1=f1, f2=f2)

    code = spec.flange_code
    f1n  = spec.flange1_depth
    f2n  = spec.flange2_depth or 0.0

    if code == "L4S":
        sd = _sd(True, "L", f1n)
        return {"top": sd, "bottom": sd, "left": sd, "right": sd}
    if code == "J4S":
        sd = _sd(True, "J", f1n, f2n)
        return {"top": sd, "bottom": sd, "left": sd, "right": sd}
    if code == "L2TB":
        a = _sd(True, "L", f1n);  i = _sd(False, "L", 0)
        return {"top": a, "bottom": a, "left": i, "right": i}
    if code == "J2TB":
        a = _sd(True, "J", f1n, f2n);  i = _sd(False, "J", 0)
        return {"top": a, "bottom": a, "left": i, "right": i}
    if code == "L2LR":
        a = _sd(True, "L", f1n);  i = _sd(False, "L", 0)
        return {"top": i, "bottom": i, "left": a, "right": a}
    if code == "J2LR":
        a = _sd(True, "J", f1n, f2n);  i = _sd(False, "J", 0)
        return {"top": i, "bottom": i, "left": a, "right": a}

    return {
        "top":    _sd(spec.top_f1    > 0, spec.top_type,    spec.top_f1,    spec.top_f2),
        "bottom": _sd(spec.bottom_f1 > 0, spec.bottom_type, spec.bottom_f1, spec.bottom_f2),
        "left":   _sd(spec.left_f1   > 0, spec.left_type,   spec.left_f1,   spec.left_f2),
        "right":  _sd(spec.right_f1  > 0, spec.right_type,  spec.right_f1,  spec.right_f2),
    }


def _side_extra(sd: SideDef) -> float:
    return (sd.f1 + sd.f2) if sd.active else 0.0


def flat_size(spec: PanelSpec) -> tuple[float, float]:
    sides = resolve_sides(spec)
    w = spec.face_width  + _side_extra(sides["left"])  + _side_extra(sides["right"])
    h = spec.face_height + _side_extra(sides["top"])   + _side_extra(sides["bottom"])
    return w, h


def _blank_outline(blank_w, blank_h, sides):
    """
    Closed CCW flat blank outline with correct corner geometry per
    SheetMetalRelief smMakeFace logic.

    Corner rules:
      Neither active          -> plain right-angle point
      One active only         -> straight cut flush, no notch
      Both active, both L     -> square notch f1_h x f1_v
      Both active, any J      -> square notch (clears legs) + 45deg miter
                                 across lip zone so return lips fold clean
    Miter cut: straight line from
      B = (cx + sx*ex_h,  cy + sy*f2_v)   [on vertical outer blank edge]
    to
      C = (cx + sx*f2_h,  cy + sy*ex_v)   [on horizontal outer blank edge]
    """
    w, h = blank_w, blank_h

    def _corner(h_sd, v_sd, cx, cy):
        sx = 1.0 if cx == 0.0 else -1.0
        sy = 1.0 if cy == 0.0 else -1.0
        ex_h = _side_extra(h_sd)
        ex_v = _side_extra(v_sd)

        if ex_h == 0 and ex_v == 0:
            return [(cx, cy)]
        if ex_h == 0:
            return [(cx, cy + sy * ex_v), (cx, cy)]
        if ex_v == 0:
            return [(cx, cy), (cx + sx * ex_h, cy)]

        f2_h = h_sd.f2 if h_sd.ftype == "J" else 0.0
        f2_v = v_sd.f2 if v_sd.ftype == "J" else 0.0

        if f2_h == 0 and f2_v == 0:
            # L + L: plain square notch
            return [
                (cx + sx * ex_h, cy),
                (cx + sx * ex_h, cy + sy * ex_v),
                (cx,             cy + sy * ex_v),
            ]

        # J involved: square notch + miter cut across lip zone
        # A: arrive on y-parallel blank edge
        # B: top of miter on x-parallel outer notch edge
        # C: side of miter on y-parallel outer notch edge
        # D: depart on x-parallel blank edge
        A = (cx + sx * ex_h, cy)
        B = (cx + sx * ex_h, cy + sy * f2_v)
        C = (cx + sx * f2_h, cy + sy * ex_v)
        D = (cx,             cy + sy * ex_v)
        return [A, B, C, D]

    pts = []
    pts += _corner(sides["left"],  sides["bottom"], 0.0, 0.0)
    pts += _corner(sides["right"], sides["bottom"], w,   0.0)
    pts += _corner(sides["right"], sides["top"],    w,   h)
    pts += _corner(sides["left"],  sides["top"],    0.0, h)
    return pts


def _add_rect(msp, x0, y0, x1, y1, layer):
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True, dxfattribs={"layer": layer},
    )


def _add_line(msp, x0, y0, x1, y1, layer):
    msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": layer})


def _hole_centers(face_x, face_y, face_w, face_h,
                  hole_dia, pitch, pattern, stagger_angle, margin):
    centers = []
    radius  = hole_dia / 2.0
    start_x = face_x + margin + radius
    start_y = face_y + margin + radius
    max_x   = face_x + face_w - margin - radius
    max_y   = face_y + face_h - margin - radius

    if pattern == "straight":
        y = start_y
        while y <= max_y + 1e-9:
            x = start_x
            while x <= max_x + 1e-9:
                centers.append((x, y))
                x += pitch
            y += pitch
        return centers

    alpha    = math.radians(stagger_angle)
    row_step = pitch * math.sin(alpha)
    col_off  = pitch * math.cos(alpha)
    row = 0
    y = start_y
    while y <= max_y + 1e-9:
        x = start_x + (col_off if row % 2 else 0.0)
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


def _add_slot(msp, cx, cy, length, width, orientation, layer):
    dx, dy = (width/2, length/2) if orientation == "vertical" else (length/2, width/2)
    _add_rect(msp, cx-dx, cy-dy, cx+dx, cy+dy, layer)


def _draw_bend_lines(msp, face_x, face_y, face_w, face_h,
                     blank_w, blank_h, sides, gap):
    fx0, fy0 = face_x, face_y
    fx1, fy1 = face_x + face_w, face_y + face_h

    _add_rect(msp, fx0, fy0, fx1, fy1, "bend")
    if gap > 0:
        _add_rect(msp, fx0+gap, fy0+gap, fx1-gap, fy1-gap, "bend_extent")

    sd = sides
    if sd["bottom"].active and sd["bottom"].ftype == "J" and sd["bottom"].f2 > 0:
        _add_line(msp, 0, sd["bottom"].f2, blank_w, sd["bottom"].f2, "bend")
    if sd["top"].active and sd["top"].ftype == "J" and sd["top"].f2 > 0:
        y = blank_h - sd["top"].f2
        _add_line(msp, 0, y, blank_w, y, "bend")
    if sd["left"].active and sd["left"].ftype == "J" and sd["left"].f2 > 0:
        _add_line(msp, sd["left"].f2, 0, sd["left"].f2, blank_h, "bend")
    if sd["right"].active and sd["right"].ftype == "J" and sd["right"].f2 > 0:
        x = blank_w - sd["right"].f2
        _add_line(msp, x, 0, x, blank_h, "bend")


def _draw_fastening_slots(msp, spec, face_x, face_y, sides):
    if spec.fastening_pair.strip().lower() in {"", "none"}:
        return

    face_holes = _hole_centers(
        face_x, face_y, spec.face_width, spec.face_height,
        spec.hole_dia, spec.pitch, spec.pattern,
        spec.stagger_angle, spec.margin,
    )
    radius    = spec.hole_dia / 2.0
    tolerance = max(0.01, spec.pitch * 0.25)

    for side_name, orientation, slot_cx_fn, slot_cy_fn in [
        ("right",  "vertical",
         lambda sd: face_x + spec.face_width + sd.f1/2,  lambda sd, cy: cy),
        ("top",    "horizontal",
         lambda sd, cx: cx, lambda sd: face_y + spec.face_height + sd.f1/2),
        ("left",   "vertical",
         lambda sd: face_x - sd.f1/2,                   lambda sd, cy: cy),
        ("bottom", "horizontal",
         lambda sd, cx: cx, lambda sd: face_y - sd.f1/2),
    ]:
        sd = sides[side_name]
        if not sd.active:
            continue
        if orientation == "vertical":
            ex = face_x + spec.face_width - radius - spec.margin
            cols = sorted([c for c in face_holes if abs(c[0]-ex) <= tolerance], key=lambda c: c[1])
            chosen = _select_fastening_holes(cols, spec.pitch)
            for _, cy in chosen:
                _add_slot(msp, slot_cx_fn(sd), cy, 0.75, 0.25, "vertical", "fastening")
        else:
            ey = face_y + spec.face_height - radius - spec.margin
            rows = sorted([c for c in face_holes if abs(c[1]-ey) <= tolerance], key=lambda c: c[0])
            chosen = _select_fastening_holes(rows, spec.pitch)
            for cx, _ in chosen:
                _add_slot(msp, cx, slot_cy_fn(sd), 0.75, 0.25, "horizontal", "fastening")
        break


def generate_panel_dxf(spec: PanelSpec, outdir: str):
    rules    = get_rules(spec)
    gap      = rules["gap"]
    sides    = resolve_sides(spec)
    blank_w, blank_h = flat_size(spec)
    face_x   = _side_extra(sides["left"])
    face_y   = _side_extra(sides["bottom"])

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    msp = doc.modelspace()

    for name, color in [("cut",1),("holes",2),("fastening",5),("bend",3),("bend_extent",4)]:
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)

    pts = _blank_outline(blank_w, blank_h, sides)
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "cut"})

    _draw_bend_lines(msp, face_x, face_y, spec.face_width, spec.face_height,
                     blank_w, blank_h, sides, gap)

    for x, y in _hole_centers(face_x, face_y, spec.face_width, spec.face_height,
                               spec.hole_dia, spec.pitch, spec.pattern,
                               spec.stagger_angle, spec.margin):
        msp.add_circle((x, y), spec.hole_dia/2.0, dxfattribs={"layer": "holes"})

    _draw_fastening_slots(msp, spec, face_x, face_y, sides)

    os.makedirs(outdir, exist_ok=True)
    doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))


def nest_panels(panels, sw, sh):
    return []

def write_nesting_dxf(sheets, outdir):
    return None
