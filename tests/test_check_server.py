"""
The acceptance checker, acceptance/check_server.py, and the script that
writes its reference answers, acceptance/make_golden.py.

The checker is what decides whether a migrated server is done, so it must
judge every answer by the rules in acceptance/README.md, and must report a
broken or silent server as a failure rather than crash. These tests run it
against a fake server that answers whatever each test needs, and against
the real one.

    python -m unittest tests.test_check_server
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "acceptance"))
import check_server as cs  # noqa: E402
import make_golden  # noqa: E402
import server  # noqa: E402

WANT = {"status": 200, "blocked": None, "original_nm": 1449.6, "closed_links": 100, "affected": True,
        "extra_nm": 50.0, "routes": [{"length_nm": 1499.6, "passages": ["Korea Strait", "Luzon Strait"]},
                                     {"length_nm": 1600.0, "passages": []}]}


def quietly(fn, *args):
    """fn(*args) with its printing captured: (result, printed text)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = fn(*args)
    return result, out.getvalue()


class LengthOk(unittest.TestCase):
    def test_within_one_nm_on_short_routes(self):
        self.assertTrue(cs.length_ok(100, 101))
        self.assertTrue(cs.length_ok(100, 99))
        self.assertFalse(cs.length_ok(100, 101.01))

    def test_within_a_tenth_of_a_percent_on_long_routes(self):
        self.assertTrue(cs.length_ok(5000, 5005))
        self.assertFalse(cs.length_ok(5000, 5005.1))

    def test_a_missing_length_is_wrong(self):
        self.assertFalse(cs.length_ok(100, None))


class Summarize(unittest.TestCase):
    def test_a_refusal(self):
        self.assertEqual(cs.summarize(400, {"error": "bad"}), {"status": 400, "has_error": True})
        self.assertEqual(cs.summarize(400, {"error": ""}), {"status": 400, "has_error": False})
        self.assertEqual(cs.summarize(500, None), {"status": 500, "has_error": False})
        self.assertEqual(cs.summarize(400, ["bad"]), {"status": 400, "has_error": False})

    def test_an_answer(self):
        body = {"original": {"length_nm": 1449.6}, "closed_links": 3, "affected": False, "ms": 9,
                "routes": [{"rank": 1, "length_nm": 1449.6, "passages": ["Taiwan Strait"], "line": [[0, 0]]}]}
        self.assertEqual(cs.summarize(200, body), {
            "status": 200, "blocked": None, "original_nm": 1449.6, "closed_links": 3, "affected": False,
            "extra_nm": None, "routes": [{"length_nm": 1449.6, "passages": ["Taiwan Strait"]}]})

    def test_an_empty_or_unreadable_answer(self):
        self.assertEqual(cs.summarize(200, {})["routes"], [])
        for body in (None, [], "ok", 3):
            with self.subTest(body=body):
                self.assertEqual(cs.compare(WANT, cs.summarize(200, body)), ["the answer is not a JSON object"])


class Compare(unittest.TestCase):
    def differs(self, **changes):
        return cs.compare(WANT, dict(WANT, **changes))

    def test_the_same_answer(self):
        self.assertEqual(cs.compare(WANT, dict(WANT)), [])

    def test_status(self):
        self.assertEqual(cs.compare(WANT, {"status": 500, "has_error": True}), ["HTTP status 500, expected 200"])
        self.assertEqual(cs.compare({"status": 400, "has_error": True}, {"status": 400, "has_error": True}), [])
        self.assertEqual(cs.compare({"status": 400, "has_error": True}, {"status": 400, "has_error": False}),
                         ['the refusal has no "error" message'])

    def test_blocked(self):
        blocked = dict(WANT, blocked="No sea route avoids the denial areas.", routes=[])
        self.assertEqual(cs.compare(blocked, dict(blocked, blocked="NO SEA ROUTE AVOIDS THE DENIAL AREAS.")), [])
        self.assertIn("blocked reason", cs.compare(blocked, dict(blocked, blocked="Blocked."))[0])
        self.assertIn("blocked: None", cs.compare(blocked, dict(blocked, blocked=None))[0])
        self.assertIn("blocked: 'x'", self.differs(blocked="x")[0])

    def test_lengths(self):
        self.assertEqual(self.differs(original_nm=1451.0), [])          # 0.1% of 1,449.6 is 1.45 nm
        self.assertIn("original route", self.differs(original_nm=1451.1)[0])
        self.assertIn("original route", self.differs(original_nm=None)[0])
        self.assertIn("route #1 is 1520", self.differs(routes=[dict(WANT["routes"][0], length_nm=1520),
                                                               WANT["routes"][1]])[0])

    def test_extra_distance(self):
        self.assertEqual(self.differs(extra_nm=50.9), [])
        self.assertIn("extra_nm", self.differs(extra_nm=60)[0])
        self.assertIn("extra_nm", self.differs(extra_nm=None)[0])
        self.assertEqual(cs.compare(dict(WANT, extra_nm=None), dict(WANT, extra_nm=None)), [])

    def test_affected(self):
        self.assertIn("affected False", self.differs(affected=False)[0])

    def test_closed_links_within_two_or_two_percent(self):
        self.assertEqual(self.differs(closed_links=102), [])
        self.assertIn("closed_links", self.differs(closed_links=103)[0])
        self.assertIn("closed_links", self.differs(closed_links=None)[0])
        few = dict(WANT, closed_links=3)
        self.assertEqual(cs.compare(few, dict(few, closed_links=5)), [])
        self.assertIn("closed_links", cs.compare(few, dict(few, closed_links=6))[0])

    def test_routes_and_their_passages(self):
        self.assertEqual(self.differs(routes=WANT["routes"][:1]), ["1 routes, expected 2"])
        swapped = [dict(WANT["routes"][0], passages=["Luzon Strait", "Korea Strait"]), WANT["routes"][1]]
        self.assertIn("route #1 passes", self.differs(routes=swapped)[0])
        self.assertIn("passes open water", self.differs(routes=[dict(WANT["routes"][0], passages=[]),
                                                                WANT["routes"][1]])[0])

    def test_every_difference_is_listed(self):
        self.assertEqual(len(self.differs(original_nm=1300, affected=False, closed_links=0, extra_nm=1,
                                          routes=[])), 5)


# --------------------------------------------------------------- fake server
class Fake(BaseHTTPRequestHandler):
    """Answers each path with whatever the running test put in ANSWERS:
    (status, body, seconds to wait first). Body is bytes, or JSON-able."""
    ANSWERS = {}
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def answer(self):
        if self.command == "POST":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status, body, wait = self.ANSWERS.get(self.path, (404, b"", 0))
        time.sleep(wait)
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        try:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass                    # the client gave up waiting

    do_GET = do_POST = answer


def good_ports():
    ports = [{"id": "P%d" % i, "name": "Port %d" % i, "country": "X", "lat": 0, "lon": 0}
             for i in range(cs.PORT_COUNT - 1)]
    return ports + [{"id": "WPI-60390", "name": "Busan", "country": "Korea", "lat": 35.1, "lon": 129.033333}]


class AgainstAFakeServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        cls.httpd.daemon_threads = True
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def setUp(self):
        Fake.ANSWERS = {"/api/ports": (200, good_ports(), 0)}

    def test_http_reads_json_and_error_statuses(self):
        Fake.ANSWERS["/ok"] = (200, {"a": 1}, 0)
        Fake.ANSWERS["/bad"] = (400, {"error": "no"}, 0)
        Fake.ANSWERS["/text"] = (502, b"<html>Bad gateway</html>", 0)
        self.assertEqual(cs.http(self.base + "/ok"), (200, {"a": 1}))
        self.assertEqual(cs.http(self.base + "/ok", {"x": 1}), (200, {"a": 1}))
        self.assertEqual(cs.http(self.base + "/bad"), (400, {"error": "no"}))
        self.assertEqual(cs.http(self.base + "/text"), (502, None))
        self.assertEqual(cs.http(self.base + "/missing"), (404, None))

    def test_http_gives_up_on_a_server_that_does_not_answer(self):
        Fake.ANSWERS["/slow"] = (200, {}, 3)
        started = time.perf_counter()
        with self.assertRaises(OSError):
            cs.http(self.base + "/slow", timeout=0.5)
        self.assertLess(time.perf_counter() - started, 2.5)

    def test_a_good_port_list(self):
        self.assertEqual(cs.check_ports(self.base), [])

    def test_port_list_problems(self):
        ports = good_ports()
        cases = {
            "gave HTTP 500": (500, {"error": "x"}),
            "gave HTTP 200": (200, {"ports": ports}),
            "not answer with a list of port objects": (200, ports[:-1] + ["WPI-60390"]),
            "3805 ports, expected 3806": (200, ports[1:]),
            "appear more than once": (200, ports[:-2] + [ports[0], ports[-1]]),
            "ports have no country": (200, [{k: v for k, v in p.items() if k != "country"} for p in ports]),
            "WPI-60390 is missing or not at": (200, ports[:-1] + [dict(ports[-1], lat=35.2)]),
        }
        for words, (status, body) in cases.items():
            with self.subTest(problem=words):
                Fake.ANSWERS["/api/ports"] = (status, body, 0)
                self.assertTrue(any(words in p for p in cs.check_ports(self.base)), cs.check_ports(self.base))

    def test_main_cannot_reach_a_closed_port(self):
        code, printed = quietly(cs.main, ["http://127.0.0.1:9"])
        self.assertEqual(code, 2)
        self.assertIn("Cannot reach", printed)

    def test_main_reports_a_broken_server_without_crashing(self):
        Fake.ANSWERS["/api/routes"] = (200, {"routes": [{"length_nm": "far", "passages": None}, 7]}, 0)
        Fake.ANSWERS["/api/ports"] = (200, [{"id": "WPI-60390", "lon": "east", "lat": 35.1}], 0)
        with tempfile.TemporaryDirectory() as tmp:
            report = os.path.join(tmp, "result.json")
            code, printed = quietly(cs.main, [self.base + "/", "--report", report])
            with open(report, encoding="utf-8") as f:
                saved = json.load(f)
        self.assertEqual(code, 1)
        self.assertEqual(saved["passed"] + saved["failed"], len(saved["results"]))
        self.assertIn("doesn't follow docs/API.md", printed)
        self.assertIn("0 of 29 checks passed", printed)

    def test_main_reports_a_server_that_times_out(self):
        Fake.ANSWERS["/api/routes"] = (200, {}, 2)
        with open(cs.CASES, encoding="utf-8") as f:
            two = {"cases": json.load(f)["cases"][:2]}
        real_http = cs.http
        with tempfile.TemporaryDirectory() as tmp:
            cases = os.path.join(tmp, "cases.json")
            with open(cases, "w", encoding="utf-8") as f:
                json.dump(two, f)
            with mock.patch.object(cs, "CASES", cases), \
                    mock.patch.object(cs, "http", lambda url, body=None: real_http(url, body, timeout=0.3)):
                code, printed = quietly(cs.main, [self.base])
        self.assertEqual(code, 1)
        self.assertEqual(printed.count("no answer:"), 2, printed)
        self.assertIn("1 of 3 checks passed", printed)


class AgainstTheRealServer(unittest.TestCase):
    """The reference server passes its own acceptance test, over HTTP."""

    def test_every_check_passes(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            code, printed = quietly(cs.main, ["http://127.0.0.1:%d" % httpd.server_address[1]])
        finally:
            httpd.shutdown()
            httpd.server_close()
        self.assertEqual(code, 0, printed)
        self.assertIn("29 of 29 checks passed", printed)


# -------------------------------------------------------------- make_golden
class MakeGolden(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_answer_turns_a_refusal_into_a_400(self):
        self.assertEqual(make_golden.answer({"from": "nowhere"}), (400, {"error": "Unknown origin or destination port."}))
        self.assertEqual(make_golden.answer({"from": "WPI-60390", "to": "WPI-58395", "k": 1})[0], 200)

    def test_writes_the_committed_answers(self):
        out = os.path.join(self.tmp, "golden.json")
        with mock.patch.object(make_golden, "GOLDEN", out):
            quietly(make_golden.main)
        with open(out, encoding="utf-8") as f:
            made = json.load(f)
        with open(cs.GOLDEN, encoding="utf-8") as f:
            committed = json.load(f)
        self.assertEqual(made["answers"], committed["answers"])
        self.assertEqual(made["made_from"]["data_sha256"], committed["made_from"]["data_sha256"])

    def test_stops_when_a_case_gets_an_unexpected_status(self):
        cases = os.path.join(self.tmp, "cases.json")
        with open(cases, "w", encoding="utf-8") as f:
            json.dump({"cases": [{"id": "X01", "request": {"from": "nowhere"}, "expect_status": 200}]}, f)
        with mock.patch.object(make_golden, "CASES", cases), \
                mock.patch.object(make_golden, "GOLDEN", os.path.join(self.tmp, "golden.json")):
            with self.assertRaises(SystemExit) as ctx:
                make_golden.main()
        self.assertIn("X01: the reference answered 400", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "golden.json")))


if __name__ == "__main__":
    unittest.main()
