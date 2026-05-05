import io
import tempfile
import zipfile
from pathlib import Path

import streamlit as st

from screenwall_generator import parse_csv, generate_panel_dxf


def _zip_dxfs(folder: str) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in Path(folder).glob('*.dxf'):
            z.write(f, f.name)
    mem.seek(0)
    return mem.read()


st.set_page_config(page_title='Screenwall Makr', layout='wide')

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
    "stagger_angle",
    "margin",
]

template_rows = [
    [
        "example_panel",
        "12.0",
        "24.0",
        "0.0800",
        "L",
        "1.0",
        "1.0",
        "0.125",
        "1.0",
        "staggered",
        "standard",
        "60.0",
        "1.25",
    ]
]

template_csv = ",".join(template_headers) + "\n"
for row in template_rows:
    template_csv += ",".join(row) + "\n"

st.title('Screenwall Makr')
st.caption('Production corner generator calibrated to the supplied 0.080 L and J reference DXFs.')

st.download_button(
    "Download CSV input template",
    data=template_csv,
    file_name="screenwall_template.csv",
    mime="text/csv",
)

st.caption("Use the downloaded CSV template to populate your panel data, then upload it below.")

uploaded = st.file_uploader('Upload CSV', type=['csv'])

if uploaded is not None:
    with tempfile.TemporaryDirectory() as d:
        csv_path = Path(d) / 'input.csv'
        csv_path.write_bytes(uploaded.getvalue())
        try:
            panels = parse_csv(str(csv_path))
            st.success(f'Loaded {len(panels)} panel(s).')
            if st.button('Generate DXFs', type='primary'):
                for panel in panels:
                    generate_panel_dxf(panel, d)
                z = _zip_dxfs(d)
                st.download_button('Download DXFs', z, 'screenwall_panels.zip', mime='application/zip')
        except Exception as e:
            st.error(str(e))
