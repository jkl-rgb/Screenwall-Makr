import csv
import io
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from screenwall_generator import (
    PanelSpec,
    generate_panel_dxf,
    nest_panels,
    write_nesting_dxf,
)

APP_TITLE = "Screenwall Makr"


def auth_gate():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Generate perforated panel DXFs and optional nesting layouts from a CSV or a single panel form.")

    auth_enabled = False
    try:
        auth_enabled = bool(st.secrets.get("auth", {}).get("required", False))
    except Exception:
        auth_enabled = False

    if auth_enabled:
        if not st.user.is_logged_in:
            st.info("Please sign in to use this app.")
            if st.button("Sign in"):
                st.login()
            st.stop()
        else:
            c1, c2 = st.columns([4, 1])
            with c1:
                st.success(f"Signed in as {st.user.get('email', 'user')}")
            with c2:
                if st.button("Sign out"):
                    st.logout()
                    st.stop()


def sample_csv_bytes() -> bytes:
    rows = [
        ["panel_id", "width", "height", "thickness", "flange_type", "flange1_depth", "flange2_depth", "hole_diameter", "hole_pitch", "pattern"],
        ["panel1", "24", "36", "0.0625", "L", "2", "", "0.25", "1.0", "straight"],
        ["panel2", "24", "36", "0.080", "J", "2", "1", "0.375", "1.25", "staggered"],
    ]
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerows(rows)
    return sio.getvalue().encode("utf-8")


def build_manual_csv(form_data: dict) -> bytes:
    rows = [
        ["panel_id", "width", "height", "thickness", "flange_type", "flange1_depth", "flange2_depth", "hole_diameter", "hole_pitch", "pattern"],
        [
            form_data["panel_id"],
            form_data["width"],
            form_data["height"],
            form_data["thickness"],
            form_data["flange_type"],
            form_data["flange1_depth"],
            form_data.get("flange2_depth", ""),
            form_data["hole_diameter"],
            form_data["hole_pitch"],
            form_data["pattern"],
        ],
    ]
    sio = io.StringIO()
    writer = csv.writer(sio)
    writer.writerows(rows)
    return sio.getvalue().encode("utf-8")


def parse_csv_file(uploaded_file) -> list[PanelSpec]:
    import screenwall_generator as sg

    with tempfile.NamedTemporaryFile(delete=False, suffix=".csv") as tmp:
        tmp.write(uploaded_file.read())
        tmp_path = tmp.name
    try:
        return sg.parse_csv(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def generate_outputs(panels: list[PanelSpec], stock_width: float, stock_height: float, nest: bool) -> tuple[bytes, list[str]]:
    messages = []
    with tempfile.TemporaryDirectory() as tmpdir:
        outdir = Path(tmpdir) / "output"
        outdir.mkdir(parents=True, exist_ok=True)

        for panel in panels:
            generate_panel_dxf(panel, str(outdir))
        messages.append(f"Generated {len(panels)} panel DXF file(s).")

        if nest:
            sheets = nest_panels(panels, stock_width, stock_height)
            panel_dict = {p.panel_id: p for p in panels}
            write_nesting_dxf(sheets, output_dir)
            messages.append(f"Generated nesting layout across {len(sheets)} stock sheet(s).")

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in outdir.iterdir():
                if file_path.is_file():
                    zf.write(file_path, arcname=file_path.name)
        return zip_buffer.getvalue(), messages


def main():
    auth_gate()

    st.download_button(
        "Download sample CSV",
        data=sample_csv_bytes(),
        file_name="screenwall_sample.csv",
        mime="text/csv",
    )

    tab1, tab2 = st.tabs(["Upload CSV", "Single Panel Form"])

    panels = None

    with tab1:
        uploaded = st.file_uploader("Upload panel CSV", type=["csv"])
        if uploaded is not None:
            try:
                uploaded.seek(0)
                panels = parse_csv_file(uploaded)
                st.success(f"Loaded {len(panels)} panel row(s).")
            except Exception as e:
                st.error(f"CSV error: {e}")

    with tab2:
        with st.form("single_panel"):
            c1, c2, c3 = st.columns(3)
            panel_id = c1.text_input("Panel ID", value="panel1")
            width = c2.number_input("Face width (in)", min_value=0.1, value=24.0, step=0.25)
            height = c3.number_input("Face height (in)", min_value=0.1, value=36.0, step=0.25)

            c4, c5, c6 = st.columns(3)
            thickness = c4.selectbox("Thickness", ["0.0625", "0.080", "0.125", "0.1875", "16 Ga", "14 Ga", "11 Ga"])
            flange_type = c5.selectbox("Flange type", ["L", "J"])
            pattern = c6.selectbox("Hole pattern", ["straight", "staggered"])

            c7, c8, c9, c10 = st.columns(4)
            flange1_depth = c7.number_input("Flange 1 depth (in)", min_value=0.0, value=2.0, step=0.25)
            flange2_depth = c8.number_input("Flange 2 depth (J only)", min_value=0.0, value=1.0, step=0.25)
            hole_diameter = c9.number_input("Hole diameter (in)", min_value=0.125, value=0.25, step=0.125)
            hole_pitch = c10.number_input("Hole pitch (in)", min_value=0.125, value=1.0, step=0.125)

            submitted = st.form_submit_button("Use this panel")
            if submitted:
                fake_upload = io.BytesIO(
                    build_manual_csv(
                        {
                            "panel_id": panel_id,
                            "width": width,
                            "height": height,
                            "thickness": thickness,
                            "flange_type": flange_type,
                            "flange1_depth": flange1_depth,
                            "flange2_depth": flange2_depth if flange_type == "J" else "",
                            "hole_diameter": hole_diameter,
                            "hole_pitch": hole_pitch,
                            "pattern": pattern,
                        }
                    )
                )
                fake_upload.name = "single_panel.csv"
                fake_upload.seek(0)
                panels = parse_csv_file(fake_upload)
                st.success("Single panel loaded.")

    st.subheader("Output Options")
    c1, c2, c3 = st.columns(3)
    nest = c1.checkbox("Generate nesting layout", value=True)
    stock_width = c2.number_input("Stock width (in)", min_value=1.0, value=48.0, step=1.0)
    stock_height = c3.number_input("Stock height (in)", min_value=1.0, value=96.0, step=1.0)

    if st.button("Generate DXFs", type="primary", disabled=not panels):
        try:
            zip_bytes, messages = generate_outputs(panels, stock_width, stock_height, nest)
            for msg in messages:
                st.success(msg)
            st.download_button(
                "Download DXF ZIP",
                data=zip_bytes,
                file_name="screenwall_output.zip",
                mime="application/zip",
            )
        except Exception as e:
            st.error(f"Generation failed: {e}")


if __name__ == "__main__":
    main()
