"""J-flange install slot shop rules (nominal F2 offset, outer perforation line)."""
import unittest

from screenwall_generator import (
    PanelSpec,
    _j_outer_face_holes,
    _j_slot_axis_coord_shop_ortho,
    _j_slot_stations_along_flange,
    _jj_miter_trim_horizontal,
    _nominal_finished_face_rect_xy,
    _nominal_f2_for_side,
    _ortho_void_inner_xy,
    _select_slots_outer_line,
    flat_size,
    resolve_sides,
    _hole_centers,
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


    def test_j4s_bottom_slot_x_stays_on_straight_lip_between_miters(self):
        """J+J bottom: slot stations must be >= void inner x0 + fh2 (short base), not blank 0."""
        spec = PanelSpec(
            panel_id="J4S_MITER",
            face_width=24.0,
            face_height=36.0,
            thickness=0.1875,
            flange_code="J4S",
            flange_type="J",
            flange1_depth=2.0,
            flange2_depth=2.25,
            hole_dia=0.75,
            pitch=1.25,
            pattern="straight",
            fastening_pair="b",
        )
        sides = resolve_sides(spec)
        bw, bh = flat_size(spec)
        ffx0, ffy0, ffx1, ffy1 = _nominal_finished_face_rect_xy(bw, bh, sides)
        face_w, face_h = ffx1 - ffx0, ffy1 - ffy0
        face_holes = _hole_centers(
            ffx0, ffy0, face_w, face_h,
            spec.hole_dia, spec.pitch, spec.pattern, spec.stagger_angle, spec.margin,
        )
        flen = spec.fastener_dia + 0.5
        xs = _j_slot_stations_along_flange(
            face_holes, "bottom", sides, bw, bh, slot_len=flen, spec=spec, lay=None,
        )
        self.assertTrue(xs, "expected at least one bottom slot")
        fx0, _fy0, _fx1, _fy1 = _ortho_void_inner_xy(bw, bh, sides)
        fh2_l, fh2_r = _jj_miter_trim_horizontal(sides, "bottom")
        self.assertGreater(fh2_l, 0.0, "J4S should have JJ bottom-left miter")
        self.assertGreater(fh2_r, 0.0, "J4S should have JJ bottom-right miter")
        min_x = fx0 + fh2_l + flen * 0.5
        max_x = _ortho_void_inner_xy(bw, bh, sides)[2] - fh2_r - flen * 0.5
        for x in xs:
            self.assertGreaterEqual(x, min_x - 1e-5)
            self.assertLessEqual(x, max_x + 1e-5)


if __name__ == "__main__":
    unittest.main()
