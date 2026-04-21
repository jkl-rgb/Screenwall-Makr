from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import ezdxf

# Default calibrated rules. Can be overridden per-row/per-panel.
MATERIAL_RULES: Dict[float, Dict[str, float]] = {
    0.0625: {"k_factor": 0.38, "bend_radius": 0.0625, "gap": 0.0528},
    0.0800: {"k_factor": 0.50, "bend_radius": 0.0800, "gap": 0.0528},
    0.1250: {"k_factor": 0.42, "bend_radius": 0.1875, "gap": 0.0528},
    0.1875: {"k_factor": 0.44, "bend_radius": 0.3750, "gap": 0.0528},
}

GAUGE_MAP = {
    "16 ga": 0.0625,
    "14 ga": 0.0800,
    "11 ga": 0.1250,
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


def _parse_thickness(value: str) -> float:
    raw = str(value).strip()
    key = raw.lower()
    if key in GAUGE_MAP:
        return GAUGE_MAP[key]
    return float(raw)


def _clean_optional_float(value: object) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    return float(s)


def _material_rule(spec: PanelSpec) -> Dict[str, float]:
    t = round(spec.thickness, 4)
    base = MATERIAL_RULES.get(t)
    if base is None:
        raise KeyError(
            f"No material rule for thickness {spec.thickness:.4f}. "
            f"Add one to MATERIAL_RULES or supply CSV overrides."
        )
    return {
        "k_factor": spec.k_factor_override if spec.k_factor_override is not None else base["k_factor"],
        "bend_radius": spec.bend_radius_override if spec.bend_radius_override is not None else base["bend_radius"],
        "gap": spec.gap_override if spec.gap_override is not None else base["gap"],
    }


def parse_csv(path: str) -> List[PanelSpec]:
    out: List[PanelSpec] = []
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            hole_dia = float(row["hole_diameter"])
            if hole_dia < 0.125:
                continue
            flange2 = _clean_optional_float(row.get("flange2_depth"))
            out.append(
                PanelSpec(
                    panel_id=str(row["panel_id"]).strip(),
                    face_width=float(row["width"]),
                    face_height=float(row["height"]),
                    thickness=_parse_thickness(row["thickness"]),
                    flange_type=str(row["flange_type"]).strip().upper(),
                    flange1_depth=float(row["flange1_depth"]),
                    flange2_depth=flange2,
                    hole_dia=hole_dia,
                    pitch=float(row["hole_pitch"]),
                    pattern=str(row["pattern"]).strip().lower(),
                    fastening_pair=str(row.get("fastening_pair", "none")).strip().lower() or "none",
                    k_factor_override=_clean_optional_float(row.get("k_factor_override")),
                    bend_radius_override=_clean_optional_float(row.get("bend_radius_override")),
                    gap_override=_clean_optional_float(row.get("gap_override")),
                )
            )
    return out


def bend_deduction(thickness: float, radius: float, k_factor: float, angle_deg: float = 90.0) -> float:
    angle_rad = math.radians(angle_deg)
    bend_allowance = angle_rad * (radius + k_factor * thickness)
    setback = math.tan(angle_rad / 2.0) * (radius + thickness)
    return 2.0 * setback - bend_allowance


def _flat_1d(face: float, d1: float, d2: Optional[float], thickness: float, radius: float, k_factor: float) -> float:
    bd = bend_deduction(thickness, radius, k_factor)
    total = face + 2.0 * d1 - 2.0 * bd
    if d2 and d2 > 0:
        total += 2.0 * d2 - 2.0 * bd
    return total


def flat_size(spec: PanelSpec) -> Tuple[float, float]:
    rule = _material_rule(spec)
    flange_type = spec.flange_type.upper()

    if flange_type == "L":
        w = _flat_1d(spec.face_width, spec.flange1_depth, None, spec.thickness, rule["bend_radius"], rule["k_factor"])
        h = _flat_1d(spec.face_height, spec.flange1_depth, None, spec.thickness, rule["bend_radius"], rule["k_factor"])
        return w, h

    if flange_type == "J":
        d2 = spec.flange2_depth if spec.flange2_depth is not None else 0.0
        w = _flat_1d(spec.face_width, spec.flange1_depth, None, spec.thickness, rule["bend_radius"], rule["k_factor"])
        h = _flat_1d(spec.face_height, spec.flange1_depth, d2, spec.thickness, rule["bend_radius"], rule["k_factor"])

        pair = spec.fastening_pair.lower()
        if pair == "vertical":
            h += 2.0 * rule["gap"]
        elif pair == "horizontal":
            w += 2.0 * rule["gap"]
        return w, h

    raise ValueError(f"Unsupported flange_type: {spec.flange_type}")


def _face_origin(spec: PanelSpec) -> Tuple[float, float]:
    rule = _material_rule(spec)
    bd = bend_deduction(spec.thickness, rule["bend_radius"], rule["k_factor"])
    x0 = max(spec.flange1_depth - bd, 0.0)
    y0 = max(spec.flange1_depth - bd, 0.0)
    if spec.flange_type.upper() == "J" and spec.fastening_pair.lower() == "horizontal":
        x0 += rule["gap"]
    if spec.flange_type.upper() == "J" and spec.fastening_pair.lower() == "vertical":
        y0 += rule["gap"]
    return x0, y0


def generate_hole_positions(width: float, height: float, hole_dia: float, pitch: float, pattern: str) -> List[Tuple[float, float]]:
    centers: List[Tuple[float, float]] = []
    r = hole_dia / 2.0
    if pattern == "straight":
        y = r
        while y <= height - r + 1e-9:
            x = r
            while x <= width - r + 1e-9:
                centers.append((x, y))
                x += pitch
            y += pitch
    elif pattern == "staggered":
        row_step = pitch * math.sqrt(3.0) / 2.0
        row = 0
        y = r
        while y <= height - r + 1e-9:
            x = r + (pitch / 2.0 if row % 2 else 0.0)
            while x <= width - r + 1e-9:
                centers.append((x, y))
                x += pitch
            y += row_step
            row += 1
    else:
        raise ValueError(f"Unsupported pattern: {pattern}")
    return centers


def generate_panel_dxf(spec: PanelSpec, outdir: str) -> None:
    flat_w, flat_h = flat_size(spec)
    x0, y0 = _face_origin(spec)

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    if "holes" not in doc.layers:
        doc.layers.add("holes")
    if "cut" not in doc.layers:
        doc.layers.add("cut")

    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (flat_w, 0), (flat_w, flat_h), (0, flat_h)], close=True, dxfattribs={"layer": "cut"})

    r = spec.hole_dia / 2.0
    for x, y in generate_hole_positions(spec.face_width, spec.face_height, spec.hole_dia, spec.pitch, spec.pattern):
        msp.add_circle((x0 + x, y0 + y), r, dxfattribs={"layer": "holes"})

    os.makedirs(outdir, exist_ok=True)
    doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))


class NestingSheet:
    def __init__(self, width: float, height: float, name: str):
        self.width = width
        self.height = height
        self.name = name
        self.placements: List[Tuple[str, float, float, float, float]] = []
        self._cursor_x = 0.0
        self._cursor_y = 0.0
        self._row_h = 0.0

    def try_place(self, panel_id: str, w: float, h: float) -> bool:
        if self._cursor_x + w <= self.width and self._cursor_y + h <= self.height:
            self.placements.append((panel_id, self._cursor_x, self._cursor_y, w, h))
            self._cursor_x += w
            self._row_h = max(self._row_h, h)
            return True
        if self._cursor_y + self._row_h + h <= self.height:
            self._cursor_x = 0.0
            self._cursor_y += self._row_h
            self._row_h = 0.0
            return self.try_place(panel_id, w, h)
        return False


def nest_panels(panels: List[PanelSpec], sw: float, sh: float) -> List[NestingSheet]:
    sheets: List[NestingSheet] = []
    for panel in panels:
        w, h = flat_size(panel)
        placed = False
        for sheet in sheets:
            if sheet.try_place(panel.panel_id, w, h):
                placed = True
                break
        if not placed:
            if w > sw or h > sh:
                raise ValueError(f"Panel {panel.panel_id} ({w:.3f}x{h:.3f}) does not fit on stock {sw:.3f}x{sh:.3f}")
            s = NestingSheet(sw, sh, f"Sheet_{len(sheets)+1}")
            s.try_place(panel.panel_id, w, h)
            sheets.append(s)
    return sheets


def write_nesting_dxf(sheets: List[NestingSheet], outdir: str) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    if "cut" not in doc.layers:
        doc.layers.add("cut")
    msp = doc.modelspace()

    y_offset = 0.0
    for sheet in sheets:
        msp.add_lwpolyline(
            [(0, y_offset), (sheet.width, y_offset), (sheet.width, y_offset + sheet.height), (0, y_offset + sheet.height)],
            close=True,
            dxfattribs={"layer": "cut"},
        )
        for _, x, y, w, h in sheet.placements:
            gx = x
            gy = y + y_offset
            msp.add_lwpolyline(
                [(gx, gy), (gx + w, gy), (gx + w, gy + h), (gx, gy + h)],
                close=True,
                dxfattribs={"layer": "cut"},
            )
        y_offset += sheet.height + 10.0

    os.makedirs(outdir, exist_ok=True)
    doc.saveas(os.path.join(outdir, "nesting_layout.dxf"))
