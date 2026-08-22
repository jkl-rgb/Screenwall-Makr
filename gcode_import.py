"""Build Screenwall artwork from G-code (.nc) programs.

Tier 1 of GCODE_IMPORT_PLAN.md: parse RS-274 motion into the same path
model the exporter uses ({"layer","closed","shape","segments"}), then
rebuild a layered ezdxf document that re-enters the normal pipeline
(DXF download, PDF preview, re-export).

Layer/action assignment, in priority order:
1. SCREENWALL-NC annotations — `(op=… layer=… shape=…)` comments emitted
   by gcode_export make our own files round-trip losslessly.
2. Shape heuristics for foreign files — circles -> holes, rounded slots ->
   fastening, closed loops -> cut, long single lines -> bend, short open
   strokes -> text.

Three dialects are recognized automatically:
- laser  (M3/M5 engagement, no Z motion)
- mill   (engaged while Z < 0)
- turret punch: Screenwall combo exports (RD/OB tool-table comments) AND
  Amada-style tapes — '%' markers, bare X/Y[T] hit blocks, G92 preset,
  G70/G72 no-punch positioning, pattern macros G36 (grid) / G28 (line) /
  G26 (bolt circle), tool sizes recovered from the SET-UP SHEET appended
  after the closing '%'.

Supported motion: G0/G1/G2/G3 (I/J or R arcs), G20/G21, G90/G91, G17.
Rejected with errors/warnings: G18/G19, cutter comp (G41/G42), canned
cycles. Units normalize to inches.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import ezdxf

_WORD_RE = re.compile(r"([A-Za-z])\s*([+-]?(?:\d+\.?\d*|\.\d+))")
_ANNOT_RE = re.compile(r"op=(\w+)\s+layer=(\w+)\s+shape=(\w+)")
_PANEL_RE = re.compile(r"panel=(\S+)")
_PUNCH_RD_RE = re.compile(r"T(\d+)\s*=\s*RD\s+([\d.]+)")
_PUNCH_OB_RE = re.compile(r"T(\d+)\s*=\s*OB\s+([\d.]+)\s*x\s*([\d.]+)\s*@\s*([\d.]+)\s*deg")

_UNSUPPORTED_G = {18: "G18 (XZ plane)", 19: "G19 (YZ plane)",
                  41: "G41 (cutter comp)", 42: "G42 (cutter comp)",
                  81: "G81 (canned cycle)", 83: "G83 (canned cycle)"}

_EPS = 1e-6


@dataclass
class ImportResult:
    paths: list = field(default_factory=list)
    panel_id: str | None = None
    dialect: str = "laser"
    warnings: list = field(default_factory=list)
    annotated: bool = False


def _strip_comments(line: str) -> tuple[str, list[str]]:
    comments = []
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == "(":
            j = line.find(")", i + 1)
            if j < 0:
                comments.append(line[i + 1:].strip())
                i = len(line)
            else:
                comments.append(line[i + 1:j].strip())
                i = j + 1
        elif ch == ";":
            comments.append(line[i + 1:].strip())
            i = len(line)
        else:
            out.append(ch)
            i += 1
    return "".join(out), comments


def _arc_center_from_r(p0, p1, r_word: float, ccw: bool):
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    d = math.hypot(dx, dy)
    r = abs(r_word)
    if d < _EPS or d > 2.0 * r + 1e-9:
        return None
    h = math.sqrt(max(r * r - (d / 2.0) ** 2, 0.0))
    mx, my = (p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0
    nx, ny = -dy / d, dx / d  # left normal of chord direction
    # Minor arc (<=180 deg): CCW center left of the chord, CW center right.
    # Negative R selects the major arc (opposite side).
    sign = 1.0 if ccw else -1.0
    if r_word < 0:
        sign = -sign
    return (mx + sign * h * nx, my + sign * h * ny)


def _arc_sweep(p0, p1, center, ccw) -> float:
    a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
    a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
    if ccw:
        s = (a1 - a0) % (2.0 * math.pi)
        return s if s > _EPS else 2.0 * math.pi
    s = (a0 - a1) % (2.0 * math.pi)
    return -(s if s > _EPS else 2.0 * math.pi)


def _is_punch_program(text: str) -> bool:
    if "turret punch program" in text:
        return True
    has_hits = re.search(r"^\s*X[-\d.]+\s*Y[-\d.]+\s*(T\d+)?\s*$", text, re.M)
    has_pattern = re.search(r"^\s*(?:X[-\d.]+\s*Y[-\d.]+\s*)?G(?:26|28|36|66)\b", text, re.M)
    has_contour = re.search(r"^\s*G0?[123]\b", text, re.M)
    # (?<![A-Za-z]) instead of \b: tool words attach directly to coordinates
    # (Y1.0T7 has no word boundary before the T).
    return bool((has_hits or has_pattern) and not has_contour
                and re.search(r"(?<![A-Za-z])T\d+", text))


def _split_tape(text: str) -> tuple[str, str]:
    """(program, trailer) split on RS-274 tape '%' markers.

    Amada-style posts append a human-readable SET-UP SHEET after the closing
    '%'; it is not G-code but carries the tool list (station -> type/size)."""
    lines = text.splitlines()
    marks = [i for i, ln in enumerate(lines) if ln.strip() == "%"]
    if len(marks) >= 2:
        return ("\n".join(lines[marks[0] + 1:marks[1]]),
                "\n".join(lines[marks[1] + 1:]))
    if len(marks) == 1:
        return ("\n".join(lines[marks[0] + 1:]), "")
    return (text, "")


_DIMS_RE = re.compile(r"^([\d.]+)\s*[xX]\s*([\d.]+)$")


def _tools_from_setup_sheet(trailer: str) -> dict:
    """Recover tool geometry from a set-up sheet TOOL LIST.

    Rows look like ``T139  ROUND  [angle]  0.750  170`` (station, type,
    optional angle, size, hit count). A trailing integer >= 1 is treated as
    the hit count and dropped; the last remaining number is the size."""
    tools: dict[str, tuple] = {}
    for ln in trailer.splitlines():
        toks = ln.split()
        if len(toks) < 3 or not re.fullmatch(r"T\d+", toks[0]):
            continue
        key = f"T{int(toks[0][1:]):02d}"
        ttype = toks[1].upper()
        nums: list[float] = []
        dims = None
        for t in toks[2:]:
            dm = _DIMS_RE.match(t)
            if dm:
                dims = (float(dm.group(1)), float(dm.group(2)))
                continue
            try:
                nums.append(float(t))
            except ValueError:
                pass
        if len(nums) >= 2 and nums[-1] >= 1 and abs(nums[-1] - round(nums[-1])) < 1e-9:
            nums = nums[:-1]  # hit count column
        if ttype in ("ROUND", "RD", "RO", "CIRCLE"):
            if nums and nums[-1] > 0:
                tools[key] = ("RD", nums[-1])
        elif ttype in ("OBROUND", "OBR", "OB", "OVAL", "SLOT"):
            if dims:
                ang = nums[0] if nums else 0.0
                tools[key] = ("OB", min(dims), max(dims), ang)
        elif ttype in ("RECT", "RECTANGLE", "SQ", "SQUARE"):
            if dims:
                ang = nums[0] if nums else 0.0
                tools[key] = ("RECT", min(dims), max(dims), ang)
            elif nums and nums[-1] > 0:
                tools[key] = ("RECT", nums[-1], nums[-1], 0.0)
    return tools


# ---------------------------------------------------------------------------
# Turret punch dialect: tool table + single hits -> circles / slots
# ---------------------------------------------------------------------------

def _slot_path_from_hit(cx, cy, width, length, angle_deg):
    """Rebuild the slot path (2 lines + 2 CW semicircles) around a punch hit."""
    r = width / 2.0
    a = (length - width) / 2.0
    th = math.radians(angle_deg)
    tx, ty = math.cos(th), math.sin(th)

    def P(px, py):
        return (cx + px * tx - py * ty, cy + px * ty + py * tx)

    A, B, C, D = P(-a, r), P(a, r), P(a, -r), P(-a, -r)
    c_right, c_left = P(a, 0.0), P(-a, 0.0)
    return {
        "layer": "fastening", "closed": True, "shape": "slot",
        "segments": [
            ("line", A, B),
            ("arc", B, C, c_right, False),
            ("line", C, D),
            ("arc", D, A, c_left, False),
        ],
    }


def _circle_path(cx, cy, dia):
    r = dia / 2.0
    east, west = (cx + r, cy), (cx - r, cy)
    return {
        "layer": "holes", "closed": True, "shape": "circle",
        "segments": [("arc", east, west, (cx, cy), True),
                     ("arc", west, east, (cx, cy), True)],
    }


def _rect_path_from_hit(cx, cy, width, length, angle_deg):
    """Rectangle/square punch hit (4 lines, closed) on the holes layer."""
    a, b = length / 2.0, width / 2.0
    th = math.radians(angle_deg)
    tx, ty = math.cos(th), math.sin(th)

    def P(px, py):
        return (cx + px * tx - py * ty, cy + px * ty + py * tx)

    A, B, C, D = P(-a, b), P(a, b), P(a, -b), P(-a, -b)
    return {
        "layer": "holes", "closed": True, "shape": "loop",
        "segments": [("line", A, B), ("line", B, C),
                     ("line", C, D), ("line", D, A)],
    }


# G codes that suppress the hit on their own block:
#   G26/G28/G36 pattern macros (the X/Y positions the pattern origin),
#   G70/G72 position-without-punch, G92 coordinate preset, G50 home/end.
_PUNCH_NO_HIT_G = {26, 28, 36, 50, 70, 72, 92}
# Handled or harmless-by-design; everything else earns one "ignored" warning.
_PUNCH_KNOWN_G = _PUNCH_NO_HIT_G | {4, 5, 6, 8, 9, 17, 20, 21, 25, 27,
                                    90, 91, 93, 98}
_PUNCH_SILENT_M = {0, 1, 2, 6, 30}


def _parse_punch(text: str, result: ImportResult) -> ImportResult:
    """Turret punch dialect (Screenwall combo exports and Amada-style tapes).

    Hits are bare X/Y blocks (T word optional, attached or space-separated;
    X or Y alone is modal). Pattern macros expand to hits:
      G36 I<xp> P<nx> J<yp> K<ny>  grid from the last position (origin is
                                   skipped when it was already punched)
      G28 I<pitch> J<angle> K<n>   line of n extra hits from the last position
      G26 I<r> J<start> K<n>       bolt-hole circle centered on the current
                                   position (center is never punched)
    Tool geometry comes from Screenwall tool-table comments or, for foreign
    tapes, the SET-UP SHEET tool list after the closing '%'."""
    result.dialect = "punch"
    program, trailer = _split_tape(text)
    tools: dict[str, tuple] = {}
    for m in _PUNCH_RD_RE.finditer(text):
        tools[f"T{int(m.group(1)):02d}"] = ("RD", float(m.group(2)))
    for m in _PUNCH_OB_RE.finditer(text):
        tools[f"T{int(m.group(1)):02d}"] = (
            "OB", float(m.group(2)), float(m.group(3)), float(m.group(4)))
    result.annotated = bool(tools)  # Screenwall comment table = lossless
    for key, tool in _tools_from_setup_sheet(trailer).items():
        tools.setdefault(key, tool)

    scale = 1.0 / 25.4 if re.search(r"\bG21\b", program) else 1.0
    x = y = 0.0
    absolute = True
    current: str | None = None
    last_hit: tuple | None = None
    unknown_tools: set = set()
    ignored: set = set()
    first_comment: str | None = None

    def emit_hit(hx, hy):
        nonlocal last_hit
        tool = tools.get(current)
        if current is None:
            result.warnings.append(
                f"hit at X{hx} Y{hy} before any known tool select; skipped")
            return
        if tool is None:
            if current not in unknown_tools:
                unknown_tools.add(current)
                result.warnings.append(
                    f"tool {current} has no geometry in the program or "
                    f"set-up sheet; drawn as a 0.25\" round")
            tool = ("RD", 0.25 / scale)
        if tool[0] == "RD":
            result.paths.append(_circle_path(hx, hy, tool[1] * scale))
        elif tool[0] == "OB":
            _, w, l, ang = tool
            result.paths.append(_slot_path_from_hit(hx, hy, w * scale, l * scale, ang))
        else:  # RECT
            _, w, l, ang = tool
            result.paths.append(_rect_path_from_hit(hx, hy, w * scale, l * scale, ang))
        last_hit = (hx, hy)

    for raw in program.splitlines():
        code, comments = _strip_comments(raw)
        for cm in comments:
            pm = _PANEL_RE.search(cm)
            if pm:
                result.panel_id = pm.group(1)
            elif first_comment is None and cm:
                first_comment = cm
        words = _WORD_RE.findall(code)
        if not words:
            continue
        gs: list[int] = []
        vals: dict[str, float] = {}
        for letter, num in words:
            L = letter.upper()
            if L == "G":
                gs.append(int(round(float(num))))
            elif L == "M":
                mi = int(round(float(num)))
                if mi not in _PUNCH_SILENT_M:
                    ignored.add(f"M{mi}")
            else:
                vals[L] = float(num)
        for gi in gs:
            if gi == 90:
                absolute = True
            elif gi == 91:
                absolute = False
            elif gi == 20:
                scale = 1.0
            elif gi == 21:
                scale = 1.0 / 25.4
            elif gi not in _PUNCH_KNOWN_G:
                ignored.add(f"G{gi}")
        if "T" in vals:
            current = f"T{int(vals['T']):02d}"
        if "X" in vals:
            x = vals["X"] * scale if absolute else x + vals["X"] * scale
        if "Y" in vals:
            y = vals["Y"] * scale if absolute else y + vals["Y"] * scale

        if ("X" in vals or "Y" in vals) and not any(g in _PUNCH_NO_HIT_G for g in gs):
            emit_hit(x, y)

        pattern = next((gi for gi in gs if gi in (26, 28, 36)), None)
        if pattern == 36:
            ip = vals.get("I", 0.0) * scale
            jp = vals.get("J", 0.0) * scale
            p = max(int(round(vals.get("P", 0.0))), 0)
            k = max(int(round(vals.get("K", 0.0))), 0)
            bx, by = x, y
            origin_punched = (last_hit is not None
                              and math.hypot(last_hit[0] - bx, last_hit[1] - by) < _EPS)
            for jj in range(k + 1):
                for ii in range(p + 1):
                    if ii == 0 and jj == 0 and origin_punched:
                        continue
                    emit_hit(bx + ii * ip, by + jj * jp)
            x, y = bx + p * ip, by + k * jp
        elif pattern == 28:
            pitch = vals.get("I", 0.0) * scale
            ang = math.radians(vals.get("J", 0.0))
            k = max(int(round(vals.get("K", 0.0))), 0)
            bx, by = x, y
            for i in range(1, k + 1):
                emit_hit(bx + i * pitch * math.cos(ang),
                         by + i * pitch * math.sin(ang))
            x, y = bx + k * pitch * math.cos(ang), by + k * pitch * math.sin(ang)
        elif pattern == 26:
            r = vals.get("I", 0.0) * scale
            a0 = math.radians(vals.get("J", 0.0))
            k = max(int(round(vals.get("K", 0.0))), 0)
            cx0, cy0 = x, y
            for i in range(k):
                a = a0 + 2.0 * math.pi * i / max(k, 1)
                emit_hit(cx0 + r * math.cos(a), cy0 + r * math.sin(a))

    if result.panel_id is None and first_comment:
        result.panel_id = first_comment
    if ignored:
        result.warnings.append(
            "ignored non-geometry codes: " + ", ".join(sorted(ignored)))
    return result


# ---------------------------------------------------------------------------
# Contour dialects (laser / mill)
# ---------------------------------------------------------------------------

def parse_gcode(text: str) -> ImportResult:
    """Parse a G-code program into the shared path model."""
    result = ImportResult()
    if _is_punch_program(text):
        return _parse_punch(text, result)

    mill = bool(re.search(r"\bZ-?\d", text))
    result.dialect = "mill" if mill else "laser"

    x = y = z = 0.0
    scale = 1.0
    absolute = True
    motion = None
    laser_on = False
    pending_annot = None
    segments: list = []
    seg_annot = None

    def engaged() -> bool:
        return (z < -_EPS) if mill else laser_on

    def flush():
        nonlocal segments, seg_annot
        if segments:
            start = segments[0][1]
            end = segments[-1][2]
            closed = math.hypot(end[0] - start[0], end[1] - start[1]) < 2e-3
            path = {"layer": None, "closed": closed, "shape": None, "segments": segments}
            if seg_annot:
                path["layer"], path["shape"] = seg_annot[1], seg_annot[2]
                result.annotated = True
            result.paths.append(path)
        segments = []
        seg_annot = None

    for raw in text.splitlines():
        code, comments = _strip_comments(raw)
        for cm in comments:
            am = _ANNOT_RE.search(cm)
            if am:
                pending_annot = (am.group(1), am.group(2), am.group(3))
            pm = _PANEL_RE.search(cm)
            if pm:
                result.panel_id = pm.group(1)
        words = _WORD_RE.findall(code)
        if not words:
            continue
        vals = {}
        for letter, num in words:
            letter = letter.upper()
            v = float(num)
            if letter == "G":
                gi = int(round(v))
                if gi in (0, 1, 2, 3):
                    motion = gi
                elif gi == 20:
                    scale = 1.0
                elif gi == 21:
                    scale = 1.0 / 25.4
                elif gi == 90:
                    absolute = True
                elif gi == 91:
                    absolute = False
                elif gi == 17:
                    pass
                elif gi in _UNSUPPORTED_G:
                    result.warnings.append(f"unsupported {_UNSUPPORTED_G[gi]} ignored")
            elif letter == "M":
                mi = int(round(v))
                if mi == 3:
                    laser_on = True
                elif mi == 5:
                    if not mill:
                        flush()
                    laser_on = False
                elif mi in (2, 30):
                    flush()
            else:
                vals[letter] = v * scale if letter in ("X", "Y", "Z", "I", "J", "R") else v

        if not any(k in vals for k in ("X", "Y", "Z")):
            continue

        was_engaged = engaged()
        nx = (vals["X"] if absolute else x + vals["X"]) if "X" in vals else x
        ny = (vals["Y"] if absolute else y + vals["Y"]) if "Y" in vals else y
        nz = (vals["Z"] if absolute else z + vals["Z"]) if "Z" in vals else z

        xy_moved = abs(nx - x) > _EPS or abs(ny - y) > _EPS
        if xy_moved and was_engaged and motion in (1, 2, 3):
            if motion == 1:
                segments.append(("line", (x, y), (nx, ny)))
            else:
                ccw = motion == 3
                if "I" in vals or "J" in vals:
                    center = (x + vals.get("I", 0.0), y + vals.get("J", 0.0))
                elif "R" in vals:
                    center = _arc_center_from_r((x, y), (nx, ny), vals["R"], ccw)
                    if center is None:
                        result.warnings.append(
                            f"unsolvable R arc to X{nx:.4f} Y{ny:.4f}; replaced with line")
                        segments.append(("line", (x, y), (nx, ny)))
                        center = "handled"
                else:
                    result.warnings.append("arc without I/J or R; replaced with line")
                    segments.append(("line", (x, y), (nx, ny)))
                    center = "handled"
                if center != "handled":
                    segments.append(("arc", (x, y), (nx, ny), center, ccw))
            if seg_annot is None and pending_annot is not None:
                seg_annot = pending_annot
                pending_annot = None
        elif xy_moved and not was_engaged:
            # rapid between features: a pending annotation belongs to what follows
            pass

        x, y, z = nx, ny, nz
        if mill and was_engaged and not engaged():
            flush()
        if xy_moved and was_engaged and motion == 0:
            result.warnings.append("rapid move while engaged; path split")
            flush()

    flush()
    _classify(result)
    return result


# ---------------------------------------------------------------------------
# Shape recognition + heuristic layer classification
# ---------------------------------------------------------------------------

def _detect_circle(path):
    segs = path["segments"]
    if not path["closed"] or any(s[0] != "arc" for s in segs):
        return None
    c0 = segs[0][3]
    if any(math.hypot(s[3][0] - c0[0], s[3][1] - c0[1]) > 1e-4 for s in segs[1:]):
        return None
    r = math.hypot(segs[0][1][0] - c0[0], segs[0][1][1] - c0[1])
    return c0, 2.0 * r


def _detect_slot(path):
    segs = path["segments"]
    arcs = [s for s in segs if s[0] == "arc"]
    lines = [s for s in segs if s[0] == "line"]
    if not path["closed"] or len(arcs) != 2 or len(lines) != 2:
        return None
    r0 = math.hypot(arcs[0][1][0] - arcs[0][3][0], arcs[0][1][1] - arcs[0][3][1])
    r1 = math.hypot(arcs[1][1][0] - arcs[1][3][0], arcs[1][1][1] - arcs[1][3][1])
    if abs(r0 - r1) > 1e-4:
        return None
    return True


def _loop_area(path) -> float:
    """Approximate enclosed area from segment endpoints (classification only)."""
    pts = [s[1] for s in path["segments"]]
    a = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        a += x0 * y1 - x1 * y0
    return abs(a) / 2.0


def _path_span(path) -> float:
    xs, ys = [], []
    for s in path["segments"]:
        for p in (s[1], s[2]):
            xs.append(p[0])
            ys.append(p[1])
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _classify(result: ImportResult) -> None:
    """Fill in layer/shape. Annotated paths keep their layer; others get heuristics."""
    spans = [_path_span(p) for p in result.paths if p["segments"]]
    overall = max(spans) if spans else 0.0
    for p in result.paths:
        circle = _detect_circle(p)
        if circle:
            p["shape"] = p["shape"] or "circle"
        elif _detect_slot(p):
            p["shape"] = p["shape"] or "slot"
        elif p["closed"]:
            p["shape"] = p["shape"] or "loop"
        elif len(p["segments"]) == 1 and p["segments"][0][0] == "line":
            p["shape"] = p["shape"] or "line"
        else:
            p["shape"] = p["shape"] or "polyline"
        if p["layer"]:
            continue
        if p["shape"] == "circle":
            p["layer"] = "holes"
        elif p["shape"] == "slot":
            p["layer"] = "fastening"
        elif p["closed"]:
            p["layer"] = "cut"
        elif p["shape"] == "line" and overall and _path_span(p) > 0.25 * overall:
            p["layer"] = "bend"
        else:
            p["layer"] = "text"


# ---------------------------------------------------------------------------
# Path model -> ezdxf document (inverse of gcode_export.extract_paths)
# ---------------------------------------------------------------------------

def _bulge_for(seg) -> float:
    _, p0, p1, center, ccw = seg
    return math.tan(_arc_sweep(p0, p1, center, ccw) / 4.0)


def paths_to_document(paths):
    """Rebuild a layered ezdxf document from imported paths."""
    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    msp = doc.modelspace()
    for name, color in [
        ("cut", 1), ("holes", 2), ("fastening", 5),
        ("bend", 3), ("text", 6), ("finished_face", 8),
    ]:
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)

    for p in paths:
        layer = p["layer"] or "cut"
        circle = _detect_circle(p)
        if circle:
            (cx, cy), dia = circle
            msp.add_circle((cx, cy), dia / 2.0, dxfattribs={"layer": layer})
            continue
        segs = p["segments"]
        if len(segs) == 1 and segs[0][0] == "line":
            msp.add_line(segs[0][1], segs[0][2], dxfattribs={"layer": layer})
            continue
        verts = []
        for s in segs:
            bulge = _bulge_for(s) if s[0] == "arc" else 0.0
            verts.append((s[1][0], s[1][1], bulge))
        if not p["closed"]:
            verts.append((segs[-1][2][0], segs[-1][2][1], 0.0))
        msp.add_lwpolyline(verts, format="xyb", close=p["closed"],
                           dxfattribs={"layer": layer})
    return doc


def summarize(result: ImportResult) -> dict:
    """Small measurement report for the UI (Tier 2 seed)."""
    by_layer: dict[str, int] = {}
    hole_dias = set()
    xs, ys = [], []
    for p in result.paths:
        by_layer[p["layer"]] = by_layer.get(p["layer"], 0) + 1
        c = _detect_circle(p)
        if c:
            hole_dias.add(round(c[1], 4))
        for s in p["segments"]:
            for pt in (s[1], s[2]):
                xs.append(pt[0])
                ys.append(pt[1])
    return {
        "panel_id": result.panel_id,
        "dialect": result.dialect,
        "annotated": result.annotated,
        "paths_per_layer": by_layer,
        "hole_diameters": sorted(hole_dias),
        "extents_in": (round(max(xs) - min(xs), 4), round(max(ys) - min(ys), 4)) if xs else (0.0, 0.0),
        "warnings": result.warnings,
    }


def gcode_to_document(text: str):
    """One-call import: G-code text -> (ezdxf doc, summary dict)."""
    result = parse_gcode(text)
    return paths_to_document(result.paths), summarize(result), result
