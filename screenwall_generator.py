from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from typing import Optional

import ezdxf

# ---------------------------------------------------------------------------
# Material rules — keyed by thickness (inches), then alloy
# r  = inside bend radius
# k  = k-factor (ANSI: neutral axis offset / thickness)
# ---------------------------------------------------------------------------
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

# Valid flange type codes
FLANGE_CODES = {"L4S", "J4S", "L2TB", "J2TB", "L2LR", "J2LR", "MIX"}


# ---------------------------------------------------------------------------
# Per-side flange definition
# ---------------------------------------------------------------------------
@dataclass
class SideDef:
    """Describes the flange on one side of a panel."""
    active: bool         # True = flanged, False = straight cut
    ftype: str           # "L" or "J"
    f1: float            # flat leg depth (after bend deduction)
    f2: float = 0.0      # flat return lip depth for J (after bend deduction)


# ---------------------------------------------------------------------------
# Panel specification
# ---------------------------------------------------------------------------
@dataclass
class PanelSpec:
    panel_id: str
    face_width: float
    face_height: float
    thickness: float
    flange_code: str          # one of FLANGE_CODES
    flange_type: str          # "L" or "J" (resolved from code for non-MIX)
    flange1_depth: float      # nominal leg depth (non-MIX)
    flange2_depth: Optional[float]  # nominal return lip depth (non-MIX J)
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
    # MIX per-side nominal depths (before bend deduction)
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


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------
def _to_float(v, default=0.0):
    if v in (None, ""):
        return default
    return float(v)


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

            # Support legacy flange_type column OR new flange_code column
            raw_code = (row.get("flange_code") or row.get("flange_type") or "").strip().upper()
            # Upgrade legacy L/J to L4S/J4S
            if raw_code == "L":
                raw_code = "L4S"
            elif raw_code == "J":
                raw_code = "J4S"
            if raw_code not in FLANGE_CODES:
                raise ValueError(f"Row {i}: flange_code must be one of {FLANGE_CODES}")

            pattern = (row.get("pattern") or "").strip().lower()
            if pattern not in {"straight", "staggered"}:
                raise ValueError(f"Row {i}: pattern must be straight or staggered")

            # Resolve base flange_type from code
            ftype = "J" if raw_code.startswith("J") else "L"

            out.append(PanelSpec(
                panel_id=panel_id,
                face_width=float(row["width"]),
                face_height=float(row["height"]),
                thickness=_thickness_to_float(row["thickness"]),
                flange_code=raw_code,
                flange_type=ftype,
                flange1_depth=_to_float(row.get("flange1_depth"), 0.0),
                flange2_depth=_to_float(row.get("flange2_depth"), None),
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


# ---------------------------------------------------------------------------
# Bend math
# ---------------------------------------------------------------------------
def get_rules(spec: PanelSpec) -> dict:
    t = round(spec.thickness, 4)
    nearest = min(MATERIAL_RULES.keys(), key=lambda x: abs(x - t))
    alloy_key = spec.alloy if spec.alloy in MATERIAL_RULES[nearest] else "default"
    base = MATERIAL_RULES[nearest][alloy_key]
    return {
        "k": spec.k_factor_override if spec.k_factor_override is not None else base["k"],
        "r": spec.bend_radius_override if spec.bend_radius_override is not None else base["r"],
        "gap": spec.gap_override if spec.gap_override is not None else 0.0528,
    }


def _setback(r: float, t: float) -> float:
    return r + t


def _flat_leg(nominal: float, r: float, k: float, t: float) -> float:
    """Flat length of one flange leg from nominal outside-mold-line depth."""
    return max(nominal - _setback(r, t), 0.0)


# ---------------------------------------------------------------------------
# Resolve per-side SideDefs from a PanelSpec
#
# Returns dict: {"top": SideDef, "bottom": SideDef, "left": SideDef, "right": SideDef}
# f1/f2 in SideDef are already flat (bend-deducted) lengths.
# ---------------------------------------------------------------------------
def resolve_sides(spec: PanelSpec) -> dict[str, SideDef]:
    rules = get_rules(spec)
    k, r, t = rules["k"], rules["r"], spec.thickness

    def _sd(active, ftype, nom_f1, nom_f2=0.0) -> SideDef:
        f1 = _flat_leg(nom_f1, r, k, t) if active else 0.0
        f2 = (_flat_leg(nom_f2, r, k, t) if ftype == "J" else 0.0) if active else 0.0
        return SideDef(active=active, ftype=ftype, f1=f1, f2=f2)

    code = spec.flange_code
    ft = spec.flange_type
    f1n = spec.flange1_depth
    f2n = spec.flange2_depth or 0.0

    if code == "L4S":
        sd = _sd(True, "L", f1n)
        return {"top": sd, "bottom": sd, "left": sd, "right": sd}

    if code == "J4S":
        sd = _sd(True, "J", f1n, f2n)
        return {"top": sd, "bottom": sd, "left": sd, "right": sd}

    if code == "L2TB":
        active = _sd(True, "L", f1n)
        inactive = _sd(False, "L", 0)
        return {"top": active, "bottom": active, "left": inactive, "right": inactive}

    if code == "J2TB":
        active = _sd(True, "J", f1n, f2n)
        inactive = _sd(False, "J", 0)
        return {"top": active, "bottom": active, "left": inactive, "right": inactive}

    if code == "L2LR":
        active = _sd(True, "L", f1n)
        inactive = _sd(False, "L", 0)
        return {"top": inactive, "bottom": inactive, "left": active, "right": active}

    if code == "J2LR":
        active = _sd(True, "J", f1n, f2n)
        inactive = _sd(False, "J", 0)
        return {"top": inactive, "bottom": inactive, "left": active, "right": active}

    # MIX
    return {
        "top":    _sd(spec.top_f1 > 0,    spec.top_type,    spec.top_f1,    spec.top_f2),
        "bottom": _sd(spec.bottom_f1 > 0,  spec.bottom_type, spec.bottom_f1, spec.bottom_f2),
        "left":   _sd(spec.left_f1 > 0,    spec.left_type,   spec.left_f1,   spec.left_f2),
        "right":  _sd(spec.right_f1 > 0,   spec.right_type,  spec.right_f1,  spec.right_f2),
    }


# ---------------------------------------------------------------------------
# Blank size
#
# Horizontal (width) direction is governed by left+right sides.
# Vertical (height) direction is governed by top+bottom sides.
# extra = f1 + f2 for J, f1 for L, 0 for inactive.
# ---------------------------------------------------------------------------
def _side_extra(sd: SideDef) -> float:
    if not sd.active:
        return 0.0
    return sd.f1 + sd.f2


def flat_size(spec: PanelSpec) -> tuple[float, float]:
    sides = resolve_sides(spec)
    w = spec.face_width  + _side_extra(sides["left"])  + _side_extra(sides["right"])
    h = spec.face_height + _side_extra(sides["top"])   + _side_extra(sides["bottom"])
    return w, h


# ---------------------------------------------------------------------------
# Blank outline with correct corner notches
#
# Coordinate system: blank origin at bottom-left.
# face zone: x in [extra_left, extra_left + face_width]
#            y in [extra_bottom, extra_bottom + face_height]
#
# Corner notch rule:
#   Both sides flanged  → square notch, size = max(extra_h, extra_v)
#                         (use max so both bends clear)
#   Only one side flanged → no notch; straight cut flush to that flange edge
#   Neither flanged     → no notch; plain corner
#
# The polyline walks CCW from bottom-left corner.
# ---------------------------------------------------------------------------
def _blank_outline(
    blank_w: float,
    blank_h: float,
    sides: dict[str, SideDef],
) -> list[tuple[float, float]]:
    el = _side_extra(sides["left"])
    er = _side_extra(sides["right"])
    eb = _side_extra(sides["bottom"])
    et = _side_extra(sides["top"])

    w, h = blank_w, blank_h

    def _notch(extra_horiz: float, extra_vert: float):
        """Return notch size if both sides active, else 0."""
        if extra_horiz > 0 and extra_vert > 0:
            return max(extra_horiz, extra_vert)
        return 0.0

    n_bl = _notch(el, eb)   # bottom-left
    n_br = _notch(er, eb)   # bottom-right
    n_tr = _notch(er, et)   # top-right
    n_tl = _notch(el, et)   # top-left

    pts = []

    # Bottom-left corner
    if n_bl > 0:
        pts += [(0.0, n_bl), (n_bl, n_bl), (n_bl, 0.0)]
    else:
        pts += [(0.0, 0.0)]

    # Bottom-right corner
    if n_br > 0:
        pts += [(w - n_br, 0.0), (w - n_br, n_br), (w, n_br)]
    else:
        pts += [(w, 0.0)]

    # Top-right corner
    if n_tr > 0:
        pts += [(w, h - n_tr), (w - n_tr, h - n_tr), (w - n_tr, h)]
    else:
        pts += [(w, h)]

    # Top-left corner
    if n_tl > 0:
        pts += [(n_tl, h), (n_tl, h - n_tl), (0.0, h - n_tl)]
    else:
        pts += [(0.0, h)]

    return pts


# ---------------------------------------------------------------------------
# DXF helpers
# ---------------------------------------------------------------------------
def _add_rect(msp, x0, y0, x1, y1, layer: str):
    msp.add_lwpolyline(
        [(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
        close=True,
        dxfattribs={"layer": layer},
    )


def _add_line(msp, x0, y0, x1, y1, layer: str):
    msp.add_line((x0, y0), (x1, y1), dxfattribs={"layer": layer})


def _hole_centers(
    face_x, face_y, face_w, face_h,
    hole_dia, pitch, pattern, stagger_angle, margin,
) -> list[tuple[float, float]]:
    centers = []
    radius = hole_dia / 2.0
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


def _add_slot(msp, cx, cy, length, width, orientation, layer: str):
    if orientation == "vertical":
        dx, dy = width / 2.0, length / 2.0
    else:
        dx, dy = length / 2.0, width / 2.0
    _add_rect(msp, cx - dx, cy - dy, cx + dx, cy + dy, layer)


# ---------------------------------------------------------------------------
# Bend lines
#
# Draw bend lines at the correct positions for each active side.
# For J flanges draw both the leg bend line and the return lip bend line.
# For partial configs (2-sided) draw only on active sides.
# Straight-cut sides get a cut indicator line at the face edge.
# ---------------------------------------------------------------------------
def _draw_bend_lines(msp, face_x, face_y, face_w, face_h,
                     blank_w, blank_h, sides: dict[str, SideDef], gap: float):
    fx0, fy0 = face_x, face_y
    fx1, fy1 = face_x + face_w, face_y + face_h

    # --- Leg bend lines (mold line at face perimeter) ---
    # These are always drawn as the full face rectangle regardless of
    # which sides are active; the rectangle correctly represents the
    # bend tangent lines where flanges exist and face edges elsewhere.
    _add_rect(msp, fx0, fy0, fx1, fy1, "bend")
    if gap > 0:
        _add_rect(msp, fx0 + gap, fy0 + gap, fx1 - gap, fy1 - gap, "bend_extent")

    # --- Return lip bend lines for J sides ---
    # Each active J side gets a bend line parallel to the face edge,
    # offset outward by f2 from the cut edge of the blank.
    sd = sides
    if sd["bottom"].active and sd["bottom"].ftype == "J" and sd["bottom"].f2 > 0:
        y = sd["bottom"].f2
        _add_line(msp, 0.0, y, blank_w, y, "bend")
    if sd["top"].active and sd["top"].ftype == "J" and sd["top"].f2 > 0:
        y = blank_h - sd["top"].f2
        _add_line(msp, 0.0, y, blank_w, y, "bend")
    if sd["left"].active and sd["left"].ftype == "J" and sd["left"].f2 > 0:
        x = sd["left"].f2
        _add_line(msp, x, 0.0, x, blank_h, "bend")
    if sd["right"].active and sd["right"].ftype == "J" and sd["right"].f2 > 0:
        x = blank_w - sd["right"].f2
        _add_line(msp, x, 0.0, x, blank_h, "bend")


# ---------------------------------------------------------------------------
# Main DXF generator
# ---------------------------------------------------------------------------
def generate_panel_dxf(spec: PanelSpec, outdir: str):
    rules  = get_rules(spec)
    gap    = rules["gap"]
    sides  = resolve_sides(spec)

    blank_w, blank_h = flat_size(spec)

    el = _side_extra(sides["left"])
    eb = _side_extra(sides["bottom"])
    face_x = el
    face_y = eb

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    msp = doc.modelspace()

    for name, color in [
        ("cut", 1), ("holes", 2), ("fastening", 5),
        ("bend", 3), ("bend_extent", 4),
    ]:
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)

    # Cut outline
    pts = _blank_outline(blank_w, blank_h, sides)
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "cut"})

    # Bend lines
    _draw_bend_lines(msp, face_x, face_y, spec.face_width, spec.face_height,
                     blank_w, blank_h, sides, gap)

    # Holes
    for x, y in _hole_centers(
        face_x, face_y, spec.face_width, spec.face_height,
        spec.hole_dia, spec.pitch, spec.pattern,
        spec.stagger_angle, spec.margin,
    ):
        msp.add_circle((x, y), spec.hole_dia / 2.0, dxfattribs={"layer": "holes"})

    # Fastening slots — place on first active side with enough holes
    _draw_fastening_slots(msp, spec, face_x, face_y, sides)

    os.makedirs(outdir, exist_ok=True)
    doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))


def _draw_fastening_slots(msp, spec: PanelSpec, face_x, face_y,
                           sides: dict[str, SideDef]):
    if spec.fastening_pair.strip().lower() in {"", "none"}:
        return

    face_holes = _hole_centers(
        face_x, face_y, spec.face_width, spec.face_height,
        spec.hole_dia, spec.pitch, spec.pattern,
        spec.stagger_angle, spec.margin,
    )
    radius    = spec.hole_dia / 2.0
    tolerance = max(0.01, spec.pitch * 0.25)

    # Prefer right side, then top, then left, then bottom
    preference = [
        ("right",  "vertical",   face_x + spec.face_width,  None),
        ("top",    "horizontal",  None,  face_y + spec.face_height),
        ("left",   "vertical",   face_x, None),
        ("bottom", "horizontal",  None,  face_y),
    ]

    for side_name, orientation, sx, sy in preference:
        sd = sides[side_name]
        if not sd.active:
            continue
        if orientation == "vertical":
            ex = face_x + spec.face_width - radius - spec.margin
            cols = sorted(
                [c for c in face_holes if abs(c[0] - ex) <= tolerance],
                key=lambda c: c[1],
            )
            chosen = _select_fastening_holes(cols, spec.pitch)
            slot_cx = sx + sd.f1 / 2.0
            for _, cy in chosen:
                _add_slot(msp, slot_cx, cy, 0.75, 0.25, "vertical", "fastening")
        else:
            ey = face_y + spec.face_height - radius - spec.margin
            rows = sorted(
                [c for c in face_holes if abs(c[1] - ey) <= tolerance],
                key=lambda c: c[0],
            )
            chosen = _select_fastening_holes(rows, spec.pitch)
            slot_cy = sy + sd.f1 / 2.0
            for cx, _ in chosen:
                _add_slot(msp, cx, slot_cy, 0.75, 0.25, "horizontal", "fastening")
        break  # Only one side gets fastening slots


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------
def nest_panels(panels, sw, sh):
    return []


def write_nesting_dxf(sheets, outdir):
    return None
