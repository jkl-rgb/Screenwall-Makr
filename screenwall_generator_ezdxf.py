"""
screenwall_generator.py
-----------------------------

Drop-in replacement for the original screenwall generator that uses the
`ezdxf` library to create Autodesk-compatible DXF files.

What changed:
- DXF creation now uses ezdxf instead of hand-written DXF text.
- Individual panel DXFs use valid layers: `holes` and `cut`.
- Nesting layout DXF uses valid `cut` layer geometry.

Install requirement before running:
    pip install ezdxf

Usage:
    python screenwall_generator_ezdxf.py input_panels.csv output_dir --nest
"""

import csv
import math
import os
from typing import List, Tuple, Dict, Optional

try:
    import ezdxf
except ImportError as exc:
    raise SystemExit(
        "This version requires ezdxf. Install it with: pip install ezdxf"
    ) from exc


MATERIAL_PROPERTIES: Dict[float, Dict[str, float]] = {
    0.0625: {"k_factor": 0.38, "radius_mult": 1.0},
    0.08: {"k_factor": 0.40, "radius_mult": 1.0},
    0.125: {"k_factor": 0.42, "radius_mult": 1.5},
    0.1875: {"k_factor": 0.44, "radius_mult": 2.0},
}


def lookup_material(thickness: float) -> Tuple[float, float]:
    available = sorted(MATERIAL_PROPERTIES.keys())
    candidate = None
    for t in available:
        if t <= thickness:
            candidate = t
    if candidate is None:
        candidate = available[0]
    props = MATERIAL_PROPERTIES.get(candidate, {"k_factor": 0.38, "radius_mult": 1.0})
    return props["k_factor"], props["radius_mult"]


def bend_allowance(thickness: float, radius: float, k_factor: float, angle_deg: float = 90.0) -> float:
    return (math.pi * angle_deg / 180.0) * (radius + k_factor * thickness)


def flange_flat_length(depth: float, thickness: float, radius: float, k_factor: float) -> float:
    inside_leg = max(depth - thickness, 0.0)
    straight_leg = max(inside_leg - radius, 0.0)
    ba = bend_allowance(thickness, radius, k_factor, 90.0)
    return straight_leg + ba


def calculate_flat_dimension(face_dim: float, flange_depths: List[float], thickness: float,
                             k_factor: float, radius_mult: float) -> float:
    total = face_dim
    for _ in range(2):
        for depth in flange_depths:
            radius = thickness * radius_mult
            total += flange_flat_length(depth, thickness, radius, k_factor)
    return total


def generate_hole_positions(width: float, height: float, hole_dia: float, pitch: float,
                            pattern: str, margin: float) -> List[Tuple[float, float]]:
    centres: List[Tuple[float, float]] = []
    start_x = margin + hole_dia / 2.0
    start_y = margin + hole_dia / 2.0

    if pattern.lower() == "straight":
        y = start_y
        while y <= height - margin - hole_dia / 2.0 + 1e-6:
            x = start_x
            while x <= width - margin - hole_dia / 2.0 + 1e-6:
                centres.append((x, y))
                x += pitch
            y += pitch
    elif pattern.lower() == "staggered":
        row_step = pitch * math.sqrt(3) / 2.0
        row_index = 0
        y = start_y
        while y <= height - margin - hole_dia / 2.0 + 1e-6:
            x_offset = 0.0 if row_index % 2 == 0 else pitch / 2.0
            x = start_x + x_offset
            while x <= width - margin - hole_dia / 2.0 + 1e-6:
                centres.append((x, y))
                x += pitch
            y += row_step
            row_index += 1
    else:
        raise ValueError(f"Unknown pattern type: {pattern}")

    return centres


def _ensure_layer(doc: "ezdxf.EzDxf", layer_name: str, color: int) -> None:
    if layer_name not in doc.layers:
        doc.layers.add(name=layer_name, color=color)


def write_dxf(panel_id: str, flat_width: float, flat_height: float,
              holes: List[Tuple[float, float]], hole_dia: float,
              output_path: str) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1  # inches
    _ensure_layer(doc, "holes", 1)
    _ensure_layer(doc, "cut", 2)
    msp = doc.modelspace()

    radius = hole_dia / 2.0
    for x, y in holes:
        msp.add_circle((x, y), radius, dxfattribs={"layer": "holes"})

    msp.add_lwpolyline(
        [
            (0.0, 0.0),
            (flat_width, 0.0),
            (flat_width, flat_height),
            (0.0, flat_height),
        ],
        close=True,
        dxfattribs={"layer": "cut"},
    )

    os.makedirs(output_path, exist_ok=True)
    filepath = os.path.join(output_path, f"{panel_id}.dxf")
    doc.saveas(filepath)
    print(f"Wrote {filepath}")


class PanelSpec:
    def __init__(self, panel_id: str, face_width: float, face_height: float,
                 thickness: float, flange_type: str, flange1_depth: float,
                 flange2_depth: Optional[float], hole_dia: float, pitch: float,
                 pattern: str):
        self.panel_id = panel_id
        self.face_width = face_width
        self.face_height = face_height
        self.thickness = thickness
        self.flange_type = flange_type.upper()
        self.flange1_depth = flange1_depth
        self.flange2_depth = flange2_depth
        self.hole_dia = hole_dia
        self.pitch = pitch
        self.pattern = pattern

    def flange_depths(self) -> List[float]:
        if self.flange_type == "L":
            return [self.flange1_depth]
        if self.flange_type == "J":
            d2 = self.flange2_depth if self.flange2_depth is not None else self.flange1_depth
            return [self.flange1_depth, d2]
        raise ValueError(f"Unknown flange type: {self.flange_type}")


def parse_csv(filepath: str) -> List[PanelSpec]:
    specs: List[PanelSpec] = []
    with open(filepath, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            pid = row["panel_id"].strip()
            face_w = float(row["width"])
            face_h = float(row["height"])
            th_raw = row["thickness"].strip()
            if th_raw.lower().endswith("ga"):
                num = th_raw[:-2].strip()
                gauge = float(num)
                gauge_map = {16: 0.0625, 14: 0.08, 11: 0.125}
                thickness = gauge_map.get(int(round(gauge)))
                if thickness is None:
                    raise ValueError(f"Unsupported gauge: {gauge}")
            else:
                thickness = float(th_raw)

            ftype = row["flange_type"].strip().upper()
            f1 = float(row["flange1_depth"])
            f2 = row.get("flange2_depth")
            f2_val: Optional[float] = float(f2) if f2 not in ("", None) else None
            hole_dia = float(row["hole_diameter"])
            hole_pitch = float(row["hole_pitch"])
            pattern = row["pattern"].strip().lower()
            if hole_dia < 0.125:
                print(f"Skipping panel {pid}: hole diameter {hole_dia} < 0.125\"")
                continue
            specs.append(PanelSpec(pid, face_w, face_h, thickness, ftype, f1, f2_val, hole_dia, hole_pitch, pattern))
    return specs


def generate_panel_dxf(spec: PanelSpec, output_dir: str) -> None:
    k_factor, radius_mult = lookup_material(spec.thickness)
    flange_depths = spec.flange_depths()
    flat_w = calculate_flat_dimension(spec.face_width, flange_depths, spec.thickness, k_factor, radius_mult)
    flat_h = calculate_flat_dimension(spec.face_height, flange_depths, spec.thickness, k_factor, radius_mult)
    offset_x = (flat_w - spec.face_width) / 2.0
    offset_y = (flat_h - spec.face_height) / 2.0
    margin = spec.hole_dia / 2.0 + spec.thickness
    hole_centres = generate_hole_positions(spec.face_width, spec.face_height, spec.hole_dia, spec.pitch, spec.pattern, margin)
    shifted = [(x + offset_x, y + offset_y) for (x, y) in hole_centres]
    write_dxf(spec.panel_id, flat_w, flat_h, shifted, spec.hole_dia, output_dir)


class FreeRect:
    def __init__(self, x: float, y: float, w: float, h: float):
        self.x = x
        self.y = y
        self.w = w
        self.h = h


class NestingSheet:
    def __init__(self, width: float, height: float, name: str):
        self.width = width
        self.height = height
        self.name = name
        self.free_rects: List[FreeRect] = [FreeRect(0.0, 0.0, width, height)]
        self.placements: List[Tuple[str, float, float, float, float]] = []

    def try_place(self, panel_id: str, w: float, h: float) -> Optional[Tuple[float, float]]:
        for idx, rect in enumerate(self.free_rects):
            if w <= rect.w and h <= rect.h:
                x = rect.x
                y = rect.y
                new_rects: List[FreeRect] = []
                if rect.w - w > 0:
                    new_rects.append(FreeRect(x + w, y, rect.w - w, h))
                    if rect.h - h > 0:
                        new_rects.append(FreeRect(x + w, y + h, rect.w - w, rect.h - h))
                if rect.h - h > 0:
                    new_rects.append(FreeRect(x, y + h, w, rect.h - h))
                self.free_rects.pop(idx)
                self.free_rects.extend(new_rects)
                self.free_rects = self._prune_free_rects(self.free_rects)
                self.placements.append((panel_id, x, y, w, h))
                return (x, y)
        return None

    @staticmethod
    def _prune_free_rects(rects: List[FreeRect]) -> List[FreeRect]:
        pruned: List[FreeRect] = []
        for i, r1 in enumerate(rects):
            contained = False
            for j, r2 in enumerate(rects):
                if i != j and r1.x >= r2.x and r1.y >= r2.y and r1.x + r1.w <= r2.x + r2.w and r1.y + r1.h <= r2.y + r2.h:
                    contained = True
                    break
            if not contained:
                pruned.append(r1)
        return pruned


def nest_panels(panels: List[PanelSpec], stock_width: float, stock_height: float) -> List[NestingSheet]:
    sheets: List[NestingSheet] = []
    for spec in panels:
        k_factor, radius_mult = lookup_material(spec.thickness)
        depths = spec.flange_depths()
        flat_w = calculate_flat_dimension(spec.face_width, depths, spec.thickness, k_factor, radius_mult)
        flat_h = calculate_flat_dimension(spec.face_height, depths, spec.thickness, k_factor, radius_mult)
        placed = False
        for sheet in sheets:
            if sheet.try_place(spec.panel_id, flat_w, flat_h) is not None:
                placed = True
                break
        if not placed:
            if flat_w > stock_width or flat_h > stock_height:
                raise ValueError(
                    f"Panel {spec.panel_id} ({flat_w:.2f}x{flat_h:.2f}) does not fit on stock sheet {stock_width}x{stock_height}"
                )
            sheet_name = f"Sheet_{len(sheets) + 1}"
            new_sheet = NestingSheet(stock_width, stock_height, sheet_name)
            new_sheet.try_place(spec.panel_id, flat_w, flat_h)
            sheets.append(new_sheet)
    return sheets


def write_nesting_dxf(sheets: List[NestingSheet], output_dir: str) -> None:
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    _ensure_layer(doc, "cut", 2)
    msp = doc.modelspace()

    y_offset = 0.0
    for sheet in sheets:
        sx, sy = 0.0, y_offset
        sw, sh = sheet.width, sheet.height
        msp.add_lwpolyline(
            [(sx, sy), (sx + sw, sy), (sx + sw, sy + sh), (sx, sy + sh)],
            close=True,
            dxfattribs={"layer": "cut"},
        )
        for _, px, py, pw, ph in sheet.placements:
            gx = px + sx
            gy = py + sy
            msp.add_lwpolyline(
                [(gx, gy), (gx + pw, gy), (gx + pw, gy + ph), (gx, gy + ph)],
                close=True,
                dxfattribs={"layer": "cut"},
            )
        y_offset += sheet.height + 10.0

    os.makedirs(output_path := output_dir, exist_ok=True)
    filepath = os.path.join(output_path, "nesting_layout.dxf")
    doc.saveas(filepath)
    print(f"Wrote {filepath}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate perforated panel DXF files and nest them.")
    parser.add_argument("csv", help="Input CSV file with panel definitions")
    parser.add_argument("output", help="Output directory for DXF files")
    parser.add_argument("--stock_width", type=float, default=48.0, help="Width of stock sheet (inches)")
    parser.add_argument("--stock_height", type=float, default=96.0, help="Height of stock sheet (inches)")
    parser.add_argument("--nest", action="store_true", help="Whether to produce a combined nesting layout")
    args = parser.parse_args()

    panels = parse_csv(args.csv)
    if not panels:
        print("No valid panels to process.")
        return

    for spec in panels:
        generate_panel_dxf(spec, args.output)

    if args.nest:
        sheets = nest_panels(panels, args.stock_width, args.stock_height)
        write_nesting_dxf(sheets, args.output)


if __name__ == "__main__":
    main()
