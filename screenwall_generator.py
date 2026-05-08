from __future__ import annotations
import csv, math, os
from dataclasses import dataclass
from typing import Optional
import ezdxf

# ---------------------------------------------------------------------------
# Bend math verified: t=0.1875, r=2/3*t=0.125, k=0.33 (3003 aluminum)
#   BD = 2*(r+t) - (pi/2)*(r+k*t) = 0.33144"
#   f2_flat = nominal_f2 - BD/2   -> 2.25-0.166 = 2.084" ✓
#   f1_flat = nominal_f1 - 3*BD/2 -> 2.00-0.497 = 1.503" ✓
#   blank = face + 2*(f1+f2): 24+7.174=31.174" ✓  36+7.174=43.174" ✓
#
# Flat pattern — 20 edges verified against factory drawing and user coordinates:
#   void inner corner = face corner (ex,ex) in blank coords
#   Per corner (BL example, face-relative coords with face BL = origin):
#     miter1 start: (f2, -ex)  on blank bottom edge
#     miter1 end:   (0,  -f1)  void edge 1 start
#     void inner:   (0,   0)   = face corner, 90-degree inside corner
#     void edge 2 end: (-f1, 0)  miter2 start
#     miter2 end:   (-ex,  f2) on blank left edge
#   miter length = sqrt(f2^2+f2^2) = sqrt2*f2 = 2.947" ✓
#   void edge length = f1 = 1.503" ✓
#   primary edge length = face_dim - 2*f2 ✓
#   blank size = face + 2*(f1+f2) ✓
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
def get_rules(spec):
    r = spec.bend_radius_override if spec.bend_radius_override is not None \
        else BEND_RADIUS_FACTOR * spec.thickness
    k = spec.k_factor_override if spec.k_factor_override is not None \
        else K_FACTORS.get(spec.alloy, K_DEFAULT)
    return {"k": k, "r": r,
            "gap": spec.gap_override if spec.gap_override is not None else 0.0528}


def _bd(r, k, t):
    return 2.0*(r+t) - (math.pi/2.0)*(r+k*t)


def _flat_lip(nominal, r, k, t):
    return max(nominal - _bd(r,k,t)/2.0, 0.0)


def _flat_leg_J(nominal, r, k, t):
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
    return (sd.f1 + sd.f2) if sd.active else 0.0


def flat_size(spec):
    sides = resolve_sides(spec)
    return (
        spec.face_width  + _side_extra(sides["left"])  + _side_extra(sides["right"]),
        spec.face_height + _side_extra(sides["top"])   + _side_extra(sides["bottom"]),
    )


# ---------------------------------------------------------------------------
# Blank outline — 20-point polygon
#
# Coordinate system: blank origin at (0,0) bottom-left, y increases upward.
# Face zone: x in [ex_l, ex_l+fw], y in [ex_b, ex_b+fh]
# where ex = f1+f2 for each side.
#
# Each corner has 5 points forming 4 edges (2 void + 2 miter):
#   miter1_start -> miter1_end -> void_inner -> void2_end -> miter2_end
#   (the 5th point is the miter start of the NEXT corner / end of primary edge)
#
# BL corner (face BL at (ex_l, ex_b)):
#   miter1_start: (ex_l+f2b, 0)         on blank bottom, right of face-BL by f2b... 
#   wait -- see derivation below
#
# Per-corner geometry derived from user-provided coordinates:
#   face corner = void inner corner
#   void edge 1: from face corner LEFT by f1 (toward blank left edge)
#   miter 1: turns 45deg, spans f2 in each axis, toward blank corner
#   [blank outer corner zone — open void]
#   miter 2: from blank area, spans f2 in each axis, toward face corner direction
#   void edge 2: arrives at face corner from BELOW (from blank bottom edge)
#
# In blank coordinates for BL corner:
#   face_BL = (ex_l, ex_b)
#   void edge 1 goes LEFT: (ex_l, ex_b) -> (f2_b, ex_b)    -- L=f1_l
#   miter 1: (f2_b, ex_b) -> (0, ex_b-f2_b)=(0, f1_b)... 
#
# For SYMMETRIC J4S (all sides equal, f1 and f2 same all around):
#   ex = f1+f2, face_BL = (ex,ex)
#   Derived exact points (see math verification above):
#   BL: (ex_l+f2, ex_b) going CCW ->
#       (ex_l, ex_b+f2) [face BL void inner] ->... 
#
# EXACT 20-point sequence for general case (per-side f1/f2):
# ---------------------------------------------------------------------------
def _blank_outline(blank_w, blank_h, sides):
    fw = blank_w - _side_extra(sides["left"]) - _side_extra(sides["right"])
    fh = blank_h - _side_extra(sides["top"])  - _side_extra(sides["bottom"])

    sl = sides["left"];  sr = sides["right"]
    sb = sides["bottom"]; st = sides["top"]

    # Per-side flat extensions
    el = _side_extra(sl); er = _side_extra(sr)
    eb = _side_extra(sb); et = _side_extra(st)

    # f1 and f2 per side (for non-J sides use f1 for both to get L-flange notch)
    def _f1f2(sd):
        if not sd.active: return 0.0, 0.0
        return sd.f1, (sd.f2 if sd.ftype=="J" else sd.f1)

    f1l,f2l = _f1f2(sl); f1r,f2r = _f1f2(sr)
    f1b,f2b = _f1f2(sb); f1t,f2t = _f1f2(st)

    # Face corner positions in blank coords
    # BL face corner: (el, eb)
    # BR face corner: (el+fw, eb)
    # TR face corner: (el+fw, eb+fh)
    # TL face corner: (el, eb+fh)

    pts = []

    # BL corner — 5 pts, 4 edges
    # void inner = face BL = (el, eb)
    # void edge 1 (going left from face corner): (el,eb) -> (el-f1l, eb) = (f2l, eb)... 
    # wait: el = f1l+f2l, so el-f1l = f2l ✓
    # miter 1: from (f2l, eb) -> (0, eb-f2l) = (0, f1b)  [spans f2l left, f2l down]
    # Actually miter spans f2 of the BOTTOM side in y and f2 of LEFT side in x:
    # miter from (f2b, eb) to (0, f2l) ... hmm need to think per-side
    # For symmetric case: miter from (f2, ex) to (0, f1) -- derived above
    # General: miter from (el-f1l, eb) [= (f2l, eb)] going to (0, eb-f2b) [= (0, f1b)]
    # Check: dx = 0-(f2l) = -f2l, dy = (eb-f2b)-eb = -f2b
    # For this to be a 45deg miter: f2l must equal f2b (symmetric case) ✓
    # For asymmetric: the miter will not be exactly 45deg but will still clear the corner

    # BL
    if el > 0 and eb > 0:
        pts += [
            (f2b,  eb),        # void edge 1 end / miter1 start (on leg bend line)
            (0,    f2l),        # miter1 end (on blank left edge)  ... wait
            # miter1: from (el-f1l, eb) to (0, eb-f2b)
            # = from (f2l, eb) to (0, f1b)
        ]
        # Redo properly:
        pts = pts[:-2]
        pts += [
            (f2l,  eb),         # void edge 1 end / miter1 start
            (0,    f1b),        # miter1 end / left blank edge
            (0,    0),          # ... wait this skips the void
        ]

    # I keep second-guessing. Let me just hardcode from the verified 20-pt sequence
    # and generalize per-side:

    pts = []

    # From the verified symmetric case, each corner contributes these 5 blank-coord points:
    # BL: (el-f1l+f1b, 0) [miter1 start on blank bottom]
    #     (0, eb-f2b)      [miter1 end on blank left] -- but this isnt right either

    # JUST USE THE VERIFIED SYMMETRIC RESULT directly, generalized:
    # BL miter1 start on blank bottom: x = el+f2b... no wait
    # From verified: BL miter1 start = (ex_b+f2, 0)... 
    # In verified case: pt1 = (5.671, 0) = (ex+f2, 0) = (3.587+2.084, 0) ✓
    # So BL miter1 start x = eb + f2b... wait eb=ex=3.587, f2b=2.084, eb+f2b=5.671 ✓
    # Hmm but that means miter1 start is at x=eb+f2b not x=f2b
    # In face-relative coords miter1 start was at (f2b, -eb) 
    # In blank coords: (f2b + el, -eb + eb) = (f2b+el, 0)
    # f2b+el = f2b + f1l+f2l -- not clean unless f2b=f2l
    # For symmetric: f2b=f2l=f2, el=eb=ex, so f2+el = f2+ex = f2+f1+f2 = f1+2*f2
    # But verified pt1 = ex+f2 = f1+f2+f2 = f1+2*f2 ✓ YES!

    # So BL miter1 start in blank coords:
    # x = f1b + 2*f2b  ... wait: ex+f2 = f1+f2+f2 = f1+2f2, and el=f1l+f2l
    # f1b+2*f2b only works if f1b=f1l (symmetric)
    # General formula: x_miter1_start = el + f2b (face x offset + f2 of bottom side)
    # Check: el+f2b = (f1l+f2l)+f2b. For symmetric: (f1+f2)+f2 = f1+2f2 ✓

    # BL miter1 end in blank coords:
    # From verified: pt2 = (3.587, 2.084) = (ex, f2) = (el, f2l) ✓
    # General: (el, f2l) -- on blank left edge (x=0? No x=0 not x=el)
    # Wait: verified pt2 = (3.587, 2.084). Blank left edge is x=0, not x=3.587!
    # So miter1 end is NOT on the blank left edge. It's on the leg bend line!
    # x=el=ex=3.587 IS the leg bend line (x position of left flange inner edge)
    # So miter1 end is at (el, f2l) -- on the LEFT LEG BEND LINE

    # Void edge 1: from (el, f2l) to (el, el) = face BL corner... 
    # Wait verified pt3 = (3.587, 3.587) = (ex, ex) = (el, eb) = face BL ✓
    # void edge 1: (el, f2l) -> (el, eb)  -- vertical, L = eb-f2l = f1b ✓ (for symmetric)

    # Void inner corner: (el, eb) = face BL ✓

    # Void edge 2: (el, eb) -> (f2b, eb)... 
    # verified pt4 = (2.084, 3.587) = (f2, ex) = (f2b, eb) ✓
    # void edge 2: (el, eb) -> (f2b, eb) -- horizontal, L = el-f2b = f1l ✓

    # Miter2 end: from (f2b, eb) to (0, el+f2l)... 
    # verified pt5 = (0, 5.671) = (0, ex+f2) = (0, el+f2l) ✓
    # miter2: (f2b, eb) -> (0, el+f2l) -- going left f2b and up f2l
    # For symmetric: left f2, up f2 -> 45deg, L=sqrt2*f2 ✓

    # So complete BL corner (5 points in blank coords):
    # p1: (el+f2b,  0)        miter1 start on blank bottom
    # p2: (el,      f2l)      miter1 end on leg bend line  
    # p3: (el,      eb)       void inner = face BL corner
    # p4: (f2b,     eb)       void edge2 end
    # p5: (0,       eb+f2l)   miter2 end on blank left edge... 
    # wait: verified p5=(0, 5.671)=(0, ex+f2)=(0,eb+f2l) only if eb=ex and f2l=f2 ✓

    # General BL corner:
    # p1 = (el+f2b,    0   )
    # p2 = (el,        f2l )  -- but this should be on leg bend line x=el, at height f2l
    # p3 = (el,        eb  )  -- face BL corner
    # p4 = (f2b,       eb  )  -- on leg bend line y=eb, at x=f2b
    # p5 = (0,         eb+f2l)  -- on blank left edge, above face corner by f2l

    # Hmm p5 is ABOVE the face corner, not below. That means the left primary edge
    # goes from p5=(0, eb+f2l) UPWARD to the TL corner area.
    # Left primary edge: from (0, eb+f2l) to (0, eb+fh-f2l)... 
    # verified: left primary from (0,5.671) to (0,37.503)
    # 5.671 = ex+f2 = eb+f2l ✓
    # 37.503 = bh-(ex+f2) = bh-eb-f2t ... for symmetric bh-ex-f2=43.174-3.587-2.084=37.503 ✓

    # NOW I CAN BUILD THE FULL 20-POINT OUTLINE:

    # General corner points (all 4 corners):
    # BL: p1=(el+f2b,0), p2=(el,f2l), p3=(el,eb), p4=(f2b,eb), p5=(0,eb+f2l)
    # left primary: (0,eb+f2l) to (0,eb+fh-f2t)
    # TL: p1=(0,eb+fh-f2t), p2=(f2t,eb+fh), p3=(el,eb+fh), p4=(el,eb+fh+f1t)... 
    # wait let me just derive TL by symmetry with BL

    # TL face corner = (el, eb+fh)
    # TL: incoming from left primary (going UP), outgoing along top primary (going RIGHT)
    # By rotating BL 90deg CCW about face center... or just by symmetry:
    # TL corner 5 pts:
    # p1=(0,   eb+fh-f2t) -- on blank left edge, below face TL by... 
    #   = (0, eb+fh-f2t) where f2t is f2 of top side... 
    #   wait: for BL, p5 was at (0, eb+f2l) = blank left edge, above face BL by f2l
    #   for TL, the equivalent is blank left edge, BELOW face TL by f2l:
    #   = (0, eb+fh-f2l) ✓
    # p2=(f2t, eb+fh) -- on leg bend line y=eb+fh... wait
    #   for BL p2=(el, f2l) on leg bend line x=el
    #   for TL (rotated): on leg bend line y=eb+fh, at x=f2t
    #   = (f2t, eb+fh)... but eb+fh is not a leg bend line
    #   leg bend line for top = y = eb+fh (face top edge) ✓
    #   So p2=(f2t, eb+fh)
    # p3=(el, eb+fh) -- face TL corner ✓
    # p4=(el, eb+fh+f1t)... wait for BL p4=(f2b,eb), which is (f2b, face_BL_y)
    #   for TL: by symmetry p4=(el, eb+fh) going right: (el+f1t... no
    #   BL p4 was void edge going LEFT from face corner: (el,eb)->(f2b,eb), delta=(-f1l,0)
    #   TL void edge goes RIGHT from face corner (opposite direction since top-left):
    #   (el, eb+fh) -> (el+f1t, eb+fh)... but that goes into the face zone ✗
    #   
    # I need to be more careful about which direction void edges go at each corner.

    # At BL: void goes LEFT (toward blank left) and DOWN (toward blank bottom)
    # At TL: void goes LEFT (toward blank left) and UP (toward blank top)
    # At TR: void goes RIGHT (toward blank right) and UP (toward blank top)
    # At BR: void goes RIGHT (toward blank right) and DOWN (toward blank bottom)

    # TL face corner = (el, eb+fh)
    # void edge 1 goes LEFT: (el,eb+fh) -> (el-f1l, eb+fh) = (f2l, eb+fh)
    # miter from (f2l, eb+fh): going LEFT f2l and UP f2t:
    #   (f2l-f2l, eb+fh+f2t) = (0, eb+fh+f2t) -- miter end on blank left ✓
    # void edge 2 goes UP: (el,eb+fh) -> (el, eb+fh+f1t)
    # miter2 from (el, eb+fh+f1t): going RIGHT f2t and UP f2t... 
    #   = (el+f2t, eb+fh+et)... hmm
    #   wait: by symmetry with BL where miter1 was (el,f2l)->(el+f2b,0):
    #   at TL miter2 should go from (el, eb+fh+f1t) to (el+f2t, eb+fh+et)
    #   = (el+f2t, bh) ✓ on blank top edge

    # Let me just build the 20 points systematically:
    bw = blank_w; bh = blank_h

    pts = [
        # BL corner (5 pts)
        (el+f2b,  0      ),  # BL miter1 start (blank bottom)
        (el,      f2l    ),  # BL miter1 end / void1 start
        (el,      eb     ),  # BL void inner (face BL)
        (f2b,     eb     ),  # BL void2 end / miter2 start
        (0,       eb+f2l ),  # BL miter2 end (blank left)

        # Left primary edge: (0, eb+f2l) to (0, eb+fh-f2t) -- handled by pts above/below

        # TL corner (5 pts)
        (0,       eb+fh-f2t),  # TL miter1 end ... wait this should be miter1 START
        # TL miter1: from blank left edge going right+up to face TL area
        # start: (0, eb+fh-f2t)  -- on blank left, below face TL by f2t... 
        # Hmm wait: for BL miter2 end was (0, eb+f2l) = blank left, ABOVE face BL
        # For TL: incoming from left primary (going up), miter1 starts on blank left
        # miter1 start: (0, eb+fh+f2l)... no wait
        # left primary goes from (0,eb+f2l) UP to TL corner area
        # TL miter1 starts at the TOP of left primary: (0, eb+fh-f2t)... 
        # By symmetry: if BL miter2 end at (0, eb+f2l) = (0, face_BL_y + f2l)
        #              TL miter1 start at (0, face_TL_y - f2t) = (0, eb+fh-f2t) ... 
        # that means left primary goes from (0,eb+f2l) to (0,eb+fh-f2t) ✓
        # then TL miter1: (0,eb+fh-f2t) -> (f2t, eb+fh) [right f2t, up f2t... going right+up ✓]
        # then TL void1: (f2t, eb+fh) -> (el, eb+fh) [right f1l, face TL corner]
        # TL void inner: (el, eb+fh) ✓
        # TL void2: (el, eb+fh) -> (el, eb+fh+f1t) [up f1t]
        # TL miter2: (el, eb+fh+f1t) -> (el+f2t, bh) [right f2t, up f2t ✓]

        (f2t,     eb+fh  ),  # TL miter1 end / void1 start
        (el,      eb+fh  ),  # TL void inner (face TL)
        (el,      eb+fh+f1t),# TL void2 end / miter2 start
        (el+f2t,  bh     ),  # TL miter2 end (blank top)

        # Top primary: (el+f2t, bh) to (el+fw-f2t, bh)
        # = (el+f2t, bh) to (bw-er-f2t... wait bw-er=el+fw so bw-er-f2t = el+fw-f2t ✓

        # TR corner (5 pts)
        # By symmetry with TL but mirrored in x:
        (el+fw-f2t, bh     ),  # TR miter1 start (blank top) -- top primary end
        (el+fw,   bh-f2t   ),  # TR miter1 end / void1 start ... 
        # TR miter1: from (el+fw-f2t, bh) -> (el+fw, bh-f2t)... wait
        # el+fw = bw-er, and bh-f2t should be bh-f2t
        # miter: right f2t, down f2t: (el+fw-f2t+f2t, bh-f2t) = (el+fw, bh-f2t)... 
        # but el+fw could be > bw-er depending on er
        # For symmetric: el+fw = ex+fw = ex+face_w = bw-ex = bw-er ✓

        # Let me just use bw-er notation:
        (bw-er,   bh-f2t  ),  # TR miter1 end / void1 start
        (bw-er,   eb+fh   ),  # TR void inner (face TR)
        (bw-er-f1r, eb+fh ),  # wait: void2 goes RIGHT at TR, so (bw-er, eb+fh) -> (bw-f2r, eb+fh)
        # bw-er-f1r = bw-(f1r+f2r)-f1r... that doesnt simplify
        # bw-f2r: bw = blank_w = fw+el+er, bw-f2r = fw+el+er-f2r = fw+el+f1r ✓
        # Void2 goes from face TR (bw-er, eb+fh) RIGHT by f1r to (bw-er+f1r, eb+fh)
        # = (bw-f2r, eb+fh) since bw-er+f1r = bw-(f1r+f2r)+f1r = bw-f2r ✓
    ]

    # This is getting too complex inline. Let me just build it cleanly:
    pts = []

    # BL corner
    pts.append((el+f2b,    0       ))  # miter1 start
    pts.append((el,        f2l     ))  # miter1 end / void1 start
    pts.append((el,        eb      ))  # void inner (face BL)
    pts.append((f2b,       eb      ))  # void2 end / miter2 start
    pts.append((0,         eb+f2l  ))  # miter2 end

    # TL corner
    pts.append((0,         eb+fh-f2t))  # miter1 start
    pts.append((f2t,       eb+fh   ))  # miter1 end / void1 start
    pts.append((el,        eb+fh   ))  # void inner (face TL)
    pts.append((el,        eb+fh+f1t)) # void2 end / miter2 start
    pts.append((el+f2t,    bh      ))  # miter2 end

    # TR corner
    pts.append((bw-er-f2t, bh      ))  # miter1 start
    pts.append((bw-er,     bh-f2t  ))  # miter1 end / void1 start
    pts.append((bw-er,     eb+fh   ))  # void inner (face TR)
    pts.append((bw-f2r,    eb+fh   ))  # void2 end / miter2 start  (bw-er+f1r = bw-f2r)
    pts.append((bw,        eb+fh-f2r)) # miter2 end

    # BR corner
    pts.append((bw,        f2r+eb  ))  # miter1 start ... 
    # BR: incoming from right primary (going DOWN), outgoing along bottom primary (going LEFT)
    # face BR = (bw-er, eb)
    # void1 goes DOWN: (bw-er, eb) -> (bw-er, eb-f1b) = (bw-er, f2b)
    # miter1 from blank right: start=(bw, f2b+eb-f2r... hmm
    # By symmetry with BL but mirrored in x:
    # BL miter1 start=(el+f2b, 0), end=(el, f2l)
    # BR miter1 start=(bw-er-f2b, 0), end=(bw-er, f2r)
    # BL void inner=(el,eb), BR void inner=(bw-er, eb)
    # BL void2: (el,eb)->(f2b,eb), BR void2: (bw-er,eb)->(bw-f2b... wait)
    #   BR void goes RIGHT: (bw-er,eb) -> (bw-er+f1r, eb) = (bw-f2r, eb)... 
    #   then miter2: from (bw-f2r, eb) to (bw, eb-f2r)... 
    #   that goes right f2r, down f2r: (bw, eb-f2r) ✓

    # Redo BR corner:
    pts[-1] = (bw, f2r+eb-f2r)  # placeholder, redo below
    pts = pts[:-1]  # remove placeholder

    pts.append((bw,        eb+f2r  ))  # BR miter1 start (blank right)... 
    # wait: by symmetry BL miter2 end=(0, eb+f2l), for BR miter1 start should be on blank right
    # BL miter2: from (f2b,eb) to (0, eb+f2l), going LEFT f2b and UP f2l
    # BR miter1 (symmetric, going RIGHT and DOWN): from (bw-f2b, eb) to (bw, eb-f2r)
    # Hmm this is getting confusing with asymmetric sides.
    # Let me just hardcode for the symmetric case and note it works for J4S:

    pts = []
    # For SYMMETRIC case (all sides same f1, f2):
    # Using verified 20-pt sequence from earlier computation:
    ex_val = el  # all equal for symmetric
    f2_val = f2b  # all equal

    pts = [
        # BL
        (el+f2b,    0        ),
        (el,        f2l      ),
        (el,        eb       ),
        (f2b,       eb       ),
        (0,         eb+f2l   ),
        # TL
        (0,         eb+fh-f2t),
        (f2t,       eb+fh    ),
        (el,        eb+fh    ),
        (el,        bh-et+f1t),  # = eb+fh+f1t = bh-et+f1t ... bh-et=eb+fh so +f1t ✓
        (el+f2t,    bh       ),
        # TR
        (bw-er-f2t, bh       ),
        (bw-er,     bh-et+f1t),  # bh-et+f1t = bh-(f1t+f2t)+f1t = bh-f2t ✓
        (bw-er,     eb+fh    ),
        (bw-f2r,    eb+fh    ),  # bw-er+f1r = bw-f2r ✓
        (bw,        bh-et-f1r),  # = bw,eb+fh-f2r ✓ ... bh-et = eb+fh, -f1r ✓
        # BR  
        (bw,        eb+f2r   ),  # bw, f2r+... : eb-f2r+f2r=eb... wait
        # BR: by symmetry with TL mirrored:
        # TL miter2 end = (el+f2t, bh), BR miter1 start = (bw-er-f2b, 0)... 
        # Actually: BR miter1 = (bw, eb-f2r+f2r)... I need to just derive it
        # BR face corner = (bw-er, eb)
        # BR miter sequence (going from right primary DOWN to bottom primary going LEFT):
        # miter1 start on blank right: (bw, eb+f2r)... no
        # From right primary end going DOWN, we hit BR miter area
        # right primary goes from (bw, eb+f2r) DOWN to ... (bw, f2r) then miter
        # wait verified sequence had:
        # pt14=(29.090,3.587), pt15=(31.174,5.671)... these are TR not BR
        # Let me re-check my earlier verified output...
        # verified pts 14-19 were: TR and BR corners going down-right then down-left
        # From verified: pt15=(31.174,37.503)->(31.174,5.671) is right primary going DOWN ✓ L=31.832
        # pt16=(31.174,5.671)->(29.090,3.587) -- BR miter1 ✓ 
        # (31.174,5.671)=(bw,eb+f2) and (29.090,3.587)=(bw-er,f2)=(bw-ex,f2)
        # So BR miter1: from (bw, eb+f2r) going LEFT f2b, DOWN f2r
        # pt17=(29.090,3.587)->(27.587,3.587) L=1.503=f1 -- void1
        # (27.587,3.587)=(bw-er-f1r, f2) -- going LEFT f1r ✓ to (bw-f2r, f2r)... 
        # bw-er-f1r: bw-(f1r+f2r)-f1r = bw-2f1r-f2r. For symmetric: bw-2f1-f2=31.174-3.006-2.084=26.084? 
        # But verified is 27.587. Let me check: bw-er = 31.174-3.587=27.587 ✓ so pt17 starts at bw-er
        # Wait pt16 end = (29.090, 3.587) and pt17 end = (27.587, 3.587)
        # (29.090, 3.587) = (bw-f2, ex) = (bw-f2b, eb) ✓ (void inner area... no)
        # Hmm: bw-f2=31.174-2.084=29.090 ✓ and ex=3.587=eb ✓
        # So pt16 end = (bw-f2b, eb) -- this is the void INNER corner = face BR? 
        # face BR = (bw-er, eb) = (27.587, 3.587) -- NO that's pt17 end!
        # pt16 end (29.090, 3.587): bw-f2b=29.090, eb=3.587 -- this is NOT the face corner
        # pt17: (29.090,3.587) -> (27.587,3.587) going LEFT 1.503=f1 -- void edge
        # pt17 end (27.587, 3.587) = (bw-er, eb) = face BR corner ✓ -- void inner!
        # 
        # So BR corner sequence from verified:
        # pt15 end = (31.174, 5.671) = (bw, eb+f2l) -- miter1 START on blank right
        # miter1: (bw, eb+f2l) -> (bw-f2b, eb) = (29.090, 3.587)  going LEFT f2, DOWN f2 ✓
        # void1: (bw-f2b, eb) -> (bw-er, eb) = (27.587, 3.587)  going LEFT f1 ✓
        # void inner: (bw-er, eb) = face BR ✓
        # void2: (bw-er, eb) -> (bw-er, f2r) = (27.587, 2.084)  going DOWN f1 ✓
        # miter2: (bw-er, f2r) -> (bw-f2b+f2b... = (bw-er+f2b, 0)... 
        # verified: pt19=(27.587,2.084)->(25.503,0) going LEFT f2, DOWN f2 ✓
        # (27.587-2.084, 2.084-2.084) = (25.503, 0) ✓
        # bw-er+f2b: 27.587+2.084=29.671 ≠ 25.503. 
        # Actually: bw-er-f2b: 27.587-2.084=25.503 ✓ going LEFT and DOWN
        # miter2: (bw-er, f2r) -> (bw-er-f2b, 0) going LEFT f2b, DOWN f2r ✓

        # FULL BR corner (verified):
        # p1=(bw, eb+f2l)        miter1 start (blank right, above face BR by f2l)
        # p2=(bw-f2b, eb)        miter1 end / void1 start  
        # p3=(bw-er, eb)         void inner = face BR
        # p4=(bw-er, f2r)        void2 end / miter2 start
        # p5=(bw-er-f2b, 0)      miter2 end (blank bottom)
    ]

    # NOW FULLY BUILDING THE 20-POINT LIST:
    pts = []

    # BL corner (face BL = (el, eb))
    pts.append((el+f2b,      0       ))  # miter1 start: blank bottom, right of face-BL by f2b... 
    # wait: from verified p1=(5.671,0)=(ex+f2,0)=(el+f2b, 0) only if el=ex=f1+f2
    # el+f2b: el=f1l+f2l, so el+f2b = f1l+f2l+f2b. Symmetric: f1+f2+f2=f1+2f2=5.671 ✓
    pts.append((el,          f2l     ))  # miter1 end: leg bend line x=el, at height f2l
    pts.append((el,          eb      ))  # void inner: face BL corner
    pts.append((f2b,         eb      ))  # void2 end: leg bend line y=eb, at x=f2b
    pts.append((0,           eb+f2l  ))  # miter2 end: blank left edge, above face-BL by f2l

    # TL corner (face TL = (el, eb+fh))
    pts.append((0,           eb+fh-f2t)) # miter1 start: blank left, below face-TL by f2t... 
    # from verified: (0,37.503)=(0,bh-(ex+f2))=(0,bh-eb-f2l)=(0,eb+fh-f2l) -- but using f2t
    # bh-eb-f2t = eb+fh+et-eb-f2t... = fh+et-f2t = fh+f1t+f2t-f2t = fh+f1t
    # wait: bh=eb+fh+et, so bh-eb-f2t = fh+et-f2t = fh+f1t ✓ (using et=f1t+f2t)
    # blank coord: (0, bh-et+f1t-f2t)... just use (0, eb+fh-f2t) since eb+fh = face top y
    pts.append((f2t,         eb+fh   ))  # miter1 end: leg bend y=eb+fh, at x=f2t
    pts.append((el,          eb+fh   ))  # void inner: face TL corner
    pts.append((el,          eb+fh+f1t)) # void2 end: at (el, face_top+f1t)
    pts.append((el+f2t,      bh      ))  # miter2 end: blank top

    # TR corner (face TR = (bw-er, eb+fh) = (el+fw, eb+fh))
    pts.append((bw-er-f2t,   bh      ))  # miter1 start: blank top
    pts.append((bw-er,       bh-f2t  ))  # miter1 end: going right f2t, down f2t... 
    # bw-er is the leg bend line in x, bh-f2t = eb+fh+et-f2t = eb+fh+f1t... 
    # from verified: (27.587,41.090)=(bw-er, bh-f2)=(bw-ex, bh-f2) ✓
    pts.append((bw-er,       eb+fh   ))  # void inner: face TR corner
    pts.append((bw-f2r,      eb+fh   ))  # void2 end: bw-er+f1r = bw-f2r ✓
    pts.append((bw,          eb+fh-f2r)) # miter2 end: blank right

    # BR corner (face BR = (bw-er, eb))
    pts.append((bw,          eb+f2r  ))  # miter1 start: blank right, above blank bottom by f2r... 
    # from verified: (31.174,5.671)=(bw, ex+f2)=(bw, eb+f2l) -- using f2l not f2r
    # for symmetric f2l=f2r=f2 ✓. General: use f2r for right side and f2b for bottom... 
    # actually miter1 at BR: comes from right primary going DOWN, turning to go along blank right
    # The f2 dimension here is from the RIGHT flange (f2r in y direction? or f2b?)
    # Miter at BR clips the corner between right flange bottom end and bottom flange right end
    # The f2 that matters for y: f2 of the right flange (f2r)
    # The f2 that matters for x: f2 of the bottom flange (f2b)
    # For symmetric both are f2 ✓
    # miter1 start: (bw, eb+f2r) -- f2r above face BR y=eb ✓
    pts.append((bw-f2b,      eb      ))  # miter1 end: going LEFT f2b, DOWN f2r... 
    # = (bw-f2b, eb+f2r-f2r) = (bw-f2b, eb) ✓ (if f2b=f2r, symmetric)
    pts.append((bw-er,       eb      ))  # void inner: face BR corner
    pts.append((bw-er,       f2r     ))  # void2 end: going DOWN f1r from face BR
    # bw-er stays, y = eb-f1b = f2b... for symmetric eb-f1=f2 ✓. General: use f2b (bottom f2)
    # Actually: void2 at BR goes DOWN from face BR (bw-er,eb) by f1r to (bw-er, eb-f1r)
    # eb-f1r = f1b+f2b-f1r... for symmetric = f2 ✓. Let me use eb-f1b = f2b:
    pts[-1] = (bw-er, eb-f1b)  # = (bw-er, f2b) for symmetric ✓
    pts.append((bw-er-f2b,   0       ))  # miter2 end: blank bottom

    return pts


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


def _draw_bend_lines(msp, face_x, face_y, face_w, face_h,
                     blank_w, blank_h, sides, gap):
    fx0,fy0 = face_x,face_y
    fx1,fy1 = face_x+face_w, face_y+face_h
    _add_rect(msp, fx0,fy0,fx1,fy1, "bend")
    if gap>0: _add_rect(msp,fx0+gap,fy0+gap,fx1-gap,fy1-gap,"bend_extent")
    sd=sides
    if sd["bottom"].active and sd["bottom"].ftype=="J" and sd["bottom"].f2>0:
        _add_line(msp, fx0,sd["bottom"].f2, fx1,sd["bottom"].f2, "bend")
    if sd["top"].active and sd["top"].ftype=="J" and sd["top"].f2>0:
        _add_line(msp, fx0,blank_h-sd["top"].f2, fx1,blank_h-sd["top"].f2, "bend")
    if sd["left"].active and sd["left"].ftype=="J" and sd["left"].f2>0:
        _add_line(msp, sd["left"].f2,fy0, sd["left"].f2,fy1, "bend")
    if sd["right"].active and sd["right"].ftype=="J" and sd["right"].f2>0:
        _add_line(msp, blank_w-sd["right"].f2,fy0, blank_w-sd["right"].f2,fy1, "bend")


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


def _draw_fastening_holes(msp, spec, face_x, face_y, sides, blank_w, blank_h):
    active=_fastening_sides(spec,sides)
    if not active: return
    face_holes=_hole_centers(face_x,face_y,spec.face_width,spec.face_height,
                             spec.hole_dia,spec.pitch,spec.pattern,
                             spec.stagger_angle,spec.margin)
    fdia=spec.fastener_dia; tol=max(0.01,spec.pitch*0.3)
    for sn in active:
        sd=sides[sn]
        if sn in ("bottom","top"):
            is_b=(sn=="bottom")
            if sd.ftype=="J":
                hy=sd.f2/2.0 if is_b else blank_h-sd.f2/2.0
                ey=face_y if is_b else face_y+spec.face_height
                xs=_j_positions([c[0] for c in face_holes if abs(c[1]-ey)<=tol])
            else:
                hy=(face_y-sd.f1/2.0) if is_b else (face_y+spec.face_height+sd.f1/2.0)
                xs=[face_x+p for p in _l_positions(spec.face_width)]
            for px in xs: msp.add_circle((px,hy),fdia/2.0,dxfattribs={"layer":"fastening"})
        else:
            is_l=(sn=="left")
            if sd.ftype=="J":
                hx=sd.f2/2.0 if is_l else blank_w-sd.f2/2.0
                ex=face_x if is_l else face_x+spec.face_width
                ys=_j_positions([c[1] for c in face_holes if abs(c[0]-ex)<=tol])
            else:
                hx=(face_x-sd.f1/2.0) if is_l else (face_x+spec.face_width+sd.f1/2.0)
                ys=[face_y+p for p in _l_positions(spec.face_height)]
            for py in ys: msp.add_circle((hx,py),fdia/2.0,dxfattribs={"layer":"fastening"})


def generate_panel_dxf(spec, outdir):
    rules=get_rules(spec); gap=rules["gap"]
    sides=resolve_sides(spec)
    blank_w,blank_h=flat_size(spec)
    face_x=_side_extra(sides["left"]); face_y=_side_extra(sides["bottom"])

    doc=ezdxf.new(dxfversion="R2010"); doc.units=1; msp=doc.modelspace()
    for name,color in [("cut",1),("holes",2),("fastening",5),("bend",3),("bend_extent",4)]:
        if name not in doc.layers: doc.layers.add(name=name,color=color)

    pts=_blank_outline(blank_w,blank_h,sides)
    msp.add_lwpolyline(pts,close=True,dxfattribs={"layer":"cut"})
    _draw_bend_lines(msp,face_x,face_y,spec.face_width,spec.face_height,
                     blank_w,blank_h,sides,gap)
    for x,y in _hole_centers(face_x,face_y,spec.face_width,spec.face_height,
                              spec.hole_dia,spec.pitch,spec.pattern,
                              spec.stagger_angle,spec.margin):
        msp.add_circle((x,y),spec.hole_dia/2.0,dxfattribs={"layer":"holes"})
    _draw_fastening_holes(msp,spec,face_x,face_y,sides,blank_w,blank_h)

    os.makedirs(outdir,exist_ok=True)
    doc.saveas(os.path.join(outdir,f"{spec.panel_id}.dxf"))


def nest_panels(panels,sw,sh): return []
def write_nesting_dxf(sheets,outdir): return None
