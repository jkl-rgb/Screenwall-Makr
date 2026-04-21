# (FULL FILE — replace entire file)

from __future__ import annotations
import csv, math, os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import ezdxf

MATERIAL_RULES = {
    0.0800: {"k_factor": 0.50, "bend_radius": 0.0800, "gap": 0.0528},
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

def parse_csv(path):
    out = []
    with open(path) as f:
        r = csv.DictReader(f)
        for row in r:
            out.append(PanelSpec(
                panel_id=row["panel_id"],
                face_width=float(row["width"]),
                face_height=float(row["height"]),
                thickness=float(row["thickness"]),
                flange_type=row["flange_type"],
                flange1_depth=float(row["flange1_depth"]),
                flange2_depth=float(row["flange2_depth"] or 0),
                hole_dia=float(row["hole_diameter"]),
                pitch=float(row["hole_pitch"]),
                pattern=row["pattern"],
                fastening_pair=row.get("fastening_pair","none")
            ))
    return out

def bend_deduction(T, R, K):
    BA = (math.pi/2)*(R + K*T)
    SB = (R+T)
    return 2*SB - BA

def flat_size(spec):
    rule = MATERIAL_RULES[round(spec.thickness,4)]
    BD = bend_deduction(spec.thickness, rule["bend_radius"], rule["k_factor"])
    d = spec.flange1_depth
    return (
        spec.face_width + 2*d - 2*BD,
        spec.face_height + 2*d - 2*BD
    )

def generate_panel_dxf(spec, outdir):
    w,h = flat_size(spec)

    doc = ezdxf.new()
    msp = doc.modelspace()

    doc.layers.add("holes")
    doc.layers.add("cut")

    msp.add_lwpolyline([(0,0),(w,0),(w,h),(0,h)], close=True, dxfattribs={"layer":"cut"})

    pitch = spec.pitch
    r = spec.hole_dia/2

    y= r
    while y < h-r:
        x = r
        while x < w-r:
            msp.add_circle((x,y), r, dxfattribs={"layer":"holes"})
            x += pitch
        y += pitch

    doc.saveas(os.path.join(outdir,f"{spec.panel_id}.dxf"))

def nest_panels(panels, sw, sh):
    return []

def write_nesting_dxf(sheets, outdir):
    pass