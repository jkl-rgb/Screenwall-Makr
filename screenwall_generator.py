from __future__ import annotations
import csv, math, os
from dataclasses import dataclass
from typing import Optional
import ezdxf

# ---------------------------------------------------------------------------
# Bend math verified against Fusion 360 (0.1875" 3003, r=0.125, k=0.33):
#   BA  = (pi/2)*(r+k*t) = 0.294"  BA/2 = 0.147" (bend CL offset from HC) ✓
#   BD  = 2*(r+t) - BA   = 0.331"
#   J f1 flat = nominal_f1 - 3*BD/2 = 1.503" → ex=f1+f2=3.587 → blank=31.174 ✓
#   L f1 flat = F1 - OSS + BA (§8 L_total); not nominal - BD/2 (that is short by BA/2).
#   f2  = nominal_f2 - BD/2   = 2.084" (blank edge to bend2 CL) ✓
#   Hard corner at ex+BD = 3.918" from blank edge
#   HC-HC = 31.174-2*3.918 = 23.338" ≈ Fusion 23.36" ✓
#   bend1 CL: F1 inset `_f1_bend_inset_from_face` (−0.168″ vs outward along flat @ ref).
#   bend2 CL: face + developed f1 (1.497″ @ 2″ F1 ref). L/J developed flats per
#   SHOP_FLAT_CALIBRATION_REF (L 1.665″/2″ F1, J lip 1.582″/1.75″ F2 at ref).
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
    ("5052", 0.1875): {"r": 0.0625, "k": 0.32},  # shop tight punch + K32; aligns SHOP_FLAT_CALIBRATION_REF
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

# ---------------------------------------------------------------------------
# Shop flat artwork (finished face + perimeter inset) vs bend-line theory
#
# Shop datum: CSV `face_width` × `face_height` = nominal finished face at developed
# f1+f2 runouts (before BD corner arc). HC for blank outline is inset ±BD from that.
# DXF `finished_face` is that nominal loop; perimeter `cut` is the detailed blank.
# `SHOP_FLANGE_CORNER_INSET` (0.195″) = distance from nominal finished **face corner**
# to the **inside leg edge** of the flanges at that corner (perimeter cut datum); not
# a relief square. Scale from the single shop reference until per-gauge coupons exist.
# `SHOP_F1_BEND_INSET_REF` (0.168″) = F1 bend-1 CL **inset** from finished face (toward
# panel interior, opposite +outward along the flat). Bend-2 CL = face + developed f1
# outward (e.g. 1.497″ @ ref F1=2″). Other (r,k,t) blend inset via Δ(BA/2+0.027″) vs ref.
# Developed flat calibration: L outer 1.665″ / 2″ F1; J 1.497″ + 1.582″ @ 2″ / 1.75″ F2.
# Calibration coupon: 5052, 0.1875″, tight punch / shop practice; reference
# bend model for the *anchor* theory term uses r=0.0625″, k=0.32 (K32) at
# ref_t so that other alloys/gauges inherit deltas from MATERIAL_TABLE r,k,t.
#
# L: F1_od = 2.000″ → developed flat to L outer ≈ 1.665″ (shop auto at ref).
# J: F1_od = 2.000″, F2_od = 1.750″ → face→bend2 ≈ 1.497″, bend2→outer ≈ 1.582″ at ref.
#
# When spec.shop_flat_mode == "auto", developed f1/f2 use:
#   ratio = shop_ratio_ref + (theory(F_ref,r,k,t)/F_ref − theory(F_ref,rr,kr,tr)/F_ref)
# so at the anchor (rr,kr,tr) the shop numbers are recovered; other materials
# shift with the same relative change in bend theory.
# ---------------------------------------------------------------------------
SHOP_FLANGE_CORNER_INSET = 0.195  # face corner → inside flange leg edge (cut datum); scale from ref
SHOP_FINISHED_FACE_INSET = SHOP_FLANGE_CORNER_INSET  # legacy name (same inches)
L_BEND_CL_OUTWARD = 0.027  # used only in _f1_bend_inset_theory() for non-ref bend delta
SHOP_F1_BEND_INSET_REF = 0.168  # F1 bend-1 CL inset from finished face (toward interior) @ ref (r,k,t)
# Shown in Streamlit so you can confirm the running app loaded this tree (not an older copy).
GENERATOR_ARTWORK_TAG = "shop-f1inset168-L1665-J1497-1582"

SHOP_FLAT_CALIBRATION_REF = {
    "ref_r": 0.0625,
    "ref_k": 0.32,
    "ref_t": 0.1875,
    "ref_f1_od": 2.0,
    "ref_f2_od": 1.75,
    # Shop 5052 / 0.1875: face → L outer = 1.665″ @ F1=2″; J face→bend2 CL = 1.497″ @ F1=2″;
    # bend2 CL → J outer = 1.582″ @ F2=1.75″ (upper-right corner artwork).
    "L_flat_over_f1_od": 1.665 / 2.0,
    "J_f1_flat_over_f1_od": 1.497 / 2.0,
    "J_f2_flat_over_f2_od": 1.582 / 1.75,
}

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
FLANGE_CODES = {
    "L4S", "J4S", "L2TB", "J2TB", "L2LR", "J2LR", "MIX",
    "RT4S", "RT4J",
}
FASTENING_PAIR_VALUES = {"all", "standard", "tb", "lr", "t", "b", "l", "r", "none"}

STICK_TEXT_HEIGHT = 0.5
STICK_CHAR_WIDTH = 0.6
STICK_CHAR_ADVANCE = 0.75
STICK_LINE_SPACING = 0.18

# Panel ID must clear install slots (e.g. 2" margin + ~0.75" slot from flange end).
PANEL_ID_CLEAR_FROM_FLANGE_END = 4.0  # inches along flange from start (fx0 / fy0)
PANEL_ID_CLEAR_PAST_SLOT = 0.125    # past slot end along flange before text center

STICK_FONT = {
    "A": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0), (0.6, 0.0)], [(0.0, 0.5), (0.6, 0.5)]],
    "B": [[(0.0, 0.0), (0.0, 1.0), (0.5, 1.0), (0.6, 0.9), (0.6, 0.6), (0.5, 0.5), (0.0, 0.5)],
          [(0.0, 0.5), (0.5, 0.5), (0.6, 0.4), (0.6, 0.1), (0.5, 0.0), (0.0, 0.0)]],
    "C": [[(0.6, 1.0), (0.0, 1.0), (0.0, 0.0), (0.6, 0.0)]],
    "D": [[(0.0, 0.0), (0.0, 1.0), (0.45, 1.0), (0.6, 0.85), (0.6, 0.15), (0.45, 0.0), (0.0, 0.0)]],
    "E": [[(0.6, 1.0), (0.0, 1.0), (0.0, 0.0), (0.6, 0.0)], [(0.0, 0.5), (0.45, 0.5)]],
    "F": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0)], [(0.0, 0.5), (0.45, 0.5)]],
    "G": [[(0.6, 1.0), (0.0, 1.0), (0.0, 0.0), (0.6, 0.0), (0.6, 0.5), (0.35, 0.5)]],
    "H": [[(0.0, 0.0), (0.0, 1.0)], [(0.6, 0.0), (0.6, 1.0)], [(0.0, 0.5), (0.6, 0.5)]],
    "I": [[(0.0, 1.0), (0.6, 1.0)], [(0.3, 1.0), (0.3, 0.0)], [(0.0, 0.0), (0.6, 0.0)]],
    "J": [[(0.0, 1.0), (0.6, 1.0), (0.6, 0.15), (0.45, 0.0), (0.15, 0.0), (0.0, 0.15)]],
    "K": [[(0.0, 0.0), (0.0, 1.0)], [(0.6, 1.0), (0.0, 0.45), (0.6, 0.0)]],
    "L": [[(0.0, 1.0), (0.0, 0.0), (0.6, 0.0)]],
    "M": [[(0.0, 0.0), (0.0, 1.0), (0.3, 0.55), (0.6, 1.0), (0.6, 0.0)]],
    "N": [[(0.0, 0.0), (0.0, 1.0), (0.6, 0.0), (0.6, 1.0)]],
    "O": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0), (0.6, 0.0), (0.0, 0.0)]],
    "P": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0), (0.6, 0.5), (0.0, 0.5)]],
    "Q": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0), (0.6, 0.0), (0.0, 0.0)], [(0.35, 0.25), (0.65, -0.05)]],
    "R": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0), (0.6, 0.5), (0.0, 0.5)], [(0.0, 0.5), (0.6, 0.0)]],
    "S": [[(0.6, 1.0), (0.0, 1.0), (0.0, 0.5), (0.6, 0.5), (0.6, 0.0), (0.0, 0.0)]],
    "T": [[(0.0, 1.0), (0.6, 1.0)], [(0.3, 1.0), (0.3, 0.0)]],
    "U": [[(0.0, 1.0), (0.0, 0.0), (0.6, 0.0), (0.6, 1.0)]],
    "V": [[(0.0, 1.0), (0.3, 0.0), (0.6, 1.0)]],
    "W": [[(0.0, 1.0), (0.15, 0.0), (0.3, 0.45), (0.45, 0.0), (0.6, 1.0)]],
    "X": [[(0.0, 1.0), (0.6, 0.0)], [(0.0, 0.0), (0.6, 1.0)]],
    "Y": [[(0.0, 1.0), (0.3, 0.5), (0.6, 1.0)], [(0.3, 0.5), (0.3, 0.0)]],
    "Z": [[(0.0, 1.0), (0.6, 1.0), (0.0, 0.0), (0.6, 0.0)]],
    "0": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0), (0.6, 0.0), (0.0, 0.0)], [(0.0, 0.0), (0.6, 1.0)]],
    "1": [[(0.3, 0.0), (0.3, 1.0)], [(0.15, 0.8), (0.3, 1.0), (0.45, 1.0)], [(0.1, 0.0), (0.5, 0.0)]],
    "2": [[(0.0, 0.8), (0.15, 1.0), (0.45, 1.0), (0.6, 0.8), (0.6, 0.6), (0.0, 0.0), (0.6, 0.0)]],
    "3": [[(0.0, 1.0), (0.6, 1.0), (0.35, 0.5), (0.6, 0.5), (0.6, 0.0), (0.0, 0.0)]],
    "4": [[(0.6, 0.0), (0.6, 1.0)], [(0.0, 0.55), (0.6, 0.55)], [(0.0, 0.55), (0.45, 1.0)]],
    "5": [[(0.6, 1.0), (0.0, 1.0), (0.0, 0.5), (0.5, 0.5), (0.6, 0.4), (0.6, 0.0), (0.0, 0.0)]],
    "6": [[(0.6, 1.0), (0.1, 1.0), (0.0, 0.85), (0.0, 0.0), (0.6, 0.0), (0.6, 0.5), (0.0, 0.5)]],
    "7": [[(0.0, 1.0), (0.6, 1.0), (0.2, 0.0)]],
    "8": [[(0.0, 0.0), (0.0, 1.0), (0.6, 1.0), (0.6, 0.0), (0.0, 0.0)], [(0.0, 0.5), (0.6, 0.5)]],
    "9": [[(0.6, 0.5), (0.0, 0.5), (0.0, 1.0), (0.6, 1.0), (0.6, 0.0), (0.1, 0.0)]],
    "_": [[(0.0, 0.0), (0.6, 0.0)]],
    "-": [[(0.1, 0.5), (0.5, 0.5)]],
    " ": [],
}


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
    # Right trapezoid (RT4S / RT4J): parallel top or bottom with 90° leg corners;
    # opposing edge is angled. Leg lengths are vertical spans from the straight
    # parallel HC line to the angled HC corners at left / right.
    rt_opposing_edge: Optional[str] = None  # "top" | "bottom"
    rt_leg_left: Optional[float] = None
    rt_leg_right: Optional[float] = None
    # "off" = pure bend-line theory (_flat_leg_L / _flat_leg_J / _flat_lip).
    # "auto" = shop artwork cut-line flats blended with theory across materials.
    shop_flat_mode: str = "auto"


def _to_float(v, default=0.0):
    if v in (None, ""):
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def _to_optional_float(v):
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


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
    with open(path, "rb") as f:
        raw_bytes = f.read()
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
        rt_edge = (row.get("rt_opposing_edge") or "").strip().lower()
        rt_ll = _to_optional_float(row.get("rt_leg_left"))
        rt_lr = _to_optional_float(row.get("rt_leg_right"))
        if raw_code in ("RT4S", "RT4J"):
            if rt_edge not in ("top", "bottom"):
                raise ValueError(
                    f"Row {i}: rt_opposing_edge is required for {raw_code} (top or bottom)."
                )
            if rt_ll is None or rt_lr is None or rt_ll <= 0 or rt_lr <= 0:
                raise ValueError(
                    f"Row {i}: rt_leg_left and rt_leg_right must be positive for {raw_code}."
                )
        elif rt_edge or rt_ll is not None or rt_lr is not None:
            raise ValueError(
                f"Row {i}: rt_* fields are only valid for RT4S/RT4J (got flange_code={raw_code})."
            )
        pattern = (row.get("pattern") or "").strip().lower()
        if pattern not in {"straight", "staggered"}:
            raise ValueError(f"Row {i}: pattern must be straight or staggered")
        ftype = "J" if (raw_code.startswith("J") or raw_code == "RT4J") else "L"
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
        shop_flat_mode = (row.get("shop_flat_mode") or "auto").strip().lower()
        if shop_flat_mode not in ("off", "auto"):
            raise ValueError(
                f"Row {i}: shop_flat_mode must be off or auto (got {shop_flat_mode!r})."
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
        face_w = float(row["width"])
        face_h = float(row["height"])
        if raw_code in ("RT4S", "RT4J"):
            face_h = max(face_h, float(rt_ll), float(rt_lr))

        out.append(PanelSpec(
            panel_id=panel_id,
            face_width=face_w,
            face_height=face_h,
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
            rt_opposing_edge=rt_edge or None,
            rt_leg_left=rt_ll,
            rt_leg_right=rt_lr,
            shop_flat_mode=shop_flat_mode,
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


def _f1_bend_inset_theory(r: float, k: float, t: float) -> float:
    """Legacy bend-1 offset from nominal face (BA/2 + 0.027″) for blending vs shop ref."""
    return _ba_half(r, k, t) + L_BEND_CL_OUTWARD


def _f1_bend_inset_from_face(spec: PanelSpec, rules: dict) -> float:
    """Positive distance: F1 bend-1 CL is INSET from nominal finished-face edge (toward
    panel interior / opening), not toward the blank outer.

    Shop reference 0.168″ at SHOP_FLAT_CALIBRATION_REF (r,k,t); other materials shift by
    Δ(_f1_bend_inset_theory) vs that reference (same scaling as before; only placement
    direction in _bend1_cl_positions uses inset sign per side).
    """
    ref = SHOP_FLAT_CALIBRATION_REF
    rr, kr, tr = ref["ref_r"], ref["ref_k"], ref["ref_t"]
    r, k, t = rules["r"], rules["k"], spec.thickness
    return SHOP_F1_BEND_INSET_REF + (
        _f1_bend_inset_theory(r, k, t) - _f1_bend_inset_theory(rr, kr, tr)
    )


def _flat_lip(nominal, r, k, t):
    """f2: blank edge to bend2 CL = nominal - BD/2."""
    return max(nominal - _bd(r,k,t)/2.0, 0.0)


def _flat_leg_J(nominal, r, k, t):
    """f1: used for blank size = nominal - 3*BD/2."""
    return max(nominal - 3.0*_bd(r,k,t)/2.0, 0.0)


def _flat_leg_L(nominal, r, k, t):
    """Developed single-bend leg (face O.D. to blank edge), §8: F1 - OSS + BA.

    Equivalent to nominal - BD/2 + BA/2; the older nominal - BD/2 form was
    short by BA/2 (~0.147\" here) vs Fusion / bend-tangent layout."""
    return max(nominal - (r + t) + 2.0 * _ba_half(r, k, t), 0.0)


def _shop_flat_mode(spec: PanelSpec) -> str:
    return (getattr(spec, "shop_flat_mode", "auto") or "auto").strip().lower()


def _shop_blend_ratio(
    shop_ratio_at_ref: float,
    F_ref: float,
    theory_flat_fn,
    r: float,
    k: float,
    t: float,
) -> float:
    """Blend shop cut-line / nominal ratio with relative bend-theory change vs ref (r,k,t)."""
    ref = SHOP_FLAT_CALIBRATION_REF
    rr, kr, tr = ref["ref_r"], ref["ref_k"], ref["ref_t"]
    den = F_ref if abs(F_ref) > 1e-12 else 1.0
    t_ref = theory_flat_fn(F_ref, rr, kr, tr) / den
    t_now = theory_flat_fn(F_ref, r, k, t) / den
    return shop_ratio_at_ref + (t_now - t_ref)


def _developed_leg_L(spec: PanelSpec, F1_nom: float, rules: dict) -> float:
    if _shop_flat_mode(spec) != "auto":
        return _flat_leg_L(F1_nom, rules["r"], rules["k"], spec.thickness)
    ref = SHOP_FLAT_CALIBRATION_REF
    rat = _shop_blend_ratio(
        ref["L_flat_over_f1_od"],
        ref["ref_f1_od"],
        _flat_leg_L,
        rules["r"],
        rules["k"],
        spec.thickness,
    )
    return max(F1_nom * rat, 0.0)


def _developed_leg_J(spec: PanelSpec, F1_nom: float, rules: dict) -> float:
    if _shop_flat_mode(spec) != "auto":
        return _flat_leg_J(F1_nom, rules["r"], rules["k"], spec.thickness)
    ref = SHOP_FLAT_CALIBRATION_REF
    rat = _shop_blend_ratio(
        ref["J_f1_flat_over_f1_od"],
        ref["ref_f1_od"],
        _flat_leg_J,
        rules["r"],
        rules["k"],
        spec.thickness,
    )
    return max(F1_nom * rat, 0.0)


def _developed_lip_J(spec: PanelSpec, F2_nom: float, rules: dict) -> float:
    if F2_nom <= 0:
        return 0.0
    if _shop_flat_mode(spec) != "auto":
        return _flat_lip(F2_nom, rules["r"], rules["k"], spec.thickness)
    ref = SHOP_FLAT_CALIBRATION_REF
    rat = _shop_blend_ratio(
        ref["J_f2_flat_over_f2_od"],
        ref["ref_f2_od"],
        _flat_lip,
        rules["r"],
        rules["k"],
        spec.thickness,
    )
    return max(F2_nom * rat, 0.0)


def _is_right_trapezoid(spec: PanelSpec) -> bool:
    return spec.flange_code in ("RT4S", "RT4J")


# ---------------------------------------------------------------------------
# Right trapezoid (RT4S / RT4J) — vector helpers and offset outline
# ---------------------------------------------------------------------------
def _v2_add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _v2_sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _v2_scale(a, s):
    return (a[0] * s, a[1] * s)


def _v2_dot(a, b):
    return a[0] * b[0] + a[1] * b[1]


def _v2_len(a):
    return math.hypot(a[0], a[1])


def _v2_norm(a):
    L = _v2_len(a)
    if L < 1e-12:
        return (1.0, 0.0)
    return (a[0] / L, a[1] / L)


def _v2_neg(a):
    return (-a[0], -a[1])


def _perp_outward_ccw(p0, p1):
    """Unit outward normal for a CCW polygon edge from p0 → p1."""
    d = _v2_norm(_v2_sub(p1, p0))
    return (d[1], -d[0])


def _line_intersect_inf(p, d, q, e):
    """Infinite-line intersection: p + t d = q + u e."""
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-12:
        return None
    qmp = _v2_sub(q, p)
    t = (qmp[0] * e[1] - qmp[1] * e[0]) / den
    return _v2_add(p, _v2_scale(d, t))


def _offset_polygon_miter(pts_ccw, dists):
    """Parallel offset of convex polygon (CCW). dists[i] offsets edge i (pts[i]→pts[i+1])."""
    n = len(pts_ccw)
    lines = []
    for i in range(n):
        p0 = pts_ccw[i]
        p1 = pts_ccw[(i + 1) % n]
        out_n = _perp_outward_ccw(p0, p1)
        off = dists[i]
        d = _v2_norm(_v2_sub(p1, p0))
        q = _v2_add(p0, _v2_scale(out_n, off))
        lines.append((q, d))
    out_pts = []
    for i in range(n):
        p0, d0 = lines[i]
        p1, d1 = lines[(i + 1) % n]
        inter = _line_intersect_inf(p0, d0, p1, d1)
        if inter is None:
            inter = pts_ccw[(i + 1) % n]
        out_pts.append(inter)
    return out_pts


def _poly_bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _polygon_signed_area(pts):
    """Positive for CCW vertices (y up)."""
    s = 0.0
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        s += x0 * y1 - x1 * y0
    return s * 0.5


def _nominal_finished_face_rect_xy(bw: float, bh: float, sides) -> tuple[float, float, float, float]:
    """CSV `face_width` × `face_height`: aperture at f1+f2 runouts (before BD corner arc)."""
    el = _side_extra(sides["left"])
    er = _side_extra(sides["right"])
    eb = _side_extra(sides["bottom"])
    et = _side_extra(sides["top"])
    return el, eb, bw - er, bh - et


def _finished_face_quad_ccw_ortho(bw: float, bh: float, sides):
    """Four CCW corners of the nominal finished face (matches CSV width/height)."""
    ffx0, ffy0, ffx1, ffy1 = _nominal_finished_face_rect_xy(bw, bh, sides)
    return [(ffx0, ffy0), (ffx1, ffy0), (ffx1, ffy1), (ffx0, ffy1)]


def _rt_nominal_face_corners(bl, br, tr, tl, sides, bd: float):
    """RT nominal face corners: shift each HC toward blank outer by BD on active flange legs."""
    dxl = bd if sides["left"].active else 0.0
    dxr = bd if sides["right"].active else 0.0
    dyb = bd if sides["bottom"].active else 0.0
    dyt = bd if sides["top"].active else 0.0
    nbl = (bl[0] - dxl, bl[1] - dyb)
    nbr = (br[0] + dxr, br[1] - dyb)
    ntr = (tr[0] + dxr, tr[1] + dyt)
    ntl = (tl[0] - dxl, tl[1] + dyt)
    return nbl, nbr, ntr, ntl


def _ff_edge_rt_dict(spec, lay, sides, ffc):
    """Per-side finished-face references for RT slots / panel ID (replaces bend1_rt_dict)."""
    bl, br, tr, tl = lay["bl"], lay["br"], lay["tr"], lay["tl"]
    ff_bl, ff_br, ff_tr, ff_tl = ffc
    fx0, fy0 = bl[0], bl[1]
    fx1 = br[0]
    mx = (fx0 + fx1) * 0.5
    pos = {}
    t_top = _v2_norm(_v2_sub(ff_tr, ff_tl))
    t_bot = _v2_norm(_v2_sub(ff_br, ff_bl))

    def y_on_ray(p0, t, xq):
        if abs(t[0]) < 1e-12:
            return p0[1]
        s = (xq - p0[0]) / t[0]
        return p0[1] + s * t[1]

    if sides["bottom"].active:
        if spec.rt_opposing_edge == "top":
            pos["bottom"] = ff_bl[1]
        else:
            pos["bottom"] = y_on_ray(ff_bl, t_bot, mx)
    if sides["top"].active:
        if spec.rt_opposing_edge == "top":
            pos["top"] = y_on_ray(ff_tl, t_top, mx)
        else:
            pos["top"] = max(ff_tr[1], ff_tl[1])
    if sides["left"].active:
        pos["left"] = ff_bl[0]
    if sides["right"].active:
        pos["right"] = ff_br[0]
    return pos


def _translate_poly(pts, dx, dy):
    return [(p[0] + dx, p[1] + dy) for p in pts]


def _rt_legs(spec: PanelSpec) -> tuple[float, float]:
    return float(spec.rt_leg_left or 0.0), float(spec.rt_leg_right or 0.0)


def _rt_hc_corners_raw(spec: PanelSpec, fx0: float, fx1: float, eb_bd: float) -> tuple:
    """BL, BR, TR, TL in CCW order before blank translation (y measured up from blank bottom)."""
    hL, hR = _rt_legs(spec)
    if spec.rt_opposing_edge == "top":
        yb = eb_bd
        return (
            (fx0, yb),
            (fx1, yb),
            (fx1, yb + hR),
            (fx0, yb + hL),
        )
    ytop = eb_bd + max(hL, hR)
    return (
        (fx0, ytop - hL),
        (fx1, ytop - hR),
        (fx1, ytop),
        (fx0, ytop),
    )


def _rt_face_centroid(bl, br, tr, tl):
    return (
        (bl[0] + br[0] + tr[0] + tl[0]) / 4.0,
        (bl[1] + br[1] + tr[1] + tl[1]) / 4.0,
    )


def _inward_normal_edge(p0, p1, centroid):
    """Unit normal from edge p0→p1 pointing into the polygon (toward centroid)."""
    mid = _v2_scale(_v2_add(p0, p1), 0.5)
    tin = _v2_sub(centroid, mid)
    return _v2_norm(tin)


def _rt_blank_layout(spec: PanelSpec, sides, bd: float):
    """HC corners, outer offset polygon, translation to origin, bw, bh."""
    el = _side_extra(sides["left"])
    er = _side_extra(sides["right"])
    eb = _side_extra(sides["bottom"])
    et = _side_extra(sides["top"])
    W = spec.face_width
    bw = W + el + er + 2.0 * bd
    fx0 = el + bd
    fx1 = bw - er - bd
    eb_bd = eb + bd
    bl, br, tr, tl = _rt_hc_corners_raw(spec, fx0, fx1, eb_bd)
    dists = [eb + bd, er + bd, et + bd, el + bd]
    outer = _offset_polygon_miter([bl, br, tr, tl], dists)
    minx, miny, maxx, maxy = _poly_bbox(outer)
    dx, dy = -minx, -miny
    bl = _v2_add(bl, (dx, dy))
    br = _v2_add(br, (dx, dy))
    tr = _v2_add(tr, (dx, dy))
    tl = _v2_add(tl, (dx, dy))
    outer = _translate_poly(outer, dx, dy)
    minx, miny, maxx, maxy = _poly_bbox(outer)
    bh = maxy - miny
    return {
        "bw": bw,
        "bh": bh,
        "fx0": bl[0],
        "fy0": bl[1],
        "fx1": br[0],
        "bl": bl,
        "br": br,
        "tr": tr,
        "tl": tl,
        "outer": outer,
    }


def _rt_flat_size(spec, sides, rules):
    r, k, t = rules["r"], rules["k"], spec.thickness
    bd = _bd(r, k, t)
    lay = _rt_blank_layout(spec, sides, bd)
    return lay["bw"], lay["bh"]


def _point_in_convex_quad(px, py, bl, br, tr, tl):
    poly = (bl, br, tr, tl)
    sgn = None
    for i in range(4):
        a = poly[i]
        b = poly[(i + 1) % 4]
        ex = b[0] - a[0]
        ey = b[1] - a[1]
        vx = px - a[0]
        vy = py - a[1]
        c = ex * vy - ey * vx
        if abs(c) < 1e-9:
            continue
        if sgn is None:
            sgn = c > 0
        elif (c > 0) != sgn:
            return False
    return True


def _hole_centers_rt(fx0, fx1, fbl, fbr, ftr, ftl,
                     hole_dia, pitch, pattern, stagger_angle, margin):
    """Hole grid in the axis-aligned bbox of the finished-face quad, filtered to that quad."""
    min_y = min(fbl[1], fbr[1], ftr[1], ftl[1])
    max_y = max(fbl[1], fbr[1], ftr[1], ftl[1])
    raw = _hole_centers(
        fx0, min_y, fx1 - fx0, max_y - min_y,
        hole_dia, pitch, pattern, stagger_angle, margin,
    )
    out = [(x, y) for x, y in raw if _point_in_convex_quad(x, y, fbl, fbr, ftr, ftl)]
    if not out and raw:
        out = [raw[0]]
    return out


# ---------------------------------------------------------------------------
# Resolve sides
# ---------------------------------------------------------------------------
def resolve_sides(spec):
    rules = get_rules(spec)
    k, r, t = rules["k"], rules["r"], spec.thickness

    def J(f1n, f2n):
        return SideDef(
            True,
            "J",
            _developed_leg_J(spec, f1n, rules),
            _developed_lip_J(spec, f2n, rules),
        )

    def L(f1n):
        return SideDef(True, "L", _developed_leg_L(spec, f1n, rules), 0.0)

    def OFF():
        return SideDef(False, "L", 0.0, 0.0)

    c = spec.flange_code
    f1 = spec.flange1_depth
    f2 = spec.flange2_depth or 0.0

    if c == "L4S":
        sd = L(f1)
        return {s: sd for s in ("top", "bottom", "left", "right")}
    if c == "J4S":
        sd = J(f1, f2)
        return {s: sd for s in ("top", "bottom", "left", "right")}
    if c == "RT4S":
        sd = L(f1)
        return {s: sd for s in ("top", "bottom", "left", "right")}
    if c == "RT4J":
        sd = J(f1, f2)
        return {s: sd for s in ("top", "bottom", "left", "right")}
    if c == "L2TB":
        return {"top": L(f1), "bottom": L(f1), "left": OFF(), "right": OFF()}
    if c == "J2TB":
        return {"top": J(f1, f2), "bottom": J(f1, f2), "left": OFF(), "right": OFF()}
    if c == "L2LR":
        return {"top": OFF(), "bottom": OFF(), "left": L(f1), "right": L(f1)}
    if c == "J2LR":
        return {"top": OFF(), "bottom": OFF(), "left": J(f1, f2), "right": J(f1, f2)}

    def _mix(active, ft, n1, n2):
        if not active:
            return OFF()
        return J(n1, n2) if ft == "J" else L(n1)

    return {
        "top": _mix(spec.top_f1 > 0, spec.top_type, spec.top_f1, spec.top_f2),
        "bottom": _mix(spec.bottom_f1 > 0, spec.bottom_type, spec.bottom_f1, spec.bottom_f2),
        "left": _mix(spec.left_f1 > 0, spec.left_type, spec.left_f1, spec.left_f2),
        "right": _mix(spec.right_f1 > 0, spec.right_type, spec.right_f1, spec.right_f2),
    }


def _side_extra(sd):
    """f1+f2 per side - used for blank size calculation."""
    return (sd.f1 + sd.f2) if sd.active else 0.0


def flat_size(spec):
    """Blank = face + 2*(f1+f2) per axis. RT uses miter offset of HC trapezoid."""
    sides = resolve_sides(spec)
    if _is_right_trapezoid(spec):
        return _rt_flat_size(spec, sides, get_rules(spec))
    return (
        spec.face_width + _side_extra(sides["left"]) + _side_extra(sides["right"]),
        spec.face_height + _side_extra(sides["top"]) + _side_extra(sides["bottom"]),
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
# Per-corner edge counts under this rule (abstract model; DXF passes notch_size=0,
# so the cut polyline does not add the former 2×T / five-point square detours):
#   J+J corner: 4 edges (void, miter, miter, void)
#   L+L / J+L / L+J corner: 2 edges (void segments to HC, no embedded relief square)
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

def _blank_outline(blank_w, blank_h, sides, bd, ba2, notch_size, spec, rules, gap=0.0):
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
    bend1 = _bend1_cl_positions(fx0, fy0, fx1, fy1, blank_w, blank_h, sides, ba2, bd, spec, rules)

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
                fh1, fh2, fv1, fv2, b1h, b1v):
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

        notch_path = None
        if hsd.active and vsd.active and notch_size > 1e-9:
            half = notch_size / 2.0
            x0, x1 = b1v - half, b1v + half
            y0, y1 = b1h - half, b1h + half
            flange_x = x0 if ox < 0 else x1
            if hsd.ftype == "J" and vsd.ftype == "J":
                # J+J: keep the verified five-point walk around the relief square.
                face_x = x1 if ox < 0 else x0
                flange_y = y0 if oy < 0 else y1
                face_y = y1 if oy < 0 else y0
                notch_path = [
                    (flange_x, hc_y),
                    (flange_x, face_y),
                    (face_x, face_y),
                    (face_x, flange_y),
                    (hc_x, flange_y),
                ]
            else:
                # L+L, J+L, L+J: bend relief from the two bend-1 centerlines.
                # Center a square (notch_size, typically 2*T = 0.375") on the
                # intersection C of those lines (outside the void corner). Keep
                # only the two square edges in the quadrant that opens toward the
                # hard corner, then bridge along the inner face line to hc so the
                # flange outline meets the hard corner without a BD-wide gap.
                cx, cy = b1v, b1h
                dx = hc_x - cx
                dy = hc_y - cy
                eps = 1e-9
                if dx > eps and dy > eps:
                    q = [(cx - half, cy + half), (cx + half, cy + half), (cx + half, cy - half)]
                    bridge = (hc_x, cy - half)
                elif dx > eps and dy < -eps:
                    q = [(cx - half, cy - half), (cx + half, cy - half), (cx + half, cy + half)]
                    bridge = (hc_x, cy + half)
                elif dx < -eps and dy > eps:
                    q = [(cx + half, cy + half), (cx - half, cy + half), (cx - half, cy - half)]
                    bridge = (hc_x, cy - half)
                else:
                    q = [(cx + half, cy - half), (cx - half, cy - half), (cx - half, cy + half)]
                    bridge = (hc_x, cy + half)

                entry = (flange_x, hc_y)
                notch_path = [entry]
                if abs(entry[0] - q[0][0]) > 1e-9 or abs(entry[1] - q[0][1]) > 1e-9:
                    notch_path.append(q[0])
                notch_path.extend(q[1:])
                if abs(notch_path[-1][0] - bridge[0]) > 1e-9 or abs(notch_path[-1][1] - bridge[1]) > 1e-9:
                    notch_path.append(bridge)

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
            if notch_path:
                prefix = [v_void] if v_J else []
                suffix = [h_void, h_blank] if h_J else [h_blank]
                return [*prefix, *notch_path, *suffix]
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
            if notch_path:
                prefix = [h_void] if h_J else []
                suffix = [v_void, v_blank] if v_J else [v_void]
                return [*prefix, *reversed(notch_path), *suffix]
            if h_J and v_J:   return [h_void, hc, v_void, v_blank]
            if h_J and v_L:   return [h_void, hc, v_void]
            if h_L and v_J:   return [hc, v_void, v_blank]
            return             [hc, v_void]

    bl = _corner(sb, sl, fx0, fy0, -1, -1, True,  fh1_bl, fh2_bl, fv1_bl, fv2_bl, bend1.get("bottom", fy0), bend1.get("left", fx0))
    br = _corner(sb, sr, fx1, fy0, +1, -1, False, fh1_br, fh2_br, fv1_br, fv2_br, bend1.get("bottom", fy0), bend1.get("right", fx1))
    tr = _corner(st, sr, fx1, fy1, +1, +1, True,  fh1_tr, fh2_tr, fv1_tr, fv2_tr, bend1.get("top", fy1), bend1.get("right", fx1))
    tl = _corner(st, sl, fx0, fy1, -1, +1, False, fh1_tl, fh2_tl, fv1_tl, fv2_tl, bend1.get("top", fy1), bend1.get("left", fx0))

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


def _skew_relief_notch_path(C, hc, t1, t2, half):
    """Parallelogram relief (edges parallel to bend tangents t1, t2), quadrant toward hc."""
    t1n = _v2_norm(t1)
    t2n = _v2_norm(t2)
    v = _v2_sub(hc, C)
    s1 = 1 if _v2_dot(v, t1n) >= 0 else -1
    s2 = 1 if _v2_dot(v, t2n) >= 0 else -1
    p_e1 = _v2_add(C, _v2_scale(t1n, s1 * half))
    p_e2 = _v2_add(C, _v2_scale(t2n, s2 * half))
    p_far = _v2_add(_v2_add(C, _v2_scale(t1n, s1 * half)), _v2_scale(t2n, s2 * half))
    if abs(t1n[0]) >= abs(t2n[0]):
        entry = (hc[0], p_e2[1])
    else:
        entry = (p_e1[0], hc[1])
    notch_path = [entry]
    for q in (p_e1, p_far, p_e2):
        if abs(q[0] - notch_path[-1][0]) > 1e-9 or abs(q[1] - notch_path[-1][1]) > 1e-9:
            notch_path.append(q)
    if abs(notch_path[-1][0] - hc[0]) > 1e-9 or abs(notch_path[-1][1] - hc[1]) > 1e-9:
        notch_path.append(hc)
    return notch_path


def _jj_notch_path_axis(hc, h_void, v_void, ox, oy, half):
    """Original J+J five-point relief in axis frame (ox, oy = ±1 like _corner)."""
    x0, x1 = h_void[0] - ox * half, h_void[0] + ox * half
    y0, y1 = v_void[1] - oy * half, v_void[1] + oy * half
    flange_x = x0 if ox < 0 else x1
    face_x = x1 if ox < 0 else x0
    flange_y = y0 if oy < 0 else y1
    face_y = y1 if oy < 0 else y0
    return [
        (flange_x, hc[1]),
        (flange_x, face_y),
        (face_x, face_y),
        (face_x, flange_y),
        (hc[0], flange_y),
    ]


def _rt_corner_core(
    hsd, vsd, hc, arrive_vert,
    h_void, h_blank, v_void, v_blank,
    b1h_p, b1h_t, b1v_p, b1v_t,
    notch_size, gap,
    u_h, u_v,
    fh2_param, fv2_param,
    jj_ox, jj_oy,
):
    """Void / miter / relief dispatch (jj_ox/jj_oy for J+J axis template when bends are orthogonal)."""
    C = _line_intersect_inf(b1h_p, b1h_t, b1v_p, b1v_t)
    if C is None:
        C = hc

    fh2 = fh2_param if (hsd.active and hsd.ftype == "J" and vsd.active and vsd.ftype == "J") else 0.0
    fv2 = fv2_param if (hsd.active and hsd.ftype == "J" and vsd.active and vsd.ftype == "J") else 0.0
    h_J = hsd.active and fh2 > 1e-9
    h_L = hsd.active and not h_J
    v_J = vsd.active and fv2 > 1e-9
    v_L = vsd.active and not v_J

    if gap > 0 and fh2 > 1e-9 and fv2 > 1e-9:
        halfg = gap / 2.0
        dh = _v2_sub(h_void, hc)
        dv = _v2_sub(v_void, hc)
        if _v2_len(dh) > 1e-9:
            h_void = _v2_add(h_void, _v2_scale(_v2_norm(dh), halfg))
        if _v2_len(dv) > 1e-9:
            v_void = _v2_add(v_void, _v2_scale(_v2_norm(dv), halfg))

    notch_path = None
    if hsd.active and vsd.active and notch_size > 1e-9:
        half = notch_size / 2.0
        if hsd.ftype == "J" and vsd.ftype == "J":
            if jj_ox is not None and jj_oy is not None:
                notch_path = _jj_notch_path_axis(hc, h_void, v_void, jj_ox, jj_oy, half)
            else:
                notch_path = _skew_relief_notch_path(C, hc, b1h_t, b1v_t, half)
        else:
            notch_path = _skew_relief_notch_path(C, hc, b1h_t, b1v_t, half)

    if arrive_vert:
        if not hsd.active and not vsd.active:
            return []
        if hsd.active and not vsd.active:
            return [hc, h_void, h_blank] if h_J else [hc, h_blank]
        if not hsd.active and vsd.active:
            return [v_void, hc] if v_J else [hc]
        if notch_path:
            prefix = [v_void] if v_J else []
            suffix = [h_void, h_blank] if h_J else [h_blank]
            return [*prefix, *notch_path, *suffix]
        if v_J and h_J:
            return [v_void, hc, h_void, h_blank]
        if v_J and h_L:
            return [v_void, hc, h_blank]
        if v_L and h_J:
            return [hc, h_void, h_blank]
        return [hc, h_blank]
    else:
        if not hsd.active and not vsd.active:
            return []
        if hsd.active and not vsd.active:
            return [h_void, hc] if h_J else [hc]
        if not hsd.active and vsd.active:
            return [hc, v_void, v_blank] if v_J else [hc, v_void]
        if notch_path:
            prefix = [h_void] if h_J else []
            suffix = [v_void, v_blank] if v_J else [v_void]
            return [*prefix, *list(reversed(notch_path)), *suffix]
        if h_J and v_J:
            return [h_void, hc, v_void, v_blank]
        if h_J and v_L:
            return [h_void, hc, v_void]
        if h_L and v_J:
            return [hc, v_void, v_blank]
        return [hc, v_void]


def _blank_outline_rt(spec, blank_w, blank_h, sides, bd, ba2, notch_size, gap, lay):
    """CCW blank outline for right trapezoid (HC quad BL,BR,TR,TL already translated)."""
    bl, br, tr, tl = lay["bl"], lay["br"], lay["tr"], lay["tl"]
    sl, sr, sb, st = sides["left"], sides["right"], sides["bottom"], sides["top"]
    centroid = _rt_face_centroid(bl, br, tr, tl)

    def _corner_params(this_sd, other_sd):
        if not this_sd.active:
            return (0.0, 0.0)
        is_jj = this_sd.ftype == "J" and other_sd.active and other_sd.ftype == "J"
        if is_jj:
            return (this_sd.f1 + bd, this_sd.f2)
        return (this_sd.f1 + this_sd.f2 + bd, 0.0)

    fh1_bl, fh2_bl = _corner_params(sb, sl)
    fv1_bl, fv2_bl = _corner_params(sl, sb)
    fh1_br, fh2_br = _corner_params(sb, sr)
    fv1_br, fv2_br = _corner_params(sr, sb)
    fh1_tr, fh2_tr = _corner_params(st, sr)
    fv1_tr, fv2_tr = _corner_params(sr, st)
    fh1_tl, fh2_tl = _corner_params(st, sl)
    fv1_tl, fv2_tl = _corner_params(sl, st)

    fx0, fy0 = bl[0], bl[1]
    fx1 = br[0]

    n_top_in = _inward_normal_edge(tl, tr, centroid)
    n_top_out = _v2_neg(n_top_in)
    t_top = _v2_norm(_v2_sub(tr, tl))
    n_bot_in = _inward_normal_edge(bl, br, centroid)
    n_bot_out = _v2_neg(n_bot_in)
    t_bot = _v2_norm(_v2_sub(br, bl))

    bend_left_x = fx0 - ba2
    bend_right_x = fx1 + ba2

    if spec.rt_opposing_edge == "top":
        bend_bottom_y = fy0 - ba2
        b1_top_p = _v2_add(tl, _v2_scale(n_top_in, ba2))
        b1_top_t = t_top

        def _corner_bl():
            hc = bl
            u_h, u_v = n_bot_out, (-1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_bl))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_bl + fh2_bl), _v2_scale((-1.0, 0.0), fh2_bl)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_bl))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_bl + fv2_bl), _v2_scale((0.0, 1.0), fv2_bl)))
            return _rt_corner_core(
                sb, sl, hc, True, h_void, h_blank, v_void, v_blank,
                (fx0, bend_bottom_y), (1.0, 0.0), (bend_left_x, fy0), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_bl, fv2_bl, -1, -1,
            )

        def _corner_br():
            hc = br
            u_h, u_v = n_bot_out, (1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_br))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_br + fh2_br), _v2_scale((1.0, 0.0), fh2_br)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_br))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_br + fv2_br), _v2_scale((0.0, 1.0), fv2_br)))
            return _rt_corner_core(
                sb, sr, hc, False, h_void, h_blank, v_void, v_blank,
                (fx0, bend_bottom_y), (1.0, 0.0), (bend_right_x, fy0), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_br, fv2_br, 1, -1,
            )

        def _corner_tr():
            hc = tr
            u_h, u_v = n_top_out, (1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_tr))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_tr + fh2_tr), _v2_scale(t_top, -fh2_tr)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_tr))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_tr + fv2_tr), _v2_scale((0.0, -1.0), fv2_tr)))
            return _rt_corner_core(
                st, sr, hc, True, h_void, h_blank, v_void, v_blank,
                b1_top_p, b1_top_t, (bend_right_x, br[1]), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_tr, fv2_tr, None, None,
            )

        def _corner_tl():
            hc = tl
            u_h, u_v = n_top_out, (-1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_tl))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_tl + fh2_tl), _v2_scale(t_top, fh2_tl)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_tl))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_tl + fv2_tl), _v2_scale((0.0, -1.0), fv2_tl)))
            return _rt_corner_core(
                st, sl, hc, False, h_void, h_blank, v_void, v_blank,
                b1_top_p, b1_top_t, (bend_left_x, bl[1]), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_tl, fv2_tl, None, None,
            )

        bl_arr = (bl[0] + (-1) * (fv1_bl + fv2_bl), bl[1] + fv2_bl) if sl.active else bl
        br_arr = (br[0] - fh2_br, br[1] - fh1_br - fh2_br) if sb.active else br
        tr_arr = (tr[0] + fv1_tr + fv2_tr, tr[1] - fv2_tr) if sr.active else tr
        tl_arr = _v2_add(tl, _v2_add(_v2_scale(n_top_out, fh1_tl + fh2_tl), _v2_scale(t_top, fh2_tl))) if st.active else tl

    else:
        b1_bot_p = _v2_add(bl, _v2_scale(n_bot_in, ba2))
        b1_bot_t = t_bot
        bend_top_y = tr[1] + ba2

        def _corner_bl():
            hc = bl
            u_h, u_v = n_bot_out, (-1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_bl))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_bl + fh2_bl), _v2_scale(t_bot, fh2_bl)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_bl))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_bl + fv2_bl), _v2_scale((0.0, 1.0), fv2_bl)))
            return _rt_corner_core(
                sb, sl, hc, True, h_void, h_blank, v_void, v_blank,
                b1_bot_p, b1_bot_t, (bend_left_x, bl[1]), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_bl, fv2_bl, None, None,
            )

        def _corner_br():
            hc = br
            u_h, u_v = n_bot_out, (1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_br))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_br + fh2_br), _v2_scale(t_bot, -fh2_br)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_br))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_br + fv2_br), _v2_scale((0.0, 1.0), fv2_br)))
            return _rt_corner_core(
                sb, sr, hc, False, h_void, h_blank, v_void, v_blank,
                b1_bot_p, b1_bot_t, (bend_right_x, br[1]), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_br, fv2_br, None, None,
            )

        def _corner_tr():
            hc = tr
            u_h, u_v = n_top_out, (1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_tr))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_tr + fh2_tr), _v2_scale((1.0, 0.0), fh2_tr)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_tr))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_tr + fv2_tr), _v2_scale((0.0, -1.0), fv2_tr)))
            return _rt_corner_core(
                st, sr, hc, True, h_void, h_blank, v_void, v_blank,
                (fx0, bend_top_y), (1.0, 0.0), (bend_right_x, br[1]), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_tr, fv2_tr, 1, 1,
            )

        def _corner_tl():
            hc = tl
            u_h, u_v = n_top_out, (-1.0, 0.0)
            h_void = _v2_add(hc, _v2_scale(u_h, fh1_tl))
            h_blank = _v2_add(hc, _v2_add(_v2_scale(u_h, fh1_tl + fh2_tl), _v2_scale((-1.0, 0.0), fh2_tl)))
            v_void = _v2_add(hc, _v2_scale(u_v, fv1_tl))
            v_blank = _v2_add(hc, _v2_add(_v2_scale(u_v, fv1_tl + fv2_tl), _v2_scale((0.0, -1.0), fv2_tl)))
            return _rt_corner_core(
                st, sl, hc, False, h_void, h_blank, v_void, v_blank,
                (fx0, bend_top_y), (1.0, 0.0), (bend_left_x, bl[1]), (0.0, 1.0),
                notch_size, gap, u_h, u_v, fh2_tl, fv2_tl, -1, 1,
            )

        bl_arr = (bl[0] + (-1) * (fv1_bl + fv2_bl), bl[1] + fv2_bl) if sl.active else bl
        br_arr = (br[0] + fv1_br + fv2_br, br[1] + fv2_br) if sr.active else br
        tr_arr = (tr[0] + fh2_tr, tr[1] + (fh1_tr + fh2_tr)) if st.active else tr
        tl_arr = (tl[0] - fh2_tl, tl[1] + (fh1_tl + fh2_tl)) if st.active else tl

    bl_seq = _corner_bl()
    br_seq = _corner_br()
    tr_seq = _corner_tr()
    tl_seq = _corner_tl()

    pts = []

    def _app(pt):
        if pt is None:
            return
        if not pts or abs(pt[0] - pts[-1][0]) > 1e-9 or abs(pt[1] - pts[-1][1]) > 1e-9:
            pts.append(pt)

    _app(bl_arr)
    for p in bl_seq:
        _app(p)
    _app(br_arr)
    for p in br_seq:
        _app(p)
    _app(tr_arr)
    for p in tr_seq:
        _app(p)
    _app(tl_arr)
    for p in tl_seq:
        _app(p)
    return pts


def _add_line(msp, x0, y0, x1, y1, layer):
    msp.add_line((x0,y0),(x1,y1), dxfattribs={"layer": layer})


def _hole_centers(face_x, face_y, face_w, face_h,
                  hole_dia, pitch, pattern, stagger_angle, margin):
    centers = []
    hr = hole_dia / 2.0
    min_x = face_x + margin + hr
    max_x = face_x + face_w - margin - hr
    min_y = face_y + margin + hr
    max_y = face_y + face_h - margin - hr
    usable_x = max_x - min_x
    usable_y = max_y - min_y
    if usable_x < -1e-9 or usable_y < -1e-9:
        return centers

    def _count(span, step):
        if span < -1e-9:
            return 0
        return max(1, int(math.floor(span / step + 1e-9)) + 1)

    if pattern == "straight":
        row_step = pitch
        col_off = 0.0
    else:
        alpha = math.radians(stagger_angle)
        row_step = pitch * math.sin(alpha)
        col_off = pitch * math.cos(alpha)

    row_count = _count(usable_y, row_step)
    used_y = row_step * (row_count - 1)
    start_y = min_y + max(0.0, (usable_y - used_y) / 2.0)

    even_count = _count(usable_x, pitch)
    even_right = pitch * (even_count - 1)
    odd_count = _count(usable_x - col_off, pitch) if col_off <= usable_x + 1e-9 else 0
    odd_right = (col_off + pitch * (odd_count - 1)) if odd_count else float("-inf")
    field_right = max(even_right, odd_right)
    start_x = min_x + max(0.0, (usable_x - field_right) / 2.0)

    for row in range(row_count):
        y = start_y + row * row_step
        offset = col_off if row % 2 else 0.0
        col_count = odd_count if row % 2 else even_count
        for col in range(col_count):
            x = start_x + offset + col * pitch
            if x <= max_x + 1e-9:
                centers.append((x, y))
    return centers


def _bend1_cl_positions(fx0, fy0, fx1, fy1, blank_w, blank_h, sides, ba2, bd, spec, rules):
    """Bend-1 centerline for each active side (HC quad fx0..fy1).

    L and J: F1 bend-1 CL is **inset** `_f1_bend_inset_from_face` from the nominal
    finished-face edge — toward panel interior (e.g. top: ffy1 − ins; right: ffx1 − ins),
    not toward the blank outer. Bend-2 CL for J remains at blank edge − developed f2.
    """
    ffy0 = fy0 - bd
    ffy1 = fy1 + bd
    ffx0 = fx0 - bd
    ffx1 = fx1 + bd
    ins = _f1_bend_inset_from_face(spec, rules)
    pos = {}
    for name, sd in sides.items():
        if not sd.active:
            continue
        if name == "bottom":
            pos[name] = ffy0 + ins
        elif name == "top":
            pos[name] = ffy1 - ins
        elif name == "left":
            pos[name] = ffx0 + ins
        elif name == "right":
            pos[name] = ffx1 - ins
    return pos


def _draw_bend_lines(msp, fx0, fy0, fx1, fy1, blank_w, blank_h, sides, ba2, bd, spec, rules):
    """Bend 1 / Bend 2 centerlines on `bend` for L and J (bend 2 only for J with f2 > 0)."""
    bend1 = _bend1_cl_positions(fx0, fy0, fx1, fy1, blank_w, blank_h, sides, ba2, bd, spec, rules)
    sd = sides
    if sd["bottom"].active:
        _add_line(msp, fx0, bend1["bottom"], fx1, bend1["bottom"], "bend")
    if sd["top"].active:
        _add_line(msp, fx0, bend1["top"], fx1, bend1["top"], "bend")
    if sd["left"].active:
        _add_line(msp, bend1["left"], fy0, bend1["left"], fy1, "bend")
    if sd["right"].active:
        _add_line(msp, bend1["right"], fy0, bend1["right"], fy1, "bend")
    if sd["bottom"].active and sd["bottom"].ftype == "J" and sd["bottom"].f2 > 0:
        _add_line(msp, fx0, sd["bottom"].f2, fx1, sd["bottom"].f2, "bend")
    if sd["top"].active and sd["top"].ftype == "J" and sd["top"].f2 > 0:
        _add_line(msp, fx0, blank_h - sd["top"].f2, fx1, blank_h - sd["top"].f2, "bend")
    if sd["left"].active and sd["left"].ftype == "J" and sd["left"].f2 > 0:
        _add_line(msp, sd["left"].f2, fy0, sd["left"].f2, fy1, "bend")
    if sd["right"].active and sd["right"].ftype == "J" and sd["right"].f2 > 0:
        _add_line(msp, blank_w - sd["right"].f2, fy0, blank_w - sd["right"].f2, fy1, "bend")


def _draw_bend_lines_rt(msp, spec, lay, sides, rules, ba2, blank_w, blank_h, ffc):
    """RT bend CL on `bend` for L and J; ffc = nominal face quad (nbl, nbr, ntr, ntl)."""
    bl, br, tr, tl = lay["bl"], lay["br"], lay["tr"], lay["tl"]
    fx0, fy0 = bl[0], bl[1]
    fx1 = br[0]
    nbl, nbr, ntr, ntl = ffc[0], ffc[1], ffc[2], ffc[3]
    centroid = _rt_face_centroid(bl, br, tr, tl)
    sd = sides
    dist_l = _f1_bend_inset_from_face(spec, rules)

    n_top_in = _inward_normal_edge(tl, tr, centroid)
    n_top_out = _v2_neg(n_top_in)
    n_bot_in = _inward_normal_edge(bl, br, centroid)
    n_bot_out = _v2_neg(n_bot_in)

    if spec.rt_opposing_edge == "top":
        if sd["bottom"].active:
            yb = nbl[1] + dist_l
            _add_line(msp, fx0, yb, fx1, yb, "bend")
        if sd["top"].active:
            if sd["top"].ftype == "J":
                p0 = _v2_add(tl, _v2_scale(n_top_in, dist_l))
                p1 = _v2_add(tr, _v2_scale(n_top_in, dist_l))
                _add_line(msp, p0[0], p0[1], p1[0], p1[1], "bend")
            else:
                p0 = _v2_add(ntl, _v2_scale(n_top_in, dist_l))
                p1 = _v2_add(ntr, _v2_scale(n_top_in, dist_l))
                _add_line(msp, p0[0], p0[1], p1[0], p1[1], "bend")
        if sd["left"].active:
            xL = nbl[0] + dist_l
            _add_line(msp, xL, bl[1], xL, tl[1], "bend")
        if sd["right"].active:
            xR = nbr[0] - dist_l
            _add_line(msp, xR, br[1], xR, tr[1], "bend")
        if sd["bottom"].active and sd["bottom"].ftype == "J" and sd["bottom"].f2 > 0:
            _add_line(msp, fx0, sd["bottom"].f2, fx1, sd["bottom"].f2, "bend")
        if sd["top"].active and sd["top"].ftype == "J" and sd["top"].f2 > 0:
            o = lay["outer"]
            tl_o, tr_o = o[3], o[2]
            n = _v2_norm(_perp_outward_ccw(tl_o, tr_o))
            if _v2_dot(n, _v2_sub(centroid, _v2_scale(_v2_add(tl_o, tr_o), 0.5))) > 0:
                n = _v2_neg(n)
            q0 = _v2_add(tl_o, _v2_scale(n, -sd["top"].f2))
            q1 = _v2_add(tr_o, _v2_scale(n, -sd["top"].f2))
            _add_line(msp, q0[0], q0[1], q1[0], q1[1], "bend")
    else:
        if sd["bottom"].active:
            if sd["bottom"].ftype == "J":
                p0 = _v2_add(bl, _v2_scale(n_bot_in, dist_l))
                p1 = _v2_add(br, _v2_scale(n_bot_in, dist_l))
            else:
                p0 = _v2_add(nbl, _v2_scale(n_bot_in, dist_l))
                p1 = _v2_add(nbr, _v2_scale(n_bot_in, dist_l))
            _add_line(msp, p0[0], p0[1], p1[0], p1[1], "bend")
        if sd["top"].active:
            p0 = _v2_add(ntl, _v2_scale(n_top_in, dist_l))
            p1 = _v2_add(ntr, _v2_scale(n_top_in, dist_l))
            _add_line(msp, p0[0], p0[1], p1[0], p1[1], "bend")
        if sd["left"].active:
            xL = nbl[0] + dist_l
            _add_line(msp, xL, bl[1], xL, tl[1], "bend")
        if sd["right"].active:
            xR = nbr[0] - dist_l
            _add_line(msp, xR, br[1], xR, tr[1], "bend")
        if sd["bottom"].active and sd["bottom"].ftype == "J" and sd["bottom"].f2 > 0:
            o = lay["outer"]
            bl_o, br_o = o[0], o[1]
            n = _v2_norm(_perp_outward_ccw(bl_o, br_o))
            if _v2_dot(n, _v2_sub(centroid, _v2_scale(_v2_add(bl_o, br_o), 0.5))) > 0:
                n = _v2_neg(n)
            q0 = _v2_add(bl_o, _v2_scale(n, -sd["bottom"].f2))
            q1 = _v2_add(br_o, _v2_scale(n, -sd["bottom"].f2))
            _add_line(msp, q0[0], q0[1], q1[0], q1[1], "bend")
        if sd["top"].active and sd["top"].ftype == "J" and sd["top"].f2 > 0:
            _add_line(msp, fx0, blank_h - sd["top"].f2, fx1, blank_h - sd["top"].f2, "bend")
    if sd["left"].active and sd["left"].ftype == "J" and sd["left"].f2 > 0:
        _add_line(msp, sd["left"].f2, min(bl[1], tl[1]), sd["left"].f2, max(bl[1], tl[1]), "bend")
    if sd["right"].active and sd["right"].ftype == "J" and sd["right"].f2 > 0:
        _add_line(msp, blank_w - sd["right"].f2, min(br[1], tr[1]), blank_w - sd["right"].f2, max(br[1], tr[1]), "bend")


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


def _preferred_id_side(sides):
    order = ("top", "right", "bottom", "left")
    for name in order:
        sd = sides[name]
        if sd.active and sd.ftype == "L":
            return name
    for name in order:
        if sides[name].active:
            return name
    return None


def _j_slot_normal_center(side_name, flange_line, face_holes, bd):
    if not face_holes:
        return flange_line[side_name]

    if side_name == "left":
        nearest = min(x for x, _ in face_holes)
        return flange_line[side_name] - ((nearest - flange_line[side_name]) + bd)
    if side_name == "right":
        nearest = max(x for x, _ in face_holes)
        return flange_line[side_name] + ((flange_line[side_name] - nearest) + bd)
    if side_name == "bottom":
        nearest = min(y for _, y in face_holes)
        return flange_line[side_name] - ((nearest - flange_line[side_name]) + bd)

    nearest = max(y for _, y in face_holes)
    return flange_line[side_name] + ((flange_line[side_name] - nearest) + bd)


def _holes_on_same_row(face_holes, y, tol=1e-6):
    return [(x, hy) for x, hy in face_holes if abs(hy - y) <= tol]


def _holes_on_same_col(face_holes, x, tol=1e-6):
    return [(hx, y) for hx, y in face_holes if abs(hx - x) <= tol]


def _panel_id_along_coordinate(start, span, slot_centers, has_slots, slot_length):
    """Along-flange coordinate (x for top/bottom, y for left/right) for panel ID center.

    When this side has fastening slots, keep the label past the first slot (from
    the low-x / low-y flange start) and at least PANEL_ID_CLEAR_FROM_FLANGE_END
    from that end so it does not sit in the first slot (2" margin + slot).
    Otherwise center on the flange span.
    """
    if not has_slots or not slot_centers:
        return start + span / 2.0
    first = min(slot_centers)
    past_first = first + slot_length / 2.0 + PANEL_ID_CLEAR_PAST_SLOT
    from_corner = start + PANEL_ID_CLEAR_FROM_FLANGE_END
    opp_margin = start + span - PANEL_ID_CLEAR_FROM_FLANGE_END
    along = max(from_corner, past_first)
    if along > opp_margin:
        return start + span / 2.0
    return along


def _panel_id_anchor(spec, sides, ff_edge, face_holes, fx0, fy0, face_w, face_h, blank_w, blank_h, bd, slot_length):
    side_name = _preferred_id_side(sides)
    if not side_name:
        return None

    fast_sides = set(_fastening_sides(spec, sides))
    sd = sides[side_name]
    if side_name in ("top", "bottom"):
        along_positions = (
            _aligned_positions([x for x, _ in face_holes])
            if sd.ftype == "J"
            else [fx0 + p for p in _l_positions(face_w)]
        )
        has_slots = side_name in fast_sides
        along_x = _panel_id_along_coordinate(
            fx0, face_w, along_positions, has_slots, slot_length
        )
        normal = (
            (sd.f2 / 2.0) if side_name == "bottom" else (blank_h - sd.f2 / 2.0)
            if sd.ftype == "J"
            else ((ff_edge[side_name] / 2.0) if side_name == "bottom" else ((blank_h + ff_edge[side_name]) / 2.0))
        )
        return {
            "side": side_name,
            "x": along_x,
            "y": normal,
            "rotation": 0.0,
        }

    along_positions = (
        _aligned_positions([y for _, y in face_holes])
        if sd.ftype == "J"
        else [fy0 + p for p in _l_positions(face_h)]
    )
    has_slots = side_name in fast_sides
    along_y = _panel_id_along_coordinate(
        fy0, face_h, along_positions, has_slots, slot_length
    )
    normal = (
        (sd.f2 / 2.0) if side_name == "left" else (blank_w - sd.f2 / 2.0)
        if sd.ftype == "J"
        else ((ff_edge[side_name] / 2.0) if side_name == "left" else ((blank_w + ff_edge[side_name]) / 2.0))
    )
    return {
        "side": side_name,
        "x": normal,
        "y": along_y,
        "rotation": 90.0,
    }


def _stroke_glyph(char):
    return STICK_FONT.get(char.upper(), [[(0.0, 0.0), (0.6, 1.0)], [(0.0, 1.0), (0.6, 0.0)]])


def _draw_stick_text(msp, text, x, y, height, rotation_deg, layer):
    scale = height
    advance = STICK_CHAR_ADVANCE * scale
    width = STICK_CHAR_WIDTH * scale
    total_width = max(width, len(text) * advance - (advance - width))
    x_offset = -total_width / 2.0
    y_offset = -height / 2.0
    theta = math.radians(rotation_deg)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)

    cursor_x = 0.0
    for char in text:
        glyph = _stroke_glyph(char)
        for stroke in glyph:
            pts = []
            for px, py in stroke:
                lx = x_offset + cursor_x + px * scale
                ly = y_offset + py * scale
                rx = x + (lx * cos_t - ly * sin_t)
                ry = y + (lx * sin_t + ly * cos_t)
                pts.append((rx, ry))
            if len(pts) >= 2:
                msp.add_lwpolyline(pts, close=False, dxfattribs={"layer": layer})
        cursor_x += advance


def _draw_panel_id_text(doc, msp, spec, sides, ff_edge, face_holes, fx0, fy0, face_w, face_h, blank_w, blank_h, bd):
    slot_length = spec.slot_length if spec.slot_length is not None else (spec.fastener_dia + INSTALL_SLOT_EXTRA)
    anchor = _panel_id_anchor(spec, sides, ff_edge, face_holes, fx0, fy0, face_w, face_h, blank_w, blank_h, bd, slot_length)
    if not anchor:
        return

    _draw_stick_text(
        msp,
        spec.panel_id,
        anchor["x"],
        anchor["y"],
        STICK_TEXT_HEIGHT,
        anchor["rotation"],
        "text",
    )


def _draw_fastening_slots(msp, spec, ffx0, ffy0, ffx1, ffy1, face_w, face_h,
                          sides, blank_w, blank_h, bd):
    active=_fastening_sides(spec,sides)
    if not active: return
    ff_edge = {"left": ffx0, "bottom": ffy0, "right": ffx1, "top": ffy1}
    face_holes=_hole_centers(ffx0,ffy0,face_w,face_h,
                             spec.hole_dia,spec.pitch,spec.pattern,
                             spec.stagger_angle,spec.margin)
    fdia=spec.fastener_dia
    flen=spec.slot_length if spec.slot_length is not None else (fdia+INSTALL_SLOT_EXTRA)
    for sn in active:
        sd=sides[sn]
        if sn in ("bottom","top"):
            is_b=(sn=="bottom")
            if sd.ftype=="J":
                xs=_aligned_positions([c[0] for c in face_holes])
                for px in xs:
                    col_holes = _holes_on_same_col(face_holes, px)
                    hy = _j_slot_normal_center(sn, ff_edge, col_holes or face_holes, bd)
                    _add_slot(msp, px, hy, fdia, flen, "horizontal", "fastening")
                continue
            else:
                hy=(ff_edge[sn]/2.0) if is_b else ((blank_h + ff_edge[sn]) / 2.0)
                xs=[ffx0+p for p in _l_positions(face_w)]
            for px in xs:
                _add_slot(msp, px, hy, fdia, flen, "horizontal", "fastening")
        else:
            is_l=(sn=="left")
            if sd.ftype=="J":
                ys=_aligned_positions([c[1] for c in face_holes])
                for py in ys:
                    row_holes = _holes_on_same_row(face_holes, py)
                    hx = _j_slot_normal_center(sn, ff_edge, row_holes or face_holes, bd)
                    _add_slot(msp, hx, py, fdia, flen, "vertical", "fastening")
                continue
            else:
                hx=(ff_edge[sn]/2.0) if is_l else ((blank_w + ff_edge[sn]) / 2.0)
                ys=[ffy0+p for p in _l_positions(face_h)]
            for py in ys:
                _add_slot(msp, hx, py, fdia, flen, "vertical", "fastening")


def _add_slot_axis(msp, cx, cy, width, length, tx, ty, layer):
    """Rounded slot with long axis parallel to unit vector (tx, ty)."""
    if width <= 0 or length < width:
        return
    r = width / 2.0
    a = (length - width) / 2.0

    def R(px, py):
        return (cx + px * tx - py * ty, cy + px * ty + py * tx)

    pts = [
        (*R(-a, r), 0.0),
        (*R(a, r), -1.0),
        (*R(a, -r), 0.0),
        (*R(-a, -r), -1.0),
    ]
    msp.add_lwpolyline(pts, format="xyb", close=True, dxfattribs={"layer": layer})


def _y_on_edge_at_x(p0, p1, xq):
    if abs(p1[0] - p0[0]) < 1e-12:
        return (p0[1] + p1[1]) * 0.5
    t = (xq - p0[0]) / (p1[0] - p0[0])
    return p0[1] + t * (p1[1] - p0[1])


def _draw_fastening_slots_rt(msp, spec, lay, ff_dict, ffc, sides, blank_w, blank_h, bd, face_holes):
    active = _fastening_sides(spec, sides)
    if not active:
        return
    bl, br, tr, tl = lay["bl"], lay["br"], lay["tr"], lay["tl"]
    ff_bl, ff_br, ff_tr, ff_tl = ffc
    fx0, fy0 = bl[0], bl[1]
    fx1 = br[0]
    face_w = fx1 - fx0
    min_y = min(ff_bl[1], ff_br[1], ff_tr[1], ff_tl[1])
    max_y = max(ff_bl[1], ff_br[1], ff_tr[1], ff_tl[1])
    face_h = max_y - min_y
    fdia = spec.fastener_dia
    flen = spec.slot_length if spec.slot_length is not None else (fdia + INSTALL_SLOT_EXTRA)
    t_top = _v2_norm(_v2_sub(ff_tr, ff_tl))
    o = lay["outer"]

    for sn in active:
        sd = sides[sn]
        if sn in ("bottom", "top"):
            if sd.ftype == "J":
                xs = _aligned_positions([c[0] for c in face_holes])
                for px in xs:
                    col = _holes_on_same_col(face_holes, px)
                    hy = _j_slot_normal_center(sn, ff_dict, col or face_holes, bd)
                    _add_slot(msp, px, hy, fdia, flen, "horizontal", "fastening")
                continue
            if sn == "top" and spec.rt_opposing_edge == "top" and sd.ftype == "L":
                tl_o, tr_o = o[3], o[2]
                for px in [fx0 + p for p in _l_positions(face_w)]:
                    yb = _y_on_edge_at_x(ff_tl, ff_tr, px)
                    yo = _y_on_edge_at_x(tl_o, tr_o, px)
                    cy = (yb + yo) * 0.5
                    _add_slot_axis(msp, px, cy, fdia, flen, t_top[0], t_top[1], "fastening")
                continue
            hy = (ff_dict[sn] / 2.0) if sn == "bottom" else ((blank_h + ff_dict[sn]) / 2.0)
            for px in [fx0 + p for p in _l_positions(face_w)]:
                _add_slot(msp, px, hy, fdia, flen, "horizontal", "fastening")
        else:
            if sd.ftype == "J":
                ys = _aligned_positions([c[1] for c in face_holes])
                for py in ys:
                    row = _holes_on_same_row(face_holes, py)
                    hx = _j_slot_normal_center(sn, ff_dict, row or face_holes, bd)
                    _add_slot(msp, hx, py, fdia, flen, "vertical", "fastening")
                continue
            hx = (ff_dict[sn] / 2.0) if sn == "left" else ((blank_w + ff_dict[sn]) / 2.0)
            for py in [min_y + p for p in _l_positions(face_h)]:
                _add_slot(msp, hx, py, fdia, flen, "vertical", "fastening")


# ---------------------------------------------------------------------------
# Section 6 corner reliefs (optional separate geometry; perimeter joinery uses
# `_blank_outline` / `_blank_outline_rt` with notch_size=0 for current shop DXF).
# ---------------------------------------------------------------------------
def _draw_corner_reliefs(msp, fx0, fy0, fx1, fy1, blank_w, blank_h, sides, r, t, ba2):
    return


# ---------------------------------------------------------------------------
# Main DXF generator
# ---------------------------------------------------------------------------
def generate_panel_dxf(spec, outdir):
    rules = get_rules(spec)
    r, k, t = rules["r"], rules["k"], spec.thickness
    bd = _bd(r, k, t)
    ba2 = _ba_half(r, k, t)
    sides = resolve_sides(spec)
    gap = spec.gap_override if spec.gap_override is not None else MITER_GAP_DEFAULT

    doc = ezdxf.new(dxfversion="R2010")
    doc.units = 1
    msp = doc.modelspace()
    for name, color in [
        ("cut", 1),
        ("holes", 2),
        ("fastening", 5),
        ("bend", 3),
        ("text", 6),
        ("finished_face", 8),
    ]:
        if name not in doc.layers:
            doc.layers.add(name=name, color=color)

    # Corner 2×T relief notches removed per shop artwork; keep param for API.
    notch_size = 0.0

    if _is_right_trapezoid(spec):
        lay = _rt_blank_layout(spec, sides, bd)
        bw, bh = lay["bw"], lay["bh"]
        bl, br, tr, tl = lay["bl"], lay["br"], lay["tr"], lay["tl"]
        fx0, fy0 = bl[0], bl[1]
        fx1 = br[0]
        face_w = fx1 - fx0
        face_h = max(tl[1], tr[1]) - min(bl[1], br[1])
        pts = _blank_outline_rt(spec, bw, bh, sides, bd, ba2, notch_size, gap, lay)
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "cut"})
        ffc = list(_rt_nominal_face_corners(bl, br, tr, tl, sides, bd))
        msp.add_lwpolyline(ffc, close=True, dxfattribs={"layer": "finished_face"})
        ff_dict = _ff_edge_rt_dict(spec, lay, sides, ffc)
        _draw_corner_reliefs(msp, fx0, fy0, fx1, fy0 + face_h, bw, bh, sides, r, t, ba2)
        _draw_bend_lines_rt(msp, spec, lay, sides, rules, ba2, bw, bh, ffc)
        face_holes = _hole_centers_rt(
            fx0, fx1, ffc[0], ffc[1], ffc[2], ffc[3],
            spec.hole_dia, spec.pitch, spec.pattern,
            spec.stagger_angle, spec.margin,
        )
        for x, y in face_holes:
            msp.add_circle((x, y), spec.hole_dia / 2.0, dxfattribs={"layer": "holes"})
        min_ffy = min(p[1] for p in ffc)
        max_ffy = max(p[1] for p in ffc)
        ff_face_h = max_ffy - min_ffy
        _draw_fastening_slots_rt(msp, spec, lay, ff_dict, ffc, sides, bw, bh, bd, face_holes)
        _draw_panel_id_text(doc, msp, spec, sides, ff_dict, face_holes, fx0, min_ffy, face_w, ff_face_h, bw, bh, bd)
        os.makedirs(outdir, exist_ok=True)
        doc.saveas(os.path.join(outdir, f"{spec.panel_id}.dxf"))
        return

    bw, bh = flat_size(spec)
    ffx0, ffy0, ffx1, ffy1 = _nominal_finished_face_rect_xy(bw, bh, sides)
    face_w = ffx1 - ffx0
    face_h = ffy1 - ffy0
    ff_edge = {"left": ffx0, "bottom": ffy0, "right": ffx1, "top": ffy1}
    hcx0, hcy0, hcx1, hcy1 = ffx0 + bd, ffy0 + bd, ffx1 - bd, ffy1 - bd
    pts = _blank_outline(bw, bh, sides, bd, ba2, notch_size, spec, rules, gap)
    msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": "cut"})
    ff = _finished_face_quad_ccw_ortho(bw, bh, sides)
    msp.add_lwpolyline(ff, close=True, dxfattribs={"layer": "finished_face"})

    _draw_corner_reliefs(msp, hcx0, hcy0, hcx1, hcy1, bw, bh, sides, r, t, ba2)
    _draw_bend_lines(msp, hcx0, hcy0, hcx1, hcy1, bw, bh, sides, ba2, bd, spec, rules)

    face_holes = _hole_centers(
        ffx0, ffy0, face_w, face_h,
        spec.hole_dia, spec.pitch, spec.pattern,
        spec.stagger_angle, spec.margin,
    )
    for x, y in face_holes:
        msp.add_circle((x, y), spec.hole_dia / 2.0, dxfattribs={"layer": "holes"})

    _draw_fastening_slots(msp, spec, ffx0, ffy0, ffx1, ffy1, face_w, face_h, sides, bw, bh, bd)
    _draw_panel_id_text(doc, msp, spec, sides, ff_edge, face_holes, ffx0, ffy0, face_w, face_h, bw, bh, bd)

    os.makedirs(outdir,exist_ok=True)
    doc.saveas(os.path.join(outdir,f"{spec.panel_id}.dxf"))


def nest_panels(panels,sw,sh): return []
def write_nesting_dxf(sheets,outdir): return None
