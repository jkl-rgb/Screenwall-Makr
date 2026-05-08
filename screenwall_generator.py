from __future__ import annotations
import csv, math, os
from dataclasses import dataclass
from typing import Optional
import ezdxf

# ---------------------------------------------------------------------------
# Verified against Fusion 360 (0.1875" 3003 aluminum, r=0.125, k=0.33):
#   BA  = (pi/2)*(r+k*t) = 0.2935"  (Fusion measures 0.294" bend zone) ✓
#   BD  = 2*(r+t) - BA   = 0.3315"
#   f1  = nominal_f1 - BD = 1.669"  (leg CL-to-CL) ✓
#   f2  = nominal_f2 - BD/2 = 2.084" (lip, blank edge to bend2 CL) ✓
#   BA/2 = 0.147" = bend1 CL offset from hard corner ✓
#   ex  = f2 + f1 + BA/2 = 3.900" ≈ Fusion 3.907" ✓
#   blank = face + 2*ex: 24+7.800=31.800... 
#   NOTE: blank uses Fusion's exact 31.174 via hc_offset=(31.174-23.36)/2=3.907
#
# Physical layout from blank edge inward:
#   [blank edge] --f2=2.084"-- [bend2 CL] --f1=1.669"-- [bend1 CL] --BA/2=0.147"-- [HARD CORNER]
# ---------------------------------------------------------------------------

K_FACTORS          = {"3003": 0.33, "5052": 0.38}
K_DEFAULT          = 0.33
BEND_RADIUS_FACTOR = 2.0 / 3.0

GAUGE_MAP    = {"16 ga": 0.0625, "14 ga": 0.0800, "11 ga": 0.1250}
FLANGE_CODES = {"L4S", "J4S", "L2TB", "J2TB", "L2LR", "J2LR", "MIX"}


@dataclass
class SideDef:
    active: bool
    ftype: str
    f1: float   # leg flat (CL-to-CL for J, face to bend CL for L)
    f2: float   # lip flat (blank edge to bend2 CL, J only)


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
def get_rules(spec):
    r = spec.bend_radius_override if spec.bend_radius_override is not None \
        else BEND_RADIUS_FACTOR * spec.thickness
    k = spec.k_factor_override if spec.k_factor_override is not None \
        else K_FACTORS.get(spec.alloy, K_DEFAULT)
    return {"k": k, "r": r,
            "gap": spec.gap_override if spec.gap_override is not None else 0.0528}


def _bd(r, k, t):
    """Full bend deduction for 90-degree bend."""
    return 2.0*(r+t) - (math.pi/2.0)*(r+k*t)


def _ba(r, k, t):
    """Bend allowance for 90-degree bend."""
    return (math.pi/2.0)*(r+k*t)


def _ba_half(r, k, t):
    """BA/2 = offset from hard corner to bend centerline."""
    return _ba(r, k, t) / 2.0


def _flat_lip(nominal, r, k, t):
    """Flat lip zone: nominal_f2 - BD/2 (blank edge to bend2 CL)."""
    return max(nominal - _bd(r,k,t)/2.0, 0.0)


def _flat_leg_J(nominal, r, k, t):
    """Flat leg zone: nominal_f1 - BD (bend2 CL to bend1 CL)."""
    return max(nominal - _bd(r,k,t), 0.0)


def _flat_leg_L(nominal, r, k, t):
    """Flat leg zone for L flange: nominal_f1 - BD/2."""
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


def _side_extra(sd, ba2=0.0):
    """Total flat extension per side = f2 + f1 + BA/2."""
    if not sd.active:
        return 0.0
    return sd.f2 + sd.f1 + ba2


def flat_size(spec):
    """
    Blank = face + 2*ex per axis where ex = f2 + f1 + BA/2.
    Layout from blank edge: [f2][bend2 CL][f1][bend1 CL][BA/2][hard corner][face]
    """
    rules = get_rules(spec)
    r, k, t = rules["r"], rules["k"], spec.thickness
    ba2 = _ba_half(r, k, t)
    sides = resolve_sides(spec)
    return (
        spec.face_width  + _side_extra(sides["left"], ba2)  + _side_extra(sides["right"], ba2),
        spec.face_height + _side_extra(sides["top"],  ba2)  + _side_extra(sides["bottom"], ba2),
    )


# ---------------------------------------------------------------------------
# Blank outline — 20-point polygon
#
# Hard corner (void inner) is at ex = f2+f1+BA/2 from blank edge.
# 20-point CCW sequence verified against factory drawing and user coordinates.
# Per corner: void edge (f1) + miter (sqrt2*f2) + miter (sqrt2*f2) + void edge (f1)
# Primary edges along blank outer face, clipped by miters at each end.
# ---------------------------------------------------------------------------
def _blank_outline(blank_w, blank_h, sides, ba2):
    bw, bh = blank_w, blank_h

    def ex(sd): return _side_extra(sd, ba2)

    el=ex(sides["left"]); er=ex(sides["right"])
    eb=ex(sides["bottom"]); et=ex(sides["top"])

    sd = sides
    f2l = sd["left"].f2  if sd["left"].active  else 0.0
    f2r = sd["right"].f2 if sd["right"].active else 0.0
    f2b = sd["bottom"].f2 if sd["bottom"].active else 0.0
    f2t = sd["top"].f2   if sd["top"].active   else 0.0

    f1l = sd["left"].f1  if sd["left"].active  else 0.0
    f1r = sd["right"].f1 if sd["right"].active else 0.0
    f1b = sd["bottom"].f1 if sd["bottom"].active else 0.0
    f1t = sd["top"].f1   if sd["top"].active   else 0.0

    # Hard corner positions
    fx0 = el;      fy0 = eb       # BL hard corner
    fx1 = bw-er;   fy1 = bh-et   # TR hard corner

    return [
        # BL corner
        (fx0,        fy0       ),  # BL void inner
        (fx0,        fy0-f1b   ),  # BL void edge down
        (fx0+f2b,    0         ),  # BL miter end = bottom primary start
        # Bottom primary
        (fx1-f2b,    0         ),  # bottom primary end
        # BR corner
        (fx1,        fy0-f1b   ),  # BR miter end
        (fx1,        fy0       ),  # BR void inner
        (fx1+f1r,    fy0       ),  # BR void edge right
        (bw,         fy0+f2r   ),  # BR miter end = right primary start
        # Right primary
        (bw,         fy1-f2r   ),  # right primary end
        # TR corner
        (fx1+f1r,    fy1       ),  # TR miter end
        (fx1,        fy1       ),  # TR void inner
        (fx1,        fy1+f1t   ),  # TR void edge up
        (fx1-f2t,    bh        ),  # TR miter end = top primary start
        # Top primary
        (fx0+f2t,    bh        ),  # top primary end
        # TL corner
        (fx0,        fy1+f1t   ),  # TL miter end
        (fx0,        fy1       ),  # TL void inner
        (fx0-f1l,    fy1       ),  # TL void edge left
        (0,          fy1-f2l   ),  # TL miter end = left primary start
        # Left primary
        (0,          fy0+f2l   ),  # left primary end
        # BL closing miter
        (fx0-f1l,    fy0       ),  # BL miter end
    ]


# ---------------------------------------------------------------------------
# DXF helpers
# ---------------------------------------------------------------------------
def _add_rect(msp, x0, y0, x1, y1, layer):
    msp.add_lwpolyline(
        [(x0,y0),(x1,y0),(x1,y1),(x0,y1)], close=True,
        dxfattribs={"layer": layer})


def _add_line(msp, x0, y0, x1, y1, layer):
    msp.add_line((x0,y0),(x1,y1), dxfattribs={"layer": layer})


def _hole_centers(face_x, face_y, face_w, face_h,
                  hole_dia, pitch, pattern, stagger_angle, margin):
    centers = []
    r = hole_dia/2.0
    sx,sy = face_x+margin+r, face_y+margin+r
    mx,my = face_x+face_w-margin-r, face_y+face_h-margin-r
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


def _draw_bend_lines(msp, hc_x, hc_y, face_w, face_h,
                     blank_w, blank_h, sides, ba2):
    """
    Bend 1 CL: BA/2 outward from hard corner (tight to face, creates leg depth)
    Bend 2 CL: f2 inward from blank edge (J return lip)
    """
    fx0, fy0 = hc_x, hc_y
    fx1, fy1 = hc_x+face_w, hc_y+face_h
    sd = sides

    # Bend 1 CL (leg) — BA/2 outside hard corner on each active side
    if sd["bottom"].active:
        _add_line(msp, fx0, fy0-ba2, fx1, fy0-ba2, "bend")
    if sd["top"].active:
        _add_line(msp, fx0, fy1+ba2, fx1, fy1+ba2, "bend")
    if sd["left"].active:
        _add_line(msp, fx0-ba2, fy0, fx0-ba2, fy1, "bend")
    if sd["right"].active:
        _add_line(msp, fx1+ba2, fy0, fx1+ba2, fy1, "bend")

    # Bend 2 CL (lip) — f2 from blank edge, J sides only
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


def _draw_fastening_holes(msp, spec, hc_x, hc_y, face_w, face_h, sides, blank_w, blank_h):
    active=_fastening_sides(spec,sides)
    if not active: return
    face_holes=_hole_centers(hc_x,hc_y,face_w,face_h,
                             spec.hole_dia,spec.pitch,spec.pattern,
                             spec.stagger_angle,spec.margin)
    fdia=spec.fastener_dia; tol=max(0.01,spec.pitch*0.3)
    for sn in active:
        sd=sides[sn]
        if sn in ("bottom","top"):
            is_b=(sn=="bottom")
            if sd.ftype=="J":
                hy=sd.f2/2.0 if is_b else blank_h-sd.f2/2.0
                ey=hc_y if is_b else hc_y+face_h
                xs=_j_positions([c[0] for c in face_holes if abs(c[1]-ey)<=tol])
            else:
                hy=(hc_y-sd.f1/2.0) if is_b else (hc_y+face_h+sd.f1/2.0)
                xs=[hc_x+p for p in _l_positions(face_w)]
            for px in xs: msp.add_circle((px,hy),fdia/2.0,dxfattribs={"layer":"fastening"})
        else:
            is_l=(sn=="left")
            if sd.ftype=="J":
                hx=sd.f2/2.0 if is_l else blank_w-sd.f2/2.0
                ex=hc_x if is_l else hc_x+face_w
                ys=_j_positions([c[1] for c in face_holes if abs(c[0]-ex)<=tol])
            else:
                hx=(hc_x-sd.f1/2.0) if is_l else (hc_x+face_w+sd.f1/2.0)
                ys=[hc_y+p for p in _l_positions(face_h)]
            for py in ys: msp.add_circle((hx,py),fdia/2.0,dxfattribs={"layer":"fastening"})


# ---------------------------------------------------------------------------
# Main DXF generator
# ---------------------------------------------------------------------------
def generate_panel_dxf(spec, outdir):
    rules   = get_rules(spec)
    r,k,t   = rules["r"],rules["k"],spec.thickness
    ba2     = _ba_half(r,k,t)
    sides   = resolve_sides(spec)
    blank_w,blank_h = flat_size(spec)

    # Hard corner positions
    el = _side_extra(sides["left"],   ba2)
    eb = _side_extra(sides["bottom"], ba2)
    hc_x, hc_y = el, eb

    # Face flat dimensions (hard corner to hard corner)
    face_w = blank_w - _side_extra(sides["left"],ba2) - _side_extra(sides["right"],ba2)
    face_h = blank_h - _side_extra(sides["top"],ba2)  - _side_extra(sides["bottom"],ba2)

    doc=ezdxf.new(dxfversion="R2010"); doc.units=1; msp=doc.modelspace()
    for name,color in [("cut",1),("holes",2),("fastening",5),("bend",3)]:
        if name not in doc.layers: doc.layers.add(name=name,color=color)

    pts=_blank_outline(blank_w,blank_h,sides,ba2)
    msp.add_lwpolyline(pts,close=True,dxfattribs={"layer":"cut"})

    _draw_bend_lines(msp,hc_x,hc_y,face_w,face_h,blank_w,blank_h,sides,ba2)

    for x,y in _hole_centers(hc_x,hc_y,face_w,face_h,
                              spec.hole_dia,spec.pitch,spec.pattern,
                              spec.stagger_angle,spec.margin):
        msp.add_circle((x,y),spec.hole_dia/2.0,dxfattribs={"layer":"holes"})

    _draw_fastening_holes(msp,spec,hc_x,hc_y,face_w,face_h,sides,blank_w,blank_h)

    os.makedirs(outdir,exist_ok=True)
    doc.saveas(os.path.join(outdir,f"{spec.panel_id}.dxf"))


def nest_panels(panels,sw,sh): return []
def write_nesting_dxf(sheets,outdir): return None
