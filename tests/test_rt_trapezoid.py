"""Tests for Right Trapezoid panel type (RT4S / RT4J)."""
import os
import tempfile
import unittest

import ezdxf

from screenwall_generator import (
    PanelSpec,
    SHOP_FLANGE_CORNER_INSET,
    flat_size,
    generate_panel_dxf,
    parse_csv,
    resolve_sides,
    _is_right_trapezoid,
    _panel_id_anchor_rt,
    _rt_nominal_face_corners,
    _rt_blank_layout,
    _rt_parallel_side,
    get_rules,
    _bd,
)


def _text_layer_points(path: str) -> list[tuple[float, float]]:
    doc = ezdxf.readfile(path)
    pts = []
    for e in doc.modelspace():
        if e.dxftype() != "LWPOLYLINE" or e.dxf.layer != "text":
            continue
        for p in e.get_points("xy"):
            pts.append((float(p[0]), float(p[1])))
    return pts


def _rt_spec(**overrides):
    base = dict(
        panel_id="T",
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
    base.update(overrides)
    return PanelSpec(**base)


class RightTrapezoidTests(unittest.TestCase):
    def test_is_rt(self):
        s = _rt_spec()
        self.assertTrue(_is_right_trapezoid(s))
        self.assertFalse(_is_right_trapezoid(_rt_spec(flange_code="L4S")))

    def test_flat_size_positive(self):
        s = _rt_spec()
        bw, bh = flat_size(s)
        self.assertGreater(bw, s.face_width)
        self.assertGreater(bh, max(s.rt_leg_left or 0, s.rt_leg_right or 0))

    def test_layout_corners_ccw(self):
        s = _rt_spec(rt_leg_left=20.0, rt_leg_right=26.0)
        sides = resolve_sides(s)
        rules = get_rules(s)
        bd = _bd(rules["r"], rules["k"], s.thickness)
        lay = _rt_blank_layout(s, sides, bd)
        bl, br, tr, tl = lay["bl"], lay["br"], lay["tr"], lay["tl"]
        self.assertAlmostEqual(bl[0], lay["fx0"])
        self.assertAlmostEqual(bl[1], lay["fy0"])
        self.assertAlmostEqual(br[1], bl[1])
        self.assertGreater(tr[1], br[1])
        self.assertGreater(tl[1], bl[1])

    def test_generate_dxf_rt4s(self):
        s = _rt_spec(panel_id="UNIT_RT4S")
        td = tempfile.mkdtemp()
        try:
            generate_panel_dxf(s, td)
            path = os.path.join(td, "UNIT_RT4S.dxf")
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 1000)
        finally:
            os.unlink(os.path.join(td, "UNIT_RT4S.dxf"))
            os.rmdir(td)

    def test_generate_dxf_rt4j_bottom(self):
        s = _rt_spec(
            panel_id="UNIT_RT4J",
            flange_code="RT4J",
            flange_type="J",
            flange2_depth=2.25,
            fastening_pair="lr",
            rt_opposing_edge="bottom",
            rt_leg_left=21.0,
            rt_leg_right=25.0,
        )
        td = tempfile.mkdtemp()
        try:
            generate_panel_dxf(s, td)
            path = os.path.join(td, "UNIT_RT4J.dxf")
            self.assertTrue(os.path.isfile(path))
        finally:
            os.unlink(path)
            os.rmdir(td)

    def test_panel_id_on_parallel_bottom_when_opposing_top(self):
        s = _rt_spec(panel_id="RT_ID_BOT")
        sides = resolve_sides(s)
        rules = get_rules(s)
        bd = _bd(rules["r"], rules["k"], s.thickness)
        lay = _rt_blank_layout(s, sides, bd)
        bl, br, tr, tl = lay["bl"], lay["br"], lay["tr"], lay["tl"]
        ffc = list(_rt_nominal_face_corners(bl, br, tr, tl, sides, SHOP_FLANGE_CORNER_INSET))
        ff_bl = ffc[0]
        o_bl = lay["outer"][0]
        self.assertEqual(_rt_parallel_side(s), "bottom")
        anchor = _panel_id_anchor_rt(s, sides, lay, ffc, [], lay["bw"], lay["bh"], 0.75)
        self.assertIsNotNone(anchor)
        self.assertEqual(anchor["side"], "bottom")
        self.assertAlmostEqual(anchor["y"], (ff_bl[1] + o_bl[1]) * 0.5, places=3)

        td = tempfile.mkdtemp()
        try:
            generate_panel_dxf(s, td)
            path = os.path.join(td, "RT_ID_BOT.dxf")
            pts = _text_layer_points(path)
            self.assertTrue(pts)
            ys = [p[1] for p in pts]
            cy = (min(ys) + max(ys)) * 0.5
            self.assertLess(cy, min(tr[1], tl[1]) - 1.0)
            self.assertGreater(cy, min(o_bl[1], ff_bl[1]) - 0.05)
            self.assertLess(cy, max(o_bl[1], ff_bl[1]) + 0.05)
        finally:
            os.unlink(os.path.join(td, "RT_ID_BOT.dxf"))
            os.rmdir(td)

    def test_parse_csv_rt_row(self):
        td = tempfile.mkdtemp()
        path = os.path.join(td, "rt.csv")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "panel_id,width,height,thickness,material,alloy,"
                    "flange_code,flange1_depth,flange2_depth,"
                    "hole_diameter,hole_pitch,pattern,fastening_pair,"
                    "fastener_dia,slot_length,stagger_angle,margin,"
                    "rt_opposing_edge,rt_leg_left,rt_leg_right\n"
                    "CSV_RT,18,20,0.1875,aluminum,3003,"
                    "RT4S,2.0,,0.75,1.25,straight,tb,0.1875,,60,1.25,"
                    "top,18,22\n"
                )
            rows = parse_csv(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].flange_code, "RT4S")
            self.assertEqual(rows[0].rt_opposing_edge, "top")
            self.assertEqual(rows[0].rt_leg_left, 18.0)
            self.assertEqual(rows[0].rt_leg_right, 22.0)
        finally:
            os.unlink(path)
            os.rmdir(td)


if __name__ == "__main__":
    unittest.main()
