"""DXF `finished_face` matches CSV face size; bend CLs on `bend` for L and J."""
import os
import tempfile
import unittest

import ezdxf

from screenwall_generator import (
    PanelSpec,
    flat_size,
    generate_panel_dxf,
    resolve_sides,
    _nominal_finished_face_rect_xy,
)


def _lines_on_layer(path: str, layer: str) -> int:
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    n = 0
    for e in msp:
        if e.dxf.layer != layer:
            continue
        if e.dxftype() == "LINE":
            n += 1
    return n


def _lwpolys_on_layer(path: str, layer: str) -> list[list[tuple[float, float]]]:
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    out: list[list[tuple[float, float]]] = []
    for e in msp:
        if e.dxftype() != "LWPOLYLINE":
            continue
        if e.dxf.layer != layer:
            continue
        out.append([(float(p[0]), float(p[1])) for p in e.get_points("xy")])
    return out


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


class FinishedFaceDxfTests(unittest.TestCase):
    def test_l4s_finished_face_matches_csv_dimensions(self):
        s = PanelSpec(
            panel_id="UNIT_FF_L4S",
            face_width=47.75,
            face_height=80.875,
            thickness=0.1875,
            flange_code="L4S",
            flange_type="L",
            flange1_depth=2.0,
            flange2_depth=None,
            hole_dia=0.75,
            pitch=1.25,
            pattern="straight",
            fastening_pair="tb",
        )
        td = tempfile.mkdtemp()
        try:
            generate_panel_dxf(s, td)
            path = os.path.join(td, "UNIT_FF_L4S.dxf")
            self.assertIn("finished_face", ezdxf.readfile(path).layers)
            ff = _lwpolys_on_layer(path, "finished_face")
            self.assertEqual(len(ff), 1)
            self.assertEqual(len(ff[0]), 4)
            x0, y0, x1, y1 = _bbox(ff[0])
            self.assertAlmostEqual(x1 - x0, s.face_width, places=4)
            self.assertAlmostEqual(y1 - y0, s.face_height, places=4)
            self.assertGreaterEqual(_lines_on_layer(path, "bend"), 4)
        finally:
            os.unlink(os.path.join(td, "UNIT_FF_L4S.dxf"))
            os.rmdir(td)

    def test_nominal_finished_face_matches_csv_dimensions(self):
        s = PanelSpec(
            panel_id="x",
            face_width=24.0,
            face_height=30.0,
            thickness=0.1875,
            flange_code="L4S",
            flange_type="L",
            flange1_depth=2.0,
            flange2_depth=None,
            hole_dia=0.75,
            pitch=1.25,
            pattern="straight",
            fastening_pair="none",
        )
        sides = resolve_sides(s)
        bw, bh = flat_size(s)
        ffx0, ffy0, ffx1, ffy1 = _nominal_finished_face_rect_xy(bw, bh, sides)
        self.assertAlmostEqual(ffx1 - ffx0, s.face_width, places=6)
        self.assertAlmostEqual(ffy1 - ffy0, s.face_height, places=6)

    def test_rt4s_finished_face_present(self):
        s = PanelSpec(
            panel_id="UNIT_FF_RT4S",
            face_width=24.0,
            face_height=30.0,
            thickness=0.1875,
            flange_code="RT4S",
            flange_type="L",
            flange1_depth=2.0,
            flange2_depth=None,
            hole_dia=0.75,
            pitch=1.25,
            pattern="straight",
            fastening_pair="tb",
            rt_opposing_edge="top",
            rt_leg_left=22.0,
            rt_leg_right=28.0,
        )
        td = tempfile.mkdtemp()
        try:
            generate_panel_dxf(s, td)
            path = os.path.join(td, "UNIT_FF_RT4S.dxf")
            self.assertIn("finished_face", ezdxf.readfile(path).layers)
            ff = _lwpolys_on_layer(path, "finished_face")
            self.assertEqual(len(ff), 1)
            self.assertEqual(len(ff[0]), 4)
            self.assertGreaterEqual(_lines_on_layer(path, "bend"), 4)
        finally:
            os.unlink(os.path.join(td, "UNIT_FF_RT4S.dxf"))
            os.rmdir(td)


if __name__ == "__main__":
    unittest.main()
