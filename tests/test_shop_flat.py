"""Shop-calibrated developed flats (finished-face artwork vs bend theory)."""
import unittest

from screenwall_generator import (
    L_BEND_CL_OUTWARD,
    PanelSpec,
    SHOP_FINISHED_FACE_INSET,
    SHOP_FLANGE_CORNER_INSET,
    SHOP_FLAT_CALIBRATION_REF,
    SHOP_F1_BEND_INSET_REF,
    get_rules,
    resolve_sides,
    _developed_leg_L,
    _developed_leg_J,
    _developed_lip_J,
    _f1_bend_inset_from_face,
)


def _spec(**kw):
    d = dict(
        panel_id="S",
        face_width=24.0,
        face_height=24.0,
        thickness=0.1875,
        flange_code="L4S",
        flange_type="L",
        flange1_depth=2.0,
        flange2_depth=None,
        hole_dia=0.75,
        pitch=1.25,
        pattern="straight",
        fastening_pair="none",
        material="aluminum",
        alloy="5052",
        shop_flat_mode="auto",
    )
    d.update(kw)
    return PanelSpec(**d)


class ShopFlatCalibrationTests(unittest.TestCase):
    def test_anchor_L_matches_shop_cutline(self):
        spec = _spec()
        rules = get_rules(spec)
        f1 = _developed_leg_L(spec, 2.0, rules)
        self.assertAlmostEqual(f1, 1.665, places=3)

    def test_anchor_J_matches_shop_cutline(self):
        spec = _spec(flange_code="J4S", flange_type="J", flange2_depth=1.75)
        rules = get_rules(spec)
        f1 = _developed_leg_J(spec, 2.0, rules)
        f2 = _developed_lip_J(spec, 1.75, rules)
        # 78060 / WT-1.01 calibration: deduction 0.335 per bend at 3/16"
        # (J leg 1.5x, lip 0.5x); J side hc->blank = 3.080 + 0.195 = 3.275
        # vs 3.267 measured (F2 = 1-3/4).
        self.assertAlmostEqual(f1, 2.0 - 1.5 * 0.335, places=6)
        self.assertAlmostEqual(f2, 1.75 - 0.5 * 0.335, places=6)
        self.assertAlmostEqual(f1 + f2, 3.080, places=3)

    def test_alloy_does_not_shift_shop_flats(self):
        """78060 evidence: 3003 artwork uses the same cut lines as 5052."""
        for alloy in ("5052", "3003"):
            spec = _spec(alloy=alloy)
            rules = get_rules(spec)
            self.assertAlmostEqual(
                _developed_leg_L(spec, 2.0, rules), 1.665, places=6, msg=alloy)

    def test_shop_deduction_scales_with_thickness(self):
        spec = _spec(thickness=0.125)
        rules = get_rules(spec)
        expect = 2.0 - 0.335 * (0.125 / 0.1875)
        self.assertAlmostEqual(_developed_leg_L(spec, 2.0, rules), expect, places=6)

    def test_f1_bend_inset_at_calibration_alloy(self):
        spec = _spec()
        rules = get_rules(spec)
        self.assertAlmostEqual(_f1_bend_inset_from_face(spec, rules), SHOP_F1_BEND_INSET_REF, places=3)

    def test_off_mode_matches_theory_only(self):
        spec = _spec(shop_flat_mode="off")
        rules = get_rules(spec)
        f1_auto = _developed_leg_L(_spec(), 2.0, get_rules(_spec()))
        f1_off = _developed_leg_L(spec, 2.0, rules)
        self.assertNotAlmostEqual(f1_auto, f1_off, places=3)

    def test_finished_face_inset_constant(self):
        self.assertAlmostEqual(SHOP_FLANGE_CORNER_INSET, 0.195, places=3)
        self.assertAlmostEqual(SHOP_FINISHED_FACE_INSET, SHOP_FLANGE_CORNER_INSET, places=6)
        self.assertAlmostEqual(L_BEND_CL_OUTWARD, 0.027, places=3)
        self.assertAlmostEqual(SHOP_FLAT_CALIBRATION_REF["L_flat_over_f1_od"], 1.665 / 2.0, places=6)


if __name__ == "__main__":
    unittest.main()
