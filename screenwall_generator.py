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
    ftype: str
    f1: float
    f2: float


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
    fastener_dia: float = 0.1875
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
                fastener_dia=_to_float(row.get("fastener_dia"), 0.1875),
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


def _flat_leg(nominal: float, r: float, k: float, t: float) -> float:
    return max(nominal - (r + t), 0.0)


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


# ---------------------------------------------------------------------------
# Blank outline
#
# CCW walk: BL -> BR -> TR -> TL
#
# Each corner bridges the incoming edge to the outgoing edge.
# arrive_horiz=True  means we arrive travelling in +x or -x (along bottom/top edge)
# arrive_horiz=False means we arrive travelling in +y or -y (along left/right edge)
#
# The point sequence within each corner must connect the last point of
# the incoming edge to the first point of the outgoing edge.
#
#   BL (0,0):  arrive going UP   on left edge  -> arrive_horiz=False
#   BR (w,0):  arrive going RIGHT on bottom edge -> arrive_horiz=True
#   TR (w,h):  arrive going UP   on right edge  -> arrive_horiz=False
#   TL (0,h):  arrive going LEFT  on top edge   -> arrive_horiz=True
#
# horiz_sd = SideDef for the flange on the horizontal (x-direction) side
# vert_sd  = SideDef for the flange on the vertical   (y-direction) side
# ---------------------------------------------------------------------------
def _blank_outline(blank_w, blank_h, sides):
    w, h = blank_w, blank_h

    def _corner(horiz_sd, vert_sd, cx, cy, arrive_horiz):
        sx = 1.0 if cx == 0.0 else -1.0   # inward x
        sy = 1.0 if cy == 0.0 else -1.0   # inward y
        ex = _side_extra(horiz_sd)
        ey = _side_extra(vert_sd)

        # No flanges on either side
        if ex == 0 and ey == 0:
            return [(cx, cy)]

        # Only vertical flange active
        if ex == 0:
            if arrive_horiz:
                return [(cx, cy)]
            else:
                return [(cx, cy + sy * ey), (cx, cy)]

        # Only horizontal flange active
        if ey == 0:
            if arrive_horiz:
                return [(cx, cy), (cx + sx * ex, cy)]
            else:
                return [(cx, cy)]

        # Both flanges active - compute notch geometry
        f2x = horiz_sd.f2 if horiz_sd.ftype == "J" else 0.0
        f2y = vert_sd.f2  if vert_sd.ftype  == "J" else 0.0
        has_miter = (f2x > 0 or f2y > 0)

        if not has_miter:
            # L+L plain square notch
            # Points: from incoming edge -> inner corner -> to outgoing edge
            if arrive_horiz:
                # arrive along x, depart along y
                return [
                    (cx + sx * ex, cy),
                    (cx + sx * ex, cy + sy * ey),
                    (cx,           cy + sy * ey),
                ]
            else:
                # arrive along y, depart along x
                return [
                    (cx,           cy + sy * ey),
                    (cx + sx * ex, cy + sy * ey),
                    (cx + sx * ex, cy),
                ]
        else:
            # Miter corner for J
            # Four points forming: notch-start -> miter-top -> miter-bottom -> notch-end
            # Miter diagonal goes from (cx+sx*ex, cy+sy*f2y) to (cx+sx*f2x, cy+sy*ey)
            P_h = (cx + sx * ex,  cy)               # on blank horiz edge
            P_mt = (cx + sx * ex,  cy + sy * f2y)   # miter top (on outer notch, at lip depth y)
            P_mb = (cx + sx * f2x, cy + sy * ey)    # miter bot (on outer notch, at lip depth x)
            P_v  = (cx,            cy + sy * ey)     # on blank vert edge

            if arrive_horiz:
                # arrive along x -> P_h first, depart along y -> P_v last
                return [P_h, P_mt, P_mb, P_v]
            else:
                # arrive along y -> P_v first, depart along x -> P_h last
                return [P_v, P_mb, P_mt, P_h]

    sl, sr, sb, st = sides["left"], sides["right"], sides["bottom"], sides["top"]

    pts = []
    pts += _corner(sb, sl, 0.0, 0.0, arrive_horiz=False)  # BL
    pts += _corner(sb, sr, w,   0.0, arrive_horiz=True)   # BR
    pts += _corner(st, sr, w,   h,   arrive_horiz=False)  # TR
    pts += _corner(st, sl, 0.0, h,   arrive_horiz=True)   # TL
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
        _add_line(msp, 0, blank_h - sd["top"].f2, blank_w, blank_h - sd["top"].f2, "bend")
    if sd["left"].active and sd["left"].ftype == "J" and sd["left"].f2 > 0:
        _add_line(msp, sd["left"].f2, 0, sd["left"].f2, blank_h, "bend")
    if sd["right"].active and sd["right"].ftype == "J" and sd["right"].f2 > 0:
        _add_line(msp, blank_w - sd["right"].f2, 0, blank_w - sd["right"].f2, blank_h, "bend")


# ---------------------------------------------------------------------------
# Fastening holes
# ---------------------------------------------------------------------------
def _fastening_sides(spec: PanelSpec, sides: dict) -> list[str]:
    fp = spec.fastening_pair.strip().lower()
    if fp in ("", "none"):
        return []
    active = [s for s in ("top", "bottom", "left", "right") if sides[s].active]
    if fp in ("all", "standard"):
        return active
    if fp == "tb":
        return [s for s in ("top", "bottom") if s in active]
    if fp == "lr":
        return [s for s in ("left", "right") if s in active]
    mapping = {"t": "top", "b": "bottom", "l": "left", "r": "right"}
    name = mapping.get(fp, fp)
    return [name] if name in active else []


def _l_fastening_positions(side_length: float, margin: float = 2.0,
                            target: float = 12.0) -> list[float]:
    """Equal-margin positions ~12" o.c. along a side."""
    span = side_length - 2.0 * margin
    if span <= 0:
        return [side_length / 2.0]
    n_gaps = max(1, round(span / target))
    spacing = span / n_gaps
    return [margin + i * spacing for i in range(n_gaps + 1)]


def _j_fastening_positions(coords: list[float]) -> list[float]:
    """
    Select ~12" o.c. subset from sorted face hole coordinates.
    Always uses the first available hole as the starting point,
    then picks the next hole that is >= 11.5" away, and so on.
    """
    if not coords:
        return []
    holes = sorted(coords)
    selected = [holes[0]]
    for h in holes[1:]:
        if h - selected[-1] >= 11.5:
            selected.append(h)
    if holes[-1] not in selected and holes[-1] - selected[-1] > 0.5:
        selected.append(holes[-1])
    return selected


def _draw_fastening_holes(msp, spec: PanelSpec, face_x, face_y,
                           sides: dict, blank_w, blank_h):
    active_sides = _fastening_sides(spec, sides)
    if not active_sides:
        return

    face_holes = _hole_centers(
        face_x, face_y, spec.face_width, spec.face_height,
        spec.hole_dia, spec.pitch, spec.pattern,
        spec.stagger_angle, spec.margin,
    )
    fdia = spec.fastener_dia
    tol  = max(0.01, spec.pitch * 0.3)

    for side_name in active_sides:
        sd = sides[side_name]

        if side_name in ("bottom", "top"):
            is_bottom = (side_name == "bottom")
            if sd.ftype == "J":
                # Hole centered in lip zone, aligned to face edge-row perforations
                hole_y = sd.f2 / 2.0 if is_bottom else blank_h - sd.f2 / 2.0
                edge_y = face_y if is_bottom else face_y + spec.face_height
                row_xs = [c[0] for c in face_holes if abs(c[1] - edge_y) <= tol]
                positions_x = _j_fastening_positions(row_xs)
            else:
                # L: hole centered in leg, no alignment needed
                hole_y = (face_y - sd.f1 / 2.0) if is_bottom else (face_y + spec.face_height + sd.f1 / 2.0)
                raw = _l_fastening_positions(spec.face_width)
                positions_x = [face_x + p for p in raw]

            for px in positions_x:
                msp.add_circle((px, hole_y), fdia / 2.0, dxfattribs={"layer": "fastening"})

        else:  # left or right
            is_left = (side_name == "left")
            if sd.ftype == "J":
                hole_x = sd.f2 / 2.0 if is_left else blank_w - sd.f2 / 2.0
                edge_x = face_x if is_left else face_x + spec.face_width
                col_ys = [c[1] for c in face_holes if abs(c[0] - edge_x) <= tol]
                positions_y = _j_fastening_positions(col_ys)
            else:
                hole_x = (face_x - sd.f1 / 2.0) if is_left else (face_x + spec.face_width + sd.f1 / 2.0)
                raw = _l_fastening_positions(spec.face_height)
                positions_y = [face_y + p for p in raw]

            for py in positions_y:
                msp.add_circle((hole_x, py), fdia / 2.0, dxfattribs={"layer": "fastening"})


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

    _draw_fastening_holes(msp, spec, face_x, face_y, sides, blank_w, blank_h)

    os.makedirs(outdir, exist_ok=True)
    doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))


def nest_panels(panels, sw, sh):
    return []

def write_nesting_dxf(sheets, outdir):
    return None
