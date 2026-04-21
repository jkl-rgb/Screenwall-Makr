from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from typing import Optional, Iterable, Tuple

import ezdxf

# Calibrated directly from the supplied Fusion reference DXFs:
#   - L_3003_Aluminum_080_SW_Makr.dxf
#   - J_3003_Aluminum_080_SW_Makr.dxf
# This file is intentionally locked to the provided 0.080 3003-H14 rule set.

THICKNESS = 0.0800
K_FACTOR = 0.50
BEND_RADIUS = 0.0800
GAP = 0.0528

GAUGE_MAP = {
    "16 Ga": 0.0625,
    "14 Ga": 0.0800,
    "11 Ga": 0.1250,
}

# L profile constants from supplied DXF
L_SIDE_STEP = 1.9858307972620377
L_FACE_SHRINK_PER_SIDE = 0.11733523804665032
L_BEND_OFFSET = 0.05158301765434606
L_BEND_EXTENT_OFFSET = 0.1458307972620373
L_BEND_EXTENT_INSET = 0.042664761953347785

# J profile constants from supplied DXF
J_FACE_SHRINK_PER_SIDE = 0.787335238046637
J_INNER_RETURN = 0.67
J_CHAMFER = 0.59
J_MID_STEP = 2.49583079726204
J_RADIUS_TANGENT = 2.68432635647742
J_OUTER = 3.274326356477418
J_Y1 = 0.7784955592153812
J_Y2 = 2.6043263564774253
J_Y3 = 2.684326356477421
J_Y4 = 3.2743263564773573
J_BEND_H1 = 0.6842477796078207
J_BEND_H2 = 2.5527433388230802
J_BEND_H3 = 2.4584955592153874
J_BEND_V1 = -0.721583017654333
J_BEND_V2 = -2.590078576869663
J_BEND_V3 = -2.68432635647742
J_BEND_V4 = -2.495830797262029
J_BEND_V5 = -0.8158307972620283
J_BEND_EXT_H_TOP1 = 0.5900000000000005
J_BEND_EXT_H_TOP2 = 0.7784955592153877
J_BEND_EXT_H_TOP3 = 2.4584955592153883
J_BEND_EXT_H_TOP4 = 2.6469911184307744
J_BEND_OFFSET = 0.1458307972620373

# exact relative control-point deltas extracted from the supplied J DXF
SPLINE_DELTAS = {
    "TR_TOP": [
        (0.0, 0.0), (0.0, 0.013483607817), (-0.001529247899, 0.026967215633),
        (-0.004502315576, 0.04045082345), (-0.015383355346, 0.089799068705),
        (-0.04710116983, 0.13914731396), (-0.08, 0.188495559215),
    ],
    "TL_TOP": [
        (0.0, 0.0), (-0.03289883017, -0.049348245255), (-0.064616644654, -0.09869649051),
        (-0.075497684424, -0.148044735766), (-0.078470752101, -0.161528343582),
        (-0.08, -0.175011951399), (-0.08, -0.188495559215),
    ],
    "LT_UPPER": [
        (0.0, 0.0), (-0.013483607817, 0.0), (-0.026967215633, -0.001529247899),
        (-0.04045082345, -0.004502315576), (-0.089799068705, -0.015383355346),
        (-0.13914731396, -0.04710116983), (-0.188495559215, -0.08),
    ],
    "LT_LOWER": [
        (0.0, 0.0), (0.049348245255, -0.03289883017), (0.09869649051, -0.064616644654),
        (0.148044735766, -0.075497684424), (0.161528343582, -0.078470752101),
        (0.175011951399, -0.08), (0.188495559215, -0.08),
    ],
    "BL_BOTTOM": [
        (0.0, 0.0), (0.0, -0.013483607817), (0.001529247899, -0.026967215633),
        (0.004502315576, -0.04045082345), (0.015383355346, -0.089799068705),
        (0.04710116983, -0.13914731396), (0.08, -0.188495559215),
    ],
    "BR_BOTTOM": [
        (0.0, 0.0), (0.03289883017, 0.049348245255), (0.064616644654, 0.09869649051),
        (0.075497684424, 0.148044735766), (0.078470752101, 0.161528343582),
        (0.08, 0.175011951399), (0.08, 0.188495559215),
    ],
    "RT_LOWER": [
        (0.0, 0.0), (0.013483607817, 0.0), (0.026967215633, 0.001529247899),
        (0.04045082345, 0.004502315576), (0.089799068705, 0.015383355346),
        (0.13914731396, 0.04710116983), (0.188495559215, 0.08),
    ],
    "RT_UPPER": [
        (0.0, 0.0), (-0.049348245255, 0.03289883017), (-0.09869649051, 0.064616644654),
        (-0.148044735766, 0.075497684424), (-0.161528343582, 0.078470752101),
        (-0.175011951399, 0.08), (-0.188495559215, 0.08),
    ],
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
    k_factor_override: float = K_FACTOR
    bend_radius_override: float = BEND_RADIUS
    gap_override: float = GAP


def _to_float_thickness(raw: str) -> float:
    value = raw.strip()
    if value in GAUGE_MAP:
        return GAUGE_MAP[value]
    return float(value)


def _validate_supported(spec: PanelSpec) -> None:
    if round(spec.thickness, 4) != THICKNESS:
        raise ValueError("This production generator is calibrated only for 0.080 material.")
    if spec.flange_type.upper() == "L":
        if abs(spec.flange1_depth - 2.0) > 1e-6:
            raise ValueError("L production corners are calibrated for 2.000 flange depth.")
    elif spec.flange_type.upper() == "J":
        f2 = spec.flange2_depth if spec.flange2_depth is not None else 0.75
        if abs(spec.flange1_depth - 2.0) > 1e-6 or abs(f2 - 0.75) > 1e-6:
            raise ValueError("J production corners are calibrated for 2.000 / 0.750 flange depths.")
    else:
        raise ValueError(f"Unsupported flange type: {spec.flange_type}")


def parse_csv(path: str):
    panels = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            panels.append(
                PanelSpec(
                    panel_id=row["panel_id"].strip(),
                    face_width=float(row["width"]),
                    face_height=float(row["height"]),
                    thickness=_to_float_thickness(row["thickness"]),
                    flange_type=row["flange_type"].strip().upper(),
                    flange1_depth=float(row["flange1_depth"]),
                    flange2_depth=float(row["flange2_depth"]) if str(row.get("flange2_depth", "")).strip() else None,
                    hole_dia=float(row["hole_diameter"]),
                    pitch=float(row["hole_pitch"]),
                    pattern=row["pattern"].strip().lower(),
                    fastening_pair=row.get("fastening_pair", "none").strip().lower() or "none",
                    k_factor_override=float(row.get("k_factor_override") or K_FACTOR),
                    bend_radius_override=float(row.get("bend_radius_override") or BEND_RADIUS),
                    gap_override=float(row.get("gap_override") or GAP),
                )
            )
    return panels


def _ensure_layer(doc: ezdxf.EzDxf, name: str, color: int) -> None:
    if name not in doc.layers:
        doc.layers.add(name=name, color=color)


def _add_control_spline(msp, start: Tuple[float, float], deltas: Iterable[Tuple[float, float]], layer: str) -> None:
    sx, sy = start
    control_points = [(sx + dx, sy + dy) for dx, dy in deltas]
    msp.add_open_spline(control_points, degree=3, dxfattribs={"layer": layer})


def _l_geometry(spec: PanelSpec):
    web_w = spec.face_width - 2.0 * L_FACE_SHRINK_PER_SIDE
    web_h = spec.face_height - 2.0 * L_FACE_SHRINK_PER_SIDE
    step = L_SIDE_STEP
    outer_points = [
        (-step, web_h + step),
        (-step, step),
        (0.0, step),
        (0.0, 0.0),
        (web_w, 0.0),
        (web_w, step),
        (web_w + step, step),
        (web_w + step, web_h + step),
        (web_w, web_h + step),
        (web_w, web_h + 2.0 * step),
        (0.0, web_h + 2.0 * step),
        (0.0, web_h + step),
    ]
    bends = [
        ((0.0, step - L_BEND_OFFSET), (web_w, step - L_BEND_OFFSET)),
        ((0.0, web_h + step + L_BEND_OFFSET), (web_w, web_h + step + L_BEND_OFFSET)),
        ((-L_BEND_OFFSET, step), (-L_BEND_OFFSET, web_h + step)),
        ((web_w + L_BEND_OFFSET, step), (web_w + L_BEND_OFFSET, web_h + step)),
    ]
    bend_extents = [
        ((-L_BEND_EXTENT_OFFSET, step), (-L_BEND_EXTENT_OFFSET, web_h + step)),
        ((0.0, web_h + step + L_BEND_EXTENT_OFFSET), (web_w, web_h + step + L_BEND_EXTENT_OFFSET)),
        ((web_w + L_BEND_EXTENT_OFFSET, step), (web_w + L_BEND_EXTENT_OFFSET, web_h + step)),
        ((0.0, step - L_BEND_EXTENT_OFFSET), (web_w, step - L_BEND_EXTENT_OFFSET)),
    ]
    bend_extent_rect = [
        (L_BEND_EXTENT_INSET, step + L_BEND_EXTENT_INSET),
        (web_w - L_BEND_EXTENT_INSET, step + L_BEND_EXTENT_INSET),
        (web_w - L_BEND_EXTENT_INSET, web_h + step - L_BEND_EXTENT_INSET),
        (L_BEND_EXTENT_INSET, web_h + step - L_BEND_EXTENT_INSET),
    ]
    return web_w, web_h, outer_points, bends, bend_extents, bend_extent_rect


def _j_geometry(spec: PanelSpec):
    web_w = spec.face_width - 2.0 * J_FACE_SHRINK_PER_SIDE
    web_h = spec.face_height - 2.0 * J_FACE_SHRINK_PER_SIDE
    lines = [
        ((web_w + J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER), (web_w + J_INNER_RETURN, web_h + J_Y2 + J_OUTER)),
        ((web_w + J_CHAMFER, web_h + J_Y3 + J_OUTER), (web_w, web_h + 2.0 * J_OUTER)),
        ((web_w, web_h + 2.0 * J_OUTER), (0.0, web_h + 2.0 * J_OUTER)),
        ((0.0, web_h + 2.0 * J_OUTER), (-J_CHAMFER, web_h + J_Y3 + J_OUTER)),
        ((-J_INNER_RETURN, web_h + J_Y2 + J_OUTER), (-J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER)),
        ((-J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER), (-J_MID_STEP, web_h + J_INNER_RETURN + J_OUTER)),
        ((-J_OUTER, web_h + J_OUTER), (-J_OUTER, J_OUTER)),
        ((-J_OUTER, J_OUTER), (-J_RADIUS_TANGENT, J_Y3)),
        ((-J_MID_STEP, J_Y2), (-J_INNER_RETURN, J_Y2)),
        ((-J_INNER_RETURN, J_Y2), (-J_INNER_RETURN, J_Y1)),
        ((-J_CHAMFER, J_CHAMFER), (0.0, 0.0)),
        ((0.0, 0.0), (web_w, 0.0)),
        ((web_w, 0.0), (web_w + J_CHAMFER, J_CHAMFER)),
        ((web_w + J_INNER_RETURN, J_Y1), (web_w + J_INNER_RETURN, J_Y2)),
        ((web_w + J_INNER_RETURN, J_Y2), (web_w + J_MID_STEP, J_Y2)),
        ((web_w + J_OUTER, J_OUTER), (web_w + J_OUTER, web_h + J_OUTER)),
        ((web_w + J_OUTER, web_h + J_OUTER), (web_w + J_RADIUS_TANGENT, web_h + J_OUTER)),
        ((web_w + J_MID_STEP, web_h + J_INNER_RETURN + J_OUTER), (web_w + J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER)),
    ]
    spline_starts = [
        ((web_w + J_INNER_RETURN, web_h + J_Y2 + J_OUTER), "TR_TOP"),
        ((-J_CHAMFER, web_h + J_Y3 + J_OUTER), "TL_TOP"),
        ((-J_MID_STEP, web_h + J_INNER_RETURN + J_OUTER), "LT_UPPER"),
        ((-J_RADIUS_TANGENT, J_Y3), "LT_LOWER"),
        ((-J_INNER_RETURN, J_Y1), "BL_BOTTOM"),
        ((web_w + J_CHAMFER, J_CHAMFER), "BR_BOTTOM"),
        ((web_w + J_MID_STEP, J_Y2), "RT_LOWER"),
        ((web_w + J_RADIUS_TANGENT, web_h + J_OUTER), "RT_UPPER"),
    ]
    bends = [
        ((-J_INNER_RETURN + 0.02343145750501662, J_BEND_H1), (web_w + J_INNER_RETURN - 0.023431457505011287, J_BEND_H1)),
        ((J_BEND_V2, web_h + 0.20242829007576148), (J_BEND_V2, J_Y2 + 0.023431457505063034)),
        ((J_BEND_V1, web_h + J_INNER_RETURN + J_OUTER), (J_BEND_V1, J_Y2)),
        ((-J_INNER_RETURN, J_BEND_H2), (web_w + J_INNER_RETURN, J_BEND_H2)),
        ((web_w + 2.5900785768708646, J_Y2 + 0.02343145750554815), (web_w + 2.5900785768708646, web_h + 0.20242829007528033)),
        ((web_w + J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER + 0.051583017654348095), (-J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER + 0.051583017654352536)),
        ((web_w + J_BEND_H1 - J_BEND_H1 + J_INNER_RETURN + 0.05158301765435342, J_Y2), (web_w + J_INNER_RETURN + 0.05158301765435342, web_h + J_INNER_RETURN + J_OUTER)),
        ((web_w + J_INNER_RETURN - 0.02343145750526042, web_h + 2.5385736933469605), (-J_INNER_RETURN + 0.023431457505257755, web_h + 2.5385736933469605)),
    ]
    # explicit bend extents mirrored from the supplied DXF
    bend_extents = [
        ((J_BEND_V3, J_Y3), (J_BEND_V3, web_h + J_OUTER)),
        ((J_BEND_V4, web_h + J_INNER_RETURN + J_OUTER), (J_BEND_V4, J_Y2)),
        ((web_w + J_RADIUS_TANGENT, web_h + J_OUTER), (web_w + J_RADIUS_TANGENT, J_Y3)),
        ((web_w + J_MID_STEP, J_Y2), (web_w + J_MID_STEP, web_h + J_INNER_RETURN + J_OUTER)),
        ((-J_CHAMFER, J_CHAMFER), (web_w + J_CHAMFER, J_CHAMFER)),
        ((-J_INNER_RETURN, J_Y1), (web_w + J_INNER_RETURN, J_Y1)),
        ((-J_CHAMFER, web_h + J_Y3 + J_OUTER), (web_w + J_CHAMFER, web_h + J_Y3 + J_OUTER)),
        ((web_w + J_INNER_RETURN, web_h + J_Y2 + J_OUTER), (-J_INNER_RETURN, web_h + J_Y2 + J_OUTER)),
        ((J_BEND_V5, J_Y2), (J_BEND_V5, web_h + J_INNER_RETURN + J_OUTER)),
        ((-J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER + J_BEND_OFFSET), (web_w + J_INNER_RETURN, web_h + J_INNER_RETURN + J_OUTER + J_BEND_OFFSET)),
        ((web_w + J_INNER_RETURN + J_BEND_OFFSET, web_h + J_INNER_RETURN + J_OUTER), (web_w + J_INNER_RETURN + J_BEND_OFFSET, J_Y2)),
        ((web_w + J_INNER_RETURN, J_BEND_H3), (-J_INNER_RETURN, J_BEND_H3)),
    ]
    bend_extent_rect = [
        (-0.6273352380466433, 2.6469911184307766),
        (web_w + 0.6273352380466433, 2.6469911184307766),
        (web_w + 0.6273352380466433, web_h + 3.9016615945240455),
        (-0.6273352380466433, web_h + 3.9016615945240455),
    ]
    return web_w, web_h, lines, spline_starts, bends, bend_extents, bend_extent_rect


def _generate_holes(msp, spec: PanelSpec, x0: float, y0: float, width: float, height: float) -> None:
    r = spec.hole_dia / 2.0
    pitch = spec.pitch
    if spec.pattern == "straight":
        y = y0 + r
        while y <= y0 + height - r + 1e-9:
            x = x0 + r
            while x <= x0 + width - r + 1e-9:
                msp.add_circle((x, y), r, dxfattribs={"layer": "HOLES"})
                x += pitch
            y += pitch
    elif spec.pattern == "staggered":
        row_step = pitch * math.sqrt(3.0) / 2.0
        row = 0
        y = y0 + r
        while y <= y0 + height - r + 1e-9:
            x = x0 + r + (pitch / 2.0 if row % 2 else 0.0)
            while x <= y0 * 0 + x0 + width - r + 1e-9:
                msp.add_circle((x, y), r, dxfattribs={"layer": "HOLES"})
                x += pitch
            y += row_step
            row += 1
    else:
        raise ValueError(f"Unsupported pattern: {spec.pattern}")


def generate_panel_dxf(spec: PanelSpec, outdir: str) -> None:
    _validate_supported(spec)
    os.makedirs(outdir, exist_ok=True)

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    _ensure_layer(doc, "OUTER_PROFILES", 1)
    _ensure_layer(doc, "BEND", 3)
    _ensure_layer(doc, "BEND_EXTENT", 8)
    _ensure_layer(doc, "HOLES", 5)
    msp = doc.modelspace()

    if spec.flange_type.upper() == "L":
        web_w, web_h, outer_points, bends, bend_extents, bend_extent_rect = _l_geometry(spec)
        msp.add_lwpolyline(outer_points, close=True, dxfattribs={"layer": "OUTER_PROFILES"})
        for s, e in bends:
            msp.add_line(s, e, dxfattribs={"layer": "BEND"})
        for s, e in bend_extents:
            msp.add_line(s, e, dxfattribs={"layer": "BEND_EXTENT"})
        msp.add_lwpolyline(bend_extent_rect, close=True, dxfattribs={"layer": "BEND_EXTENT"})
        _generate_holes(msp, spec, 0.0, 0.0, web_w, web_h)
    else:
        web_w, web_h, lines, spline_starts, bends, bend_extents, bend_extent_rect = _j_geometry(spec)
        for s, e in lines:
            msp.add_line(s, e, dxfattribs={"layer": "OUTER_PROFILES"})
        for start, key in spline_starts:
            _add_control_spline(msp, start, SPLINE_DELTAS[key], "OUTER_PROFILES")
        for s, e in bends:
            msp.add_line(s, e, dxfattribs={"layer": "BEND"})
        for s, e in bend_extents:
            msp.add_line(s, e, dxfattribs={"layer": "BEND_EXTENT"})
        msp.add_lwpolyline(bend_extent_rect, close=True, dxfattribs={"layer": "BEND_EXTENT"})
        _generate_holes(msp, spec, 0.0, 0.0, web_w, web_h)

    doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))


def nest_panels(panels, sw, sh):
    return []


def write_nesting_dxf(sheets, outdir):
    return None
