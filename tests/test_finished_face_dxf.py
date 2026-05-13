"""DXF `finished_face` layer: shop finished-face rectangle (not bend CL, not cut poly offset)."""
import os
import tempfile
import unittest

import ezdxf

from screenwall_generator import (
    PanelSpec,
    SHOP_FINISHED_FACE_INSET,
    _ff_span_from_blank_edge,
    _finished_face_rect_xy,
    flat_size,
    generate_panel_dxf,
    resolve_sides,
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
    def test_l4s_finished_face_layer_matches_ff_span(self):
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
        sides = resolve_sides(s)
        bw, bh = flat_size(s)
        ffx0, ffy0, ffx1, ffy1 = _finished_face_rect_xy(bw, bh, sides)
        span_b = _ff_span_from_blank_edge(sides["bottom"])
        span_l = _ff_span_from_blank_edge(sides["left"])
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
            self.assertEqual(len(ff[0]), 4)
            bx0, by0, bx1, by1 = _bbox(ff[0])
            self.assertAlmostEqual(bx0, ffx0, places=5)
            self.assertAlmostEqual(by0, ffy0, places=5)
            self.assertAlmostEqual(bx1, ffx1, places=5)
            self.assertAlmostEqual(by1, ffy1, places=5)
            self.assertAlmostEqual(by0, span_b, places=5)
            self.assertAlmostEqual(bx0, span_l, places=5)
            # Finished face lies strictly inside the blank envelope when flanges exist
            mx0, my0, mx1, my1 = _bbox(cut[0])
            self.assertGreater(bx0, mx0 + 1e-6)
            self.assertGreater(by0, my0 + 1e-6)
            self.assertLess(bx1, mx1 - 1e-6)
            self.assertLess(by1, my1 - 1e-6)
            # Inset toward face from developed run (f1+f2)
            self.assertGreater(sides["bottom"].f1, by0 + SHOP_FINISHED_FACE_INSET - 1e-6)
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
            self.assertEqual(len(ff[0]), 4)
        finally:
            os.unlink(os.path.join(td, "UNIT_FF_RT4S.dxf"))
            os.rmdir(td)


if __name__ == "__main__":
    unittest.main()
