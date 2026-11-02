"""
The scenario runner, run_scenarios.py, and the map pictures in its reports,
map_snapshot.py: the checks it makes, a whole run into a throwaway folder,
and runs with broken scenario files.

    python -m unittest tests.test_scenarios
"""
import contextlib
import io
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import map_snapshot as ms  # noqa: E402
import run_scenarios as rs  # noqa: E402
import server  # noqa: E402
from sea_routing import DenialArea, great_circle_nm  # noqa: E402

SAN_BERNARDINO = os.path.join(rs.SCENARIOS, "02_denial_san_bernardino_strait.json")


def scenario(**fields):
    s = {"id": "T01", "name": "Busan to Subic Bay", "from": "WPI-60390", "to": "WPI-58395", "k": 2,
         "method": "distinct", "areas": [], "expect": {"route_found": True}}
    s.update(fields)
    return s


def quietly(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args)
    return result, out.getvalue()


# ------------------------------------------------------------------ inputs
class Inputs(unittest.TestCase):
    def test_a_port_by_id_or_by_exact_name(self):
        self.assertEqual(rs.resolve_port("WPI-60390"), "WPI-60390")
        self.assertEqual(rs.resolve_port("busan"), "WPI-60390")

    def test_an_unknown_or_shared_name(self):
        with self.assertRaisesRegex(rs.ScenarioError, "No port is called 'Atlantis'"):
            rs.resolve_port("Atlantis")
        with self.assertRaisesRegex(rs.ScenarioError, "'Kingston' matches [0-9]+ ports; use one of these ids"):
            rs.resolve_port("Kingston")

    def test_loading_fills_in_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"id": "X", "name": "X", "from": "a", "to": "b"}, f)
            s = rs.load_scenario(path)
        self.assertEqual((s["k"], s["method"], s["areas"], s["expect"], s["purpose"]), (3, "distinct", [], {}, ""))

    def test_loading_needs_the_four_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "x.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"id": "X", "name": "X", "from": "a"}, f)
            with self.assertRaisesRegex(rs.ScenarioError, "x.json has no 'to'"):
                rs.load_scenario(path)

    def test_every_scenario_in_the_repository_loads(self):
        files = sorted(os.listdir(rs.SCENARIOS))
        ids = [rs.load_scenario(os.path.join(rs.SCENARIOS, f))["id"] for f in files if f.endswith(".json")]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 10)


# ------------------------------------------------------------------ checks
class Checks(unittest.TestCase):
    def test_check_shows_yes_and_no(self):
        self.assertEqual(rs.check("x", True, False, False),
                         {"check": "x", "expected": "yes", "actual": "no", "passed": False})
        self.assertEqual(rs.check("x", 3, 4, 1)["passed"], True)

    def test_ranges(self):
        self.assertEqual(rs.range_text({"min": 1000, "max": 2000}), "1,000 to 2,000 nm")
        self.assertEqual(rs.range_text({"min": 5}), ">= 5 nm")
        self.assertEqual(rs.range_text({"max": 5}), "<= 5 nm")
        self.assertTrue(rs.in_range(5, {"min": 5, "max": 5}))
        self.assertTrue(rs.in_range(5, {"max": 9}))
        self.assertFalse(rs.in_range(4.9, {"min": 5}))
        self.assertFalse(rs.in_range(None, {"min": 0}))

    def result(self, **fields):
        base = {"routes": [{"rank": 1, "length_nm": 1500, "passages": ["Luzon Strait"]}],
                "affected": True, "extra_nm": 50.0}
        base.update(fields)
        return base

    def outcomes(self, expect, **fields):
        return {c["check"]: c["passed"] for c in rs.expectation_checks(self.result(**fields), expect)}

    def test_each_expectation_passes_and_fails(self):
        cases = [({"route_found": True}, {}, True), ({"route_found": False}, {}, False),
                 ({"route_found": False}, {"routes": [], "blocked": "x"}, True),
                 ({"blocked_contains": "inside"}, {"blocked": "Destination port X is INSIDE a denial area."}, True),
                 ({"blocked_contains": "inside"}, {}, False),
                 ({"routes_at_least": 1}, {}, True), ({"routes_at_least": 2}, {}, False),
                 ({"best_length_nm": {"min": 1400, "max": 1600}}, {}, True),
                 ({"best_length_nm": {"max": 1400}}, {}, False),
                 ({"best_length_nm": {"min": 0}}, {"routes": []}, False),
                 ({"best_passes": ["Luzon Strait"]}, {}, True), ({"best_passes": ["Taiwan Strait"]}, {}, False),
                 ({"best_avoids": ["Taiwan Strait"]}, {}, True), ({"best_avoids": ["Luzon Strait"]}, {}, False),
                 ({"best_avoids": ["Luzon Strait"]}, {"routes": []}, False),
                 ({"affected": True}, {}, True), ({"affected": False}, {}, False),
                 ({"extra_nm": {"min": 0, "max": 100}}, {}, True),
                 ({"extra_nm": {"min": 0}}, {"extra_nm": None}, False)]
        for expect, fields, passed in cases:
            with self.subTest(expect=expect, fields=fields):
                self.assertEqual(list(self.outcomes(expect, **fields).values()), [passed])

    def test_an_unknown_expectation_fails_rather_than_being_ignored(self):
        self.assertEqual(self.outcomes({"best_lenght_nm": {"max": 1}}), {"Unknown expectation 'best_lenght_nm'": False})

    def answer(self, areas=()):
        r = server.find_routes({"from": "WPI-60390", "to": "WPI-58395", "k": 3, "method": "distinct",
                                "areas": list(areas)})
        return r, server.parse_areas(list(areas))

    def test_the_rules_every_answer_keeps(self):
        r, areas = self.answer([{"type": "circle", "center": [120.5, 24.0], "radius_nm": 60}])
        checks = rs.invariant_checks(r, "WPI-60390", "WPI-58395", areas)
        self.assertEqual(len(checks), 6)
        self.assertTrue(all(c["passed"] for c in checks), checks)

    def test_the_rules_catch_a_wrong_answer(self):
        r, _ = self.answer()
        bad = json.loads(json.dumps(r))
        bad["routes"][0]["line"][0] = [100, 0]                      # starts far from Busan
        bad["routes"][1]["length_nm"] = 10                          # shorter than the great circle
        bad["routes"].append(dict(bad["routes"][2], rank=4))        # listed twice
        wall = DenialArea({"type": "polygon", "points": [[118, 21], [123, 21], [123, 27], [118, 27]]})
        failed = {c["check"] for c in rs.invariant_checks(bad, "WPI-60390", "WPI-58395", [wall]) if not c["passed"]}
        self.assertEqual(failed, {"Every route starts at the origin and ends at the destination",
                                  "No route is shorter than the great-circle distance",
                                  "Routes are ranked shortest first",
                                  "Each route's drawn line matches its reported length",
                                  "No route is listed twice", "No route enters an area of denial"})

    def test_no_routes_means_no_route_rules(self):
        self.assertEqual(rs.invariant_checks({"routes": []}, "WPI-60390", "WPI-58395", []), [])


# ------------------------------------------------------------- whole runs
class Runs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "report")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def write(self, name, s):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s if isinstance(s, str) else json.dumps(s))
        return path

    def run_main(self, *files):
        return quietly(rs.main, list(files) + ["--out", self.out, "--no-png"])

    def results(self):
        with open(os.path.join(self.out, "results.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_a_real_scenario_passes_and_writes_a_report(self):
        code, printed = self.run_main(SAN_BERNARDINO)
        self.assertEqual(code, 0, printed)
        self.assertIn("S02    PASS", printed)
        saved = self.results()
        self.assertEqual((saved["run"]["passed"], saved["run"]["failed"]), (1, 0))
        snaps = saved["scenarios"][0]["snapshots"]
        self.assertEqual([s["png"] for s in snaps], [None, None])
        for s in snaps:
            ET.parse(os.path.join(self.out, s["svg"]))                 # well-formed SVG
        with open(os.path.join(self.out, "report.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn("San Bernardino", html)
        self.assertIn("<svg", html)

    def test_broken_scenarios_are_reported_and_the_run_fails(self):
        files = [self.write("a_missing_to.json", {"id": "B1", "name": "x", "from": "Busan"}),
                 self.write("b_bad_json.json", "{ not json"),
                 self.write("c_unknown_port.json", scenario(id="B3", to="Atlantis")),
                 self.write("d_refused.json", scenario(id="B4", k=9)),
                 self.write("e_first.json", scenario(id="B5")),
                 self.write("f_same_id.json", scenario(id="B5"))]
        code, printed = self.run_main(*files)
        self.assertEqual(code, 1)
        self.assertEqual(printed.count("ERROR"), 5, printed)
        saved = self.results()
        self.assertEqual((saved["run"]["passed"], saved["run"]["failed"]), (1, 5))
        self.assertIn("is used twice", saved["scenarios"][-1]["blocked"])

    def test_a_scenario_that_fails_its_expectations(self):
        code, printed = self.run_main(self.write("x.json", scenario(expect={"best_passes": ["Panama Canal"]})))
        self.assertEqual(code, 1)
        self.assertIn("failed: Best route goes through Panama Canal", printed)

    def test_a_blocked_scenario_with_an_unsafe_name_is_escaped(self):
        s = scenario(name="<script>alert(1)</script>", areas=[{"type": "circle", "center": [120.28, 14.8],
                                                               "radius_nm": 30}],
                     expect={"route_found": False, "blocked_contains": "inside"})
        code, printed = self.run_main(self.write("x.json", s))
        self.assertEqual(code, 0, printed)
        with open(os.path.join(self.out, "report.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_comparing_with_an_earlier_report(self):
        with mock.patch.object(rs, "REPORTS", self.tmp):
            self.assertEqual(rs.previous_results(self.out), (None, {}))
            self.run_main(SAN_BERNARDINO)
            when, prev = rs.previous_results(os.path.join(self.tmp, "later"))
        self.assertIn("S02", prev)
        self.assertTrue(when)

    def test_no_scenario_files(self):
        with mock.patch.object(rs, "SCENARIOS", self.tmp):
            with self.assertRaisesRegex(SystemExit, "No scenario files"):
                rs.main(["--out", self.out, "--no-png"])

    def test_git_version_without_git(self):
        self.assertTrue(rs.git_version())
        with mock.patch.object(subprocess, "run", side_effect=OSError("no git")):
            self.assertEqual(rs.git_version(), "unknown")

    def test_area_text(self):
        self.assertEqual(rs._area_text({"type": "circle", "center": [-70.5, -33.25], "radius_nm": 50}),
                         "Circle, 50 nm radius, centred 33.25°S 70.50°W")
        self.assertEqual(rs._area_text({"type": "polygon", "points": [[0, 0]] * 4}), "Polygon, 4 corners")
        self.assertEqual(rs._nm(None), "—")
        self.assertEqual(rs._nm(1449.6), "1,450")


# ------------------------------------------------------------- map pictures
class MapGeometry(unittest.TestCase):
    def test_a_circle_is_true_on_the_globe(self):
        for center, r in (([124, 22], 240), ([0, 80], 600), ([179.5, -40], 1000)):
            pts = ms.circle_points(center, r)
            self.assertEqual(len(pts), 120)
            for p in pts:
                self.assertAlmostEqual(great_circle_nm(p, center), r, delta=0.01)

    def test_lines_stay_continuous_across_the_date_line(self):
        self.assertEqual(ms._continuous([(179, 0), (-179, 1), (-178, 2)], 179), [(179, 0), (181, 1), (182, 2)])
        self.assertEqual(ms._continuous([(-179, 0)], 179), [(181, 0)])

    def test_offsets_bring_a_shape_into_view(self):
        self.assertEqual(ms._offsets(-175, -170, (170, -10, 200, 10)), [360])
        self.assertEqual(ms._offsets(0, 10, (-20, -10, 20, 10)), [0])
        self.assertEqual(ms._offsets(0, 10, (100, -10, 120, 10)), [])
        self.assertEqual(ms._offsets(-180, 180, (-200, -10, 200, 10)), [-360, 0, 360])   # a 400-degree view shows its edges twice

    def test_fit_view_keeps_the_picture_shape(self):
        for lines in ([[(120, 10), (121, 10.5)]], [[(-170, -60), (170, 70)]], [[(0, 0)]]):
            x0, y0, x1, y1 = ms.fit_view(lines)
            with self.subTest(lines=lines):
                self.assertLessEqual(x1 - x0, 360)
                self.assertLessEqual(y1 - y0, 170)
                self.assertGreaterEqual(y0, -85 - 1e-9)
                self.assertLessEqual(y1, 85 + 1e-9)
                if x1 - x0 < 360 and y1 - y0 < 170:
                    self.assertAlmostEqual((x1 - x0) / (y1 - y0), ms.WIDTH / ms.HEIGHT)

    def test_fit_view_has_a_smallest_size(self):
        x0, _, x1, _ = ms.fit_view([[(120, 10), (120.1, 10)]])
        self.assertGreaterEqual(x1 - x0, 12)

    def test_labels(self):
        self.assertEqual([ms._lon_label(x) for x in (0, 30, -30, 190, 180, -180, 540)],
                         ["0°", "30°E", "30°W", "170°W", "180°", "180°", "180°"])
        self.assertEqual([ms._lat_label(y) for y in (0, 10, -10)], ["0°", "10°N", "10°S"])


class MapPictures(unittest.TestCase):
    def picture(self, areas=(), closeup=False, title="T", **request):
        body = dict({"from": "WPI-60390", "to": "WPI-58395", "k": 2, "method": "distinct"}, **request)
        body["areas"] = list(areas)
        result = server.find_routes(body)
        result.update({"from": body["from"], "to": body["to"], "from_name": "Busan", "to_name": "Subic & Bay"})
        closed = server.GRAPH.links_in_areas(server.parse_areas(list(areas)))
        svg = ms.render(server.GRAPH, result, list(areas), closed, title, "sub", closeup=closeup)
        return svg, ET.fromstring(svg)

    def test_routes_without_areas(self):
        svg, root = self.picture()
        self.assertEqual(root.get("width"), str(ms.WIDTH))
        self.assertIn("Subic &amp; Bay", svg)
        self.assertIn("#1  1,450 nm", svg)
        self.assertNotIn("Area of denial", svg)

    def test_areas_closed_lanes_and_the_original_route(self):
        taiwan = {"type": "polygon", "points": [[118.5, 26.5], [121.8, 25.6], [121.0, 21.8], [119.0, 22.2]]}
        for closeup in (False, True):
            with self.subTest(closeup=closeup):
                svg, _ = self.picture([taiwan, {"type": "circle", "center": [124.2, 22.4], "radius_nm": 240}],
                                      closeup=closeup)
                for words in ("Area of denial", "Lanes closed by the areas", "Original, no areas"):
                    self.assertIn(words, svg)

    def test_a_blocked_answer_and_a_title_that_needs_escaping(self):
        svg, _ = self.picture([{"type": "circle", "center": [120.28, 14.8], "radius_nm": 30}], closeup=True,
                              title="<b>A & B</b>")
        self.assertIn("Blocked: Destination port", svg)
        self.assertIn("&lt;b&gt;A &amp; B&lt;/b&gt;", svg)

    def test_a_route_across_the_date_line(self):
        svg, root = self.picture(**{"from": "WPI-16010", "to": "WPI-56550"})       # San Diego to Apra
        self.assertTrue(math.isfinite(float(root.get("height"))))
        self.assertIn("180°", svg)


class Png(unittest.TestCase):
    def test_find_browser_uses_falcon_browser(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            fake = f.name
        try:
            with mock.patch.dict(os.environ, {"FALCON_BROWSER": fake}):
                self.assertEqual(ms.find_browser(), fake)
        finally:
            os.remove(fake)

    def test_no_browser(self):
        with mock.patch.object(os.path, "isfile", return_value=False):
            self.assertIsNone(ms.find_browser())

    def test_a_browser_that_fails_makes_no_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            svg, png = os.path.join(tmp, "a.svg"), os.path.join(tmp, "a.png")
            with open(svg, "w", encoding="utf-8") as f:
                f.write("<svg xmlns='http://www.w3.org/2000/svg'/>")
            with open(png, "wb") as f:
                f.write(b"old")                                        # an old picture is removed first
            self.assertFalse(ms.svg_to_png(svg, png, sys.executable, wait_s=0.2))
            self.assertFalse(os.path.exists(png))
            self.assertFalse(ms.svg_to_png(svg, png, os.path.join(tmp, "no-such-browser.exe"), wait_s=0.2))


if __name__ == "__main__":
    unittest.main()
