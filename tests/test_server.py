"""
The web server, server.py: every check on a request, the shape of the
answer, and the HTTP layer itself, run on a real socket.

    python -m unittest tests.test_server
"""
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
import server  # noqa: E402  (loads and checks the real data model)
import toy_network as toy  # noqa: E402
from sea_routing import SeaGraph  # noqa: E402

BUSAN, SUBIC = "WPI-60390", "WPI-58395"
GOOD = {"from": BUSAN, "to": SUBIC, "k": 2, "method": "yen"}


def circle(center, r):
    return {"type": "circle", "center": center, "radius_nm": r}


def square(lon, lat, half=0.1):
    return {"type": "polygon", "points": [[lon - half, lat - half], [lon + half, lat - half],
                                          [lon + half, lat + half], [lon - half, lat + half]]}


class ParseAreas(unittest.TestCase):
    def refused(self, raw, words):
        with self.assertRaises(server.BadRequest) as ctx:
            server.parse_areas(raw)
        self.assertIn(words, str(ctx.exception))

    def test_none_and_empty_mean_no_areas(self):
        self.assertEqual(server.parse_areas(None), [])
        self.assertEqual(server.parse_areas([]), [])

    def test_a_circle_and_a_polygon(self):
        a, b = server.parse_areas([circle([124.2, 22.4], 240), square(120, 20)])
        self.assertEqual((a.kind, a.r), ("circle", 240.0))
        self.assertAlmostEqual(a.c[0], 124.2, places=9)       # longitude goes through the wrap to -180..180
        self.assertEqual(a.c[1], 22.4)
        self.assertEqual((b.kind, len(b.poly)), ("polygon", 4))

    def test_must_be_a_list_of_at_most_twenty(self):
        self.refused({"type": "circle"}, "Up to 20 areas")
        self.refused("circle", "Up to 20 areas")
        self.assertEqual(len(server.parse_areas([circle([0, 0], 1)] * 20)), 20)
        self.refused([circle([0, 0], 1)] * 21, "Up to 20 areas")

    def test_each_area_is_a_circle_or_a_polygon(self):
        for bad in ([{"type": "square"}], [{}], ["circle"], [None], [[1, 2]]):
            with self.subTest(bad=bad):
                self.refused(bad, "must be a circle or a polygon")

    def test_circle_radius_limits(self):
        self.assertEqual(server.parse_areas([circle([0, 0], 3000)])[0].r, 3000)
        self.assertEqual(server.parse_areas([circle([0, 0], 0.01)])[0].r, 0.01)
        for r in (0, -5, 3000.01, float("nan"), float("inf"), "10", None, True, [10]):
            with self.subTest(radius=r):
                self.refused([circle([0, 0], r)], "radius must be between 0 and 3000")

    def test_points_must_be_longitude_latitude_numbers(self):
        for center in (None, [1], [1, 2, 3], ["1", "2"], [1, None], [float("nan"), 0], [0, float("inf")],
                       [True, False], {"lon": 1, "lat": 2}, "1,2"):
            with self.subTest(center=center):
                self.refused([circle(center, 10)], "A point must be [longitude, latitude]")

    def test_latitude_range(self):
        server.parse_areas([circle([0, 90], 10), circle([0, -90], 10)])
        for lat in (90.01, -91):
            with self.subTest(lat=lat):
                self.refused([circle([0, lat], 10)], "Latitude must be between -90 and 90")

    def test_longitude_wraps_round(self):
        self.assertEqual(server.parse_areas([circle([190, 10], 5)])[0].c, (-170, 10))
        self.assertEqual(server.parse_areas([circle([-540, 10], 5)])[0].c, (-180, 10))

    def test_polygon_corner_limits(self):
        pts = [[0, 0], [1, 0], [1, 1]]
        self.assertEqual(len(server.parse_areas([{"type": "polygon", "points": pts}])[0].poly), 3)
        many = [[i * 0.01, (i % 2) * 0.01] for i in range(200)]
        self.assertEqual(len(server.parse_areas([{"type": "polygon", "points": many}])[0].poly), 200)
        for bad in (pts[:2], [], many + [[3, 3]], None, "0 0, 1 0, 1 1"):
            with self.subTest(points=bad):
                self.refused([{"type": "polygon", "points": bad}], "A polygon needs 3 to 200 points")
        self.refused([{"type": "polygon", "points": [[0, 0], [1, 0], [1, "x"]]}], "A point must be")


class FindRoutesChecks(unittest.TestCase):
    def refused(self, body, words):
        with self.assertRaises(server.BadRequest) as ctx:
            server.find_routes(body)
        self.assertIn(words, str(ctx.exception))

    def test_ports_must_be_known_port_ids(self):
        for bad in ({"from": "WPI-0", "to": SUBIC}, {"from": BUSAN}, {"to": SUBIC}, {},
                    {"from": [BUSAN], "to": SUBIC}, {"from": BUSAN, "to": {"id": SUBIC}},
                    {"from": 60390, "to": SUBIC}, {"from": None, "to": None}, {"from": "busan", "to": SUBIC}):
            with self.subTest(body=bad):
                self.refused(bad, "Unknown origin or destination port")

    def test_ports_must_differ(self):
        self.refused({"from": BUSAN, "to": BUSAN}, "the same port")

    def test_k_is_a_whole_number_from_one_to_five(self):
        for k in (0, 6, -1, 2.0, 2.5, "3", None, True, False, [3]):
            with self.subTest(k=k):
                self.refused(dict(GOOD, k=k), "K must be a whole number from 1 to 5")

    def test_method(self):
        for method in ("dijkstra", "YEN", "", None, ["yen"], 1):
            with self.subTest(method=method):
                self.refused(dict(GOOD, method=method), "Method must be 'yen' or 'distinct'")

    def test_defaults_are_three_routes_by_yen(self):
        r = server.find_routes({"from": BUSAN, "to": SUBIC})
        self.assertEqual((r["k"], r["method"], r["areas"], len(r["routes"])), (3, "yen", 0, 3))


class FindRoutesAnswers(unittest.TestCase):
    def test_the_answer_without_areas(self):
        r = server.find_routes(dict(GOOD, k=5))
        self.assertEqual(r["original"]["rank"], 0)
        self.assertEqual(r["original"]["length_nm"], 1449.6)
        self.assertEqual([x["rank"] for x in r["routes"]], [1, 2, 3, 4, 5])
        self.assertEqual(r["routes"][0]["length_nm"], r["original"]["length_nm"])
        self.assertEqual((r["closed_links"], r["affected"]), (0, False))
        self.assertNotIn("extra_nm", r)
        self.assertNotIn("blocked", r)
        self.assertIsInstance(r["ms"], int)
        self.assertIn("Taiwan Strait", r["routes"][0]["passages"])     # names, not ids
        json.dumps(r)                                                  # everything serialises

    def test_an_area_on_the_route(self):
        taiwan = {"type": "polygon", "points": [[118.5, 26.5], [121.8, 25.6], [121.0, 21.8], [119.0, 22.2]]}
        r = server.find_routes(dict(GOOD, k=1, areas=[taiwan]))
        self.assertTrue(r["affected"])
        self.assertGreater(r["closed_links"], 0)
        self.assertNotIn("Taiwan Strait", r["routes"][0]["passages"])
        self.assertAlmostEqual(r["extra_nm"], r["routes"][0]["length_nm"] - 1449.6, delta=0.11)
        self.assertEqual(r["extra_days_at_18kt"], round(r["extra_nm"] / 18 / 24, 1))

    def test_an_area_off_the_route(self):
        r = server.find_routes(dict(GOOD, k=1, areas=[circle([-160, -60], 50)]))
        self.assertEqual((r["affected"], r["closed_links"], r["extra_nm"]), (False, 0, 0.0))

    def test_a_port_inside_an_area(self):
        for role, center in (("Origin", [129.033333, 35.1]), ("Destination", [120.28, 14.8])):
            with self.subTest(role=role):
                r = server.find_routes(dict(GOOD, areas=[circle(center, 30)]))
                self.assertEqual(r["routes"], [])
                self.assertTrue(r["blocked"].startswith(role + " port "), r["blocked"])
                self.assertIn("inside a denial area", r["blocked"])
                self.assertNotIn("closed_links", r)

    def test_both_methods_answer(self):
        for method in ("yen", "distinct"):
            with self.subTest(method=method):
                self.assertEqual(len(server.find_routes(dict(GOOD, k=3, method=method))["routes"]), 3)


class FindRoutesOnTheToy(unittest.TestCase):
    """Answers that need a network small enough to cut off completely."""

    @classmethod
    def setUpClass(cls):
        cls.folder = toy.ToyFolder()
        cls.patch = mock.patch.object(server, "GRAPH", SeaGraph.load(cls.folder.__enter__()))
        cls.patch.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.folder.__exit__(None, None, None)

    def test_no_route_avoids_the_areas(self):
        r = server.find_routes({"from": "PA", "to": "PB", "areas": [circle([1, 0.5], 5), circle([1, -1], 5)]})
        self.assertEqual(r["routes"], [])
        self.assertEqual(r["blocked"], "No sea route avoids the denial areas.")
        self.assertTrue(r["affected"])
        self.assertNotIn("extra_nm", r)

    def test_ports_in_seas_that_do_not_connect(self):
        with self.assertRaises(server.BadRequest) as ctx:
            server.find_routes({"from": "PA", "to": "PZ"})
        self.assertIn("No sea route connects these ports", str(ctx.exception))

    def test_unknown_passage_ids_are_shown_as_they_are(self):
        self.assertEqual(server.find_routes({"from": "PA", "to": "PB", "k": 1})["routes"][0]["passages"], ["north"])


# ------------------------------------------------------------------ HTTP
class Http(unittest.TestCase):
    """The real request handler on a real socket, on a free port."""

    class Handler(server.Handler):
        timeout = 1.0           # so the slow-client test is quick

    @classmethod
    def setUpClass(cls):
        cls.httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), cls.Handler)
        cls.httpd.daemon_threads = True
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            if isinstance(body, (dict, list)):
                body = json.dumps(body)
            conn.request(method, path, body=body, headers=headers or {})
            r = conn.getresponse()
            return r.status, r.getheader("Content-Type"), r.read()
        finally:
            conn.close()

    def raw(self, data, wait=10):
        """Send bytes as they are and return whatever comes back."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=wait) as s:
            s.sendall(data)
            out = b""
            try:
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    out += chunk
                    if b"\r\n\r\n" in out and len(out.split(b"\r\n\r\n", 1)[1]) >= int(
                            out.split(b"Content-Length: ")[1].split(b"\r\n")[0]):
                        break
            except socket.timeout:
                pass
            return out

    def test_port_list(self):
        status, ctype, body = self.request("GET", "/api/ports?anything=1")
        ports = json.loads(body)
        self.assertEqual((status, ctype), (200, "application/json"))
        self.assertEqual(len(ports), 3806)
        self.assertEqual(set(ports[0]), {"id", "name", "country", "lat", "lon", "size"})
        self.assertEqual(ports, sorted(ports, key=lambda p: (p["name"], p["country"])))

    def test_the_map_page_and_its_files(self):
        for path, ctype in (("/", "text/html; charset=utf-8"), ("/index.html", "text/html; charset=utf-8"),
                            ("/land-50m.json", "application/json"),
                            ("/vendor/d3-geo.min.js", "application/javascript")):
            with self.subTest(path=path):
                status, got, body = self.request("GET", path)
                self.assertEqual((status, got), (200, ctype))
                self.assertGreater(len(body), 100)

    def test_nothing_else_is_served(self):
        for path in ("/nope", "/server.py", "/../server.py", "/web/index.html", "/api/routes"):
            with self.subTest(path=path):
                self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("POST", "/api/ports", GOOD)[0], 404)

    def test_routes(self):
        status, ctype, body = self.request("POST", "/api/routes", GOOD, {"Content-Type": "application/json"})
        self.assertEqual((status, ctype), (200, "application/json"))
        self.assertEqual(len(json.loads(body)["routes"]), 2)

    def assertRefused(self, status, body, code=400):
        self.assertEqual(status, code, body)
        self.assertTrue(json.loads(body)["error"])

    def test_bad_bodies_are_refused_with_a_message(self):
        for body in ("", "{not json", "[1, 2]", '"text"', "null", "42", b"\xff\xfe{}", json.dumps({"from": [1]})):
            with self.subTest(body=body):
                status, _, out = self.request("POST", "/api/routes", body)
                self.assertRefused(status, out)

    def test_a_missing_or_wrong_length_is_refused(self):
        for length in ("abc", "-5", "0", str(server.MAX_BODY_BYTES + 1)):
            with self.subTest(length=length):
                out = self.raw(b"POST /api/routes HTTP/1.1\r\nHost: x\r\nContent-Length: " + length.encode()
                               + b"\r\nConnection: close\r\n\r\n")
                self.assertTrue(out.startswith(b"HTTP/1.1 400"), out[:80])

    def test_a_client_that_stops_sending_is_dropped(self):
        started = time.perf_counter()
        out = self.raw(b"POST /api/routes HTTP/1.1\r\nHost: x\r\nContent-Length: 100\r\n\r\n{\"from\": ")
        self.assertTrue(out.startswith(b"HTTP/1.1 408"), out[:80])
        self.assertLess(time.perf_counter() - started, 5)

    def test_an_unexpected_failure_is_a_500_with_a_message(self):
        with mock.patch.object(server, "find_routes", side_effect=RuntimeError("boom")):
            status, _, out = self.request("POST", "/api/routes", GOOD)
        self.assertRefused(status, out, 500)

    def test_two_requests_on_one_connection(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            for _ in range(2):
                conn.request("POST", "/api/routes", body=json.dumps(GOOD))
                r = conn.getresponse()
                self.assertEqual(r.status, 200)
                r.read()
        finally:
            conn.close()

    def test_requests_at_the_same_time_get_the_right_answers(self):
        bodies = [dict(GOOD, k=k, method=m) for k in (1, 2, 3) for m in ("yen", "distinct")] * 2
        expected = [server.find_routes(b)["routes"] for b in bodies]
        with ThreadPoolExecutor(max_workers=8) as pool:
            answers = list(pool.map(lambda b: self.request("POST", "/api/routes", b), bodies))
        for b, want, (status, _, out) in zip(bodies, expected, answers):
            with self.subTest(body=b):
                self.assertEqual(status, 200)
                self.assertEqual([r["length_nm"] for r in json.loads(out)["routes"]], [r["length_nm"] for r in want])


# ---------------------------------------------------------------- start-up
def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class StartUp(unittest.TestCase):
    """python server.py, as someone would run it, in its own process."""

    def start(self, port, code=None):
        env = dict(os.environ, SEA_NETWORK_PORT=str(port), PYTHONIOENCODING="utf-8")
        args = [sys.executable, "-u", "-c", code] if code else [sys.executable, "-u", "server.py"]
        return subprocess.Popen(args, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def test_starts_and_answers(self):
        port = free_port()
        proc = self.start(port)
        try:
            deadline = time.time() + 60
            while True:
                try:
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                    conn.request("GET", "/api/ports")
                    r = conn.getresponse()
                    self.assertEqual((r.status, len(json.loads(r.read()))), (200, 3806))
                    conn.close()
                    break
                except OSError:
                    self.assertIsNone(proc.poll(), "the server stopped")
                    self.assertLess(time.time(), deadline, "the server did not start within 60 s")
                    time.sleep(0.25)
        finally:
            proc.terminate()
            out = proc.communicate(timeout=30)[0]
        self.assertIn("http://localhost:%d" % port, out)

    def test_says_so_when_the_port_is_taken(self):
        with socket.socket() as taken:
            taken.bind(("127.0.0.1", 0))
            taken.listen()
            proc = self.start(taken.getsockname()[1])
            out = proc.communicate(timeout=120)[0]
        self.assertEqual(proc.returncode, 1)
        self.assertIn("is already in use", out)
        self.assertIn("SEA_NETWORK_PORT", out)

    def test_refuses_to_start_on_bad_data(self):
        code = ("import runpy, validate_data\n"
                "def broken(*args):\n"
                "    raise validate_data.DataError('nodes/ports.csv row 5 (WPI-1): lat is not a valid position')\n"
                "validate_data.check = broken\n"
                "runpy.run_path('server.py', run_name='__main__')\n")
        proc = self.start(free_port(), code)
        out = proc.communicate(timeout=120)[0]
        self.assertEqual(proc.returncode, 1)
        self.assertIn("Not starting: nodes/ports.csv row 5", out)
        self.assertIn("MIGRATION.md", out)


if __name__ == "__main__":
    unittest.main()
