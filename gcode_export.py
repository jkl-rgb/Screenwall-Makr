"""G-code (.nc) exporter for Screenwall Makr panels.

Converts the same in-memory drawing that drives the DXF output
(`screenwall_generator.build_panel_document`) into RS-274 style G-code for a
2D cutting machine (laser/plasma/waterjet head, or a router/mill spindle).

Design notes
------------
* Geometry source of truth stays the generator; this module only walks the
  ezdxf modelspace (LWPOLYLINE / LINE / CIRCLE) and emits moves, so any
  geometry change in the generator flows into NC automatically.
* Operation order follows shop CAM practice: engrave marks first, then inner
  features (perforations, install slots), the blank perimeter cut last.
* Every path is prefixed with a structured comment:
      (op=cut layer=holes shape=circle)
  These annotations make the NC files self-describing and are the anchor for
  the planned inverse feature (rebuilding geometry actions from G-code).
* Arcs are emitted as G2/G3 with incremental I/J center offsets. Full circles
  are split into two half arcs for controller compatibility.
* The `finished_face` and `bend` layers are reference geometry; they are
  skipped by default (`bend` can be etched via `include_bend_marks`).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Optional

from screenwall_generator import APP_VERSION, build_panel_document

NC_EXTENSION = ".nc"

# Layer → operation mapping. Order inside each list is the machining order.
MARK_LAYERS = ["text"]           # engraved panel ID
OPTIONAL_MARK_LAYERS = ["bend"]  # reference bend CLs, etched only on request
CUT_LAYERS = ["holes", "fastening", "cut"]  # inner features first, perimeter last
SKIP_LAYERS = ["finished_face"]  # nominal face reference, never machined


@dataclass
class GCodeConfig:
    """Machine dialect + process settings. Units are inches / inches-per-minute."""
    mode: str = "laser"                 # "laser" (M3/M5 head) or "mill" (Z plunge)
    feed_cut: float = 60.0
    feed_mark: float = 120.0
    # Laser mode
    laser_power_cut: int = 1000         # S word while cutting
    laser_power_mark: int = 300         # S word while marking/etching
    # Mill mode
    spindle_rpm: int = 12000
    safe_z: float = 0.5
    cut_z: Optional[float] = None       # None → -(thickness + 0.02") through-cut
    mark_z: float = -0.010
    feed_plunge: float = 20.0
    # Content toggles
    include_text_marks: bool = True
    include_bend_marks: bool = False
    # Formatting
    decimals: int = 4
    program_end: str = "M30"


# ---------------------------------------------------------------------------
# ezdxf modelspace → path model
# Path = {"layer": str, "closed": bool, "shape": str, "segments": [segment]}
# segment = ("line", (x0,y0), (x1,y1))
#         | ("arc", (x0,y0), (x1,y1), (cx,cy), ccw: bool)
# ---------------------------------------------------------------------------

def _bulge_to_arc(p0, p1, bulge):
    """DXF bulge (tan of quarter included angle) → arc center + direction."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-12:
        return None
    sagitta = abs(bulge) * chord / 2.0
    radius = ((chord / 2.0) ** 2 + sagitta ** 2) / (2.0 * sagitta)
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    # Left unit normal of the chord; center sits at (radius - sagitta) along it
    # for CCW (positive bulge) arcs, mirrored for CW. Sign flips automatically
    # past 180 degrees where sagitta exceeds the radius.
    nx, ny = -dy / chord, dx / chord
    d = radius - sagitta
    if bulge < 0:
        d = -d
    return (mx + nx * d, my + ny * d), bulge > 0


def _lwpolyline_segments(entity):
    pts = [(float(p[0]), float(p[1]), float(p[2])) for p in entity.get_points("xyb")]
    closed = bool(entity.closed)
    segs = []
    n = len(pts)
    last = n if closed else n - 1
    for i in range(last):
        x0, y0, b = pts[i]
        x1, y1, _ = pts[(i + 1) % n]
        p0, p1 = (x0, y0), (x1, y1)
        if abs(b) > 1e-12:
            arc = _bulge_to_arc(p0, p1, b)
            if arc is not None:
                center, ccw = arc
                segs.append(("arc", p0, p1, center, ccw))
                continue
        if math.hypot(x1 - x0, y1 - y0) > 1e-12:
            segs.append(("line", p0, p1))
    return segs, closed


def _circle_segments(entity):
    cx, cy = float(entity.dxf.center[0]), float(entity.dxf.center[1])
    r = float(entity.dxf.radius)
    east, west = (cx + r, cy), (cx - r, cy)
    return [
        ("arc", east, west, (cx, cy), True),
        ("arc", west, east, (cx, cy), True),
    ]


def extract_paths(doc):
    """Walk modelspace and return machinable paths grouped by layer."""
    paths = []
    for e in doc.modelspace():
        layer = e.dxf.layer
        kind = e.dxftype()
        if kind == "LWPOLYLINE":
            segs, closed = _lwpolyline_segments(e)
            if segs:
                has_arc = any(s[0] == "arc" for s in segs)
                shape = "slot" if (closed and has_arc) else ("loop" if closed else "polyline")
                paths.append({"layer": layer, "closed": closed, "shape": shape, "segments": segs})
        elif kind == "LINE":
            p0 = (float(e.dxf.start[0]), float(e.dxf.start[1]))
            p1 = (float(e.dxf.end[0]), float(e.dxf.end[1]))
            if math.hypot(p1[0] - p0[0], p1[1] - p0[1]) > 1e-12:
                paths.append({"layer": layer, "closed": False, "shape": "line",
                              "segments": [("line", p0, p1)]})
        elif kind == "CIRCLE":
            paths.append({"layer": layer, "closed": True, "shape": "circle",
                          "segments": _circle_segments(e)})
    return paths


# ---------------------------------------------------------------------------
# Path model → G-code
# ---------------------------------------------------------------------------

class _Emitter:
    def __init__(self, cfg: GCodeConfig, thickness: float):
        self.cfg = cfg
        self.thickness = thickness
        self.lines: list[str] = []

    def n(self, v: float) -> str:
        s = f"{v:.{self.cfg.decimals}f}".rstrip("0").rstrip(".")
        return s if s not in ("", "-0") else "0"

    def raw(self, line: str):
        self.lines.append(line)

    def comment(self, text: str):
        self.lines.append(f"({text})")

    @property
    def cut_z(self) -> float:
        if self.cfg.cut_z is not None:
            return self.cfg.cut_z
        return -(self.thickness + 0.02)

    def header(self, panel_id: str):
        c = self.cfg
        self.raw("%")
        self.comment(f"Screenwall Makr v{APP_VERSION} G-code export")
        self.comment(f"panel={panel_id} units=inch mode={c.mode}")
        self.raw("G20 G90 G17")
        if c.mode == "mill":
            self.raw(f"G0 Z{self.n(c.safe_z)}")
            self.raw(f"M3 S{c.spindle_rpm}")

    def footer(self):
        c = self.cfg
        if c.mode == "mill":
            self.raw(f"G0 Z{self.n(c.safe_z)}")
        self.raw("M5")
        self.raw(c.program_end)
        self.raw("%")

    def path(self, path: dict, op: str):
        """Emit one continuous path: rapid to start, engage, follow, disengage."""
        c = self.cfg
        segs = path["segments"]
        start = segs[0][1]
        feed = c.feed_cut if op == "cut" else c.feed_mark
        self.comment(f"op={op} layer={path['layer']} shape={path['shape']}")
        self.raw(f"G0 X{self.n(start[0])} Y{self.n(start[1])}")
        if c.mode == "laser":
            power = c.laser_power_cut if op == "cut" else c.laser_power_mark
            self.raw(f"M3 S{power}")
        else:
            z = self.cut_z if op == "cut" else c.mark_z
            self.raw(f"G1 Z{self.n(z)} F{self.n(c.feed_plunge)}")
        first = True
        for seg in segs:
            f_word = f" F{self.n(feed)}" if first else ""
            first = False
            if seg[0] == "line":
                _, _, p1 = seg
                self.raw(f"G1 X{self.n(p1[0])} Y{self.n(p1[1])}{f_word}")
            else:
                _, p0, p1, center, ccw = seg
                code = "G3" if ccw else "G2"
                i, j = center[0] - p0[0], center[1] - p0[1]
                self.raw(
                    f"{code} X{self.n(p1[0])} Y{self.n(p1[1])} "
                    f"I{self.n(i)} J{self.n(j)}{f_word}"
                )
        if c.mode == "laser":
            self.raw("M5")
        else:
            self.raw(f"G0 Z{self.n(c.safe_z)}")


def _ordered_ops(cfg: GCodeConfig):
    ops: list[tuple[str, str]] = []  # (layer, op)
    if cfg.include_text_marks:
        ops.extend((lay, "mark") for lay in MARK_LAYERS)
    if cfg.include_bend_marks:
        ops.extend((lay, "mark") for lay in OPTIONAL_MARK_LAYERS)
    ops.extend((lay, "cut") for lay in CUT_LAYERS)
    return ops


def _emit_program(paths: list[dict], panel_id: str, thickness: float,
                  cfg: GCodeConfig, layer_ops: list[tuple[str, str]]) -> str:
    by_layer: dict[str, list[dict]] = {}
    for p in paths:
        by_layer.setdefault(p["layer"], []).append(p)
    em = _Emitter(cfg, thickness)
    em.header(panel_id)
    for layer, op in layer_ops:
        for p in by_layer.get(layer, []):
            em.path(p, op)
    em.footer()
    return "\n".join(em.lines) + "\n"


def doc_to_gcode(doc, panel_id: str, thickness: float, config: Optional[GCodeConfig] = None) -> str:
    """Convert a built panel document (ezdxf) into a G-code program string."""
    cfg = config or GCodeConfig()
    return _emit_program(extract_paths(doc), panel_id, thickness, cfg, _ordered_ops(cfg))


# ---------------------------------------------------------------------------
# Turret punch + laser combo (two machines, two programs per panel)
#
# Punch program: perforations and install slots as single hits — round (RD)
# tool per hole diameter, obround (OB) tool per slot width x length x angle.
# T numbers are assigned in order of first use; the tool-table comment block
# at the top is the operator's remap sheet for actual turret stations.
# Laser program: panel-ID etch + blank perimeter (+ optional bend etch).
# Any holes/fastening path a punch tool cannot represent falls back to the
# laser program as a contour cut instead of being dropped.
# ---------------------------------------------------------------------------
PUNCH_LAYERS = ["holes", "fastening"]


def _circle_params(path):
    """(center, dia) for a two-half-arc circle path."""
    arcs = [s for s in path["segments"] if s[0] == "arc"]
    if len(arcs) != 2:
        return None
    c = arcs[0][3]
    if math.hypot(arcs[1][3][0] - c[0], arcs[1][3][1] - c[1]) > 1e-9:
        return None
    r = math.hypot(arcs[0][1][0] - c[0], arcs[0][1][1] - c[1])
    return c, 2.0 * r


def _slot_params(path):
    """(center, width, length, angle_deg) for a 2-line + 2-semicircle slot."""
    arcs = [s for s in path["segments"] if s[0] == "arc"]
    lines = [s for s in path["segments"] if s[0] == "line"]
    if len(arcs) != 2 or len(lines) != 2:
        return None
    c0, c1 = arcs[0][3], arcs[1][3]
    r0 = math.hypot(arcs[0][1][0] - c0[0], arcs[0][1][1] - c0[1])
    r1 = math.hypot(arcs[1][1][0] - c1[0], arcs[1][1][1] - c1[1])
    if abs(r0 - r1) > 1e-6:
        return None
    width = 2.0 * r0
    dx, dy = c1[0] - c0[0], c1[1] - c0[1]
    length = math.hypot(dx, dy) + width
    center = ((c0[0] + c1[0]) / 2.0, (c0[1] + c1[1]) / 2.0)
    angle = math.degrees(math.atan2(dy, dx)) % 180.0
    return center, width, length, angle


def _extract_punch_tools(paths):
    """Split punchable hits from paths. Returns (tools, leftover_paths).

    tools: list of {"key", "desc", "hits": [(x, y)]} in first-use order.
    leftover_paths: holes/fastening paths no punch tool can represent.
    """
    tools: dict[tuple, dict] = {}
    leftovers = []
    for p in paths:
        if p["layer"] not in PUNCH_LAYERS:
            continue
        if p["shape"] == "circle":
            params = _circle_params(p)
            if params is None:
                leftovers.append(p)
                continue
            center, dia = params
            key = ("RD", round(dia, 4))
            desc = f"RD {dia:.4f}"
        elif p["shape"] == "slot":
            params = _slot_params(p)
            if params is None:
                leftovers.append(p)
                continue
            center, width, length, angle = params
            key = ("OB", round(width, 4), round(length, 4), round(angle, 1))
            desc = f"OB {width:.4f} x {length:.4f} @ {angle:.1f} deg"
            if angle not in (0.0, 90.0):
                desc += " (auto-index station required)"
        else:
            leftovers.append(p)
            continue
        tool = tools.setdefault(key, {"key": key, "desc": desc, "hits": []})
        tool["hits"].append(center)
    return list(tools.values()), leftovers


def _serpentine(hits):
    """Row-major hit order with alternating x direction to shorten travel."""
    rows: dict[float, list] = {}
    for x, y in hits:
        rows.setdefault(round(y, 3), []).append((x, y))
    ordered = []
    for i, y in enumerate(sorted(rows)):
        ordered.extend(sorted(rows[y], key=lambda h: h[0], reverse=bool(i % 2)))
    return ordered


def punch_program(paths, panel_id: str, config: Optional[GCodeConfig] = None) -> str:
    """Fanuc/Amada-style single-hit turret punch program for punchable paths."""
    cfg = config or GCodeConfig()
    em = _Emitter(cfg, 0.0)
    tools, _ = _extract_punch_tools(paths)

    em.raw("%")
    em.comment(f"Screenwall Makr v{APP_VERSION} turret punch program")
    em.comment(f"panel={panel_id} units=inch machine=turret-punch")
    em.comment("tool table - remap T numbers to your turret stations:")
    for idx, tool in enumerate(tools, start=1):
        em.comment(f"T{idx:02d} = {tool['desc']} ({len(tool['hits'])} hits)")
    em.comment("datum: blank lower-left at X0 Y0, inside face up (same as DXF)")
    em.raw("G20 G90")
    for idx, tool in enumerate(tools, start=1):
        em.comment(f"op=punch tool=T{idx:02d} {tool['desc']}")
        first = True
        for x, y in _serpentine(tool["hits"]):
            t_word = f" T{idx:02d}" if first else ""
            first = False
            em.raw(f"X{em.n(x)} Y{em.n(y)}{t_word}")
    em.raw(cfg.program_end)
    em.raw("%")
    return "\n".join(em.lines) + "\n"


def _combo_laser_ops(cfg: GCodeConfig) -> list[tuple[str, str]]:
    ops: list[tuple[str, str]] = []
    if cfg.include_text_marks:
        ops.extend((lay, "mark") for lay in MARK_LAYERS)
    if cfg.include_bend_marks:
        ops.extend((lay, "mark") for lay in OPTIONAL_MARK_LAYERS)
    # Unpunchable holes/fastening leftovers cut before the perimeter.
    ops.extend((lay, "cut") for lay in PUNCH_LAYERS)
    ops.append(("cut", "cut"))
    return ops


def doc_to_gcode_combo(doc, panel_id: str, thickness: float,
                       config: Optional[GCodeConfig] = None) -> dict:
    """Two programs for a punch + laser cell: {"punch": …, "laser": …}.

    Perforations and install slots go to the punch; the laser program keeps
    only marks, the perimeter, and any feature the punch cannot represent.
    """
    cfg = config or GCodeConfig()
    paths = extract_paths(doc)
    _, leftovers = _extract_punch_tools(paths)
    laser_paths = [p for p in paths if p["layer"] not in PUNCH_LAYERS] + leftovers
    return {
        "punch": punch_program(paths, panel_id, cfg),
        "laser": _emit_program(laser_paths, panel_id, thickness, cfg, _combo_laser_ops(cfg)),
    }


def panel_gcode_combo(spec, config: Optional[GCodeConfig] = None) -> dict:
    doc = build_panel_document(spec)
    return doc_to_gcode_combo(doc, spec.panel_id, spec.thickness, config)


def generate_panel_gcode_combo(spec, outdir: str, config: Optional[GCodeConfig] = None) -> dict:
    """Write `{panel_id}_punch.nc` and `{panel_id}_laser.nc`. Returns paths."""
    programs = panel_gcode_combo(spec, config)
    os.makedirs(outdir, exist_ok=True)
    out = {}
    for machine, program in programs.items():
        path = os.path.join(outdir, f"{spec.panel_id}_{machine}{NC_EXTENSION}")
        with open(path, "w", encoding="ascii") as f:
            f.write(program)
        out[machine] = path
    return out


def panel_gcode(spec, config: Optional[GCodeConfig] = None) -> str:
    """Build the panel drawing and return its G-code program."""
    doc = build_panel_document(spec)
    return doc_to_gcode(doc, spec.panel_id, spec.thickness, config)


def generate_panel_gcode(spec, outdir: str, config: Optional[GCodeConfig] = None) -> str:
    """Write `{panel_id}.nc` next to the DXFs. Returns the file path."""
    program = panel_gcode(spec, config)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{spec.panel_id}{NC_EXTENSION}")
    with open(path, "w", encoding="ascii") as f:
        f.write(program)
    return path
