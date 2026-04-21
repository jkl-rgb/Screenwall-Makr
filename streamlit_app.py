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
st.title('Screenwall Makr')
st.caption('Production corner generator calibrated to the supplied 0.080 L and J reference DXFs.')

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
