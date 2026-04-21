import io
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from screenwall_generator import parse_csv, generate_panel_dxf, nest_panels, write_nesting_dxf

st.set_page_config(page_title="Screenwall Makr", layout="wide")

SAMPLE_CSV = """panel_id,width,height,thickness,flange_type,flange1_depth,flange2_depth,hole_diameter,hole_pitch,pattern,fastening_pair,k_factor_override,bend_radius_override,gap_override
TEST-L-001,36,24,0.080,L,2,,0.25,1.0,straight,none,0.50,0.08,0.0528
TEST-J-001,36,24,0.080,J,2,0.75,0.25,1.0,straight,vertical,0.50,0.08,0.0528
"""


def make_zip(folder: str) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in Path(folder).rglob("*.dxf"):
            if path.is_file():
                zf.write(path, arcname=path.name)
    mem.seek(0)
    return mem.read()


st.title("Screenwall Makr")
st.caption("Fusion-calibrated flat-pattern generation for L and J panels.")

with st.sidebar:
    st.header("Output options")
    nest = st.checkbox("Generate nesting layout", value=True)
    stock_width = st.number_input("Stock width (in)", min_value=1.0, value=48.0, step=1.0)
    stock_height = st.number_input("Stock height (in)", min_value=1.0, value=96.0, step=1.0)

st.download_button(
    "Download revised sample CSV",
    SAMPLE_CSV,
    file_name="screenwall_sample_v2.csv",
    mime="text/csv",
)

uploaded = st.file_uploader("Upload CSV", type=["csv"])

if uploaded is not None:
    with tempfile.TemporaryDirectory() as d:
        csv_path = Path(d) / "input.csv"
        csv_path.write_bytes(uploaded.getvalue())

        try:
            panels = parse_csv(str(csv_path))
            st.success(f"Loaded {len(panels)} panel(s).")

            for pan in panels:
                generate_panel_dxf(pan, d)

            if nest:
                sheets = nest_panels(panels, stock_width, stock_height)
                write_nesting_dxf(sheets, d)

            z = make_zip(d)
            st.download_button("Download DXFs", z, "panels.zip", mime="application/zip")
        except Exception as e:
            st.error(f"Generation failed: {e}")
