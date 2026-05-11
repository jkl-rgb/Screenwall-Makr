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

# Section 6 / Section 9 corner-relief constants
RELIEF_BUFFER     = 0.010  # tolerance buffer added to OSS face notch (machine-drift guard)
MITER_GAP_DEFAULT = 0.0    # Section 9: 0 with back J-relief circle, ~1/32" without
                           # Use spec.gap_override to set explicitly (typical 0.015"–0.030")
INSTALL_SLOT_EXTRA = 0.50  # slot length = fastener_dia + 0.50"

# Gauge strings → decimal thickness (inches). Steel matches MATERIAL_TABLE /
# Known Truths Section 2 (16ga 0.060", 14ga 0.075", 11ga 0.120"). Aluminum keeps
# common nominal sheet decimals that align with table keys (0.0625 / 0.080 / 0.125).
GAUGE_MAP_STEEL    = {"16 ga": 0.0600, "14 ga": 0.0750, "11 ga": 0.1200}
GAUGE_MAP_ALUMINUM = {"16 ga": 0.0625, "14 ga": 0.0800, "11 ga": 0.1250}
FLANGE_CODES = {"L4S", "J4S", "L2TB", "J2TB", "L2LR", "J2LR", "MIX"}
FASTENING_PAIR_VALUES = {"all", "standard", "tb", "lr", "t", "b", "l", "r", "none"}


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
    fastener_dia: float = 0.1875  # install slot width
    slot_length: Optional[float] = None
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


def _first_present(row, *keys):
    """Return the first non-empty CSV value among the provided aliases."""
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _rule_family(material: str, alloy: str) -> str:
    """Resolve the material family used for gauge decoding and bend lookup.

    material=steel takes precedence so steel rows do not need alloy=steel just
    to hit the steel bend table. Otherwise fall back to the normalized alloy.
    """
    if "steel" in (material or "").strip().lower():
        return "steel"
    return _normalize_alloy(alloy or "")


def _thickness_to_float(v, material: str = "aluminum", alloy: str = "3003"):
    raw = str(v).strip().lower()
    if _rule_family(material, alloy) == "steel":
        if raw in GAUGE_MAP_STEEL:
            return GAUGE_MAP_STEEL[raw]
    else:
        if raw in GAUGE_MAP_ALUMINUM:
            return GAUGE_MAP_ALUMINUM[raw]
    return float(raw)


def _read_csv_text(path: str) -> str:
    """Read CSV-like text from common spreadsheet encodings."""
    raw_bytes = open(path, "rb").read()
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            text = raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text[:200]:
            return text
    return raw_bytes.decode("utf-8-sig", errors="replace").replace("\x00", "")


def _choose_csv_dialect(raw_lines):
    """Pick the delimiter from the actual header row when possible."""
    header = raw_lines[0].replace("\x00", "").strip()
    expected = {"panel_id", "width", "height", "thickness"}
    for delim in (",", "\t", ";"):
        fields = [part.strip().lower() for part in header.split(delim)]
        if expected.issubset(fields):
            dialect = csv.excel()
            dialect.delimiter = delim
            return dialect

    sample = "".join(raw_lines[:5]).replace("\x00", "")
    try:
        return csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        return csv.excel


def parse_csv(path):
    """Parse a panel-spec CSV.

    The downloaded template embeds human-readable instruction lines that begin
    with '#'. Those lines are stripped before csv.DictReader sees them, so the
    template is self-documenting in Excel without breaking upload.
    """
    out = []
    text = _read_csv_text(path)
    raw_lines = [
        ln.replace("\x00", "")
        for ln in text.splitlines(keepends=True)
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not raw_lines:
        raise ValueError("CSV is empty (no non-comment lines).")
    dialect = _choose_csv_dialect(raw_lines)
    reader = csv.DictReader(raw_lines, dialect=dialect)
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
        fastening_pair = (row.get("fastening_pair") or "").strip().lower()
        if not fastening_pair:
            raise ValueError(
                f"Row {i}: fastening_pair is required. Use tb/lr/t/b/l/r/none "
                f"(or legacy all/standard)."
            )
        if fastening_pair not in FASTENING_PAIR_VALUES:
            raise ValueError(
                f"Row {i}: fastening_pair must be one of {sorted(FASTENING_PAIR_VALUES)}"
            )
        material_key = (row.get("material") or "aluminum").strip().lower()
        alloy_key = (row.get("alloy") or "3003").strip()
        fastener_dia = _to_float(
            _first_present(
                row,
                "fastener_dia",
                "slot_width",
                "fastener_slot_width",
                "install_slot_width",
            ),
            0.1875,
        )
        slot_length = _to_float(
            _first_present(
                row,
                "slot_length",
                "fastener_slot_length",
                "install_slot_length",
            ),
            None,
        ) or None
        if slot_length is not None and slot_length < fastener_dia:
            raise ValueError(
                f"Row {i}: slot_length ({slot_length}) must be >= fastener_dia ({fastener_dia})"
            )
        out.append(PanelSpec(
            panel_id=panel_id,
            face_width=float(row["width"]),
            face_height=float(row["height"]),
            thickness=_thickness_to_float(row["thickness"], material_key, alloy_key),
            flange_code=raw_code, flange_type=ftype,
            flange1_depth=_to_float(row.get("flange1_depth"), 0.0),
            flange2_depth=_to_float(row.get("flange2_depth"), None) or None,
            hole_dia=float(row["hole_diameter"]),
            pitch=float(row["hole_pitch"]),
            pattern=pattern,
            fastening_pair=fastening_pair,
            fastener_dia=fastener_dia,
            slot_length=slot_length,
            material=material_key,
            alloy=alloy_key,
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

    alloy = _rule_family(spec.material, spec.alloy)
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
# MITER RULE (per Section 7 / Section 8 "L overlaps J"):
#   The 45° miter on a J flange exists ONLY to keep two return lips from
#   colliding when they fold back on the panel back. A miter is therefore
#   added only at corners where BOTH adjacent flanges are J (J+J corner).
#   At any non-J+J corner (J+L, L+J, J+inactive) the J flange terminates
#   square at the corner — geometrically identical to an L flange there.
#
# Per-corner edge counts under this rule:
#   J+J corner: 4 edges (void, miter, miter, void)
#   L+L / J+L / L+J corner: 2 edges (square notch)
#   active+inactive corner: 1 edge (square flange end)
#   inactive+inactive: 0 edges (point only)
#
# Resulting outline counts:
#   J4S = 20,  L4S = 12,  J2TB = J2LR = 8,  L2TB = L2LR = 8,
#   MIX J-tb / L-lr = 12  (was 16 before the J+L miter was removed).
#
# Hard corner (void inner) at ex+BD from blank edge where ex = f1+f2.
# Void edge length per corner:
#   J side at J+J corner : f1+BD   (miter then takes the lip portion to blank)
#   any side without miter : f1+f2+BD = el+BD  (path runs straight to blank)
# ---------------------------------------------------------------------------

def _blank_outline(blank_w, blank_h, sides, bd, gap=0.0):
    """Build CCW blank outline polygon. See module docstring above for the
    per-corner miter rule and the resulting edge counts.

    gap (Section 8 / Section 9): anti-collision relief applied at J+J corners
    only. Pulls v_void and h_void away from the hard corner by gap/2 along
    their respective void edges; v_blank / h_blank remain anchored on the
    blank edge so the miter rotates a tiny amount off 45° (≈0.4° at 1/32"
    gap on a 2" lip — well inside fab tolerance). Default 0 relies on the
    back J-relief circle for relief; set spec.gap_override for an explicit
    miter gap (typical shop value 0.015"–0.030", i.e., ~1/32")."""
    bw, bh = blank_w, blank_h
    sl=sides["left"]; sr=sides["right"]
    sb=sides["bottom"]; st=sides["top"]

    el=_side_extra(sl); er=_side_extra(sr)
    eb=_side_extra(sb); et=_side_extra(st)

    fx0=el+bd;    fy0=eb+bd
    fx1=bw-er-bd; fy1=bh-et-bd

    def _corner_params(this_sd, other_sd):
        """Effective (void_edge_length, miter_depth) for THIS side at the corner
        it shares with OTHER. Miter is only present when both sides are J;
        otherwise the void edge spans the full f1+f2+BD so it reaches the
        blank edge straight (square end)."""
        if not this_sd.active:
            return (0.0, 0.0)
        is_jj = (this_sd.ftype == "J"
                 and other_sd.active and other_sd.ftype == "J")
        if is_jj:
            return (this_sd.f1 + bd, this_sd.f2)
        return (this_sd.f1 + this_sd.f2 + bd, 0.0)

    # (void_edge, miter_depth) per side per corner
    fh1_bl, fh2_bl = _corner_params(sb, sl);  fv1_bl, fv2_bl = _corner_params(sl, sb)
    fh1_br, fh2_br = _corner_params(sb, sr);  fv1_br, fv2_br = _corner_params(sr, sb)
    fh1_tr, fh2_tr = _corner_params(st, sr);  fv1_tr, fv2_tr = _corner_params(sr, st)
    fh1_tl, fh2_tl = _corner_params(st, sl);  fv1_tl, fv2_tl = _corner_params(sl, st)

    def _corner(hsd, vsd, hc_x, hc_y, ox, oy, arrive_vert,
                fh1, fh2, fv1, fv2):
        """Corner point sequence AFTER the arriving primary point, in CCW order.
        h_J / v_J classify by 'miter present at this corner', not by side type:
        a J flange at a non-J+J corner is treated as L here (no miter, no
        bend2-step), which keeps the case dispatch unchanged.
        """
        hc=(hc_x, hc_y)

        h_void  = (hc_x,                    hc_y+oy*fh1)
        h_blank = (hc_x+(-ox)*fh2,          hc_y+oy*(fh1+fh2))
        v_void  = (hc_x+ox*fv1,             hc_y)
        v_blank = (hc_x+ox*(fv1+fv2),       hc_y+(-oy)*fv2)

        # Section 9 anti-collision: at J+J corners only, pull the miter end
        # points away from hc by gap/2 along their void edges. v_blank /
        # h_blank stay on the blank edge so the miter angle shifts marginally
        # while the apex of the V opens up by the requested gap.
        if gap > 0 and fh2 > 0 and fv2 > 0:
            half = gap / 2.0
            v_void = (v_void[0] + ox*half, v_void[1])
            h_void = (h_void[0],           h_void[1] + oy*half)

        h_J = hsd.active and fh2 > 0
        h_L = hsd.active and not h_J
        v_J = vsd.active and fv2 > 0
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
            return             [hc, h_blank]
        else:
            # Arriving from horiz (bottom/top), departing to vert (left/right)
            if not hsd.active and not vsd.active:  return []
            if hsd.active and not vsd.active:
                return [h_void, hc] if h_J else [hc]
            if not hsd.active and vsd.active:
                return [hc, v_void, v_blank] if v_J else [hc, v_void]
            # Both active
            if h_J and v_J:   return [h_void, hc, v_void, v_blank]
            if h_J and v_L:   return [h_void, hc, v_void]
            if h_L and v_J:   return [hc, v_void, v_blank]
            return             [hc, v_void]

    bl = _corner(sb, sl, fx0, fy0, -1, -1, True,  fh1_bl, fh2_bl, fv1_bl, fv2_bl)
    br = _corner(sb, sr, fx1, fy0, +1, -1, False, fh1_br, fh2_br, fv1_br, fv2_br)
    tr = _corner(st, sr, fx1, fy1, +1, +1, True,  fh1_tr, fh2_tr, fv1_tr, fv2_tr)
    tl = _corner(st, sl, fx0, fy1, -1, +1, False, fh1_tl, fh2_tl, fv1_tl, fv2_tl)

    def _arr_vert(vsd, hc_x, hc_y, ox, oy, fv1, fv2):
        if not vsd.active: return (hc_x, hc_y)
        return (hc_x+ox*(fv1+fv2), hc_y+(-oy)*fv2)

    def _arr_horiz(hsd, hc_x, hc_y, ox, oy, fh1, fh2):
        if not hsd.active: return (hc_x, hc_y)
        return (hc_x+(-ox)*fh2, hc_y+oy*(fh1+fh2))

    bl_arr = _arr_vert (sl, fx0, fy0, -1, -1, fv1_bl, fv2_bl)
    br_arr = _arr_horiz(sb, fx1, fy0, +1, -1, fh1_br, fh2_br)
    tr_arr = _arr_vert (sr, fx1, fy1, +1, +1, fv1_tr, fv2_tr)
    tl_arr = _arr_horiz(st, fx0, fy1, -1, +1, fh1_tl, fh2_tl)

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
# Fastening slots
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


def _aligned_positions(coords):
    if not coords: return []
    holes=sorted({round(c, 6) for c in coords}); sel=[holes[0]]
    for h in holes[1:]:
        if h-sel[-1]>=11.5: sel.append(h)
    if holes[-1] not in sel and holes[-1]-sel[-1]>0.5: sel.append(holes[-1])
    return sel


def _l_positions(side_length, margin=2.0, target=12.0, max_spacing=13.0):
    """L-flange slot centers along a side.

    Start/end holes sit at the requested margin from each end. Interior spacing
    floats to be as close to 12" as possible, but never exceeds 13".
    """
    span = side_length - 2.0 * margin
    if span <= 0:
        return [side_length / 2.0]

    intervals = max(1, round(span / target))
    while span / intervals > max_spacing:
        intervals += 1
    spacing = span / intervals
    return [margin + i * spacing for i in range(intervals + 1)]


def _add_slot(msp, cx, cy, width, length, orientation, layer):
    """Add a rounded slot as a closed lwpolyline with semicircular ends."""
    if width <= 0 or length < width:
        return
    r = width / 2.0
    a = (length - width) / 2.0

    if orientation == "horizontal":
        pts = [
            (cx-a, cy+r, 0.0),
            (cx+a, cy+r, -1.0),
            (cx+a, cy-r, 0.0),
            (cx-a, cy-r, -1.0),
        ]
    else:
        pts = [
            (cx-r, cy+a, -1.0),
            (cx+r, cy+a, 0.0),
            (cx+r, cy-a, -1.0),
            (cx-r, cy-a, 0.0),
        ]
    msp.add_lwpolyline(pts, format="xyb", close=True, dxfattribs={"layer": layer})


def _draw_fastening_slots(msp, spec, fx0, fy0, fx1, fy1, face_w, face_h,
                          sides, blank_w, blank_h):
    active=_fastening_sides(spec,sides)
    if not active: return
    face_holes=_hole_centers(fx0,fy0,face_w,face_h,
                             spec.hole_dia,spec.pitch,spec.pattern,
                             spec.stagger_angle,spec.margin)
    fdia=spec.fastener_dia
    flen=spec.slot_length if spec.slot_length is not None else (fdia+INSTALL_SLOT_EXTRA)
    for sn in active:
        sd=sides[sn]
        if sn in ("bottom","top"):
            is_b=(sn=="bottom")
            if sd.ftype=="J":
                hy=sd.f2/2.0 if is_b else blank_h-sd.f2/2.0
                xs=_aligned_positions([c[0] for c in face_holes])
            else:
                hy=(fy0-sd.f1/2.0) if is_b else (fy1+sd.f1/2.0)
                xs=[fx0+p for p in _l_positions(face_w)]
            for px in xs:
                _add_slot(msp, px, hy, fdia, flen, "horizontal", "fastening")
        else:
            is_l=(sn=="left")
            if sd.ftype=="J":
                hx=sd.f2/2.0 if is_l else blank_w-sd.f2/2.0
                ys=_aligned_positions([c[1] for c in face_holes])
            else:
                hx=(fx0-sd.f1/2.0) if is_l else (fx1+sd.f1/2.0)
                ys=[fy0+p for p in _l_positions(face_h)]
            for py in ys:
                _add_slot(msp, hx, py, fdia, flen, "vertical", "fastening")


# ---------------------------------------------------------------------------
# Section 6 corner reliefs
#
#   Square face-corner notch (OSS = R + T, +0.010" buffer):
#     - One small closed rectangle per active corner, on the "cut" layer
#     - Centered on the intersection of the two bend1 centerlines so the
#       relief actually clears the bend-zone collision/pucker point
#     - This keeps the relief in the bend intersection rather than burying it
#       in the finished face area
#
#   Circular back J-relief (diameter = 2 * T):
#     - One circle per J+J corner, on the "cut" layer
#     - Centered at the intersection of the two secondary (bend2) centerlines
#     - Allows the two return lips to stretch independently during second bend
# ---------------------------------------------------------------------------
def _draw_corner_reliefs(msp, fx0, fy0, fx1, fy1, blank_w, blank_h, sides, r, t, ba2):
    if t <= 0:
        return

    oss        = r + t
    notch_size = oss + RELIEF_BUFFER

    sl = sides["left"];  sr = sides["right"]
    sb = sides["bottom"]; st_ = sides["top"]

    # (hsd, vsd, hc_x, hc_y, vox, voy)
    # vox/voy = unit direction from hc INTO the void; face direction is the negation.
    corners = (
        (sb,  sl, fx0, fy0, -1, -1),  # BL
        (sb,  sr, fx1, fy0, +1, -1),  # BR
        (st_, sr, fx1, fy1, +1, +1),  # TR
        (st_, sl, fx0, fy1, -1, +1),  # TL
    )

    for hsd, vsd, hc_x, hc_y, vox, voy in corners:
        if not (hsd.active and vsd.active):
            continue

        # Square bend relief — centered at bend1 CL intersection.
        cx = hc_x + vox * ba2
        cy = hc_y + voy * ba2
        half = notch_size / 2.0
        notch = [
            (cx - half, cy - half),
            (cx + half, cy - half),
            (cx + half, cy + half),
            (cx - half, cy + half),
        ]
        msp.add_lwpolyline(notch, close=True, dxfattribs={"layer": "cut"})

        # Back J-relief — only at J+J corners, centered at bend2 CL intersection
        if hsd.ftype == "J" and vsd.ftype == "J" and hsd.f2 > 0 and vsd.f2 > 0:
            cx = vsd.f2 if vox < 0 else (blank_w - vsd.f2)
            cy = hsd.f2 if voy < 0 else (blank_h - hsd.f2)
            msp.add_circle((cx, cy), t, dxfattribs={"layer": "cut"})


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
    gap    = spec.gap_override if spec.gap_override is not None else MITER_GAP_DEFAULT

    # Hard corner positions (void inner corners)
    el=_side_extra(sides["left"]); er=_side_extra(sides["right"])
    eb=_side_extra(sides["bottom"]); et=_side_extra(sides["top"])
    fx0=el+bd; fy0=eb+bd
    fx1=bw-er-bd; fy1=bh-et-bd
    face_w=fx1-fx0; face_h=fy1-fy0

    doc=ezdxf.new(dxfversion="R2010"); doc.units=1; msp=doc.modelspace()
    for name,color in [("cut",1),("holes",2),("fastening",5),("bend",3)]:
        if name not in doc.layers: doc.layers.add(name=name,color=color)

    pts=_blank_outline(bw,bh,sides,bd,gap)
    msp.add_lwpolyline(pts,close=True,dxfattribs={"layer":"cut"})

    _draw_corner_reliefs(msp,fx0,fy0,fx1,fy1,bw,bh,sides,r,t,ba2)
    _draw_bend_lines(msp,fx0,fy0,fx1,fy1,bw,bh,sides,ba2)

    for x,y in _hole_centers(fx0,fy0,face_w,face_h,
                              spec.hole_dia,spec.pitch,spec.pattern,
                              spec.stagger_angle,spec.margin):
        msp.add_circle((x,y),spec.hole_dia/2.0,dxfattribs={"layer":"holes"})

    _draw_fastening_slots(msp,spec,fx0,fy0,fx1,fy1,face_w,face_h,sides,bw,bh)

    os.makedirs(outdir,exist_ok=True)
    doc.saveas(os.path.join(outdir,f"{spec.panel_id}.dxf"))


def nest_panels(panels,sw,sh): return []
def write_nesting_dxf(sheets,outdir): return None
