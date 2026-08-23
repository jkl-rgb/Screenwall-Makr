"""G-code import: round-trips, heuristics, dialects, units, DXF rebuild."""
import math
import re
import unittest

from screenwall_generator import build_panel_document
from gcode_export import GCodeConfig, extract_paths, panel_gcode, panel_gcode_combo
from gcode_import import gcode_to_document, parse_gcode, paths_to_document
from tests.test_gcode_export import _rt_spec, _spec


def _layer_counts(paths):
    out = {}
    for p in paths:
        out[p["layer"]] = out.get(p["layer"], 0) + 1
    return out


def _machined_layer_counts(doc):
    """Layer counts for what the laser program machines (skips reference layers)."""
    out = {}
    for p in extract_paths(doc):
        if p["layer"] in ("finished_face", "bend"):
            continue
        out[p["layer"]] = out.get(p["layer"], 0) + 1
    return out


class RoundTripTests(unittest.TestCase):
    def test_laser_annotated_round_trip_preserves_layers(self):
        spec = _spec(panel_id="UNIT_IMP_RT1")
        doc = build_panel_document(spec)
        result = parse_gcode(panel_gcode(spec))
        self.assertTrue(result.annotated)
        self.assertEqual(result.dialect, "laser")
        self.assertEqual(result.panel_id, "UNIT_IMP_RT1")
        self.assertEqual(_layer_counts(result.paths), _machined_layer_counts(doc))

    def test_laser_round_trip_hole_centers_match(self):
        spec = _spec(panel_id="UNIT_IMP_RT2")
        orig = {
            (s[3][0], s[3][1])
            for p in extract_paths(build_panel_document(spec))
            if p["layer"] == "holes"
            for s in p["segments"]
        }
        imported = {
            (s[3][0], s[3][1])
            for p in parse_gcode(panel_gcode(spec)).paths
            if p["layer"] == "holes"
            for s in p["segments"]
        }
        # Export writes 4-decimal coordinates, so match with tolerance instead
        # of rounded-set equality (centers can sit exactly on .0005 boundaries
        # and near-equal floats dedupe differently in each set).
        def covered(pts, ref):
            for px, py in pts:
                self.assertTrue(
                    any(abs(px - rx) < 6e-4 and abs(py - ry) < 6e-4 for rx, ry in ref),
                    (px, py),
                )
        covered(imported, orig)
        covered(orig, imported)

    def test_mill_dialect_round_trip(self):
        spec = _spec(panel_id="UNIT_IMP_MILL")
        result = parse_gcode(panel_gcode(spec, GCodeConfig(mode="mill")))
        self.assertEqual(result.dialect, "mill")
        self.assertEqual(
            _layer_counts(result.paths),
            _machined_layer_counts(build_panel_document(spec)),
        )

    def test_punch_program_import(self):
        spec = _spec(panel_id="UNIT_IMP_PUNCH")
        doc_counts = _machined_layer_counts(build_panel_document(spec))
        result = parse_gcode(panel_gcode_combo(spec)["punch"])
        self.assertEqual(result.dialect, "punch")
        counts = _layer_counts(result.paths)
        self.assertEqual(counts["holes"], doc_counts["holes"])
        self.assertEqual(counts["fastening"], doc_counts["fastening"])
        self.assertNotIn("cut", counts)

    def test_rt_panel_round_trip(self):
        spec = _rt_spec()
        result = parse_gcode(panel_gcode(spec))
        self.assertEqual(
            _layer_counts(result.paths),
            _machined_layer_counts(build_panel_document(spec)),
        )


class HeuristicTests(unittest.TestCase):
    def _strip_annotations(self, nc: str) -> str:
        return re.sub(r"\(op=[^)]*\)\n", "", nc)

    def test_foreign_file_classification(self):
        spec = _spec(panel_id="UNIT_IMP_HEUR")
        stripped = self._strip_annotations(panel_gcode(spec))
        self.assertNotIn("op=", stripped)
        result = parse_gcode(stripped)
        self.assertFalse(result.annotated)
        counts = _layer_counts(result.paths)
        expected = _machined_layer_counts(build_panel_document(spec))
        # circles -> holes, slots -> fastening, closed loops -> cut
        self.assertEqual(counts["holes"], expected["holes"])
        self.assertEqual(counts["fastening"], expected["fastening"])
        self.assertEqual(counts["cut"], expected["cut"])
        # panel-ID strokes land on text (never on cut)
        self.assertEqual(counts.get("text", 0), expected.get("text", 0))


class DialectDetailTests(unittest.TestCase):
    def test_g21_mm_program_normalizes_to_inches(self):
        nc = "\n".join([
            "G21 G90 G17",
            "G0 X0 Y0",
            "M3 S500",
            "G1 X25.4 Y0 F1000",
            "G1 X25.4 Y25.4",
            "G1 X0 Y25.4",
            "G1 X0 Y0",
            "M5", "M30",
        ])
        result = parse_gcode(nc)
        self.assertEqual(len(result.paths), 1)
        p = result.paths[0]
        self.assertTrue(p["closed"])
        xs = [s[1][0] for s in p["segments"]] + [s[2][0] for s in p["segments"]]
        self.assertAlmostEqual(max(xs), 1.0, places=6)

    def test_r_word_arc(self):
        nc = "\n".join([
            "G20 G90",
            "G0 X1 Y0",
            "M3",
            "G3 X0 Y1 R1 F60",
            "M5", "M30",
        ])
        result = parse_gcode(nc)
        ((kind, p0, p1, center, ccw),) = result.paths[0]["segments"]
        self.assertEqual(kind, "arc")
        self.assertTrue(ccw)
        self.assertAlmostEqual(center[0], 0.0, places=6)
        self.assertAlmostEqual(center[1], 0.0, places=6)

    def test_incremental_mode(self):
        nc = "\n".join([
            "G20 G91",
            "G0 X2 Y2",
            "M3",
            "G1 X1 Y0 F60",
            "G1 X0 Y1",
            "M5", "M30",
        ])
        p = parse_gcode(nc).paths[0]
        self.assertEqual(p["segments"][0][1], (2.0, 2.0))
        self.assertEqual(p["segments"][-1][2], (3.0, 3.0))

    def test_unsupported_codes_warn(self):
        nc = "G20 G90\nG18\nG0 X0 Y0\nM3\nG1 X1 Y1 F60\nM5\nM30\n"
        result = parse_gcode(nc)
        self.assertTrue(any("G18" in w for w in result.warnings))
        self.assertEqual(len(result.paths), 1)


class RebuildDocumentTests(unittest.TestCase):
    def test_document_entities_and_layers(self):
        spec = _spec(panel_id="UNIT_IMP_DOC")
        doc, summary, result = gcode_to_document(panel_gcode(spec))
        kinds = {}
        for e in doc.modelspace():
            kinds[(e.dxftype(), e.dxf.layer)] = kinds.get((e.dxftype(), e.dxf.layer), 0) + 1
        self.assertIn(("CIRCLE", "holes"), kinds)
        self.assertIn(("LWPOLYLINE", "fastening"), kinds)
        self.assertIn(("LWPOLYLINE", "cut"), kinds)
        expected = _machined_layer_counts(build_panel_document(spec))
        self.assertEqual(kinds[("CIRCLE", "holes")], expected["holes"])
        self.assertEqual(summary["panel_id"], "UNIT_IMP_DOC")
        self.assertEqual(summary["hole_diameters"], [0.75])
        self.assertFalse(summary["warnings"])

    def test_reimported_geometry_extents_match(self):
        spec = _spec(panel_id="UNIT_IMP_EXT")
        doc, summary, _ = gcode_to_document(panel_gcode(spec))
        orig_paths = [
            p for p in extract_paths(build_panel_document(spec))
            if p["layer"] not in ("finished_face", "bend")
        ]
        xs = [pt[0] for p in orig_paths for s in p["segments"] for pt in (s[1], s[2])]
        ys = [pt[1] for p in orig_paths for s in p["segments"] for pt in (s[1], s[2])]
        self.assertAlmostEqual(summary["extents_in"][0], max(xs) - min(xs), places=3)
        self.assertAlmostEqual(summary["extents_in"][1], max(ys) - min(ys), places=3)

    def test_slot_bulges_survive_rebuild(self):
        spec = _spec(panel_id="UNIT_IMP_BULGE")
        doc, _, _ = gcode_to_document(panel_gcode(spec))
        slot_polys = [
            e for e in doc.modelspace()
            if e.dxftype() == "LWPOLYLINE" and e.dxf.layer == "fastening"
        ]
        self.assertTrue(slot_polys)
        for poly in slot_polys:
            bulges = [pt[2] for pt in poly.get_points("xyb")]
            semis = [b for b in bulges if abs(abs(b) - 1.0) < 1e-3]
            self.assertEqual(len(semis), 2)  # two semicircular ends preserved


if __name__ == "__main__":
    unittest.main()
