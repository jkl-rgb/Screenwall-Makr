import io
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from screenwall_generator import parse_csv, generate_panel_dxf

st.set_page_config(page_title="Screenwall Makr", layout="wide")


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
    "stagger_angle", "margin",
]

MIX_EXTRA_HEADERS = [
    "top_type", "top_f1", "top_f2",
    "bottom_type", "bottom_f1", "bottom_f2",
    "left_type", "left_f1", "left_f2",
    "right_type", "right_f1", "right_f2",
]

STANDARD_EXAMPLE = [
    "example_L4S", "36", "24", "0.1875", "aluminum", "3003",
    "L4S", "2.0", "",
    "0.75", "1.25", "staggered", "standard",
    "60.0", "1.25",
    "", "", "", "", "", "", "", "", "", "", "", "",
]

MIX_EXAMPLE = [
    "example_MIX", "36", "24", "0.1875", "aluminum", "5052",
    "MIX", "", "",
    "0.75", "1.25", "staggered", "standard",
    "60.0", "1.25",
    "J", "2.0", "2.25",   # top
    "J", "2.0", "2.25",   # bottom
    "L", "2.0", "",       # left
    "L", "2.0", "",       # right
]

ALL_HEADERS = STANDARD_HEADERS + MIX_EXTRA_HEADERS

template_csv = ",".join(ALL_HEADERS) + "\n"
template_csv += ",".join(STANDARD_EXAMPLE) + "\n"
template_csv += ",".join(MIX_EXAMPLE) + "\n"

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("Screenwall Makr")
st.caption("Flat pattern DXF generator for perforated aluminum screen panels.")

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
| `MIX` | Per-side type and depth — fill in the `top_*`, `bottom_*`, `left_*`, `right_*` columns |

**Notes**
- `flange1_depth` = nominal leg depth (outside mold line). Used for all non-MIX codes.
- `flange2_depth` = nominal return lip depth for J codes. Leave blank for L codes.
- `material` = aluminum (default). `alloy` = 3003 (default) or 5052.
- For `MIX`: set `top_type` / `bottom_type` / `left_type` / `right_type` to `L` or `J`.
  Set `*_f1` (leg depth) and `*_f2` (return lip, J only). A side with `*_f1 = 0` is a straight cut.
""")

st.download_button(
    "⬇ Download CSV template",
    data=template_csv,
    file_name="screenwall_template.csv",
    mime="text/csv",
)
st.caption("Populate the template and upload below. All dimensions in inches.")

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
                summary.append({
                    "ID": p.panel_id,
                    "W×H": f"{p.face_width}″ × {p.face_height}″",
                    "Thickness": f"{p.thickness}″",
                    "Alloy": f"{p.material} {p.alloy}",
                    "Code": p.flange_code,
                    "Hole Ø": f"{p.hole_dia}″",
                    "Pitch": f"{p.pitch}″",
                    "Pattern": p.pattern,
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
