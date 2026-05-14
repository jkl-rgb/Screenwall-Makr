import base64
import io
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from screenwall_generator import (
    GENERATOR_ARTWORK_TAG,
    INSTALL_SLOT_EXTRA,
    parse_csv,
    generate_panel_dxf,
)

st.set_page_config(page_title="Screenwall Makr", layout="wide")
LOGO_PATH = Path(__file__).parent / "assets" / "artform_logo.png"
APP_BUILD = "deploy-check-2026-05-12-1549"


def _inline_image_base64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _zip_dxfs(folder: str) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for f in Path(folder).glob("*.dxf"):
            z.write(f, f.name)
    mem.seek(0)
    return mem.read()


# ---------------------------------------------------------------------------
# CSV template
# ---------------------------------------------------------------------------
STANDARD_HEADERS = [
    "panel_id", "width", "height", "thickness", "material", "alloy",
    "flange_code", "flange1_depth", "flange2_depth",
    "hole_diameter", "hole_pitch", "pattern", "fastening_pair",
    "fastener_dia", "slot_length",
    "stagger_angle", "margin",
    "shop_flat_mode",
]

MIX_EXTRA_HEADERS = [
    "top_type", "top_f1", "top_f2",
    "bottom_type", "bottom_f1", "bottom_f2",
    "left_type", "left_f1", "left_f2",
    "right_type", "right_f1", "right_f2",
]

RT_EXTRA_HEADERS = [
    "rt_opposing_edge",
    "rt_leg_left",
    "rt_leg_right",
]

STANDARD_EXAMPLE = [
    "example_L4S", "36", "24", "0.1875", "aluminum", "3003",
    "L4S", "2.0", "",
    "0.75", "1.25", "staggered", "tb", "0.1875", "",
    "60.0", "1.25",
    "auto",
    "", "", "", "", "", "", "", "", "", "", "", "",
    "", "", "",
]

MIX_EXAMPLE = [
    "example_MIX", "36", "24", "0.1875", "aluminum", "5052",
    "MIX", "", "",
    "0.75", "1.25", "staggered", "tb", "0.1875", "",
    "60.0", "1.25",
    "auto",
    "J", "2.0", "2.25",   # top
    "J", "2.0", "2.25",   # bottom
    "L", "2.0", "",       # left
    "L", "2.0", "",       # right
    "", "", "",
]

# Steel example: thickness given as a gauge string ("14 ga"). material=steel is
# now enough to drive BOTH gauge decoding and bend-rule lookup to the steel row.
# alloy may still be set to "steel" (or a shop-specific steel descriptor) for
# clarity in exported summaries and CSV review.
STEEL_EXAMPLE = [
    "example_STEEL", "36", "24", "14 ga", "steel", "steel",
    "L4S", "2.0", "",
    "0.75", "1.25", "staggered", "tb", "0.1875", "0.75",
    "60.0", "1.25",
    "auto",
    "", "", "", "", "", "", "", "", "", "", "", "",
    "", "", "",
]

RT_EXAMPLE = [
    "example_RT4S", "36", "26", "0.1875", "aluminum", "3003",
    "RT4S", "2.0", "",
    "0.75", "1.25", "staggered", "tb", "0.1875", "",
    "60.0", "1.25",
    "auto",
    "", "", "", "", "", "", "", "", "", "", "", "",
    "top", "24", "30",
]

ALL_HEADERS = STANDARD_HEADERS + MIX_EXTRA_HEADERS + RT_EXTRA_HEADERS

# Keep the downloadable template data-only. Spreadsheet apps can rewrite
# comment-prefixed instruction rows in ways that break subsequent uploads.
template_csv = ",".join(ALL_HEADERS) + "\n"
template_csv += ",".join(STANDARD_EXAMPLE) + "\n"
template_csv += ",".join(MIX_EXAMPLE) + "\n"
template_csv += ",".join(STEEL_EXAMPLE) + "\n"
template_csv += ",".join(RT_EXAMPLE) + "\n"

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    html, body, [class*="css"]  {
        font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
    }
    .stApp {
        background: #ffffff;
        color: #1d1d1f;
    }
    .block-container {
        padding-top: 2.25rem;
        padding-bottom: 2.5rem;
        max-width: 1200px;
    }
    .artform-header {
        padding-top: 0.35rem;
    }
    .artform-title {
        font-size: clamp(2.3rem, 5vw, 4rem);
        line-height: 1.02;
        font-weight: 300;
        letter-spacing: -0.03em;
        color: #1d1d1f;
        margin: 0;
    }
    .artform-subtitle {
        max-width: 42rem;
        margin-top: 0.95rem;
        font-size: 1.05rem;
        line-height: 1.6;
        font-weight: 300;
        color: #6e6e73;
    }
    .artform-build {
        margin-top: 0.8rem;
        font-size: 0.82rem;
        letter-spacing: 0.02em;
        color: #8e8e93;
    }
    .artform-rule {
        height: 1px;
        background: linear-gradient(90deg, rgba(29,29,31,0.14), rgba(29,29,31,0.05));
        margin: 1.35rem 0 1.8rem 0;
    }
    .artform-logo {
        display: block;
        width: 75%;
        max-width: 165px;
        min-width: 120px;
        margin: 0.85rem auto 0;
        height: auto;
    }
    [data-testid="stExpander"] {
        border: 1px solid #e5e5e7;
        border-radius: 18px;
        background: #fbfbfd;
        overflow: hidden;
    }
    [data-testid="stFileUploader"] {
        border-radius: 18px;
        border: 1px dashed #c7c7cc;
        background: #fbfbfd;
    }
    div.stButton > button,
    div.stDownloadButton > button {
        border-radius: 999px;
        border: 1px solid #d2d2d7;
        background: #ffffff;
        color: #1d1d1f;
        font-weight: 500;
    }
    div.stButton > button:hover,
    div.stDownloadButton > button:hover {
        border-color: #1d1d1f;
        color: #1d1d1f;
    }
</style>
""", unsafe_allow_html=True)

logo_b64 = _inline_image_base64(LOGO_PATH) if LOGO_PATH.exists() else None

header_left, header_right = st.columns([4.8, 0.8], vertical_alignment="top")
with header_left:
    st.markdown(
        f"""
        <div class="artform-header">
            <h1 class="artform-title">Screenwall Makr</h1>
            <div class="artform-subtitle">
                Flat pattern DXF generator for perforated screenwall panels,
                with bend-aware flange geometry, install-slot placement, and
                fabrication-ready CSV import controls.
            </div>
            <div class="artform-build">Build: {APP_BUILD}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with header_right:
    if logo_b64:
        st.markdown(
            f'<img class="artform-logo" src="data:image/png;base64,{logo_b64}" alt="Artform logo" />',
            unsafe_allow_html=True,
        )

st.markdown('<div class="artform-rule"></div>', unsafe_allow_html=True)

with st.expander("Flange code reference", expanded=False):
    st.markdown("""
| Code | Description |
|------|-------------|
| `L4S` | L-flange, all 4 sides, same depth |
| `J4S` | J-flange (leg + return lip), all 4 sides, same depth |
| `L2TB` | L-flange, top + bottom only |
| `J2TB` | J-flange, top + bottom only |
| `L2LR` | L-flange, left + right only |
| `J2LR` | J-flange, left + right only |
| `RT4S` | Right trapezoid, L-flange all sides — parallel top or bottom, vertical legs, angled opposite edge |
| `RT4J` | Right trapezoid, J-flange all sides (same `flange2_depth` as `J4S`) |
| `MIX` | Per-side type and depth — fill in the `top_*`, `bottom_*`, `left_*`, `right_*` columns |

**Notes**
- `flange1_depth` = nominal leg depth (outside mold line). Used for all non-MIX codes.
- `flange2_depth` = nominal return lip depth for J codes. Leave blank for L codes.
- `material` defaults to `aluminum`; `alloy` defaults to `3003`.
- `fastening_pair` is required in the CSV. Use `tb`, `lr`, `t`, `b`, `l`, `r`, or `none`.
  Typical practice is to place install slots on the long sides only.
- `fastener_dia` is the install slot width.
- Accepted width aliases on import: `slot_width`, `fastener_slot_width`, `install_slot_width`.
- `slot_length` is optional total install slot length. Leave it blank to use the default
  `fastener_dia + 0.50"`.
- Accepted length aliases on import: `fastener_slot_length`, `install_slot_length`.
- `shop_flat_mode`: `auto` (default) blends shop cut-line flats (5052 / 0.1875″ calibration) with bend theory across all table materials; `off` uses pure `_flat_leg_L` / `_flat_leg_J` / `_flat_lip` only.
- DXF layer `finished_face` (gray): **CSV `width` × `height`** = nominal face opening at developed `f1+f2` runouts (before BD corner arc). Holes / slots / margin use this. Layer `bend`: bend 1 and bend 2 centerlines for **L and J** (L bend 1 uses 0.195″ + BA/2 from the face plus 0.027″ toward the outer perimeter; J bend 1 stays BA/2 from HC). Exclude `finished_face` from perimeter cut in CAM.
- For `MIX`: set `top_type` / `bottom_type` / `left_type` / `right_type` to `L` or `J`.
  Set `*_f1` (leg depth) and `*_f2` (return lip, J only). A side with `*_f1 = 0` is a straight cut.
- For `RT4S` / `RT4J`: set `rt_opposing_edge` to `top` or `bottom` (which parallel edge is straight in plan).
  Set `rt_leg_left` and `rt_leg_right` to the vertical face heights at the left and right hard corners
  (inches). `width` is still the parallel span between the vertical legs. Leave `rt_*` blank for other codes.
""")

with st.expander("Material & gauge rules (READ FIRST)", expanded=False):
    st.markdown("""
**Thickness column** accepts either a decimal (e.g. `0.1875`) or a gauge string (`16 ga`, `14 ga`, `11 ga`).

Gauge strings decode **differently per material** to match the shop bend table (Known Truths Section 2):

| Gauge | Aluminum | Steel |
|------|---------|-------|
| `16 ga` | 0.0625″ | 0.0600″ |
| `14 ga` | 0.0800″ | 0.0750″ |
| `11 ga` | 0.1250″ | 0.1200″ |

**For STEEL panels:**
- `material = steel` drives both gauge decoding and the bend-table lookup.
- `alloy` may still be `steel` (or a steel descriptor) for clarity, but it no longer
  has to be `steel` just to get steel k-factor / radius values.

**For ALUMINUM panels:**
- `material = aluminum`
- `alloy = 3003` (default), `5052`, or `6061` (6061 needs larger bend radii — verify with shop).

**Power-user override:** if you want to force specific values regardless of material, set `k_factor_override` and `bend_radius_override` columns and they take precedence over the table.
""")

st.download_button(
    "⬇ Download CSV template",
    data=template_csv,
    file_name="screenwall_template.csv",
    mime="text/csv",
)
st.caption(
    "Populate the template and upload below. All dimensions in inches. Import guidance lives in the expanders above. "
    f"**DXF engine:** `{GENERATOR_ARTWORK_TAG}` · **UI build:** `{APP_BUILD}`"
)

uploaded = st.file_uploader("Upload CSV", type=["csv"])

if uploaded is not None:
    with tempfile.TemporaryDirectory() as d:
        csv_path = Path(d) / "input.csv"
        csv_path.write_bytes(uploaded.getvalue())
        try:
            panels = parse_csv(str(csv_path))
            st.success(f"Loaded {len(panels)} panel(s).")

            # Show parsed summary
            summary = []
            for p in panels:
                slot_length = p.slot_length if p.slot_length is not None else (p.fastener_dia + INSTALL_SLOT_EXTRA)
                summary.append({
                    "ID": p.panel_id,
                    "W×H": f"{p.face_width}″ × {p.face_height}″",
                    "Thickness": f"{p.thickness}″",
                    "Alloy": f"{p.material} {p.alloy}",
                    "Code": p.flange_code,
                    "Hole Ø": f"{p.hole_dia}″",
                    "Pitch": f"{p.pitch}″",
                    "Pattern": p.pattern,
                    "Install Sides": p.fastening_pair,
                    "Slot W×L": f"{p.fastener_dia}″ × {slot_length}″",
                })
            st.dataframe(summary, use_container_width=True)

            if st.button("Generate DXFs", type="primary"):
                errors = []
                for panel in panels:
                    try:
                        generate_panel_dxf(panel, d)
                    except Exception as e:
                        errors.append(f"{panel.panel_id}: {e}")
                if errors:
                    for err in errors:
                        st.error(err)
                else:
                    z = _zip_dxfs(d)
                    st.download_button(
                        "⬇ Download DXFs (.zip)",
                        z,
                        "screenwall_panels.zip",
                        mime="application/zip",
                    )
        except Exception as e:
            st.error(str(e))
