import importlib.util
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "distribution" / "OpenCode-CFD-Agent" / ".agents" / "skills" / "stl-geometry-analyzer" / "scripts" / "analyze_stl.py"
SPEC = importlib.util.spec_from_file_location("analyze_stl", SCRIPT)
analyze_stl = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = analyze_stl
SPEC.loader.exec_module(analyze_stl)


class StlGeometryAnalyzerTests(unittest.TestCase):
    def test_ascii_opposed_planes_report_clearance(self):
        text = """solid lower
facet normal 0 0 1
outer loop
vertex 0 0 0
vertex 1 0 0
vertex 0 1 0
endloop
endfacet
endsolid lower
solid upper
facet normal 0 0 -1
outer loop
vertex 0 1 0.2
vertex 1 0 0.2
vertex 0 0 0.2
endloop
endfacet
endsolid upper
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gap.stl"
            path.write_text(text, encoding="utf-8")
            report = analyze_stl.analyze([path], "m", 4, -0.3)
        self.assertEqual(report["triangles"], 2)
        self.assertEqual(report["bounds"]["extents"], [1.0, 1.0, 0.2])
        self.assertAlmostEqual(report["minimum_opposed_clearance"]["distance"], 0.2)
        self.assertAlmostEqual(report["mesh_guidance"]["maximum_cell_size_for_gap"], 0.05)

    def test_binary_stl_is_detected_and_parsed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one.stl"
            header = b"binary test".ljust(80, b"\0")
            facet = struct.pack("<12fH", 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0)
            path.write_bytes(header + struct.pack("<I", 1) + facet)
            self.assertTrue(analyze_stl.is_binary_stl(path))
            facets = analyze_stl.read_stl(path)
        self.assertEqual(len(facets), 1)
        self.assertEqual(facets[0].vertices[1], (1.0, 0.0, 0.0))

    def test_cli_writes_json(self):
        text = "solid one\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid one\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "one.stl"
            output = Path(directory) / "report.json"
            path.write_text(text, encoding="utf-8")
            code = analyze_stl.main([str(path), "--units", "mm", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(report["coordinate_units"], "mm")


if __name__ == "__main__":
    unittest.main()
