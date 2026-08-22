"""Amada-style turret punch tape import (bare hits, pattern macros, set-up sheet).

Fixture amada_punch_grid.nc is a real post-processor output: two X/Y/T hit
blocks each followed by a G36 grid macro (17 x 5 = 85 hits each, 170 total),
tool geometry only present in the SET-UP SHEET after the closing '%'.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gcode_import import (
    _is_punch_program,
    _split_tape,
    _tools_from_setup_sheet,
    gcode_to_document,
    parse_gcode,
)

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "amada_punch_grid.nc")


def _centers(result):
    return sorted({(round(s[3][0], 4), round(s[3][1], 4))
                   for p in result.paths for s in p["segments"]})


class FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = open(FIXTURE).read()
        cls.result = parse_gcode(cls.text)

    def test_detected_as_punch(self):
        self.assertTrue(_is_punch_program(self.text))
        self.assertEqual(self.result.dialect, "punch")

    def test_grid_expansion_hit_count(self):
        # two G36 grids, (16+1) x (4+1) each, origin hit included once
        self.assertEqual(len(self.result.paths), 170)

    def test_all_hits_are_setup_sheet_rounds(self):
        for p in self.result.paths:
            self.assertEqual(p["layer"], "holes")
            self.assertEqual(p["shape"], "circle")
        doc, summary, _ = gcode_to_document(self.text)
        self.assertEqual(summary["hole_diameters"], [0.75])
        self.assertEqual(summary["paths_per_layer"], {"holes": 170})

    def test_grid_geometry(self):
        cs = _centers(self.result)
        # grid 1 origin and far corner
        self.assertIn((3.497, 12.729), cs)
        self.assertIn((round(3.497 + 16 * 1.25, 4), round(12.729 - 4 * 2.165, 4)), cs)
        # grid 2 origin (half-pitch stagger) and far corner
        self.assertIn((2.872, 11.646), cs)
        self.assertIn((round(2.872 + 16 * 1.25, 4), round(11.646 - 4 * 2.165, 4)), cs)
        # interleaved rows: 10 rows at ~2.165/2 spacing (the post rounds
        # coordinates to 3 decimals, so steps alternate 1.082 / 1.083)
        rows = sorted({y for _, y in cs})
        self.assertEqual(len(rows), 10)
        for a, b in zip(rows, rows[1:]):
            self.assertAlmostEqual(b - a, 2.165 / 2.0, delta=6e-4)

    def test_g92_does_not_punch(self):
        cs = _centers(self.result)
        self.assertNotIn((98.425, 60.039), cs)

    def test_panel_id_from_first_comment(self):
        self.assertEqual(self.result.panel_id, "WT-1.01 PUNCH")

    def test_machine_codes_reported_not_fatal(self):
        joined = " ".join(self.result.warnings)
        self.assertIn("M13", joined)
        self.assertIn("M692", joined)
        self.assertNotIn("skipped", joined)

    def test_document_rebuild(self):
        doc, summary, _ = gcode_to_document(self.text)
        circles = [e for e in doc.modelspace() if e.dxftype() == "CIRCLE"]
        self.assertEqual(len(circles), 170)
        self.assertTrue(all(abs(c.dxf.radius - 0.375) < 1e-9 for c in circles))


class TapeAndSetupSheetTests(unittest.TestCase):
    def test_split_tape(self):
        prog, trailer = _split_tape("%\nX1Y1T1\n%\nSET-UP SHEET\nT1 ROUND 0.5 9\n")
        self.assertEqual(prog, "X1Y1T1")
        self.assertIn("SET-UP", trailer)

    def test_setup_sheet_round_with_hits_column(self):
        tools = _tools_from_setup_sheet("T139      ROUND                 0.750\t\t\t\t170\t")
        self.assertEqual(tools, {"T139": ("RD", 0.750)})

    def test_setup_sheet_round_with_angle_and_hits(self):
        tools = _tools_from_setup_sheet("T2 ROUND 0 0.500 24")
        self.assertEqual(tools, {"T02": ("RD", 0.5)})

    def test_setup_sheet_obround(self):
        tools = _tools_from_setup_sheet("T21 OBROUND 90 0.281X1.000 8")
        self.assertEqual(tools, {"T21": ("OB", 0.281, 1.0, 90.0)})

    def test_setup_sheet_square(self):
        tools = _tools_from_setup_sheet("T5 SQUARE 0.625 4")
        self.assertEqual(tools, {"T05": ("RECT", 0.625, 0.625, 0.0)})


class PatternMacroTests(unittest.TestCase):
    def _parse(self, body, sheet="T1 ROUND 0.250 99"):
        return parse_gcode("%\n" + body + "\nG50\n%\n" + sheet + "\n")

    def test_g28_line_pattern(self):
        r = self._parse("X1.0Y1.0T1\nG28I0.5J90K3")
        self.assertEqual(_centers(r), [(1.0, 1.0), (1.0, 1.5), (1.0, 2.0), (1.0, 2.5)])

    def test_g26_bolt_circle_center_not_punched(self):
        r = self._parse("X9Y9T1\nG72X2.0Y2.0\nG26I1.0J0K4")
        cs = _centers(r)
        self.assertNotIn((2.0, 2.0), cs)          # G72 center never punched
        self.assertIn((3.0, 2.0), cs)
        self.assertIn((2.0, 3.0), cs)
        self.assertIn((1.0, 2.0), cs)
        self.assertIn((2.0, 1.0), cs)
        self.assertEqual(len(r.paths), 5)          # priming hit at (9,9) + 4

    def test_g36_inline_xy_includes_origin(self):
        # pattern on the same block: X/Y positions the origin without a hit,
        # so the grid supplies all (P+1)*(K+1) holes
        r = self._parse("X9Y9T1\nX1.0Y1.0G36I1.0P1J1.0K1")
        cs = _centers(r)
        for pt in ((1.0, 1.0), (2.0, 1.0), (1.0, 2.0), (2.0, 2.0)):
            self.assertIn(pt, cs)
        self.assertEqual(len(r.paths), 5)          # priming hit + 4 grid

    def test_modal_axis_hits(self):
        r = self._parse("X1.0Y1.0T1\nX2.0\nY3.0")
        self.assertEqual(_centers(r), [(1.0, 1.0), (2.0, 1.0), (2.0, 3.0)])

    def test_incremental_mode(self):
        r = self._parse("X1.0Y1.0T1\nG91\nX0.5Y0.5\nX0.5Y0.5")
        self.assertEqual(_centers(r), [(1.0, 1.0), (1.5, 1.5), (2.0, 2.0)])

    def test_unknown_tool_defaults_with_warning(self):
        r = self._parse("X1.0Y1.0T7", sheet="")
        self.assertEqual(len(r.paths), 1)
        self.assertTrue(any("T07" in w and "0.25" in w for w in r.warnings))


class ScrewwallRoundTripStillWorks(unittest.TestCase):
    def test_own_combo_punch_round_trip(self):
        """Screenwall-exported punch programs must still import losslessly."""
        from screenwall_generator import PanelSpec, build_panel_document
        from gcode_export import doc_to_gcode_combo
        spec = PanelSpec("RT-CHK", 24.0, 18.0, 0.1875, "J4S", "J", 2.0, 1.0,
                         0.25, 1.0, "straight", fastening_pair="tb")
        doc = build_panel_document(spec)
        combo = doc_to_gcode_combo(doc, spec.panel_id, spec.thickness)
        result = parse_gcode(combo["punch"])
        self.assertEqual(result.dialect, "punch")
        self.assertTrue(result.annotated)
        self.assertEqual(result.panel_id, "RT-CHK")
        n_holes = sum(1 for p in result.paths if p["layer"] == "holes")
        n_slots = sum(1 for p in result.paths if p["layer"] == "fastening")
        self.assertGreater(n_holes, 100)
        self.assertGreater(n_slots, 0)
        self.assertFalse(any("skipped" in w for w in result.warnings))


if __name__ == "__main__":
    unittest.main()
