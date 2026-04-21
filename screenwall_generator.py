from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from typing import Optional

import ezdxf


MATERIAL_RULES = {
    0.0625: {"k": 0.38, "r": 0.0625, "gap": 0.0528},
    0.0800: {"k": 0.50, "r": 0.0800, "gap": 0.0528},
    0.1250: {"k": 0.42, "r": 0.1875, "gap": 0.0528},
    0.1875: {"k": 0.44, "r": 0.3750, "gap": 0.0528},
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


def _to_float(value, default=None):
    if value in (None, ""):
        return default
    return float(value)


def _thickness_to_float(value: str) -> float:
    raw = str(value).strip()
    gauges = {
        "16 ga": 0.0625,
        "14 ga": 0.0800,
        "11 ga": 0.1250,
    }
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
                )
            )
    return out


def bend_deduction(thickness, radius, k_factor):
    bend_allowance = (math.pi / 2.0) * (radius + k_factor * thickness)
    setback = radius + thickness
    return 2.0 * setback - bend_allowance


def get_rules(spec: PanelSpec):
    base = MATERIAL_RULES.get(round(spec.thickness, 4))
    if base is None:
        nearest = min(MATERIAL_RULES.keys(), key=lambda t: abs(t - spec.thickness))
        base = MATERIAL_RULES[nearest]

    return (
        spec.k_factor_override if spec.k_factor_override is not None else base["k"],
        spec.bend_radius_override if spec.bend_radius_override is not None else base["r"],
        spec.gap_override if spec.gap_override is not None else base["gap"],
    )


def flat_size(spec: PanelSpec):
    k, r, _gap = get_rules(spec)
    bd = bend_deduction(spec.thickness, r, k)
    f1 = spec.flange1_depth
    f2 = spec.flange2_depth or 0.0

    if spec.flange_type == "L":
        return (
            spec.face_width + 2.0 * f1 - 2.0 * bd,
            spec.face_height + 2.0 * f1 - 2.0 * bd,
        )

    return (
        spec.face_width + 2.0 * (f1 + f2) - 4.0 * bd,
        spec.face_height + 2.0 * (f1 + f2) - 4.0 * bd,
    )


def _hole_centers(face_x, face_y, face_w, face_h, hole_dia, pitch, pattern):
    centers = []
    radius = hole_dia / 2.0
    start_x = face_x + radius
    start_y = face_y + radius
    max_x = face_x + face_w - radius
    max_y = face_y + face_h - radius

    if pattern == "straight":
        y = start_y
        while y <= max_y + 1e-9:
            x = start_x
            while x <= max_x + 1e-9:
                centers.append((x, y))
                x += pitch
            y += pitch
        return centers

    row_step = pitch * math.sqrt(3.0) / 2.0
    row = 0
    y = start_y
    while y <= max_y + 1e-9:
        x_offset = 0.0 if row % 2 == 0 else pitch / 2.0
        x = start_x + x_offset
        while x <= max_x + 1e-9:
            centers.append((x, y))
            x += pitch
        y += row_step
        row += 1

    return centers


def generate_panel_dxf(spec: PanelSpec, outdir: str):
    w, h = flat_size(spec)

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    msp = doc.modelspace()

    for name, color in [("cut", 1), ("holes", 2)]:
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)

    msp.add_lwpolyline(
        [(0.0, 0.0), (w, 0.0), (w, h), (0.0, h)],
        close=True,
        dxfattribs={"layer": "cut"},
    )

    face_x = (w - spec.face_width) / 2.0
    face_y = (h - spec.face_height) / 2.0

    for x, y in _hole_centers(
        face_x, face_y, spec.face_width, spec.face_height, spec.hole_dia, spec.pitch, spec.pattern
    ):
        msp.add_circle((x, y), spec.hole_dia / 2.0, dxfattribs={"layer": "holes"})

    os.makedirs(outdir, exist_ok=True)
    doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))


def nest_panels(panels, sw, sh):
    return []


def write_nesting_dxf(sheets, outdir):
    return None
