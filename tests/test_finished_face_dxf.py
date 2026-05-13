"""DXF `finished_face` layer: shop datum outline offset outward from perimeter cut."""
import os
import tempfile
import unittest

import ezdxf

from screenwall_generator import (
    PanelSpec,
    SHOP_FINISHED_FACE_INSET,
    generate_panel_dxf,
)


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
    def test_l4s_finished_face_layer_and_outset(self):
        s = PanelSpec(
            panel_id="UNIT_FF_L4S",
            face_width=24,
            face_height=30,
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
            doc = ezdxf.readfile(path)
            self.assertIn("finished_face", doc.layers)
            cut = _lwpolys_on_layer(path, "cut")
            ff = _lwpolys_on_layer(path, "finished_face")
            self.assertEqual(len(cut), 1)
            self.assertEqual(len(ff), 1)
            d = SHOP_FINISHED_FACE_INSET
            mx0, my0, mx1, my1 = _bbox(cut[0])
            fx0, fy0, fx1, fy1 = _bbox(ff[0])
            self.assertLessEqual(fx0, mx0 - d + 1e-5)
            self.assertLessEqual(fy0, my0 - d + 1e-5)
            self.assertGreaterEqual(fx1, mx1 + d - 1e-5)
            self.assertGreaterEqual(fy1, my1 + d - 1e-5)
        finally:
            os.unlink(os.path.join(td, "UNIT_FF_L4S.dxf"))
            os.rmdir(td)

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
            doc = ezdxf.readfile(path)
            self.assertIn("finished_face", doc.layers)
            ff = _lwpolys_on_layer(path, "finished_face")
            self.assertEqual(len(ff), 1)
            self.assertGreaterEqual(len(ff[0]), 3)
        finally:
            os.unlink(os.path.join(td, "UNIT_FF_RT4S.dxf"))
            os.rmdir(td)


if __name__ == "__main__":
    unittest.main()
