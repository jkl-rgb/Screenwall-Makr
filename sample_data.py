"""CSV template data and built-in sample outputs.

No Streamlit dependency: the web UI imports the template and the sample
builder from here, and the sample builder is unit-testable headlessly.
The sample runs the exact production path (CSV row -> parse_csv ->
build_panel_document -> DXF + G-code), so what the button shows is what a
real upload would produce.
"""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

from screenwall_generator import build_panel_document, parse_csv
from gcode_export import GCodeConfig, doc_to_gcode, doc_to_gcode_combo
from pdf_preview import doc_to_pdf

# ---------------------------------------------------------------------------
# CSV template (downloadable from the UI). Keep data-only: spreadsheet apps
# can rewrite comment-prefixed instruction rows in ways that break uploads.
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
# enough to drive BOTH gauge decoding and bend-rule lookup to the steel row.
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

TEMPLATE_CSV = (
    ",".join(ALL_HEADERS) + "\n"
    + ",".join(STANDARD_EXAMPLE) + "\n"
    + ",".join(MIX_EXAMPLE) + "\n"
    + ",".join(STEEL_EXAMPLE) + "\n"
    + ",".join(RT_EXAMPLE) + "\n"
)

# ---------------------------------------------------------------------------
# Built-in sample ("Show me a sample file" button)
# ---------------------------------------------------------------------------

def build_sample_files(config: GCodeConfig | None = None):
    """Generate the sample panel's full output set in memory.

    Returns (spec, files) where files maps filename -> bytes:
      {id}.pdf            one-page geometry preview (color per layer)
      {id}.dxf            shop DXF
      {id}.nc             single-machine laser program
      {id}_punch.nc       combo: turret punch hits
      {id}_laser.nc       combo: etch + perimeter for the laser
    """
    cfg = config or GCodeConfig()
    csv_text = ",".join(ALL_HEADERS) + "\n" + ",".join(STANDARD_EXAMPLE) + "\n"
    with tempfile.TemporaryDirectory() as d:
        csv_path = Path(d) / "sample.csv"
        csv_path.write_text(csv_text, encoding="utf-8")
        spec = parse_csv(str(csv_path))[0]
        doc = build_panel_document(spec)
        dxf_path = Path(d) / f"{spec.panel_id}.dxf"
        doc.saveas(str(dxf_path))
        combo = doc_to_gcode_combo(doc, spec.panel_id, spec.thickness, cfg)
        pdf_title = (
            f"{spec.panel_id} - {spec.face_width} x {spec.face_height} in, "
            f"{spec.thickness} in {spec.material} {spec.alloy}, {spec.flange_code}"
        )
        files = {
            f"{spec.panel_id}.pdf": doc_to_pdf(doc, pdf_title),
            f"{spec.panel_id}.dxf": dxf_path.read_bytes(),
            f"{spec.panel_id}.nc": doc_to_gcode(doc, spec.panel_id, spec.thickness, cfg).encode("ascii"),
            f"{spec.panel_id}_punch.nc": combo["punch"].encode("ascii"),
            f"{spec.panel_id}_laser.nc": combo["laser"].encode("ascii"),
        }
    return spec, files


def zip_files(files: dict) -> bytes:
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    mem.seek(0)
    return mem.read()
