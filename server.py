"""
A small web front end for the sea network: pick an origin and a destination
port, see K alternative sea routes on a map.

    python server.py          then open http://localhost:8100

Python 3, stdlib only. All routing lives in sea_routing.py.
"""
import csv
import json
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sea_routing import SeaGraph

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SEA_NETWORK_PORT", "8100"))
MAX_K = 5

print("loading sea network ...")
GRAPH = SeaGraph.load()
with open(os.path.join(HERE, "neo4j-datamodel", "nodes", "passages.csv"), encoding="utf-8") as f:
    PASSAGE_NAMES = {r["passage_id"]: r["name"] for r in csv.DictReader(f)}
PORT_LIST = sorted(({"id": pid, "name": p["name"], "country": p["country"] or "",
                     "lat": float(p["lat"]), "lon": float(p["lon"]), "size": p["harbor_size"] or ""}
                    for pid, p in GRAPH.ports.items()), key=lambda p: (p["name"], p["country"]))


def find_routes(q):
    origin, goal = (q.get("from") or [""])[0], (q.get("to") or [""])[0]
    method = (q.get("method") or ["yen"])[0]
    if origin not in GRAPH.ports or goal not in GRAPH.ports:
        return {"error": "Unknown origin or destination port."}
    if origin == goal:
        return {"error": "Origin and destination are the same port."}
    try:
        k = max(1, min(MAX_K, int((q.get("k") or ["3"])[0])))
    except ValueError:
        return {"error": "K must be a number."}
    if method not in ("yen", "distinct"):
        return {"error": "Unknown method."}

    started = time.perf_counter()
    routes = (GRAPH.k_shortest(origin, goal, k) if method == "yen"
              else GRAPH.distinct_routes(origin, goal, k))
    ms = round((time.perf_counter() - started) * 1000)
    if not routes:
        return {"error": "No sea route connects these ports."}
    out = []
    for i, r in enumerate(routes, 1):
        d = GRAPH.describe(r)
        d["passages"] = [PASSAGE_NAMES.get(p, p) for p in d["passages"]]
        out.append(dict(rank=i, **d))
    return {"method": method, "k": k, "ms": ms, "routes": out}


API = {"/api/ports": lambda q: PORT_LIST, "/api/routes": find_routes}

JS = "application/javascript"
STATIC = {"/": ("web/index.html", "text/html; charset=utf-8"),
          "/index.html": ("web/index.html", "text/html; charset=utf-8"),
          "/land-50m.json": ("web/land-50m.json", "application/json"),
          "/vendor/d3-array.min.js": ("web/vendor/d3-array.min.js", JS),
          "/vendor/d3-geo.min.js": ("web/vendor/d3-geo.min.js", JS),
          "/vendor/topojson-client.min.js": ("web/vendor/topojson-client.min.js", JS)}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, q = parsed.path, urllib.parse.parse_qs(parsed.query)
        if path in API:
            try:
                body = json.dumps(API[path](q))
            except Exception as e:
                body = json.dumps({"error": "%s: %s" % (type(e).__name__, e)})
            return self.send(200, body, "application/json")
        if path in STATIC:
            name, ctype = STATIC[path]
            with open(os.path.join(HERE, name), "rb") as f:
                return self.send(200, f.read(), ctype)
        self.send(404, "not found", "text/plain")


if __name__ == "__main__":
    print("FALCON sea network  ->  http://localhost:%d   (Ctrl-C to stop)" % PORT)

    class Server(ThreadingHTTPServer):
        allow_reuse_address = False      # on Windows it would let a second copy share the port
        daemon_threads = True

    try:
        Server(("127.0.0.1", PORT), Handler).serve_forever()
    except OSError as e:
        raise SystemExit("Port %d is already in use (%s). Stop the other copy, or set "
                         "SEA_NETWORK_PORT to another port." % (PORT, e))
