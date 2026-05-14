"""J-flange install slot shop rules (nominal F2 offset, outer perforation line)."""
import unittest

from screenwall_generator import (
    PanelSpec,
    _j_outer_face_holes,
    _j_slot_axis_coord_shop_ortho,
    _nominal_f2_for_side,
    _select_slots_outer_line,
)


class JFasteningShopTests(unittest.TestCase):
    def test_nominal_f2_minus_hole_to_face_bottom(self):
        # Bottom: 1" OC from hole to inner bottom face edge, F2 nominal 1.5" → 0.5" from blank y=0.
        hy = _j_slot_axis_coord_shop_ortho(
            "bottom", 1.5, 10.0, 5.0, 20.0, 25.0, 100.0, 80.0, 12.0, 6.0
        )
        self.assertAlmostEqual(hy, 0.5, places=6)

    def test_nominal_f2_mix_side(self):
        s = PanelSpec(
            panel_id="x",
            face_width=24.0,
            face_height=30.0,
            thickness=0.1875,
            flange_code="MIX",
            flange_type="J",
            flange1_depth=2.0,
            flange2_depth=1.75,
            hole_dia=0.75,
            pitch=1.25,
            pattern="straight",
            fastening_pair="none",
            bottom_f2=2.25,
        )
        self.assertAlmostEqual(_nominal_f2_for_side(s, "bottom"), 2.25)

    def test_outer_face_row_excludes_staggered_inner_row(self):
        # Two rows at y=0 and y=2; bottom flange uses only y≈0 row.
        holes = [(0.0, 0.0), (1.0, 0.0), (0.5, 2.0), (1.5, 2.0)]
        outer = _j_outer_face_holes(holes, "bottom", tol=0.01)
        self.assertEqual(len(outer), 2)
        self.assertTrue(all(abs(y) < 0.02 for _, y in outer))

    def test_select_slots_inserts_when_gap_exceeds_12(self):
        vals = [0.0, 7.0, 14.0, 22.0]
        sel = _select_slots_outer_line(vals)
        for i in range(len(sel) - 1):
            self.assertLessEqual(sel[i + 1] - sel[i], 12.0 + 1e-6)


if __name__ == "__main__":
    unittest.main()
