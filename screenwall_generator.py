from __future__ import annotations
import csv, math, os
from dataclasses import dataclass
from typing import Optional
import ezdxf

# ---------------------------------------------------------------------------
# Bend math verified against Fusion 360 (0.1875" 3003, r=0.125, k=0.33):
#   BA  = (pi/2)*(r+k*t) = 0.294"  BA/2 = 0.147" (bend CL offset from HC) ✓
#   BD  = 2*(r+t) - BA   = 0.331"
#   f1  = nominal_f1 - 3*BD/2 = 1.503" → ex=f1+f2=3.587 → blank=31.174 ✓
#   f2  = nominal_f2 - BD/2   = 2.084" (blank edge to bend2 CL) ✓
#   Hard corner at ex+BD = 3.918" from blank edge
#   HC-HC = 31.174-2*3.918 = 23.338" ≈ Fusion 23.36" ✓
#   bend1 CL from HC = BA/2 = 0.147" ✓
#   bend2 CL from HC = 3.918-2.084 = 1.834" ≈ Fusion 1.823" ✓
#   All diffs <0.015" = accumulated rounding, within fab tolerance
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Material lookup table - r and k per alloy+thickness
# Source: Artform/Ermaksan Speed Bend Pro shop table
# Key: (alloy_normalized, thickness_rounded_4dp)
# ---------------------------------------------------------------------------
MATERIAL_TABLE = {
    # 3003-H14 Aluminum
    ("3003", 0.0600): {"r": 0.0625, "k": 0.33},
    ("3003", 0.0625): {"r": 0.0625, "k": 0.33},
    ("3003", 0.0800): {"r": 0.0800, "k": 0.35},
    ("3003", 0.1250): {"r": 0.1250, "k": 0.38},
    ("3003", 0.1875): {"r": 0.1250, "k": 0.33},  # tight bend, verified ✓
    ("3003", 0.2500): {"r": 0.2500, "k": 0.42},
    # 5052-H32 Aluminum
    ("5052", 0.1250): {"r": 0.1250, "k": 0.40},
    ("5052", 0.1875): {"r": 0.1250, "k": 0.37},  # tight bend
    # 6061-T6 Aluminum (large radii required - 1.5T to 2.0T)
    ("6061", 0.0600): {"r": 0.0900, "k": 0.40},
    ("6061", 0.0800): {"r": 0.1250, "k": 0.41},
    ("6061", 0.1250): {"r": 0.2500, "k": 0.41},
    ("6061", 0.1875): {"r": 0.3750, "k": 0.42},
    ("6061", 0.2500): {"r": 0.5000, "k": 0.42},
    # Steel
    ("steel", 0.0600): {"r": 0.0625, "k": 0.44},  # 16ga
    ("steel", 0.0625): {"r": 0.0625, "k": 0.44},  # 16ga
    ("steel", 0.0750): {"r": 0.0750, "k": 0.44},  # 14ga
    ("steel", 0.1200): {"r": 0.1250, "k": 0.45},  # 11ga
    ("steel", 0.1250): {"r": 0.1250, "k": 0.45},  # 11ga
    ("steel", 0.2500): {"r": 0.2500, "k": 0.47},  # 1/4"
}

# Fallback: r = thickness, k = 0.33 (conservative default)
K_DEFAULT          = 0.33
BEND_RADIUS_FACTOR = 1.0  # r = t fallback (not 2/3*t)

GAUGE_MAP    = {"16 ga": 0.0625, "14 ga": 0.0800, "11 ga": 0.1250}
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
    stagger_angle: float = 60.0
    margin: float = 1.25
    top_type: str = "L";    top_f1: float = 0.0;    top_f2: float = 0.0
    bottom_type: str = "L"; bottom_f1: float = 0.0; bottom_f2: float = 0.0
    left_type: str = "L";   left_f1: float = 0.0;   left_f2: float = 0.0
    right_type: str = "L";  right_f1: float = 0.0;  right_f2: float = 0.0


def _to_float(v, default=0.0):
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _thickness_to_float(v):
    raw = str(v).strip().lower()
    return GAUGE_MAP.get(raw, float(raw))


def parse_csv(path):
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV missing header row.")
        reader.fieldnames = [str(h).strip().lower() for h in reader.fieldnames]
        for i, row in enumerate(reader, start=2):
            row = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
            panel_id = (row.get("panel_id") or "").strip()
            if not panel_id:
                raise ValueError(f"Row {i}: missing panel_id")
            raw_code = (row.get("flange_code") or row.get("flange_type") or "").strip().upper()
            if raw_code == "L": raw_code = "L4S"
            elif raw_code == "J": raw_code = "J4S"
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
                flange_code=raw_code, flange_type=ftype,
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
def _normalize_alloy(alloy):
    a = alloy.strip().lower()
    if a.startswith("3003"): return "3003"
    if a.startswith("5052"): return "5052"
    if a.startswith("6061"): return "6061"
    if "steel" in a or a.startswith("az") or "ga" in a: return "steel"
    return a


def get_rules(spec):
    if spec.k_factor_override is not None and spec.bend_radius_override is not None:
        return {"k": spec.k_factor_override, "r": spec.bend_radius_override}

    alloy = _normalize_alloy(spec.alloy)
    t_key = round(spec.thickness, 4)
    entry = MATERIAL_TABLE.get((alloy, t_key))

    if entry is None:
        candidates = {t: v for (a, t), v in MATERIAL_TABLE.items() if a == alloy}
        if candidates:
            t_nearest = min(candidates.keys(), key=lambda x: abs(x - spec.thickness))
            entry = candidates[t_nearest]
        else:
            entry = {"r": spec.thickness, "k": K_DEFAULT}

    r = spec.bend_radius_override if spec.bend_radius_override is not None else entry["r"]
    k = spec.k_factor_override    if spec.k_factor_override    is not None else entry["k"]
    return {"k": k, "r": r}


def _bd(r, k, t):
    return 2.0*(r+t) - (math.pi/2.0)*(r+k*t)


def _ba_half(r, k, t):
    return (math.pi/4.0)*(r+k*t)


def _flat_lip(nominal, r, k, t):
    """f2: blank edge to bend2 CL = nominal - BD/2."""
    return max(nominal - _bd(r,k,t)/2.0, 0.0)


def _flat_leg_J(nominal, r, k, t):
    """f1: used for blank size = nominal - 3*BD/2."""
    return max(nominal - 3.0*_bd(r,k,t)/2.0, 0.0)


def _flat_leg_L(nominal, r, k, t):
    return max(nominal - _bd(r,k,t)/2.0, 0.0)


# ---------------------------------------------------------------------------
# Resolve sides
# ---------------------------------------------------------------------------
def resolve_sides(spec):
    rules = get_rules(spec)
    k, r, t = rules["k"], rules["r"], spec.thickness

    def J(f1n, f2n):
        return SideDef(True, "J", _flat_leg_J(f1n,r,k,t), _flat_lip(f2n,r,k,t))
    def L(f1n):
        return SideDef(True, "L", _flat_leg_L(f1n,r,k,t), 0.0)
    def OFF():
        return SideDef(False, "L", 0.0, 0.0)

    c=spec.flange_code; f1=spec.flange1_depth; f2=spec.flange2_depth or 0.0

    if c=="L4S": sd=L(f1);    return {s:sd for s in ("top","bottom","left","right")}
    if c=="J4S": sd=J(f1,f2); return {s:sd for s in ("top","bottom","left","right")}
    if c=="L2TB": return {"top":L(f1),"bottom":L(f1),"left":OFF(),"right":OFF()}
    if c=="J2TB": return {"top":J(f1,f2),"bottom":J(f1,f2),"left":OFF(),"right":OFF()}
    if c=="L2LR": return {"top":OFF(),"bottom":OFF(),"left":L(f1),"right":L(f1)}
    if c=="J2LR": return {"top":OFF(),"bottom":OFF(),"left":J(f1,f2),"right":J(f1,f2)}

    def _mix(active, ft, n1, n2):
        if not active: return OFF()
        return J(n1,n2) if ft=="J" else L(n1)
    return {
        "top":    _mix(spec.top_f1>0,    spec.top_type,    spec.top_f1,    spec.top_f2),
        "bottom": _mix(spec.bottom_f1>0, spec.bottom_type, spec.bottom_f1, spec.bottom_f2),
        "left":   _mix(spec.left_f1>0,   spec.left_type,   spec.left_f1,   spec.left_f2),
        "right":  _mix(spec.right_f1>0,  spec.right_type,  spec.right_f1,  spec.right_f2),
    }


def _side_extra(sd):
    """f1+f2 per side - used for blank size calculation."""
    return (sd.f1 + sd.f2) if sd.active else 0.0


def flat_size(spec):
    """Blank = face + 2*(f1+f2) per axis. Verified: 24+7.174=31.174 ✓"""
    sides = resolve_sides(spec)
    return (
        spec.face_width  + _side_extra(sides["left"])  + _side_extra(sides["right"]),
        spec.face_height + _side_extra(sides["top"])   + _side_extra(sides["bottom"]),
    )


# ---------------------------------------------------------------------------
# Blank outline - variable-sided polygon
#
# Edge count depends on corner types (per approved design):
#   J+J corner: 4 edges (void, miter, miter, void)     J4S  → 20 sides
#   L+L corner: 2 edges (void, void) square notch      L4S  → 12 sides
#   J+L corner: 3 edges (void, miter, void)            MIX  → 16 sides
#   active+inactive: 1 edge (square flange end)        2-side → 8 sides
#   inactive+inactive: 0 edges                         (point only)
#
# Hard corner (void inner) at ex+BD from blank edge where ex=f1+f2.
# Void edge length = f1+BD so miter starts exactly at bend2 centerline.
# ---------------------------------------------------------------------------

def _blank_outline(blank_w, blank_h, sides, bd):
    """
    Build CCW blank outline polygon. Edge count varies by flange config:
      J4S → 20,  L4S → 12,  2-side configs → 8,  MIX J+L → 16

    Structure: 4 corners + 4 primary edges, all 4 always present.
    Inactive sides contribute a straight face-edge primary (no flange geometry).

    Corner types (edges per corner):
      J+J: 4 edges  (void, miter, miter, void)
      L+L: 2 edges  (notch, notch) -- voids reach blank edge directly
      J+L: 3 edges  (void, miter, notch)
      J+N: 2 edges  (void, miter) -- square face corner on inactive side
      L+N: 1 edge   (notch) -- straight to face corner
      N+N: 0 edges  -- point only

    Void edge length = f1+BD so miter starts exactly at bend2 centerline.
    For L: f2=0, so void_end reaches blank edge directly (no miter).
    """
    bw, bh = blank_w, blank_h
    sl=sides["left"]; sr=sides["right"]
    sb=sides["bottom"]; st=sides["top"]

    el=_side_extra(sl); er=_side_extra(sr)
    eb=_side_extra(sb); et=_side_extra(st)

    # Hard corner positions
    fx0=el+bd;    fy0=eb+bd
    fx1=bw-er-bd; fy1=bh-et-bd

    def _ve(sd): return (sd.f1+bd) if sd.active else 0.0   # void edge length
    def _md(sd): return sd.f2 if (sd.active and sd.ftype=="J") else 0.0  # miter depth

    vb=_ve(sb); vt=_ve(st); vl=_ve(sl); vr=_ve(sr)
    mb=_md(sb); mt=_md(st); ml=_md(sl); mr=_md(sr)

    def _corner(hsd, vsd, hc_x, hc_y, ox, oy, arrive_vert):
        """
        Returns corner points AFTER the arriving point, in CCW order.
        arrive_vert: True if arriving from the vert primary (BL, TR)
                     False if arriving from the horiz primary (BR, TL)

        Sequences (after arrive point, ending with depart start):
          arrive_vert=True  (BL/TR): arrive from left/right primary
            J+J: v_void, hc, h_void, h_blank
            J+L: v_void, hc, h_blank
            L+J: hc, h_void, h_blank    [L arrives, J departs... wait]
            L+L: hc, h_blank
            J+N: v_void, hc             [depart to inactive horiz = just face]
            L+N: hc
            N+J: (empty)                [inactive arrives, handled by arrive point]
            N+L: (empty)

          arrive_vert=False (BR/TL): arrive from bottom/top primary
            J+J: hc, v_void, v_blank    [J departs to vert primary]
            L+J: hc, v_void, v_blank    [L arrives from horiz, J departs]
            J+L: h_void, hc             [J arrives from horiz... wait J is horiz at BR?]
            L+L: hc                     [L arrives and departs]
            N+J: v_void, v_blank        [inactive horiz, J departs]
            N+L: (empty)
            J+N: h_void, hc
            L+N: hc

        Note: hsd = horizontal side (bottom/top), vsd = vertical side (left/right)
        """
        fh1=_ve(hsd); fv1=_ve(vsd)
        fh2=_md(hsd); fv2=_md(vsd)
        hc=(hc_x, hc_y)

        h_void  = (hc_x,            hc_y+oy*fh1)
        h_blank = (hc_x+(-ox)*fh2,  hc_y+oy*(fh1+fh2))
        v_void  = (hc_x+ox*fv1,     hc_y)
        v_blank = (hc_x+ox*(fv1+fv2), hc_y+(-oy)*fv2)

        h_J = hsd.active and fh2>0
        h_L = hsd.active and not h_J
        v_J = vsd.active and fv2>0
        v_L = vsd.active and not v_J

        if arrive_vert:
            # Arriving from vert (left/right), departing to horiz (bottom/top)
            if not hsd.active and not vsd.active:  return []
            if hsd.active and not vsd.active:
                return [hc, h_void, h_blank] if h_J else [hc, h_blank]
            if not hsd.active and vsd.active:
                return [v_void, hc] if v_J else [hc]
            # Both active
            if v_J and h_J:   return [v_void, hc, h_void, h_blank]
            if v_J and h_L:   return [v_void, hc, h_blank]
            if v_L and h_J:   return [hc, h_void, h_blank]
            return             [hc, h_blank]  # L+L
        else:
            # Arriving from horiz (bottom/top), departing to vert (left/right)
            # v_void = blank vert edge point for L (fv2=0, so v_void=v_blank)
            if not hsd.active and not vsd.active:  return []
            if hsd.active and not vsd.active:
                return [h_void, hc] if h_J else [hc]
            if not hsd.active and vsd.active:
                return [hc, v_void, v_blank] if v_J else [hc, v_void]
            # Both active
            if h_J and v_J:   return [h_void, hc, v_void, v_blank]
            if h_J and v_L:   return [h_void, hc, v_void]   # v_void=blank vert edge for L
            if h_L and v_J:   return [hc, v_void, v_blank]
            return             [hc, v_void]  # L+L: v_void=blank vert edge

    # Build CCW polygon
    # arrive_vert alternates: BL=True, BR=False, TR=True, TL=False
    bl = _corner(sb, sl, fx0, fy0, ox=-1, oy=-1, arrive_vert=True)
    br = _corner(sb, sr, fx1, fy0, ox=+1, oy=-1, arrive_vert=False)
    tr = _corner(st, sr, fx1, fy1, ox=+1, oy=+1, arrive_vert=True)
    tl = _corner(st, sl, fx0, fy1, ox=-1, oy=+1, arrive_vert=False)

    # Arrive points (end of incoming primary at each corner)
    def _arr_vert(vsd, hc_x, hc_y, ox, oy):
        """End of vert primary = v_blank (on blank vert edge, or hc if inactive)."""
        fv1=_ve(vsd); fv2=_md(vsd)
        if not vsd.active: return (hc_x, hc_y)
        return (hc_x+ox*(fv1+fv2), hc_y+(-oy)*fv2)

    def _arr_horiz(hsd, hc_x, hc_y, ox, oy):
        """End of horiz primary = h_blank (on blank horiz edge, or hc if inactive)."""
        fh1=_ve(hsd); fh2=_md(hsd)
        if not hsd.active: return (hc_x, hc_y)
        return (hc_x+(-ox)*fh2, hc_y+oy*(fh1+fh2))

    bl_arr = _arr_vert (sl, fx0, fy0, ox=-1, oy=-1)  # end of left primary
    br_arr = _arr_horiz(sb, fx1, fy0, ox=+1, oy=-1)  # end of bottom primary
    tr_arr = _arr_vert (sr, fx1, fy1, ox=+1, oy=+1)  # end of right primary
    tl_arr = _arr_horiz(st, fx0, fy1, ox=-1, oy=+1)  # end of top primary

    pts = []
    def _app(pt):
        if pt is None: return
        if not pts or abs(pt[0]-pts[-1][0])>1e-9 or abs(pt[1]-pts[-1][1])>1e-9:
            pts.append(pt)

    _app(bl_arr);  [_app(p) for p in bl]
    _app(br_arr);  [_app(p) for p in br]
    _app(tr_arr);  [_app(p) for p in tr]
    _app(tl_arr);  [_app(p) for p in tl]

    return pts


def _add_line(msp, x0, y0, x1, y1, layer):
    msp.add_line((x0,y0),(x1,y1), dxfattribs={"layer": layer})


def _hole_centers(face_x, face_y, face_w, face_h,
                  hole_dia, pitch, pattern, stagger_angle, margin):
    centers = []
    hr = hole_dia/2.0
    sx,sy = face_x+margin+hr, face_y+margin+hr
    mx,my = face_x+face_w-margin-hr, face_y+face_h-margin-hr
    if pattern == "straight":
        y=sy
        while y<=my+1e-9:
            x=sx
            while x<=mx+1e-9: centers.append((x,y)); x+=pitch
            y+=pitch
        return centers
    alpha=math.radians(stagger_angle)
    row_step=pitch*math.sin(alpha); col_off=pitch*math.cos(alpha)
    row,y=0,sy
    while y<=my+1e-9:
        x=sx+(col_off if row%2 else 0.0)
        while x<=mx+1e-9: centers.append((x,y)); x+=pitch
        y+=row_step; row+=1
    return centers


def _draw_bend_lines(msp, fx0, fy0, fx1, fy1, blank_w, blank_h, sides, ba2):
    """
    Bend 1 CL: BA/2 outward from hard corner (creates leg depth)
    Bend 2 CL: f2 from blank edge (J return lip)
    """
    sd = sides
    # Bend 1 CL - BA/2 outside each hard corner edge
    if sd["bottom"].active:
        _add_line(msp, fx0, fy0-ba2, fx1, fy0-ba2, "bend")
    if sd["top"].active:
        _add_line(msp, fx0, fy1+ba2, fx1, fy1+ba2, "bend")
    if sd["left"].active:
        _add_line(msp, fx0-ba2, fy0, fx0-ba2, fy1, "bend")
    if sd["right"].active:
        _add_line(msp, fx1+ba2, fy0, fx1+ba2, fy1, "bend")

    # Bend 2 CL - f2 from blank edge, J sides only
    if sd["bottom"].active and sd["bottom"].ftype=="J" and sd["bottom"].f2>0:
        _add_line(msp, fx0, sd["bottom"].f2, fx1, sd["bottom"].f2, "bend")
    if sd["top"].active and sd["top"].ftype=="J" and sd["top"].f2>0:
        _add_line(msp, fx0, blank_h-sd["top"].f2, fx1, blank_h-sd["top"].f2, "bend")
    if sd["left"].active and sd["left"].ftype=="J" and sd["left"].f2>0:
        _add_line(msp, sd["left"].f2, fy0, sd["left"].f2, fy1, "bend")
    if sd["right"].active and sd["right"].ftype=="J" and sd["right"].f2>0:
        _add_line(msp, blank_w-sd["right"].f2, fy0, blank_w-sd["right"].f2, fy1, "bend")


# ---------------------------------------------------------------------------
# Fastening holes
# ---------------------------------------------------------------------------
def _fastening_sides(spec, sides):
    fp=spec.fastening_pair.strip().lower()
    if fp in ("","none"): return []
    active=[s for s in ("top","bottom","left","right") if sides[s].active]
    if fp in ("all","standard"): return active
    if fp=="tb": return [s for s in ("top","bottom") if s in active]
    if fp=="lr": return [s for s in ("left","right") if s in active]
    name={"t":"top","b":"bottom","l":"left","r":"right"}.get(fp,fp)
    return [name] if name in active else []


def _l_positions(side_length, margin=2.0, target=12.0):
    span=side_length-2.0*margin
    if span<=0: return [side_length/2.0]
    n=max(1,round(span/target)); sp=span/n
    return [margin+i*sp for i in range(n+1)]


def _j_positions(coords):
    if not coords: return []
    holes=sorted(coords); sel=[holes[0]]
    for h in holes[1:]:
        if h-sel[-1]>=11.5: sel.append(h)
    if holes[-1] not in sel and holes[-1]-sel[-1]>0.5: sel.append(holes[-1])
    return sel


def _draw_fastening_holes(msp, spec, fx0, fy0, fx1, fy1, face_w, face_h,
                           sides, blank_w, blank_h):
    active=_fastening_sides(spec,sides)
    if not active: return
    face_holes=_hole_centers(fx0,fy0,face_w,face_h,
                             spec.hole_dia,spec.pitch,spec.pattern,
                             spec.stagger_angle,spec.margin)
    fdia=spec.fastener_dia; tol=max(0.01,spec.pitch*0.3)
    for sn in active:
        sd=sides[sn]
        if sn in ("bottom","top"):
            is_b=(sn=="bottom")
            if sd.ftype=="J":
                hy=sd.f2/2.0 if is_b else blank_h-sd.f2/2.0
                ey=fy0 if is_b else fy1
                xs=_j_positions([c[0] for c in face_holes if abs(c[1]-ey)<=tol])
            else:
                hy=(fy0-sd.f1/2.0) if is_b else (fy1+sd.f1/2.0)
                xs=[fx0+p for p in _l_positions(face_w)]
            for px in xs: msp.add_circle((px,hy),fdia/2.0,dxfattribs={"layer":"fastening"})
        else:
            is_l=(sn=="left")
            if sd.ftype=="J":
                hx=sd.f2/2.0 if is_l else blank_w-sd.f2/2.0
                ex=fx0 if is_l else fx1
                ys=_j_positions([c[1] for c in face_holes if abs(c[0]-ex)<=tol])
            else:
                hx=(fx0-sd.f1/2.0) if is_l else (fx1+sd.f1/2.0)
                ys=[fy0+p for p in _l_positions(face_h)]
            for py in ys: msp.add_circle((hx,py),fdia/2.0,dxfattribs={"layer":"fastening"})


# ---------------------------------------------------------------------------
# Main DXF generator
# ---------------------------------------------------------------------------
def generate_panel_dxf(spec, outdir):
    rules  = get_rules(spec)
    r,k,t  = rules["r"],rules["k"],spec.thickness
    bd     = _bd(r,k,t)
    ba2    = _ba_half(r,k,t)
    sides  = resolve_sides(spec)
    bw,bh  = flat_size(spec)

    # Hard corner positions (void inner corners)
    el=_side_extra(sides["left"]); er=_side_extra(sides["right"])
    eb=_side_extra(sides["bottom"]); et=_side_extra(sides["top"])
    fx0=el+bd; fy0=eb+bd
    fx1=bw-er-bd; fy1=bh-et-bd
    face_w=fx1-fx0; face_h=fy1-fy0

    doc=ezdxf.new(dxfversion="R2010"); doc.units=1; msp=doc.modelspace()
    for name,color in [("cut",1),("holes",2),("fastening",5),("bend",3)]:
        if name not in doc.layers: doc.layers.add(name=name,color=color)

    pts=_blank_outline(bw,bh,sides,bd)
    msp.add_lwpolyline(pts,close=True,dxfattribs={"layer":"cut"})

    _draw_bend_lines(msp,fx0,fy0,fx1,fy1,bw,bh,sides,ba2)

    for x,y in _hole_centers(fx0,fy0,face_w,face_h,
                              spec.hole_dia,spec.pitch,spec.pattern,
                              spec.stagger_angle,spec.margin):
        msp.add_circle((x,y),spec.hole_dia/2.0,dxfattribs={"layer":"holes"})

    _draw_fastening_holes(msp,spec,fx0,fy0,fx1,fy1,face_w,face_h,sides,bw,bh)

    os.makedirs(outdir,exist_ok=True)
    doc.saveas(os.path.join(outdir,f"{spec.panel_id}.dxf"))


def nest_panels(panels,sw,sh): return []
def write_nesting_dxf(sheets,outdir): return None
