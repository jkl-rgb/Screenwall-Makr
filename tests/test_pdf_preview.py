"""Dependency-free PDF preview: structure, content stream, arc approximation."""
import math
import re
import unittest
import zlib

from screenwall_generator import build_panel_document
from pdf_preview import _arc_beziers, doc_to_pdf
from tests.test_gcode_export import _rt_spec, _spec


def _content_stream(pdf: bytes) -> str:
    m = re.search(rb"stream\n(.*?)\nendstream", pdf, re.S)
    return zlib.decompress(m.group(1)).decode("ascii")


class PdfStructureTests(unittest.TestCase):
    def test_pdf_markers_and_xref(self):
        pdf = doc_to_pdf(build_panel_document(_spec(panel_id="UNIT_PDF")), "UNIT_PDF")
        self.assertTrue(pdf.startswith(b"%PDF-1.4\n"))
        self.assertTrue(pdf.rstrip().endswith(b"%%EOF"))
        xref_at = int(re.search(rb"startxref\n(\d+)", pdf).group(1))
        self.assertEqual(pdf[xref_at:xref_at + 4], b"xref")
        # every xref offset points at the matching "N 0 obj" header
        offsets = re.findall(rb"(\d{10}) 00000 n", pdf)
        for i, off in enumerate(offsets, start=1):
            at = int(off)
            self.assertTrue(pdf[at:].startswith(f"{i} 0 obj".encode()))

    def test_content_has_all_layers_and_operators(self):
        pdf = doc_to_pdf(build_panel_document(_spec(panel_id="UNIT_PDF2")), "UNIT_PDF2 title")
        content = _content_stream(pdf)
        self.assertIn("(UNIT_PDF2 title) Tj", content)
        for layer in ("cut", "holes", "fastening", "bend", "text", "finished_face"):
            self.assertIn(f"({layer}) Tj", content)  # legend entry
        for op in (" m", " l", " c", "h S"):
            self.assertIn(op, content)
        self.assertIn("[3 2] 0 d", content)  # dashed reference layers

    def test_geometry_fits_page(self):
        pdf = doc_to_pdf(build_panel_document(_spec(panel_id="UNIT_PDF3")), "t")
        content = _content_stream(pdf)
        coords = [
            float(v)
            for line in content.splitlines()
            if line.endswith((" m", " l", " c"))
            for v in line.split()[:-1]
        ]
        self.assertTrue(coords)
        self.assertGreaterEqual(min(coords), 0.0)
        self.assertLessEqual(max(coords), 792.0)

    def test_rt_panel_renders(self):
        pdf = doc_to_pdf(build_panel_document(_rt_spec()), "rt")
        self.assertGreater(len(pdf), 5000)


class ArcBezierTests(unittest.TestCase):
    def test_semicircle_ends_on_target(self):
        p0, p1, c = (1.0, 0.0), (-1.0, 0.0), (0.0, 0.0)
        segs = list(_arc_beziers(p0, p1, c, True))
        self.assertEqual(len(segs), 2)  # 180 deg -> two <=90 deg beziers
        end = segs[-1][2]
        self.assertAlmostEqual(end[0], p1[0], places=9)
        self.assertAlmostEqual(end[1], p1[1], places=9)

    def test_bezier_midpoint_near_arc(self):
        # 90-degree CCW arc; single-segment cubic midpoint error must be tiny
        p0, p1, c = (1.0, 0.0), (0.0, 1.0), (0.0, 0.0)
        ((c1, c2, end),) = _arc_beziers(p0, p1, c, True)
        t = 0.5
        mt = 1 - t
        bx = mt**3 * p0[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * end[0]
        by = mt**3 * p0[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * end[1]
        self.assertAlmostEqual(math.hypot(bx, by), 1.0, places=4)

    def test_clockwise_direction(self):
        p0, p1, c = (1.0, 0.0), (0.0, -1.0), (0.0, 0.0)
        ((c1, _, end),) = _arc_beziers(p0, p1, c, False)
        self.assertAlmostEqual(end[1], -1.0, places=9)
        self.assertLess(c1[1], 0.0)  # first control point dips below the x-axis


if __name__ == "__main__":
    unittest.main()
