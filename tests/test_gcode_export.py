"""G-code exporter: program structure, arc math, op ordering, DXF refactor parity."""
import math
import os
import re
import tempfile
import unittest

import ezdxf

from screenwall_generator import PanelSpec, build_panel_document, generate_panel_dxf
from gcode_export import (
    GCodeConfig,
    extract_paths,
    generate_panel_gcode,
    generate_panel_gcode_combo,
    panel_gcode,
    panel_gcode_combo,
)


def _spec(panel_id="UNIT_GC", flange_code="J4S", **kw):
    base = dict(
        panel_id=panel_id,
        face_width=24.0,
        face_height=36.0,
        thickness=0.1875,
        flange_code=flange_code,
        flange_type=flange_code[0] if flange_code[0] in ("L", "J") else "L",
        flange1_depth=2.0,
        flange2_depth=2.25 if "J" in flange_code else None,
        hole_dia=0.75,
        pitch=1.25,
        pattern="staggered",
        fastening_pair="tb",
        fastener_dia=0.1875,
        material="aluminum",
        alloy="3003",
    )
    base.update(kw)
    return PanelSpec(**base)


def _rt_spec():
    return _spec(
        panel_id="UNIT_GC_RT",
        flange_code="RT4S",
        flange2_depth=None,
        rt_opposing_edge="top",
        rt_leg_left=24.0,
        rt_leg_right=30.0,
    )


class DxfRefactorParityTests(unittest.TestCase):
    def test_generate_panel_dxf_still_writes_same_entity_counts(self):
        """build_panel_document refactor must not change DXF content."""
        s = _spec(panel_id="UNIT_GC_PARITY")
        with tempfile.TemporaryDirectory() as d:
            generate_panel_dxf(s, d)
            on_disk = ezdxf.readfile(os.path.join(d, f"{s.panel_id}.dxf"))
        in_mem = build_panel_document(s)

        def census(doc):
            out = {}
            for e in doc.modelspace():
                key = (e.dxftype(), e.dxf.layer)
                out[key] = out.get(key, 0) + 1
            return out

        self.assertEqual(census(on_disk), census(in_mem))
        counts = census(in_mem)
        self.assertIn(("LWPOLYLINE", "cut"), counts)
        self.assertIn(("CIRCLE", "holes"), counts)
        self.assertIn(("LWPOLYLINE", "fastening"), counts)
        self.assertIn(("LINE", "bend"), counts)


class ExtractPathTests(unittest.TestCase):
    def test_slot_bulge_arcs_land_back_on_circle(self):
        """Bulge -1 semicircle ends must be radius-consistent with the center."""
        doc = build_panel_document(_spec())
        slots = [p for p in extract_paths(doc) if p["layer"] == "fastening"]
        self.assertTrue(slots)
        for slot in slots:
            arcs = [s for s in slot["segments"] if s[0] == "arc"]
            self.assertEqual(len(arcs), 2)  # two semicircular slot ends
            for _, p0, p1, c, ccw in arcs:
                r0 = math.hypot(p0[0] - c[0], p0[1] - c[1])
                r1 = math.hypot(p1[0] - c[0], p1[1] - c[1])
                self.assertAlmostEqual(r0, r1, places=6)
                self.assertFalse(ccw)  # bulge -1 → clockwise

    def test_circle_split_into_two_ccw_half_arcs(self):
        doc = build_panel_document(_spec())
        holes = [p for p in extract_paths(doc) if p["layer"] == "holes"]
        self.assertTrue(holes)
        h = holes[0]
        self.assertEqual(h["shape"], "circle")
        self.assertEqual(len(h["segments"]), 2)
        for _, p0, p1, c, ccw in h["segments"]:
            self.assertTrue(ccw)
            self.assertAlmostEqual(
                math.hypot(p0[0] - c[0], p0[1] - c[1]), 0.375, places=9
            )


class GcodeProgramTests(unittest.TestCase):
    def test_laser_program_structure_and_order(self):
        nc = panel_gcode(_spec())
        lines = nc.splitlines()
        self.assertEqual(lines[0], "%")
        self.assertEqual(lines[-1], "%")
        self.assertIn("G20 G90 G17", nc)
        self.assertIn("M30", nc)
        # ops present and ordered: text mark → holes → fastening → perimeter cut
        order = [m.group(1) for m in re.finditer(r"\(op=\w+ layer=(\w+) ", nc)]
        self.assertIn("text", order)
        self.assertIn("holes", order)
        self.assertIn("fastening", order)
        self.assertEqual(order[-1], "cut")  # blank perimeter cut last
        self.assertLess(order.index("text"), order.index("holes"))
        self.assertLess(max(i for i, l in enumerate(order) if l == "holes"),
                        order.index("cut"))
        # reference layers never machined by default
        self.assertNotIn("layer=finished_face", nc)
        self.assertNotIn("layer=bend", nc)
        # laser head toggles around every path
        self.assertEqual(nc.count("M3 S"), len(order))
        self.assertEqual(nc.count("\nM5"), len(order) + 1)  # + final M5

    def test_mark_and_cut_power_differ(self):
        nc = panel_gcode(_spec(), GCodeConfig(laser_power_cut=900, laser_power_mark=250))
        self.assertIn("M3 S900", nc)
        self.assertIn("M3 S250", nc)

    def test_bend_etch_opt_in(self):
        nc = panel_gcode(_spec(), GCodeConfig(include_bend_marks=True))
        self.assertIn("layer=bend", nc)

    def test_mill_mode_plunges_and_uses_thickness(self):
        s = _spec()
        nc = panel_gcode(s, GCodeConfig(mode="mill", safe_z=0.5))
        self.assertIn("M3 S12000", nc)
        self.assertIn("G0 Z0.5", nc)
        # default through-cut depth = thickness + 0.02 clearance
        self.assertIn(f"G1 Z-{s.thickness + 0.02:.4f}".rstrip("0"), nc)
        self.assertIn("G1 Z-0.01", nc)  # mark depth for panel-ID etch

    def test_arc_words_have_ij_and_close_holes(self):
        nc = panel_gcode(_spec())
        arcs = re.findall(r"G3 X([-\d.]+) Y([-\d.]+) I([-\d.]+) J([-\d.]+)", nc)
        self.assertTrue(arcs)

    def test_rt_panel_exports(self):
        nc = panel_gcode(_rt_spec())
        order = [m.group(1) for m in re.finditer(r"\(op=\w+ layer=(\w+) ", nc)]
        self.assertEqual(order[-1], "cut")
        self.assertIn("holes", order)

    def test_writes_nc_file(self):
        s = _spec(panel_id="UNIT_GC_FILE")
        with tempfile.TemporaryDirectory() as d:
            path = generate_panel_gcode(s, d)
            self.assertTrue(os.path.exists(path))
            self.assertTrue(path.endswith("UNIT_GC_FILE.nc"))
            with open(path, encoding="ascii") as f:
                self.assertIn("G20 G90 G17", f.read())


class ComboProgramTests(unittest.TestCase):
    """Turret punch + laser combo: two separate programs for two machines."""

    def test_returns_separate_punch_and_laser_programs(self):
        programs = panel_gcode_combo(_spec(panel_id="UNIT_GC_COMBO"))
        self.assertEqual(set(programs), {"punch", "laser"})

    def test_punch_program_hits_match_hole_and_slot_counts(self):
        s = _spec(panel_id="UNIT_GC_PUNCH")
        doc = build_panel_document(s)
        paths = extract_paths(doc)
        n_holes = sum(1 for p in paths if p["layer"] == "holes")
        n_slots = sum(1 for p in paths if p["layer"] == "fastening")
        self.assertTrue(n_holes and n_slots)

        punch = panel_gcode_combo(s)["punch"]
        hits = re.findall(r"^X[-\d.]+ Y[-\d.]+", punch, flags=re.M)
        self.assertEqual(len(hits), n_holes + n_slots)
        # tool table lists a round tool for the holes, obround for the slots
        self.assertIn("= RD 0.75", punch)
        self.assertRegex(punch, r"= OB 0\.1875 x 0\.6875 @ (0|90)\.0 deg")
        # one T word per tool, on the first hit of that tool
        tools = re.findall(r"Y[-\d.]+ (T\d\d)$", punch, flags=re.M)
        self.assertEqual(sorted(set(tools)), sorted(tools))
        # no contour motion in a single-hit punch program
        self.assertNotIn("G1 ", punch)
        self.assertNotIn("G2 ", punch)
        self.assertNotIn("G3 ", punch)
        self.assertIn("G20 G90", punch)
        self.assertTrue(punch.strip().endswith("%"))

    def test_combo_laser_keeps_perimeter_and_marks_only(self):
        laser = panel_gcode_combo(_spec(panel_id="UNIT_GC_CLASER"))["laser"]
        order = [m.group(1) for m in re.finditer(r"\(op=\w+ layer=(\w+) ", laser)]
        self.assertIn("text", order)
        self.assertEqual(order[-1], "cut")
        # punched features must not be laser-cut
        self.assertNotIn("holes", order)
        self.assertNotIn("fastening", order)
        self.assertIn("M3 S", laser)  # laser dialect

    def test_combo_writes_two_files(self):
        s = _spec(panel_id="UNIT_GC_TWOFILES")
        with tempfile.TemporaryDirectory() as d:
            out = generate_panel_gcode_combo(s, d)
            self.assertTrue(out["punch"].endswith("UNIT_GC_TWOFILES_punch.nc"))
            self.assertTrue(out["laser"].endswith("UNIT_GC_TWOFILES_laser.nc"))
            for path in out.values():
                self.assertTrue(os.path.exists(path))

    def test_rt_combo_slot_angles_are_axis_aligned_or_flagged(self):
        punch = panel_gcode_combo(_rt_spec())["punch"]
        for m in re.finditer(r"= OB [\d.]+ x [\d.]+ @ ([\d.]+) deg( \(auto-index)?", punch):
            angle = float(m.group(1))
            if angle not in (0.0, 90.0):
                self.assertIsNotNone(m.group(2))


if __name__ == "__main__":
    unittest.main()

