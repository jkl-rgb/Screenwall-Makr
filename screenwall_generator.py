"""
screenwall_generator.py
----------------------

This module provides a simple, self‑contained implementation for generating
perforated sheet metal panel designs and exporting them as ASCII DXF files.

The tool is tailored for the creation of “screenwall” panels – flat metal
screens with a user‑defined face area, return flanges on the edges and a
repeating array of holes across the face.  Panel specifications are read
from a CSV file and include the finished face dimensions, material
thickness, flange type (single return – “L” – or double return – “J”),
flange depths, hole diameter, hole pitch and hole pattern (straight or
staggered).

Key features:

* Flat pattern calculation:  Using the supplied material thickness and a
  recommended K‑factor table, the script computes how much material is
  required on each side of the panel to form the flanges.  The developed
  length accounts for bend radii and bend allowances to approximate the
  location of fold lines.

* Hole pattern generation:  Holes are laid out on the face area using
  either a rectangular grid (straight pattern) or a staggered grid
  (triangular pattern).  Margins are automatically applied so that holes
  stay clear of bend lines and edges.

* DXF export:  Each panel generates its own DXF file.  Two layers are
  created: one for the perforation holes and another for the perimeter
  cut.  Holes are written as CIRCLE entities and the outline as a closed
  polyline.

* Simple nesting stub:  The script includes a minimal first‑fit bin
  packing algorithm that can nest multiple panels onto stock sheets.  It
  is intended as a placeholder; users with more advanced nesting
  algorithms can swap this out or integrate existing code.

To run the generator on a CSV file:

    python screenwall_generator.py input_panels.csv output_dir

The output directory will be created if it does not exist.  A DXF file
will be produced for each panel.  If multiple panels are present the
nesting algorithm will also write a combined DXF showing all nested
panels on stock sheets.

Note:  This implementation deliberately avoids external dependencies
such as `ezdxf`.  DXF files are written manually following the
AutoCAD R12 ASCII format.  While the generated files are simple, they
are compatible with most CAD/CAM software used for laser cutting and
punching.
"""

import csv
import math
import os
from typing import List, Tuple, Dict, Optional


# -----------------------------------------------------------------------------
# Material properties
#
# The table below lists recommended bend parameters for 3003‑H14 aluminum
# sheets of various gauges.  Each entry maps the sheet thickness (inches) to
# a recommended K‑factor and minimum bend radius multiplier.  The multiplier
# expresses the minimum internal bend radius as a multiple of the sheet
# thickness.  For example, a 1.5× multiplier means the minimum radius
# for a 0.125" sheet is 0.1875".  Additional gauges can be added to this
# dictionary as needed.

MATERIAL_PROPERTIES: Dict[float, Dict[str, float]] = {
    0.0625: {"k_factor": 0.38, "radius_mult": 1.0},  # 16 Ga
    0.08:   {"k_factor": 0.40, "radius_mult": 1.0},  # 14 Ga
    0.125:  {"k_factor": 0.42, "radius_mult": 1.5},  # 11 Ga
    0.1875: {"k_factor": 0.44, "radius_mult": 2.0},  # 3/16"
}


def lookup_material(thickness: float) -> Tuple[float, float]:
    """Return (k_factor, radius_multiplier) for a given sheet thickness.

    If the exact thickness is not found, the nearest defined thickness
    below the requested value is used.  If none is found, a default
    K‑factor of 0.38 and radius multiplier of 1.0 are returned.

    Parameters
    ----------
    thickness : float
        Sheet thickness in inches.

    Returns
    -------
    (k_factor, radius_mult) : Tuple[float, float]
    """
    # Find the closest defined thickness not exceeding the requested value
    available = sorted(MATERIAL_PROPERTIES.keys())
    candidate = None
    for t in available:
        if t <= thickness:
            candidate = t
    if candidate is None:
        # Use smallest available
        candidate = available[0]
    props = MATERIAL_PROPERTIES.get(candidate, {"k_factor": 0.38, "radius_mult": 1.0})
    return props["k_factor"], props["radius_mult"]


def bend_allowance(thickness: float, radius: float, k_factor: float, angle_deg: float = 90.0) -> float:
    """Compute the bend allowance for a bend.

    The bend allowance is the length of the material arc that spans the
    neutral axis of the bend.  For a V‑bend of `angle_deg` degrees, the
    allowance is calculated as:

        BA = (π * angle_deg / 180) * (radius + k_factor × thickness)

    Parameters
    ----------
    thickness : float
        Sheet thickness (inches).
    radius : float
        Internal bend radius (inches).
    k_factor : float
        K‑factor representing the location of the neutral axis as a
        fraction of the sheet thickness.
    angle_deg : float, optional
        Bend angle in degrees.  Defaults to 90° (a right angle).

    Returns
    -------
    float
        Bend allowance in inches.
    """
    return (math.pi * angle_deg / 180.0) * (radius + k_factor * thickness)


def flange_flat_length(depth: float, thickness: float, radius: float, k_factor: float) -> float:
    """Compute the flat length required for a single flange.

    Given the outside flange depth (distance from the inside face of the
    panel to the outside edge of the flange), this function calculates
    how much of the sheet must be allocated to the flange in the flat
    pattern.  The calculation accounts for the bend radius, material
    thickness and the bend allowance for a 90° bend.

    The method used here is a simplified version of common sheet metal
    formulas:  the outside flange depth comprises three portions – the
    straight leg, the bend radius and the material thickness.  To find
    the developed length, the straight leg contribution is obtained by
    subtracting the radius and thickness from the flange depth, and the
    bend allowance is added.

    Parameters
    ----------
    depth : float
        Outside flange depth (inches).
    thickness : float
        Sheet thickness (inches).
    radius : float
        Internal bend radius (inches).
    k_factor : float
        K‑factor used to compute bend allowance.

    Returns
    -------
    float
        Flat length (inches) needed for the flange.
    """
    # Inside leg length (distance from the inside face to the theoretical
    # tangent point of the bend) is the outside depth minus the material
    # thickness.
    inside_leg = max(depth - thickness, 0.0)
    # Deduct the radius portion which will become the arc of the bend.
    straight_leg = max(inside_leg - radius, 0.0)
    # Add the bend allowance for a 90° bend
    ba = bend_allowance(thickness, radius, k_factor, 90.0)
    return straight_leg + ba


def calculate_flat_dimension(face_dim: float, flange_depths: List[float], thickness: float,
                             k_factor: float, radius_mult: float) -> float:
    """Compute the total flat dimension for one axis of the panel.

    The flat dimension equals the face dimension plus the contributions
    from flanges on both sides.  Each flange may have multiple returns
    (for “J” folds); the depths list contains the depths of each return
    in order.  For symmetry, the same depths are applied on both sides.

    Parameters
    ----------
    face_dim : float
        Finished face dimension along one axis (inches).
    flange_depths : list of float
        List of flange depths (outside dimensions) for one edge.  For an
        L‑shaped return this list has one element.  For a J‑shaped
        return this list has two elements corresponding to the two
        sequential bends.
    thickness : float
        Sheet thickness (inches).
    k_factor : float
        K‑factor for bend allowance.
    radius_mult : float
        Multiplier that, when multiplied by thickness, yields the
        minimum recommended bend radius.

    Returns
    -------
    float
        Developed length of the panel along this axis.
    """
    total = face_dim
    # For each side (left/right or top/bottom)
    for _ in range(2):
        # For J‑shaped flanges the user may specify two depths.  The
        # internal radius may be the same for each bend (use the
        # thickness × radius_mult).  For multiple bends we accumulate
        # successive flange lengths.
        for depth in flange_depths:
            radius = thickness * radius_mult
            total += flange_flat_length(depth, thickness, radius, k_factor)
    return total


def generate_hole_positions(width: float, height: float, hole_dia: float, pitch: float,
                            pattern: str, margin: float) -> List[Tuple[float, float]]:
    """Generate hole centre positions for a given face area.

    Holes are constrained by a uniform margin around the perimeter to avoid
    penetrating the bend zones.  Two pattern types are supported:

    * straight – holes are laid out on a rectangular grid with equal
      spacing in both directions.
    * staggered – holes are arranged in a triangular pattern.  Every
      alternate row is offset by half the pitch and the vertical spacing
      is pitch × sin(60°) ≈ 0.8660 × pitch.

    Parameters
    ----------
    width : float
        Width of the face area (inches).
    height : float
        Height of the face area (inches).
    hole_dia : float
        Diameter of the holes (inches).
    pitch : float
        Centre‑to‑centre pitch between adjacent holes (inches).
    pattern : str
        Either "straight" or "staggered".
    margin : float
        Minimum distance from the edge of the face area to the centre of
        the nearest hole (inches).

    Returns
    -------
    list of (x, y) tuples
        Centre coordinates of all holes.
    """
    centres: List[Tuple[float, float]] = []

    # Starting positions: offset by margin and half hole diameter to
    # ensure holes stay within the allowed area.
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
        # Triangular pitch: vertical spacing is pitch * sqrt(3) / 2
        row_step = pitch * math.sqrt(3) / 2.0
        row_index = 0
        y = start_y
        while y <= height - margin - hole_dia / 2.0 + 1e-6:
            # Offset alternate rows by half the pitch
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


def write_dxf(panel_id: str, flat_width: float, flat_height: float,
              holes: List[Tuple[float, float]], hole_dia: float,
              output_path: str) -> None:
    """Write a simple DXF file for a panel.

    Two layers are written:  'holes' for perforation circles and 'cut'
    for the perimeter outline.  The origin (0,0) lies at the lower left
    corner of the flat pattern.  All units are in inches.

    Parameters
    ----------
    panel_id : str
        Identifier for the panel.  Used in the filename.
    flat_width : float
        Total flat width including flanges (inches).
    flat_height : float
        Total flat height including flanges (inches).
    holes : list of (x, y)
        Coordinates of hole centres relative to the flat pattern origin.
    hole_dia : float
        Diameter of the holes (inches).
    output_path : str
        Directory where the DXF file will be written.

    Returns
    -------
    None
    """
    # Prepare DXF string
    dxf_lines: List[str] = []
    # Header
    dxf_lines.extend([
        "0", "SECTION", "2", "HEADER",
        "9", "$INSUNITS", "70", "1",  # inches
        "0", "ENDSEC",
    ])
    # Table definitions
    dxf_lines.extend([
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER", "70", "2",  # two layers
        # Layer 1: holes
        "0", "LAYER", "2", "holes", "70", "0", "62", "1", "6", "CONTINUOUS",
        # Layer 2: cut
        "0", "LAYER", "2", "cut", "70", "0", "62", "2", "6", "CONTINUOUS",
        "0", "ENDTAB", "0", "ENDSEC",
    ])
    # Entities section
    dxf_lines.extend([
        "0", "SECTION", "2", "ENTITIES",
    ])
    # Write holes as CIRCLE entities
    radius = hole_dia / 2.0
    for (x, y) in holes:
        dxf_lines.extend([
            "0", "CIRCLE",
            "8", "holes",  # layer name
            "10", f"{x:.5f}",
            "20", f"{y:.5f}",
            "30", "0.0",
            "40", f"{radius:.5f}",
        ])
    # Draw perimeter as a closed polyline in layer 'cut'
    # Use LWPOLYLINE for simplicity
    # The DXF LWPOLYLINE entity expects: 0 LWPOLYLINE, 8 layer, 90 number of vertices,
    # 70 bitflag (1 = closed), then pairs of 10/20 codes for X/Y of each vertex.
    dxf_lines.extend([
        "0", "LWPOLYLINE",
        "8", "cut",
        "90", "4",  # number of vertices
        "70", "1",  # closed polyline
        "10", f"{0.0:.5f}", "20", f"{0.0:.5f}",
        "10", f"{flat_width:.5f}", "20", f"{0.0:.5f}",
        "10", f"{flat_width:.5f}", "20", f"{flat_height:.5f}",
        "10", f"{0.0:.5f}", "20", f"{flat_height:.5f}",
    ])
    # End sections
    dxf_lines.extend([
        "0", "ENDSEC",
        "0", "EOF",
    ])

    # Write file
    filename = f"{panel_id}.dxf"
    filepath = os.path.join(output_path, filename)
    with open(filepath, "w") as f:
        f.write("\n".join(dxf_lines))
    print(f"Wrote {filepath}")


class PanelSpec:
    """Simple container for panel properties."""
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
        """Return a list of flange depths per side based on flange type."""
        if self.flange_type == "L":
            # Single return – one bend per side
            return [self.flange1_depth]
        elif self.flange_type == "J":
            # Double return – two bends per side
            # Both depths must be provided; if second is None use first
            d2 = self.flange2_depth if self.flange2_depth is not None else self.flange1_depth
            return [self.flange1_depth, d2]
        else:
            raise ValueError(f"Unknown flange type: {self.flange_type}")


def parse_csv(filepath: str) -> List[PanelSpec]:
    """Parse a CSV file containing panel definitions.

    The expected columns are:
        panel_id, width, height, thickness, flange_type,
        flange1_depth, flange2_depth, hole_diameter, hole_pitch, pattern

    Width, height, depths, hole diameters and pitches are interpreted as
    inches.  Thickness may be specified either as a decimal value (e.g.
    `0.125`) or as a gauge string (e.g. `16 Ga`).  Gauge values are
    mapped to decimals based on the MATERIAL_PROPERTIES table.

    Parameters
    ----------
    filepath : str
        Path to the input CSV file.

    Returns
    -------
    list of PanelSpec
    """
    specs: List[PanelSpec] = []
    with open(filepath, newline="") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            pid = row["panel_id"].strip()
            face_w = float(row["width"])
            face_h = float(row["height"])
            # Interpret thickness
            th_raw = row["thickness"].strip()
            thickness: float
            if th_raw.lower().endswith("ga"):
                # Extract gauge number and find approximate decimal thickness
                num = th_raw[:-2].strip()
                try:
                    gauge = float(num)
                except ValueError:
                    raise ValueError(f"Invalid gauge specification: {th_raw}")
                # Round gauge to nearest known value (16, 14, 11)
                # Map gauge number to decimal via inverse dictionary
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
            # Skip if hole diameter below 0.125"
            if hole_dia < 0.125:
                print(f"Skipping panel {pid}: hole diameter {hole_dia} < 0.125\"")
                continue
            specs.append(PanelSpec(pid, face_w, face_h, thickness, ftype,
                                   f1, f2_val, hole_dia, hole_pitch, pattern))
    return specs


def generate_panel_dxf(spec: PanelSpec, output_dir: str) -> None:
    """Generate a DXF file for the given panel specification."""
    k_factor, radius_mult = lookup_material(spec.thickness)
    flange_depths = spec.flange_depths()
    # Compute flat dimensions for width and height
    flat_w = calculate_flat_dimension(spec.face_width, flange_depths, spec.thickness,
                                      k_factor, radius_mult)
    flat_h = calculate_flat_dimension(spec.face_height, flange_depths, spec.thickness,
                                      k_factor, radius_mult)
    # Determine offsets (distance from flat origin to face area origin)
    # Each side contributes half the difference between flat and face
    offset_x = (flat_w - spec.face_width) / 2.0
    offset_y = (flat_h - spec.face_height) / 2.0
    # Margin around the face area where holes are not allowed.  A simple
    # rule is to leave one hole radius plus thickness of material.
    margin = spec.hole_dia / 2.0 + spec.thickness
    # Generate hole centres within the face area (face coordinates)
    hole_centres = generate_hole_positions(spec.face_width, spec.face_height,
                                           spec.hole_dia, spec.pitch,
                                           spec.pattern, margin)
    # Shift holes by offsets to place them within the flat pattern
    shifted = [(x + offset_x, y + offset_y) for (x, y) in hole_centres]
    # Write DXF
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    write_dxf(spec.panel_id, flat_w, flat_h, shifted, spec.hole_dia, output_dir)


# -----------------------------------------------------------------------------
# Simple first‑fit nesting
#
# The following classes implement a very rudimentary bin‑packing
# algorithm.  It places rectangles into sheets by scanning for the
# first sheet and the first free space that will fit.  Free spaces are
# updated after each placement.  This is not optimal but suffices for a
# functional demonstration.  Users with more sophisticated requirements
# should integrate their own nesting algorithms.

class FreeRect:
    def __init__(self, x: float, y: float, w: float, h: float):
        self.x = x
        self.y = y
        self.w = w
        self.h = h

    @property
    def area(self) -> float:
        return self.w * self.h


class NestingSheet:
    def __init__(self, width: float, height: float, name: str):
        self.width = width
        self.height = height
        self.name = name
        self.free_rects: List[FreeRect] = [FreeRect(0.0, 0.0, width, height)]
        self.placements: List[Tuple[str, float, float, float, float]] = []  # panel_id, x, y, w, h

    def try_place(self, panel_id: str, w: float, h: float) -> Optional[Tuple[float, float]]:
        """Attempt to place a rectangle of size w×h in this sheet.
        Returns the (x,y) coordinate of the placement or None if it
        doesn’t fit.  Rotations are not considered here.
        """
        for idx, rect in enumerate(self.free_rects):
            if w <= rect.w and h <= rect.h:
                # Place at top‑left of the free rectangle
                x = rect.x
                y = rect.y
                # Update free spaces: split the remaining area into up to
                # two rectangles (to the right and below the placed part)
                new_rects: List[FreeRect] = []
                if rect.w - w > 0:
                    new_rects.append(FreeRect(x + w, y, rect.w - w, h))
                    new_rects.append(FreeRect(x + w, y + h, rect.w - w, rect.h - h))
                if rect.h - h > 0:
                    new_rects.append(FreeRect(x, y + h, w, rect.h - h))
                # Replace the used free rectangle with the new ones
                self.free_rects.pop(idx)
                # Add new free rects and remove contained ones
                self.free_rects.extend(new_rects)
                self.free_rects = self._prune_free_rects(self.free_rects)
                self.placements.append((panel_id, x, y, w, h))
                return (x, y)
        return None

    @staticmethod
    def _prune_free_rects(rects: List[FreeRect]) -> List[FreeRect]:
        """Remove rectangles contained in others to reduce fragmentation."""
        pruned: List[FreeRect] = []
        for i, r1 in enumerate(rects):
            contained = False
            for j, r2 in enumerate(rects):
                if i != j:
                    if (r1.x >= r2.x and r1.y >= r2.y and
                        r1.x + r1.w <= r2.x + r2.w and
                        r1.y + r1.h <= r2.y + r2.h):
                        contained = True
                        break
            if not contained:
                pruned.append(r1)
        return pruned


def nest_panels(panels: List[PanelSpec], stock_width: float, stock_height: float) -> List[NestingSheet]:
    """Nest a list of panels onto stock sheets.

    Panels are placed in the order given.  Each panel is represented by
    its flat width and height (including flanges).  The function returns
    a list of sheets with placement information.  Rotations are not
    currently considered.  If a panel does not fit on a fresh sheet
    then a ValueError is raised.

    Parameters
    ----------
    panels : list of PanelSpec
        Panels to nest.  The flat dimensions for each panel are computed
        internally.
    stock_width : float
        Width of the stock sheet (inches).
    stock_height : float
        Height of the stock sheet (inches).

    Returns
    -------
    list of NestingSheet
    """
    sheets: List[NestingSheet] = []
    for spec in panels:
        k_factor, radius_mult = lookup_material(spec.thickness)
        depths = spec.flange_depths()
        flat_w = calculate_flat_dimension(spec.face_width, depths, spec.thickness,
                                          k_factor, radius_mult)
        flat_h = calculate_flat_dimension(spec.face_height, depths, spec.thickness,
                                          k_factor, radius_mult)
        placed = False
        for sheet in sheets:
            if sheet.try_place(spec.panel_id, flat_w, flat_h) is not None:
                placed = True
                break
        if not placed:
            # Start a new sheet
            if flat_w > stock_width or flat_h > stock_height:
                # Use plain 'x' instead of the Unicode multiplication sign to avoid
                # interpreter errors in f‑strings.  The message conveys that the
                # panel is larger than the available stock sheet.
                raise ValueError(
                    f"Panel {spec.panel_id} ({flat_w:.2f}x{flat_h:.2f}) does not fit on stock sheet {stock_width}x{stock_height}"
                )
            sheet_name = f"Sheet_{len(sheets)+1}"
            new_sheet = NestingSheet(stock_width, stock_height, sheet_name)
            new_sheet.try_place(spec.panel_id, flat_w, flat_h)
            sheets.append(new_sheet)
    return sheets


def write_nesting_dxf(sheets: List[NestingSheet], panels: Dict[str, PanelSpec], output_dir: str) -> None:
    """Write a combined DXF file showing all nested panels on stock sheets.

    Each sheet becomes a separate block inside the DXF.  The blocks are
    arranged vertically for convenience.  Perimeters of panels are drawn
    on the 'cut' layer; holes are not drawn in the combined nest.

    Parameters
    ----------
    sheets : list of NestingSheet
        Nested sheets to export.
    panels : dict mapping panel_id to PanelSpec
        Lookup for panel properties to compute flat dimensions.
    output_dir : str
        Directory where the DXF file will be written.
    """
    dxf_lines: List[str] = []
    dxf_lines.extend([
        "0", "SECTION", "2", "HEADER",
        "9", "$INSUNITS", "70", "1",  # inches
        "0", "ENDSEC",
        "0", "SECTION", "2", "TABLES",
        "0", "TABLE", "2", "LAYER", "70", "1",
        "0", "LAYER", "2", "cut", "70", "0", "62", "2", "6", "CONTINUOUS",
        "0", "ENDTAB", "0", "ENDSEC",
        "0", "SECTION", "2", "ENTITIES",
    ])
    # Place each sheet at a vertical offset so they do not overlap
    y_offset = 0.0
    for sheet_idx, sheet in enumerate(sheets):
        # Draw sheet boundary
        sx, sy = 0.0, y_offset
        sw, sh = sheet.width, sheet.height
        dxf_lines.extend([
            "0", "LWPOLYLINE", "8", "cut", "90", "4", "70", "1",
            "10", f"{sx:.5f}", "20", f"{sy:.5f}",
            "10", f"{sx + sw:.5f}", "20", f"{sy:.5f}",
            "10", f"{sx + sw:.5f}", "20", f"{sy + sh:.5f}",
            "10", f"{sx:.5f}", "20", f"{sy + sh:.5f}",
        ])
        # Draw each panel perimeter within this sheet
        for (pid, px, py, pw, ph) in sheet.placements:
            # Panel bottom left in global coordinates
            gx = px + sx
            gy = py + sy
            dxf_lines.extend([
                "0", "LWPOLYLINE", "8", "cut", "90", "4", "70", "1",
                "10", f"{gx:.5f}", "20", f"{gy:.5f}",
                "10", f"{gx + pw:.5f}", "20", f"{gy:.5f}",
                "10", f"{gx + pw:.5f}", "20", f"{gy + ph:.5f}",
                "10", f"{gx:.5f}", "20", f"{gy + ph:.5f}",
            ])
        y_offset += sheet.height + 10.0  # add spacing between sheets
    dxf_lines.extend([
        "0", "ENDSEC", "0", "EOF",
    ])
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir)
    filename = "nesting_layout.dxf"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        f.write("\n".join(dxf_lines))
    print(f"Wrote {filepath}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Generate perforated panel DXF files and nest them.")
    parser.add_argument("csv", help="Input CSV file with panel definitions")
    parser.add_argument("output", help="Output directory for DXF files")
    parser.add_argument("--stock_width", type=float, default=48.0, help="Width of stock sheet (inches)")
    parser.add_argument("--stock_height", type=float, default=96.0, help="Height of stock sheet (inches)")
    parser.add_argument("--nest", action="store_true", help="Whether to produce a combined nesting layout")
    args = parser.parse_args()
    # Parse panels
    panels = parse_csv(args.csv)
    if not panels:
        print("No valid panels to process.")
        return
    # Generate individual DXF files
    for spec in panels:
        generate_panel_dxf(spec, args.output)
    # Nesting (optional)
    if args.nest:
        try:
            sheets = nest_panels(panels, args.stock_width, args.stock_height)
            panel_dict = {spec.panel_id: spec for spec in panels}
            write_nesting_dxf(sheets, panel_dict, args.output)
        except ValueError as e:
            print(f"Nesting error: {e}")


if __name__ == "__main__":
    main()