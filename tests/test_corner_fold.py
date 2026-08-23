"""Corner fold clearance (shop rule: total gap = thickness, t/2 inset per side).

Covers:
- byte-parity with the fold-disabled golden fixture (regression safety net),
- exact staircase coordinates at square-end corners,
- exact perpendicular miter offsets at J+J corners (ortho and RT skew),
- 1:1 scaling with gauge and the corner_gap_override CSV column,
- inactive-side corners left untouched,
- install slots staying on the shortened straight lip,
- simple (non-self-intersecting) outlines for every fold-on spec.
"""
import dataclasses
import json
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import screenwall_generator as g
from tests.capture_outline_golden import golden_specs, entity_record

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "outline_golden.json")


def _cut_outline(doc, min_pts=8):
    for e in doc.modelspace():
        if (e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "cut"
                and len(e) >= min_pts):
            return [(x, y) for x, y, _ in e.get_points("xyb")]
    raise AssertionError("cut outline not found")


def _spec(panel_id):
    return next(s for s in golden_specs() if s.panel_id == panel_id)


def _has_pt(pts, x, y, tol=1e-6):
    return any(abs(px - x) < tol and abs(py - y) < tol for px, py in pts)


def _line_dist(p, a, b):
    """Perpendicular distance from p to the infinite line through a-b."""
    mx, my = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(mx, my)
    return abs((p[0] - a[0]) * (-my / ln) + (p[1] - a[1]) * (mx / ln))


class GoldenParityTests(unittest.TestCase):
    """With the fold disabled, every golden spec must be byte-identical."""

    def test_fold_disabled_matches_golden(self):
        golden = json.load(open(FIXTURE))
        for spec in golden_specs():
            spec = dataclasses.replace(spec, corner_gap_override=0.0)
            recs = sorted(
                (entity_record(e) for e in g.build_panel_document(spec).modelspace()),
                key=lambda r: json.dumps(r, sort_keys=True),
            )
            self.assertEqual(recs, golden[spec.panel_id], spec.panel_id)

    def test_only_cut_and_fastening_change_when_fold_enabled(self):
        """Holes, bend lines, etch and panel-id lettering must be untouched.

        The cut outline changes by design; fastening slots may re-station to
        stay on the shortened straight lip."""
        skip = {"cut", "fastening"}
        for spec in golden_specs():
            with self.subTest(spec.panel_id):
                off = g.build_panel_document(
                    dataclasses.replace(spec, corner_gap_override=0.0))
                on = g.build_panel_document(spec)

                def census(doc):
                    return sorted(
                        json.dumps(entity_record(e), sort_keys=True)
                        for e in doc.modelspace()
                        if e.dxf.layer not in skip
                    )
                self.assertEqual(census(off), census(on))


class CleanCornerTests(unittest.TestCase):
    """Square-end corners: both end edges step back t/2 and meet in a clean
    90-deg inside corner (no material tooth at the old hard corner)."""

    def test_l4s_bl_clean_inside_corner(self):
        spec = _spec("G-L4S")
        f = spec.thickness / 2.0
        pts = _cut_outline(g.build_panel_document(spec))
        off = _cut_outline(g.build_panel_document(
            dataclasses.replace(spec, corner_gap_override=0.0)))
        # legacy bl hard corner = min-x/min-y interior vertex of fold-off
        hcx = min(x for x, y in off if x > 0.5)
        hcy = min(y for x, y in off if y > 0.5)
        # shifted end edges + single recessed inside corner
        for x, y in ((0.0, hcy + f), (hcx + f, hcy + f), (hcx + f, 0.0)):
            self.assertTrue(_has_pt(pts, x, y), (x, y))
        # no tooth: the old hard corner and its staircase steps are gone
        self.assertFalse(_has_pt(pts, hcx, hcy))
        self.assertFalse(_has_pt(pts, hcx, hcy + f))
        self.assertFalse(_has_pt(pts, hcx + f, hcy))
        # legacy square corner points must be gone too
        self.assertFalse(_has_pt(pts, 0.0, hcy))
        self.assertFalse(_has_pt(pts, hcx, 0.0))

    def test_point_count_matches_legacy(self):
        spec = _spec("G-L4S")
        pts = _cut_outline(g.build_panel_document(spec))
        off = _cut_outline(g.build_panel_document(
            dataclasses.replace(spec, corner_gap_override=0.0)))
        # [arrival, corner, departure] per corner, same as legacy [arr, hc, dep]
        self.assertEqual(len(pts), len(off))

    def test_gap_scales_with_thickness(self):
        spec = dataclasses.replace(_spec("G-L4S"), thickness=0.125)
        f = 0.0625
        pts = _cut_outline(g.build_panel_document(spec))
        off = _cut_outline(g.build_panel_document(
            dataclasses.replace(spec, corner_gap_override=0.0)))
        hcx = min(x for x, y in off if x > 0.5)
        hcy = min(y for x, y in off if y > 0.5)
        self.assertTrue(_has_pt(pts, hcx + f, hcy + f))
        self.assertFalse(_has_pt(pts, hcx, hcy))

    def test_corner_gap_override_value(self):
        spec = dataclasses.replace(_spec("G-L4S"), corner_gap_override=0.5)
        pts = _cut_outline(g.build_panel_document(spec))
        off = _cut_outline(g.build_panel_document(
            dataclasses.replace(spec, corner_gap_override=0.0)))
        hcx = min(x for x, y in off if x > 0.5)
        hcy = min(y for x, y in off if y > 0.5)
        self.assertTrue(_has_pt(pts, hcx + 0.25, hcy + 0.25))


class MiterOffsetTests(unittest.TestCase):
    """J+J corners: miter edges offset perpendicular by t/2 into each lip."""

    def _first_miter(self, pts):
        """(blank_end, void_end) of the first bl miter segment of the walk."""
        return pts[0], pts[1]

    def test_j4s_miter_perpendicular_offset(self):
        spec = _spec("G-J4S")
        f = spec.thickness / 2.0
        on = _cut_outline(g.build_panel_document(spec))
        off = _cut_outline(g.build_panel_document(
            dataclasses.replace(spec, corner_gap_override=0.0)))
        a, b = self._first_miter(off)     # legacy bl left-lip miter
        p, q = self._first_miter(on)
        self.assertAlmostEqual(_line_dist(p, a, b), f, places=9)
        self.assertAlmostEqual(_line_dist(q, a, b), f, places=9)
        # lip shortens: blank endpoint slides f*sqrt(2) along the blank edge
        self.assertAlmostEqual(p[1] - a[1], f * math.sqrt(2.0), places=9)
        # leg end edges step back and meet in one clean recessed corner
        hc = off[2]                       # legacy [v_blank, v_void, hc, ...]
        self.assertTrue(_has_pt(on, hc[0] + f, hc[1] + f))
        self.assertFalse(_has_pt(on, hc[0], hc[1]))

    def test_rt4j_skew_miter_perpendicular_offset(self):
        spec = _spec("G-RT4J")           # rt_opposing_edge=bottom, angled edge
        f = spec.thickness / 2.0
        on = _cut_outline(g.build_panel_document(spec))
        off = _cut_outline(g.build_panel_document(
            dataclasses.replace(spec, corner_gap_override=0.0)))
        # bl corner: skewed bottom-lip miter is legacy pts[3] -> pts[4]
        a, b = off[3], off[4]
        p = min(on, key=lambda pt: math.hypot(pt[0] - b[0], pt[1] - b[1]))
        self.assertAlmostEqual(_line_dist(p, a, b), f, places=6)

    def test_gap_override_zero_keeps_legacy_jj(self):
        """corner_gap_override=0 must preserve the legacy Section 9 gap path."""
        spec = dataclasses.replace(_spec("G-J4S-GAP"), corner_gap_override=0.0)
        golden = json.load(open(FIXTURE))
        recs = sorted(
            (entity_record(e) for e in g.build_panel_document(spec).modelspace()),
            key=lambda r: json.dumps(r, sort_keys=True),
        )
        self.assertEqual(recs, golden["G-J4S-GAP"])


class InactiveSideTests(unittest.TestCase):
    def test_mix_inactive_right_corners_untouched(self):
        spec = _spec("G-MIX")            # right side inactive
        on = _cut_outline(g.build_panel_document(spec))
        off = _cut_outline(g.build_panel_document(
            dataclasses.replace(spec, corner_gap_override=0.0)))
        x_right = max(x for x, _ in off)
        self.assertEqual(
            sorted((x, y) for x, y in off if abs(x - x_right) < 1e-9),
            sorted((x, y) for x, y in on if abs(x - x_right) < 1e-9),
        )


class SlotStationTests(unittest.TestCase):
    def test_slots_stay_on_shortened_lip(self):
        spec = _spec("G-J4S")
        f = spec.thickness / 2.0
        doc = g.build_panel_document(spec)
        pts = _cut_outline(doc)
        sides = g._resolve_sides(spec) if hasattr(g, "_resolve_sides") else None
        # bottom lip straight span from the fold-on outline: between the two
        # bottom miter blank endpoints (points with y == 0)
        xs = sorted(x for x, y in pts if abs(y) < 1e-9)
        lo, hi = xs[0], xs[-1]
        slots = [e for e in doc.modelspace()
                 if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "fastening"]
        self.assertTrue(slots)
        for e in slots:
            sx = [p[0] for p in e.get_points("xyb")]
            sy = [p[1] for p in e.get_points("xyb")]
            if min(sy) < 1.0:  # bottom-lip slots only
                self.assertGreaterEqual(min(sx), lo - 1e-6)
                self.assertLessEqual(max(sx), hi + 1e-6)

    def test_trim_helper_values(self):
        j = g.SideDef(True, "J", 2.0, 1.0)
        l = g.SideDef(True, "L", 2.0, 0.0)
        off = g.SideDef(False, "L", 0.0, 0.0)
        f = 0.09375
        self.assertAlmostEqual(g._fold_lip_trim(j, j, f), f * math.sqrt(2.0))
        self.assertAlmostEqual(g._fold_lip_trim(j, l, f), f)
        self.assertEqual(g._fold_lip_trim(j, off, f), 0.0)
        self.assertEqual(g._fold_lip_trim(j, j, 0.0), 0.0)


class OutlineIntegrityTests(unittest.TestCase):
    def test_fold_on_outlines_are_simple_polygons(self):
        def _inter(s1, s2):
            (x1, y1), (x2, y2) = s1
            (x3, y3), (x4, y4) = s2
            d = (x2 - x1) * (y4 - y3) - (y2 - y1) * (x4 - x3)
            if abs(d) < 1e-12:
                return False
            t = ((x3 - x1) * (y4 - y3) - (y3 - y1) * (x4 - x3)) / d
            u = ((x3 - x1) * (y2 - y1) - (y3 - y1) * (x2 - x1)) / d
            return 1e-9 < t < 1 - 1e-9 and 1e-9 < u < 1 - 1e-9

        for spec in golden_specs():
            with self.subTest(spec.panel_id):
                pts = _cut_outline(g.build_panel_document(spec))
                segs = [(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
                for i in range(len(segs)):
                    for j in range(i + 2, len(segs)):
                        if i == 0 and j == len(segs) - 1:
                            continue
                        self.assertFalse(_inter(segs[i], segs[j]), (i, j))

    def test_blank_extents_unchanged(self):
        """Ortho panels: fold clearance removes corner material only, so the
        blank bbox must not change. (RT panels with an angled edge may lose
        the extreme tip of that edge to the lip cut, so they are excluded.)"""
        for spec in golden_specs():
            if spec.flange_code in ("RT4S", "RT4J"):
                continue
            with self.subTest(spec.panel_id):
                on = _cut_outline(g.build_panel_document(spec))
                off = _cut_outline(g.build_panel_document(
                    dataclasses.replace(spec, corner_gap_override=0.0)))
                for pick in (min, max):
                    self.assertAlmostEqual(pick(x for x, _ in on),
                                           pick(x for x, _ in off), places=6)
                    self.assertAlmostEqual(pick(y for _, y in on),
                                           pick(y for _, y in off), places=6)


class CsvColumnTests(unittest.TestCase):
    def test_parse_csv_reads_corner_gap_override(self):
        csv_text = (
            "panel_id,width,height,thickness,flange_code,flange1_depth,flange2_depth,"
            "hole_diameter,hole_pitch,pattern,fastening_pair,corner_gap_override\n"
            "P1,24,18,0.1875,J4S,2,1,0.25,1.0,straight,tb,0.125\n"
            "P2,24,18,0.1875,J4S,2,1,0.25,1.0,straight,tb,0\n"
            "P3,24,18,0.1875,J4S,2,1,0.25,1.0,straight,tb,\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
            fh.write(csv_text)
            path = fh.name
        try:
            rows = g.parse_csv(path)
        finally:
            os.unlink(path)
        self.assertEqual(rows[0].corner_gap_override, 0.125)
        self.assertEqual(rows[1].corner_gap_override, 0.0)   # explicit disable
        self.assertIsNone(rows[2].corner_gap_override)       # default = t


if __name__ == "__main__":
    unittest.main()
