import streamlit as st
import tempfile, zipfile, io
from pathlib import Path

from screenwall_generator import parse_csv, generate_panel_dxf

def zip_dir(folder):
    mem = io.BytesIO()
    with zipfile.ZipFile(mem,'w') as z:
        for f in Path(folder).glob("*.dxf"):
            z.write(f, f.name)
    return mem.getvalue()

st.title("Screenwall Makr")

template_headers = [
    "panel_id",
    "width",
    "height",
    "thickness",
    "flange_type",
    "flange1_depth",
    "flange2_depth",
    "hole_diameter",
    "hole_pitch",
    "pattern",
    "fastening_pair",
]

template_rows = [
    [
        "example_panel",
        "12.0",
        "24.0",
        "0.0800",
        "plain",
        "1.0",
        "1.0",
        "0.125",
        "1.0",
        "round",
        "none",
    ]
]

template_csv = ",".join(template_headers) + "\n"
for row in template_rows:
    template_csv += ",".join(row) + "\n"

st.download_button(
    "Download CSV input template",
    data=template_csv,
    file_name="screenwall_template.csv",
    mime="text/csv",
)

st.caption("Use the downloaded CSV template to populate your panel data, then upload it below.")

csv = st.file_uploader("Upload CSV")

if csv:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d)/"input.csv"
        p.write_bytes(csv.getvalue())

        panels = parse_csv(p)

        for pan in panels:
            generate_panel_dxf(pan, d)

        z = zip_dir(d)
        st.download_button("Download DXFs", z, "panels.zip")
