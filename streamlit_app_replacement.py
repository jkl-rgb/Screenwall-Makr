
import io
import os
import zipfile
import tempfile
from pathlib import Path

import streamlit as st

from screenwall_generator import (
    PanelSpec,
    parse_csv,
    generate_panel_dxf,
    nest_panels,
    write_nesting_dxf,
)

st.set_page_config(page_title="Screenwall Makr", layout="wide")

SAMPLE_CSV = """panel_id,width,height,thickness,flange_type,flange1_depth,flange2_depth,hole_diameter,hole_pitch,pattern
TEST-001,24,36,0.0625,L,2,,0.25,1.0,straight
TEST-002,24,36,0.0625,J,2,1,0.375,1.25,staggered
"""


def make_zip_from_folder(folder: str) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in Path(folder).rglob("*"):
            if path.is_file():
                zf.write(path, arcname=path.name)
    mem.seek(0)
    return mem.read()


def generate_outputs(panels, nest: bool, stock_width: float, stock_height: float):
    messages = []
    with tempfile.TemporaryDirectory() as outdir:
        for panel in panels:
            generate_panel_dxf(panel, outdir)
        messages.append(f"Generated {len(panels)} panel DXF file(s).")

        if nest:
            sheets = nest_panels(panels, stock_width, stock_height)
            write_nesting_dxf(sheets, str(outdir))
            messages.append(f"Generated nesting layout across {len(sheets)} stock sheet(s).")

        zip_bytes = make_zip_from_folder(outdir)
    return zip_bytes, messages


def build_panel_from_form():
    c1, c2, c3 = st.columns(3)
    panel_id = c1.text_input("Panel ID", value="TEST-001")
    width = c2.number_input("Face width (in)", min_value=0.01, value=24.0, step=0.25)
    height = c3.number_input("Face height (in)", min_value=0.01, value=36.0, step=0.25)

    c4, c5, c6 = st.columns(3)
    thickness = c4.selectbox(
        "Material thickness",
        options=["0.0625", "0.08", "0.125", "0.1875"],
        index=0,
    )
    flange_type = c5.selectbox("Flange type", options=["L", "J"], index=0)
    pattern = c6.selectbox("Pattern", options=["straight", "staggered"], index=0)

    c7, c8, c9, c10 = st.columns(4)
    flange1_depth = c7.number_input("Flange 1 depth (in)", min_value=0.0, value=2.0, step=0.25)
    flange2_depth = c8.number_input("Flange 2 depth (in)", min_value=0.0, value=1.0, step=0.25)
    hole_diameter = c9.number_input("Hole diameter (in)", min_value=0.125, value=0.25, step=0.125)
    hole_pitch = c10.number_input("Hole pitch (in)", min_value=0.125, value=1.0, step=0.125)

    if flange_type == "L":
        flange2 = None
    else:
        flange2 = float(flange2_depth)

    return PanelSpec(
        panel_id=panel_id.strip() or "PANEL-001",
        face_width=float(width),
        face_height=float(height),
        thickness=float(thickness),
        flange_type=flange_type,
        flange1_depth=float(flange1_depth),
        flange2_depth=flange2,
        hole_dia=float(hole_diameter),
        pitch=float(hole_pitch),
        pattern=pattern,
    )


def parse_uploaded_csv(uploaded_file):
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / uploaded_file.name
        csv_path.write_bytes(uploaded_file.getvalue())
        panels = parse_csv(str(csv_path))
    return panels


st.title("Screenwall Makr")
st.caption("Generate perforated panel DXFs and optional nesting layouts.")

with st.sidebar:
    st.header("Output options")
    nest = st.checkbox("Generate nesting layout", value=True)
    stock_width = st.number_input("Stock width (in)", min_value=1.0, value=48.0, step=1.0)
    stock_height = st.number_input("Stock height (in)", min_value=1.0, value=96.0, step=1.0)

tab1, tab2 = st.tabs(["CSV Upload", "Manual Single Panel"])

with tab1:
    st.subheader("Batch generate from CSV")
    st.download_button(
        "Download sample CSV",
        SAMPLE_CSV,
        file_name="screenwall_sample.csv",
        mime="text/csv",
    )
    uploaded = st.file_uploader("Upload panel CSV", type=["csv"], key="csv_upload")

    if uploaded is not None:
        try:
            panels = parse_uploaded_csv(uploaded)
            st.success(f"Loaded {len(panels)} panel(s) from CSV.")
            preview_rows = []
            for p in panels:
                preview_rows.append(
                    {
                        "panel_id": p.panel_id,
                        "width": p.face_width,
                        "height": p.face_height,
                        "thickness": p.thickness,
                        "flange_type": p.flange_type,
                        "hole_diameter": p.hole_dia,
                        "hole_pitch": p.pitch,
                        "pattern": p.pattern,
                    }
                )
            st.dataframe(preview_rows, use_container_width=True)

            if st.button("Generate DXFs from CSV", type="primary", key="gen_csv"):
                zip_bytes, messages = generate_outputs(panels, nest, stock_width, stock_height)
                for msg in messages:
                    st.success(msg)
                st.download_button(
                    "Download ZIP",
                    zip_bytes,
                    file_name="screenwall_outputs.zip",
                    mime="application/zip",
                    key="download_csv_zip",
                )
        except Exception as e:
            st.error(f"Generation failed: {e}")

with tab2:
    st.subheader("Create one panel manually")
    try:
        panel = build_panel_from_form()
        st.write(
            {
                "panel_id": panel.panel_id,
                "width": panel.face_width,
                "height": panel.face_height,
                "thickness": panel.thickness,
                "flange_type": panel.flange_type,
                "flange1_depth": panel.flange1_depth,
                "flange2_depth": panel.flange2_depth,
                "hole_diameter": panel.hole_dia,
                "hole_pitch": panel.pitch,
                "pattern": panel.pattern,
            }
        )

        if st.button("Generate DXFs for this panel", type="primary", key="gen_manual"):
            zip_bytes, messages = generate_outputs([panel], nest, stock_width, stock_height)
            for msg in messages:
                st.success(msg)
            st.download_button(
                "Download ZIP",
                zip_bytes,
                file_name=f"{panel.panel_id}_outputs.zip",
                mime="application/zip",
                key="download_manual_zip",
            )
    except Exception as e:
        st.error(f"Generation failed: {e}")
