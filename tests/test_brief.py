"""
The stakeholder brief, make_brief.py: its wording, the sentence it writes for
each kind of answer, the impact table, refusing runs it shouldn't brief, and a
whole run from scenarios to brief.

    python -m unittest tests.test_brief
"""
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import make_brief as mb  # noqa: E402
import map_snapshot  # noqa: E402
import run_scenarios as rs  # noqa: E402
import server  # noqa: E402


def scenario(sid="T01", name="A to B, Strait X denied", areas=1, original=1000.0, best=1100.0,
             before=("Strait X",), after=("Strait Y",), affected=True, blocked=None, passed=True, more=()):
    """One scenario as run_scenarios.py writes it to results.json."""
    routes = [] if blocked else [{"rank": 1, "length_nm": best, "passages": list(after), "hops": 5}] + [
        {"rank": i + 2, "length_nm": x, "passages": [], "hops": 5} for i, x in enumerate(more)]
    return {"id": sid, "name": name, "purpose": "", "file": "scenarios/x.json",
            "from": {"id": "WPI-1", "name": "A", "country": "X"}, "to": {"id": "WPI-2", "name": "B", "country": "Y"},
            "k": 3, "method": "distinct",
            "areas": [{"type": "circle", "center": [0, 0], "radius_nm": 50}] * areas,
            "passed": passed, "checks": [{"check": "c", "expected": "e", "actual": "a", "passed": passed}],
            "original": {"length_nm": original, "passages": list(before), "hops": 4},
            "routes": routes, "affected": None if blocked else affected, "closed_links": 3,
            "extra_nm": (best - original) if affected and not blocked and areas else None,
            "extra_days_at_18kt": None, "blocked": blocked, "seconds": 0.01, "snapshots": []}


def run_data(*scenarios, code="abc1234"):
    return {"run": {"started": "2026-11-03 10:56:11 UTC-0500", "code": code},
            "scenarios": list(scenarios)}


def write_run(folder, data):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "results.json"), "w", encoding="utf-8") as f:
        json.dump(data, f)
    return folder


def quietly(fn, *args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args)
    return result, out.getvalue()


def text_of(html):
    """The brief's words, without its style sheet and maps."""
    html = re.sub(r"<style.*?</style>|<svg.*?</svg>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# ------------------------------------------------------------------ words
class Words(unittest.TestCase):
    def test_test_wording_becomes_reader_wording(self):
        self.assertEqual(mb.plain("San Bernardino Strait denied"), "San Bernardino Strait closed")
        self.assertEqual(mb.plain("Taiwan inside a denial area"), "Taiwan inside a closed area")
        self.assertEqual(mb.plain("Two Areas of Denial; one Area of denial"), "Two closed areas; one closed area")
        self.assertEqual(mb.plain("Nothing to change"), "Nothing to change")

    def test_durations_in_hours_then_days(self):
        self.assertEqual(mb.duration(0), "under an hour")
        self.assertEqual(mb.duration(17.9), "under an hour")
        self.assertEqual(mb.duration(18), "1 hour")
        self.assertEqual(mb.duration(35.3), "2 hours")
        self.assertEqual(mb.duration(18 * 23.4), "23 hours")
        self.assertEqual(mb.duration(18 * 23.5), "1.0 days")
        self.assertEqual(mb.duration(18 * 60), "2.5 days")

    def test_percent_keeps_one_decimal_only_below_ten(self):
        self.assertEqual(mb.percent(35.3, 6733.1), "0.5%")
        self.assertEqual(mb.percent(165.1, 1699.1), "9.7%")
        self.assertEqual(mb.percent(9.96, 100), "10%")
        self.assertEqual(mb.percent(1208.1, 5359.1), "23%")     # 22.54%: rounded once, not twice
        self.assertEqual(mb.percent(265.4, 407.0), "65%")
        self.assertEqual(mb.percent(5, 0), "0.0%")

    def test_open_water_and_straits(self):
        self.assertEqual(mb.via([]), "through open water")
        self.assertEqual(mb.via(["Sunda Strait", "Luzon Strait"]), "via Sunda Strait, Luzon Strait")

    def test_the_speed_is_the_servers(self):
        self.assertEqual(mb.KNOTS, server.NOMINAL_KNOTS)


# --------------------------------------------------------------- findings
class Findings(unittest.TestCase):
    def test_nothing_closed(self):
        r = scenario(areas=0, best=6733.1, after=("San Bernardino Strait",), affected=False)
        self.assertEqual(mb.finding(r), "With nothing closed, the best route is 6,733 nm via San Bernardino "
                                        "Strait, about 15.6 days at 18 knots.")

    def test_a_new_way_round(self):
        r = scenario(original=6733.1, best=6768.4, before=("San Bernardino Strait",), after=("Luzon Strait",))
        self.assertEqual(mb.finding(r), "With the closed area in place, the best route is 6,768 nm via Luzon "
                                        "Strait, instead of 6,733 nm via San Bernardino Strait. That is 35 nm "
                                        "(0.5%) longer: about 2 hours more at 18 knots.")

    def test_the_same_strait_but_pushed_round(self):
        r = scenario(areas=2, original=407.0, best=672.5, before=(), after=())
        self.assertEqual(mb.finding(r), "With the 2 closed areas in place, the best route still goes through "
                                        "open water, but is 672 nm instead of 407 nm. That is 265 nm (65%) "
                                        "longer: about 15 hours more at 18 knots.")

    def test_the_numbers_on_the_page_add_up(self):
        r = scenario(original=1544.6, best=1659.2, before=("Korea Strait",), after=("Korea Strait",))
        self.assertIn("is 1,659 nm instead of 1,545 nm. That is 114 nm (7.4%) longer", mb.finding(r))
        self.assertIn("+114 nm (7.4%)", mb.impact_rows([r])[0])

    def test_an_area_that_misses_the_route(self):
        r = scenario(best=1000.0, after=("Strait X",), affected=False)
        self.assertEqual(mb.finding(r), "The closed area does not touch the normal route, which stays 1,000 nm "
                                        "via Strait X.")

    def test_no_route(self):
        r = scenario(original=4711.1, before=("Taiwan Strait",),
                     blocked="Destination port Kao-Hsiung is inside a denial area.")
        self.assertEqual(mb.finding(r), "No route is possible. Destination port Kao-Hsiung is inside a closed "
                                        "area. With nothing closed, the route is 4,711 nm via Taiwan Strait.")
        r = scenario(original=0, blocked="No sea route joins these ports.")
        self.assertEqual(mb.finding(r), "No route is possible. No sea route joins these ports.")


# ------------------------------------------------------------- summaries
class Summaries(unittest.TestCase):
    RUN = [scenario("S1", "Long, small change denied", original=6733.1, best=6768.4),
           scenario("S2", "Short, big change", original=407.0, best=672.5),
           scenario("S3", "Biggest delay", original=5359.1, best=6567.2),
           scenario("S4", "Port shut", blocked="Destination port is inside a denial area."),
           scenario("S5", "Missed", affected=False, best=1000.0),
           scenario("S6", "No areas", areas=0, affected=False)]

    def test_takeaways(self):
        self.assertEqual(mb.takeaways(self.RUN), [
            "Largest relative cost: Short, big change (65% longer, 407 to 672 nm).",
            "Longest delay: Biggest delay (about 2.8 days more at 18 knots).",
            "Smallest effect: Long, small change closed (0.5% longer).",
            "1 closure leaves no route at all: Port shut.",
            "1 closure did not change the best route."])

    def test_takeaways_count_and_skip(self):
        self.assertEqual(mb.takeaways([self.RUN[5]]), [])
        one = mb.takeaways([self.RUN[1]])
        self.assertEqual(len(one), 2)                       # no "smallest" when it is also the largest
        two_shut = mb.takeaways([self.RUN[3], scenario("S7", "Also shut", blocked="x")])
        self.assertEqual(two_shut, ["2 closures leave no route at all: Port shut; Also shut."])
        two_missed = mb.takeaways([self.RUN[4], self.RUN[4]])
        self.assertEqual(two_missed, ["2 closures did not change the best route."])

    def test_capabilities_are_read_off_the_run(self):
        circle = {"type": "circle", "center": [0, 0], "radius_nm": 150}
        run = [dict(scenario("S1", areas=0, affected=False), k=3),
               dict(scenario("S2"), areas=[circle, dict(circle, radius_nm=40),
                                           {"type": "polygon", "points": [[0, 0], [1, 0], [1, 1]]}]),
               dict(scenario("S3", blocked="x"), to={"id": "WPI-3", "name": "C", "country": "Z"})]
        segments, passages = mb.lane_network()
        self.assertEqual(mb.capabilities(run), [
            ("Origin to destination", "The best route between two ports, with up to 3 routes per move, each "
                                      "through different waters. 3 moves between 3 ports in 3 countries."),
            ("Realistic sea routes", "Routes follow a published shipping-lane network through real straits and "
                                     "canals, not straight lines (Eurostat MARNET: %s lane segments, %d named "
                                     "straits and canals). This run's routes pass through 2 named straits."
             % (format(segments, ","), passages)),
            ("Area denial and what-if scenarios",
             "4 denial areas in 2 scenarios (3 circles of 40 to 150 nm radius and 1 shape drawn on the map). Every "
             "shipping lane that enters an area is closed, the routes are found again, and the cost is measured in "
             "distance and in sailing time at 18 knots. 1 scenario shows FALCON recognising that no route remains, "
             "and saying why.")])

    def test_capabilities_of_a_smaller_run(self):
        one = dict(scenario(areas=0, affected=False, before=(), after=("Strait X",)), k=2, method="yen")
        with mock.patch.object(mb, "BUILD_REPORT", os.path.join(ROOT, "no-such-file.json")):
            self.assertIsNone(mb.lane_network())
            self.assertEqual(mb.capabilities([one]), [
                ("Origin to destination", "The best route between two ports, with up to 2 routes per move, the "
                                          "next shortest first. 1 move between 2 ports in 2 countries."),
                ("Realistic sea routes", "Routes follow a published shipping-lane network through real straits "
                                         "and canals, not straight lines. This run's routes pass through 1 named "
                                         "strait.")])
        circle = dict(scenario(), areas=[{"type": "circle", "center": [0, 0], "radius_nm": 60}])
        self.assertIn("(1 circle of 60 nm radius)", mb.capabilities([circle])[2][1])
        self.assertNotIn("no route remains", mb.capabilities([circle])[2][1])
        shape = dict(scenario(), areas=[{"type": "polygon", "points": [[0, 0], [1, 0], [1, 1]]}] * 2)
        self.assertIn("2 denial areas in 1 scenario (2 shapes drawn on the map)", mb.capabilities([shape])[2][1])
        shut = [scenario("S1", blocked="x"), scenario("S2", blocked="y")]
        self.assertIn("2 scenarios show FALCON recognising", mb.capabilities(shut)[2][1])

    def test_what_was_tested(self):
        r = dict(scenario(), checks=[{"check": n, "passed": True} for n in (
            "A route is found", "Best route avoids Strait X", "Every route starts at the origin and ends at the "
            "destination", "No route enters an area of denial", "No route is possible")])
        out = mb.tested([r, dict(scenario("S2"), checks=[])])
        self.assertEqual(out[0], "All 2 scenarios passed all 5 automated checks.")
        self.assertTrue(out[1].startswith("3 checks compare each answer with what its scenario expects"))
        self.assertTrue(out[2].startswith("2 checks hold every route to rules it must always keep"))
        self.assertEqual(mb.tested([dict(scenario(), checks=[])]), ["All 1 scenario passed all 0 automated checks."])

    def test_the_rule_checks_are_the_runners(self):
        """RULE_CHECKS must name every check run_scenarios.py makes of every answer."""
        result = server.find_routes({"from": "WPI-60390", "to": "WPI-58395", "k": 2, "method": "distinct",
                                     "areas": [{"type": "circle", "center": [124.3, 12.6], "radius_nm": 150}]})
        rules = rs.invariant_checks(result, "WPI-60390", "WPI-58395",
                                    server.parse_areas([{"type": "circle", "center": [124.3, 12.6], "radius_nm": 150}]))
        self.assertEqual(len(rules), 6)
        self.assertTrue(all(c["check"].startswith(mb.RULE_CHECKS) for c in rules))
        expect = rs.expectation_checks(result, {"route_found": True, "blocked_contains": "x", "routes_at_least": 1,
                                                "best_length_nm": {"min": 0}, "best_passes": ["Luzon Strait"],
                                                "best_avoids": ["Sunda Strait"], "affected": True,
                                                "extra_nm": {"min": 0}})
        expect += rs.expectation_checks({"routes": [], "blocked": "x"}, {"route_found": False})
        self.assertFalse(any(c["check"].startswith(mb.RULE_CHECKS) for c in expect))

    def test_the_impact_table_puts_the_biggest_first_and_no_route_last(self):
        rows = mb.impact_rows(self.RUN)
        self.assertEqual([re.search(r"href='#(S\d)'", r).group(1) for r in rows], ["S2", "S3", "S1", "S5", "S4"])
        self.assertIn("+265 nm (65%)", rows[0])
        self.assertIn("15 hours", rows[0])
        self.assertIn("none", rows[3])
        self.assertIn("No route: Destination port is inside a closed area.", rows[4])

    def test_options_list_the_normal_route_first_when_something_is_closed(self):
        closed = mb.option_rows(scenario(more=(1200.0,)))
        self.assertEqual(re.findall(r"<tr><td>([^<]+)</td>", closed),
                         ["Normal route, nothing closed", "Best route with the closure", "Alternative 2"])
        self.assertIn("<td>open water</td>", closed)
        nothing = mb.option_rows(scenario(areas=0))
        self.assertEqual(re.findall(r"<tr><td>([^<]+)</td>", nothing), ["Best route"])

    def test_port_access_from_the_build_report(self):
        self.assertRegex(mb.port_access(), r"^[0-9,]+ of [0-9,]+ ports reach their nearest lane")
        with mock.patch.object(mb, "BUILD_REPORT", os.path.join(ROOT, "no-such-file.json")):
            self.assertIsNone(mb.port_access())


# ------------------------------------------------------------------- maps
SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d">'
       '<text x="28" y="41" font-size="19">S02  ·  A → B</text>'
       '<text x="28" y="64" font-size="13.5">Distinct corridors, K=3, 1 area of denial</text>'
       '<text x="72" y="600">Original, no areas  6,733 nm</text><text x="72" y="620">Area of denial</text>'
       '<text x="72" y="640">Lanes closed by the areas</text>'
       '<text x="72" y="660">Blocked: Destination port B is inside a denial area.</text></svg>'
       % (map_snapshot.WIDTH, map_snapshot.HEIGHT))


class Maps(unittest.TestCase):
    def test_the_snapshot_is_relabelled_for_readers(self):
        svg = mb.plain_map(SVG, "A & B → C", "1 closed area")
        texts = [t.text for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]
        self.assertEqual(texts, ["A & B → C", "1 closed area", "Normal route  6,733 nm", "Closed area",
                                 "Lanes closed", "Blocked: Destination port B is inside a closed area."])
        self.assertIn('class="map" width="100%" height="auto"', svg)

    def test_a_run_without_the_snapshot_has_no_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(mb.map_for(tmp, scenario()), "")
            r = dict(scenario(), snapshots=[{"label": "Whole route", "svg": "snapshots/gone.svg", "png": None}])
            self.assertEqual(mb.map_for(tmp, r), "")
            os.makedirs(os.path.join(tmp, "snapshots"))
            with open(os.path.join(tmp, "snapshots", "gone.svg"), "w", encoding="utf-8") as f:
                f.write(SVG)
            self.assertIn(">A → B<", mb.map_for(tmp, r))
            self.assertIn(">Nothing closed<", mb.map_for(tmp, dict(r, areas=[])))
            self.assertIn(">2 closed areas<", mb.map_for(tmp, dict(r, areas=r["areas"] * 2)))


# ------------------------------------------------------------- the brief
class Brief(unittest.TestCase):
    def test_the_whole_brief(self):
        data = run_data(Summaries.RUN[1], Summaries.RUN[3], Summaries.RUN[5])
        with tempfile.TemporaryDirectory() as tmp:
            path = mb.write_brief(tmp, data, note="Short moves <suffer> most.")
            with open(path, encoding="utf-8") as f:
                html = f.read()
        words = text_of(html)
        for part in ("FALCON · origin-to-destination scenarios", "3 scenarios · run of 2026-11-03", "At a glance",
                     "What this run shows", "Origin to destination.", "Realistic sea routes.",
                     "Area denial and what-if scenarios.", "Mode tested: Sea. Air moves between airfields",
                     "How it was tested", "What it found", "What each closure costs", "Assumptions and limits",
                     "Data sources", "FALCON code abc1234. All 3 scenarios passed 3 automated checks",
                     "They do not predict or detect events."):
            self.assertIn(part, words)
        self.assertIn("Short moves &lt;suffer&gt; most.", html)
        for _, _, url in mb.SOURCES:
            self.assertIn("href='%s'" % url, html)
        self.assertNotIn("WPI-", words)
        self.assertNotRegex(words, r"(?i)denied|area of denial|\bK\s*=")

    def test_a_run_with_no_closures_has_no_impact_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(mb.write_brief(tmp, run_data(Summaries.RUN[5])), encoding="utf-8") as f:
                html = f.read()
        self.assertNotIn("What each closure costs", html)
        self.assertNotIn("What it found", html)
        self.assertIn("What this run shows", html)
        self.assertNotIn("class='note'", html)

    def test_names_are_escaped(self):
        r = scenario(name="<b>A</b> & B denied")
        with tempfile.TemporaryDirectory() as tmp:
            with open(mb.write_brief(tmp, run_data(r)), encoding="utf-8") as f:
                html = f.read()
        self.assertNotIn("<b>A</b>", html)
        self.assertIn("&lt;b&gt;A&lt;/b&gt; &amp; B closed", html)


# ---------------------------------------------------------- picking a run
class Runs(unittest.TestCase):
    def test_the_newest_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("2026-10-29_151903", "2026-11-03_105611", "2026-11-01_090000"):
                write_run(os.path.join(tmp, name), run_data(scenario()))
            with mock.patch.object(mb, "REPORTS", tmp):
                self.assertEqual(os.path.basename(mb.newest_run()), "2026-11-03_105611")

    def test_no_runs_yet(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(mb, "REPORTS", tmp):
            with self.assertRaisesRegex(mb.BriefError, "No scenario runs in .*run_scenarios.py first"):
                mb.newest_run()

    def test_runs_it_will_not_brief(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(mb.BriefError, "has no results.json"):
                mb.load_run(tmp)
            with open(os.path.join(tmp, "results.json"), "w") as f:
                f.write("{not json")
            with self.assertRaisesRegex(mb.BriefError, "is not valid JSON"):
                mb.load_run(tmp)
            for empty in ([], {"run": {}}, {"run": {}, "scenarios": []}, {"scenarios": [scenario()]}):
                write_run(tmp, empty)
                with self.assertRaisesRegex(mb.BriefError, "has no scenarios in it"):
                    mb.load_run(tmp)
            write_run(tmp, run_data(scenario("S1"), scenario("S2", passed=False)))
            with self.assertRaisesRegex(mb.BriefError, "^Scenario S2 failed QA"):
                mb.load_run(tmp)
            write_run(tmp, run_data(scenario("S1", passed=False), scenario("S2", passed=False)))
            with self.assertRaisesRegex(mb.BriefError, "^Scenarios S1, S2 failed QA"):
                mb.load_run(tmp)
            write_run(tmp, run_data(scenario("S1")))
            self.assertEqual(mb.load_run(tmp)["scenarios"][0]["id"], "S1")


# ---------------------------------------------------------- command line
class CommandLine(unittest.TestCase):
    def test_a_failed_run_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_run(tmp, run_data(scenario(passed=False)))
            code, out = quietly(mb.main, [tmp])
            self.assertEqual(code, 1)
            self.assertIn("failed QA", out)
            self.assertFalse(os.path.exists(os.path.join(tmp, "brief.html")))

    def test_html_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_run(tmp, run_data(scenario(), code="abc1234 + local changes"))
            code, out = quietly(mb.main, [tmp, "--no-pdf", "--note", "Mine."])
            self.assertEqual(code, 0)
            self.assertIn("Note: this run used code with uncommitted changes", out)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "brief.html")))
            self.assertNotIn("PDF", out)

    def test_without_a_browser_or_when_it_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_run(tmp, run_data(scenario()))
            with mock.patch.object(map_snapshot, "find_browser", return_value=None):
                code, out = quietly(mb.main, [tmp])
            self.assertEqual(code, 0)
            self.assertIn("No PDF: no Edge or Chrome found", out)
            with mock.patch.object(map_snapshot, "find_browser", return_value="edge"), \
                    mock.patch.object(mb, "html_to_pdf", return_value=False):
                _, out = quietly(mb.main, [tmp])
            self.assertIn("No PDF: the browser did not write one", out)
            with mock.patch.object(map_snapshot, "find_browser", return_value="edge"), \
                    mock.patch.object(mb, "html_to_pdf", return_value=True):
                _, out = quietly(mb.main, [tmp])
            self.assertIn("PDF:   ", out)

    def test_a_browser_that_will_not_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = os.path.join(tmp, "brief.html")
            with open(page, "w") as f:
                f.write("<p>x</p>")
            pdf = os.path.join(tmp, "brief.pdf")
            with open(pdf, "w") as f:
                f.write("old")
            self.assertFalse(mb.html_to_pdf(page, pdf, os.path.join(tmp, "no-such-browser.exe")))
            self.assertFalse(os.path.exists(pdf))           # an old PDF is never left looking new

    @unittest.skipUnless(map_snapshot.find_browser(), "needs Edge or Chrome")
    def test_a_real_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_run(tmp, run_data(scenario()))
            code, _ = quietly(mb.main, [tmp])
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "brief.pdf"), "rb") as f:
                self.assertEqual(f.read(5), b"%PDF-")


# ------------------------------------------------- from scenarios to brief
class FromScenarios(unittest.TestCase):
    def test_a_real_run_becomes_a_brief(self):
        files = [os.path.join(rs.SCENARIOS, n) for n in ("01_basic_san_diego_to_subic_bay.json",
                                                         "02_denial_san_bernardino_strait.json",
                                                         "05_taiwan_quarantine_honolulu_to_kaohsiung.json")]
        with tempfile.TemporaryDirectory() as tmp:
            code, _ = quietly(rs.main, files + ["--out", tmp, "--no-png"])
            self.assertEqual(code, 0)
            code, _ = quietly(mb.main, [tmp, "--no-pdf"])
            self.assertEqual(code, 0)
            with open(os.path.join(tmp, "brief.html"), encoding="utf-8") as f:
                html = f.read()
        words = text_of(html)
        self.assertIn("the best route is 6,768 nm via Luzon Strait, instead of 6,733 nm via San Bernardino "
                      "Strait. That is 35 nm (0.5%) longer", words)
        self.assertIn("No route is possible. Destination port Kao-Hsiung is inside a closed area.", words)
        self.assertNotRegex(words, r"(?i)denied|area of denial|WPI-")
        maps = re.findall(r"<svg.*?</svg>", html, flags=re.S)
        self.assertEqual(len(maps), 3)
        for svg in maps:
            ET.fromstring(svg)                               # each map is still a valid picture
            self.assertNotRegex(svg, r">[^<]*(?i:denial|corridors|K=)[^<]*<")


if __name__ == "__main__":
    unittest.main()
