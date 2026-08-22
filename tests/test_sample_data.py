"""Built-in sample bundle: template integrity and generated file set."""
import io
import unittest
import zipfile

from sample_data import ALL_HEADERS, TEMPLATE_CSV, build_sample_files, zip_files


class SampleDataTests(unittest.TestCase):
    def test_template_rows_match_header_width(self):
        rows = [r for r in TEMPLATE_CSV.strip().splitlines()]
        header = rows[0].split(",")
        self.assertEqual(header, ALL_HEADERS)
        for row in rows[1:]:
            self.assertEqual(len(row.split(",")), len(ALL_HEADERS))

    def test_sample_files_complete_and_valid(self):
        spec, files = build_sample_files()
        self.assertEqual(spec.panel_id, "example_L4S")
        expected = {
            "example_L4S.pdf",
            "example_L4S.dxf",
            "example_L4S.nc",
            "example_L4S_punch.nc",
            "example_L4S_laser.nc",
        }
        self.assertEqual(set(files), expected)
        self.assertGreater(len(files["example_L4S.dxf"]), 1000)  # real DXF payload
        self.assertTrue(files["example_L4S.pdf"].startswith(b"%PDF-"))
        laser = files["example_L4S.nc"].decode("ascii")
        self.assertIn("G20 G90 G17", laser)
        self.assertIn("layer=cut", laser)
        punch = files["example_L4S_punch.nc"].decode("ascii")
        self.assertIn("T01", punch)
        self.assertIn("turret punch program", punch)
        combo_laser = files["example_L4S_laser.nc"].decode("ascii")
        self.assertNotIn("layer=holes", combo_laser)

    def test_zip_roundtrip(self):
        _, files = build_sample_files()
        blob = zip_files(files)
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            self.assertEqual(set(z.namelist()), set(files))
            for name in files:
                self.assertEqual(z.read(name), files[name])


if __name__ == "__main__":
    unittest.main()
