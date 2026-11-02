"""
The data model in neo4j-datamodel/ can be rebuilt, byte for byte, from the
sources in data/ by tools/datamodel/build.py. If this fails, the committed
CSVs no longer match what the build makes: rebuild them, or find out why.
Also the build's small helpers, and check_routes.py, the Neo4j-style check.

    python -m unittest tests.test_build        (a few seconds)
"""
import contextlib
import filecmp
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "tools", "datamodel", "build.py")
DATA = os.path.join(ROOT, "neo4j-datamodel")
sys.path.insert(0, HERE)
import toy_network as toy  # noqa: E402


def tool(name):
    """A script in tools/datamodel/, imported as a module."""
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", "datamodel", name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


build = tool("build")
check_routes = tool("check_routes")


class Rebuild(unittest.TestCase):
    def test_build_reproduces_the_committed_data(self):
        with tempfile.TemporaryDirectory() as out:
            run = subprocess.run([sys.executable, BUILD, "100km", out], cwd=ROOT,
                                 capture_output=True, text=True, timeout=600)
            self.assertEqual(run.returncode, 0, run.stderr)
            made = sorted(os.path.relpath(os.path.join(d, f), out)
                          for d, _, files in os.walk(out) for f in files)
            self.assertTrue(made, "the build wrote nothing")
            differ = [f for f in made
                      if not os.path.isfile(os.path.join(DATA, f))
                      or not filecmp.cmp(os.path.join(out, f), os.path.join(DATA, f), shallow=False)]
            self.assertEqual(differ, [], "these files differ from neo4j-datamodel/: %s" % differ)


class Helpers(unittest.TestCase):
    def test_distances(self):
        self.assertAlmostEqual(build.haversine_km(0, 0, 0, 1), 111.195, places=3)
        self.assertAlmostEqual(build.nm_between((0, 0), (0, 1)), 111.195 / 1.852, places=3)
        self.assertEqual(build.haversine_km(10, 10, 10, 10), 0)

    def test_one_value_per_meridian(self):
        self.assertEqual([build.norm_lon(x) for x in (180, -180, 540, -181, 179.5, 0)], [-180, -180, -180, 179, 179.5, 0])

    def test_interpolation(self):
        self.assertEqual(build.gc_interpolate((10, 20), (10, 20), 0.5), (10, 20))
        self.assertEqual(build.gc_interpolate((0, 0), (2, 0), 0.5), (1.0, 0.0))
        self.assertEqual(build.gc_interpolate((179, 0), (-179, 0), 0.5), (-180.0, 0.0))

    def test_segment_hits_box(self):
        box = [120.0, 18.8, 122.5, 22.0]                      # the Luzon Strait box
        self.assertTrue(build.segment_hits_box((121, 19), (121.5, 20), box))       # inside
        self.assertTrue(build.segment_hits_box((119, 20), (124, 20), box))         # straight through
        self.assertFalse(build.segment_hits_box((119, 23), (124, 23), box))        # passes north
        self.assertFalse(build.segment_hits_box((119, 17), (119, 25), box))        # due north, to the west
        self.assertTrue(build.segment_hits_box((179.5, 0), (-179.5, 0), [179.9, -1, 180.1, 1]))   # the date line

    def test_reading_source_values(self):
        self.assertIsNone(build.num(""))
        self.assertIsNone(build.num(None))
        self.assertIsNone(build.num("0"))
        self.assertEqual(build.num("0", zero_is_unknown=False), 0.0)
        self.assertEqual(build.num(" 3.5 "), 3.5)
        with self.assertRaises(ValueError):
            build.num("deep")
        self.assertEqual([build.yes_no(v) for v in ("Yes", " no ", "Unknown", "", None)], [True, False, None, None, None])
        self.assertEqual([build.text(v) for v in (" Busan ", "Unknown", "", None)], ["Busan", None, None, None])

    def test_paved_runways(self):
        self.assertEqual([build.is_paved(v) for v in ("ASP", "concrete", "CON-GRVD", "UNPAVED ASP", "GRASS", "", None)],
                         [True, True, True, False, False, False, False])

    def test_wkt(self):
        self.assertEqual(build.wkt([(1, 2), (-3.5, 4)]), "LINESTRING(1 2, -3.5 4)")


class CheckRoutes(unittest.TestCase):
    """check_routes.py answers as a plain Neo4j shortest path would: with no
    port rule. On the toy it takes the shortcut through Port M that the app
    refuses. That is the trap docs/ROUTING_RULES.md warns about."""

    def run_main(self, *args):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["check_routes.py"] + list(args)), contextlib.redirect_stdout(out):
            check_routes.main()
        return out.getvalue()

    def test_plain_shortest_path_ignores_the_port_rule(self):
        with toy.ToyFolder() as folder:
            graph, ports = check_routes.load(folder, set())
            self.assertEqual(ports["port a"], "PA")
            plain = check_routes.shortest_nm(graph, "PA", "PB")
            self.assertAlmostEqual(plain, toy.nm("PA", "N1", "PM", "N3", "PB"), places=3)
            self.assertLess(plain, toy.nm("PA", "N1", "N2", "N3", "PB"))      # what the app answers
            printed = self.run_main(folder, "Port A", "Port B", "Port A", "Port Z", "Port A", "Atlantis")
        self.assertIn("Port A -> Port B: 144 nm", printed)
        self.assertIn("Port A -> Port Z: no route", printed)
        self.assertIn("Port A -> Atlantis: port name not found", printed)

    def test_closing_a_passage(self):
        with toy.ToyFolder() as folder:
            printed = self.run_main(folder, "--close", "north", "Port A", "Port C")
        self.assertIn("Port A -> Port C: %d nm" % round(toy.nm("PA", "N1", "PM", "N3", "PC")), printed)

    def test_the_default_pairs_on_the_real_data(self):
        printed = self.run_main()
        self.assertEqual(printed.count(" nm"), len(check_routes.DEFAULT_PAIRS), printed)
        self.assertIn("Busan -> Subic Bay: 1,450 nm", printed)


if __name__ == "__main__":
    unittest.main()
