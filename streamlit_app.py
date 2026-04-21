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