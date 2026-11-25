"""
A small web front end for the sea network: pick an origin and a destination
port, draw denial areas, see K alternative sea routes on a map.

    python server.py          then open http://localhost:8100

Python 3, stdlib only. All routing lives in sea_routing.py.

    GET  /api/ports           every port: id, name, country, lat, lon
    POST /api/routes          {"from": port_id, "to": port_id, "k": 1-5,
                               "method": "yen" | "distinct",
                               "areas": [{"type": "circle", "center": [lon, lat], "radius_nm": r},
                                         {"type": "polygon", "points": [[lon, lat], ...]}]}
    GET  /api/cargo/meta      supply classes, carriers, handover countries, cargo scenarios
    POST /api/cargo           {"from": port_id, "to": port_id, "areas": [...], "wez": [...],
                               "cargo": [{"class": "I", "short_tons": 500}, ...], "priority": "urgent"}
                              areas of denial close the water to everyone, a WEZ to
                              commercial carriers only; see cargo_movement.py
    POST /api/compare         {"from", "to", "cargo", "areas", "wez", "lens": "mode" | "pod",
                               "base": {"areas": [...], "wez": [...]}}   the planner screen's grid,
                              for this situation and the baseline's; see comparisons.py
"""
import csv
import glob
import json
import math
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import comparisons
from cargo_movement import CargoPlanner
from sea_routing import DenialArea, SeaGraph

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get("SEA_NETWORK_PORT", "8100"))
MAX_K = 5
MAX_AREAS = 20
MAX_POLYGON_POINTS = 200
MAX_RADIUS_NM = 3000
MAX_BODY_BYTES = 1_000_000
NOMINAL_KNOTS = 18          # only to turn extra distance into rough days

print("loading sea network ...")
GRAPH = SeaGraph.load()
GRAPH.links_in_areas([])    # build the area index now, not on the first request
LOCK = threading.Lock()     # the graph's lazy caches aren't built for concurrent first use
with open(os.path.join(HERE, "neo4j-datamodel", "nodes", "passages.csv"), encoding="utf-8") as f:
    PASSAGE_NAMES = {r["passage_id"]: r["name"] for r in csv.DictReader(f)}
PLANNER = CargoPlanner(GRAPH)
PORT_LIST = sorted(({"id": pid, "name": p["name"], "country": p["country"] or "",
                     "lat": float(p["lat"]), "lon": float(p["lon"]), "size": p["harbor_size"] or "",
                     "region": p["region_name"] or ""}
                    for pid, p in GRAPH.ports.items()), key=lambda p: (p["name"], p["country"]))
# "size" is OurAirports' own type, large or medium (see neo4j-datamodel/ASSUMPTIONS.md, Airfields).
AIRFIELD_LIST = sorted(({"id": fid, "code": r["icao_code"] or r["iata_code"] or "", "name": r["name"],
                         "country": r["country_code"], "lat": float(r["lat"]), "lon": float(r["lon"]),
                         "size": r["airport_type"].replace("_airport", "").capitalize()}
                        for fid, r in PLANNER.air.fields.items()), key=lambda a: a["name"])


class BadRequest(Exception):
    pass


def _lonlat(v):
    if not (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(x, (int, float)) and math.isfinite(x) for x in v)):
        raise BadRequest("A point must be [longitude, latitude].")
    lon, lat = float(v[0]), float(v[1])
    if not -90 <= lat <= 90:
        raise BadRequest("Latitude must be between -90 and 90.")
    return ((lon + 180) % 360 - 180, lat)


def parse_areas(raw):
    if raw is None:
        return []
    if not isinstance(raw, list) or len(raw) > MAX_AREAS:
        raise BadRequest(f"Up to {MAX_AREAS} areas.")
    areas = []
    for a in raw:
        kind = a.get("type") if isinstance(a, dict) else None
        if kind == "circle":
            r = a.get("radius_nm")
            if not isinstance(r, (int, float)) or not 0 < r <= MAX_RADIUS_NM:
                raise BadRequest(f"A circle's radius must be between 0 and {MAX_RADIUS_NM} nm.")
            areas.append(DenialArea({"type": "circle", "center": _lonlat(a.get("center")), "radius_nm": r}))
        elif kind == "polygon":
            pts = a.get("points")
            if not isinstance(pts, list) or not 3 <= len(pts) <= MAX_POLYGON_POINTS:
                raise BadRequest(f"A polygon needs 3 to {MAX_POLYGON_POINTS} points.")
            areas.append(DenialArea({"type": "polygon", "points": [_lonlat(p) for p in pts]}))
        else:
            raise BadRequest("An area must be a circle or a polygon.")
    return areas


def described(route, rank):
    d = GRAPH.describe(route)
    d["passages"] = [PASSAGE_NAMES.get(p, p) for p in d["passages"]]
    return dict(rank=rank, **d)


def find_routes(body):
    origin, goal = body.get("from"), body.get("to")
    if origin not in GRAPH.ports or goal not in GRAPH.ports:
        raise BadRequest("Unknown origin or destination port.")
    if origin == goal:
        raise BadRequest("Origin and destination are the same port.")
    k = body.get("k", 3)
    if not isinstance(k, int) or not 1 <= k <= MAX_K:
        raise BadRequest(f"K must be a whole number from 1 to {MAX_K}.")
    method = body.get("method", "yen")
    if method not in ("yen", "distinct"):
        raise BadRequest("Method must be 'yen' or 'distinct'.")
    areas = parse_areas(body.get("areas"))

    started = time.perf_counter()
    with LOCK:
        original = GRAPH.shortest(origin, goal)
        if original is None:
            raise BadRequest("No sea route connects these ports.")
        result = {"method": method, "k": k, "areas": len(areas), "original": described(original, 0)}
        for role, pid in (("Origin", origin), ("Destination", goal)):
            if GRAPH.port_in_areas(pid, areas):
                result.update(routes=[], blocked=f"{role} port {GRAPH.ports[pid]['name']} is inside a denial area.")
                return result
        closed = GRAPH.links_in_areas(areas)
        routes = (GRAPH.k_shortest(origin, goal, k, closed=closed) if method == "yen"
                  else GRAPH.distinct_routes(origin, goal, k, closed=closed))
    result["closed_links"] = len(closed)
    result["affected"] = bool(closed & set(original.edges))
    result["routes"] = [described(r, i) for i, r in enumerate(routes, 1)]
    if not routes:
        result["blocked"] = "No sea route avoids the denial areas."
    elif areas:
        extra = routes[0].length_nm - original.length_nm
        result["extra_nm"] = round(extra, 1)
        result["extra_days_at_18kt"] = round(extra / NOMINAL_KNOTS / 24, 1)
    result["ms"] = round((time.perf_counter() - started) * 1000)
    return result


def _priority(v):
    if v is not None and v not in ("economic", "urgent"):
        raise BadRequest("priority must be economic or urgent.")
    return v


def find_options(body):
    """The ways to make one move side by side (planner screen, step 1): by sea and
    by air, in days port to port. With areas drawn, "before" holds the same options
    with no areas, so the screen can show the days each area adds."""
    origin, goal = body.get("from"), body.get("to")
    if origin not in GRAPH.ports or goal not in GRAPH.ports:
        raise BadRequest("Unknown origin or destination port.")
    if origin == goal:
        raise BadRequest("Origin and destination are the same port.")
    denial, wez = parse_areas(body.get("areas")), parse_areas(body.get("wez"))
    started = time.perf_counter()
    with LOCK:
        out = PLANNER.options(origin, goal, denial, wez)
        out["before"] = PLANNER.options(origin, goal) if denial or wez else None
    out["ms"] = round((time.perf_counter() - started) * 1000)
    return out


def parse_cargo(body):
    cargo = body.get("cargo")
    if cargo is not None:
        if not isinstance(cargo, list) or not 1 <= len(cargo) <= 20:
            raise BadRequest("Cargo must be a list of 1 to 20 items.")
        for item in cargo:
            tons = item.get("short_tons") if isinstance(item, dict) else None
            if not isinstance(item, dict) or item.get("class") not in PLANNER.classes:
                raise BadRequest("Each cargo item needs a class: " + ", ".join(PLANNER.classes) + ".")
            if tons is not None and not (isinstance(tons, (int, float)) and 0 <= tons <= 1e7):
                raise BadRequest("short_tons must be a number from 0 to 10,000,000.")
            _priority(item.get("priority"))
        cargo = [{k: i[k] for k in ("class", "short_tons", "priority") if i.get(k) is not None} for i in cargo]
    return cargo


def parse_countries(body):
    countries = body.get("handover_countries")
    if countries is not None and not (isinstance(countries, list) and len(countries) <= 100
                                      and all(isinstance(c, str) for c in countries)):
        raise BadRequest("handover_countries must be a list of country names.")
    return countries


def parse_move(body):
    origin, goal = body.get("from"), body.get("to")
    if origin not in GRAPH.ports or goal not in GRAPH.ports:
        raise BadRequest("Unknown origin or destination port.")
    if origin == goal:
        raise BadRequest("Origin and destination are the same port.")
    return origin, goal


def find_cargo(body):
    origin, goal = parse_move(body)
    denial, wez = parse_areas(body.get("areas")), parse_areas(body.get("wez"))
    cargo, countries = parse_cargo(body), parse_countries(body)
    priority = _priority(body.get("priority")) or "economic"
    started = time.perf_counter()
    with LOCK:
        plan = PLANNER.plan(origin, goal, cargo, denial, wez, countries, priority)
    plan["ms"] = round((time.perf_counter() - started) * 1000)
    return plan


COMPARED = {}                 # recent comparisons, so a turn's baseline isn't worked out again each time
MAX_COMPARED = 64


def find_compare(body):
    """One lens over one move (planner screen, step 2), for the situation in
    "areas" and "wez" and, when "base" ({"areas", "wez"}) is given, for the
    baseline situation too. See comparisons.py."""
    origin, goal = parse_move(body)
    lens = body.get("lens", "mode")
    if lens not in comparisons.LENSES:
        raise BadRequest("lens must be one of: " + ", ".join(comparisons.LENSES) + ".")
    cargo = parse_cargo(body) or PLANNER.default_cargo()
    countries = parse_countries(body)
    base = body.get("base")
    if base is not None and not isinstance(base, dict):
        raise BadRequest("base must be {\"areas\": [...], \"wez\": [...]}.")

    def run(raw_areas, raw_wez):
        key = json.dumps([lens, origin, goal, cargo, countries, raw_areas or [], raw_wez or []], sort_keys=True)
        s = {"origin": origin, "goal": goal, "cargo": cargo, "countries": countries,
             "denial": parse_areas(raw_areas), "wez": parse_areas(raw_wez)}
        with LOCK:
            if key not in COMPARED:
                if len(COMPARED) >= MAX_COMPARED:
                    COMPARED.pop(next(iter(COMPARED)))
                COMPARED[key] = comparisons.compare(PLANNER, lens, s)
            return COMPARED[key]

    started = time.perf_counter()
    out = dict(run(body.get("areas"), body.get("wez")))
    out["base"] = run(base.get("areas"), base.get("wez")) if base is not None else None
    out["ms"] = round((time.perf_counter() - started) * 1000)
    return out


def cargo_scenarios():
    """The files in cargo_scenarios/, filled in from their base scenarios,
    with port names turned into ids, for the map's scenario list."""
    def port_id(ref):
        if ref in GRAPH.ports:
            return ref
        hits = [pid for pid, p in GRAPH.ports.items() if p["name"].lower() == str(ref).lower()]
        return hits[0] if len(hits) == 1 else None
    bases = {}
    for path in glob.glob(os.path.join(HERE, "scenarios", "*.json")):
        with open(path, encoding="utf-8") as f:
            b = json.load(f)
        bases[b["id"]] = b
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "cargo_scenarios", "*.json"))):
        with open(path, encoding="utf-8") as f:
            s = json.load(f)
        base = bases.get(s.get("base"), {})
        frm, to = port_id(s.get("from", base.get("from"))), port_id(s.get("to", base.get("to")))
        if frm and to:
            out.append({"id": s["id"], "name": s["name"], "purpose": s.get("purpose", ""), "from": frm, "to": to,
                        "areas": s.get("areas", base.get("areas", [])), "wez": s.get("wez", []),
                        "cargo": s.get("cargo") or PLANNER.default_cargo(), "priority": s.get("priority", "economic")})
    return out


CARGO_META = {
    "classes": [{"id": cid, "name": c["name"], "moves_as": c["moves_as"],
                 "short_tons": PLANNER.rules["manifest"].get(cid)} for cid, c in PLANNER.classes.items()],
    "carriers": {k: v["label"] for k, v in PLANNER.rules["carriers"].items() if not k.startswith("_")},
    "handover_countries": PLANNER.rules["handover"]["countries"],
    "window_nm": PLANNER.rules["handover"]["window_nm"],
    "flies": {cid: v for cid, v in PLANNER.air_rules["classes"].items() if not cid.startswith("_")},
    "aircraft": {k: v["name"] for k, v in PLANNER.air_rules["aircraft"].items() if not k.startswith("_")},
    "payload_st": {k: v["payload_st"] for k, v in PLANNER.air_rules["aircraft"].items() if not k.startswith("_")},
    "knots": {k: v["knots"] for k, v in PLANNER.rules["carriers"].items() if not k.startswith("_")},
    "handling": {k: v for k, v in PLANNER.air_rules["handling"].items() if not k.startswith("_")},
    "lenses": [{"id": k, "label": v["label"], "note": v["note"]} for k, v in comparisons.LENSES.items()],
    "scenarios": cargo_scenarios(),
}


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

    def send_json(self, code, obj):
        self.send(code, json.dumps(obj), "application/json")

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/ports":
            return self.send_json(200, PORT_LIST)
        if path == "/api/airfields":
            return self.send_json(200, AIRFIELD_LIST)
        if path == "/api/cargo/meta":
            return self.send_json(200, CARGO_META)
        if path in STATIC:
            name, ctype = STATIC[path]
            with open(os.path.join(HERE, name), "rb") as f:
                return self.send(200, f.read(), ctype)
        self.send(404, "not found", "text/plain")

    def do_POST(self):
        handler = {"/api/routes": find_routes, "/api/cargo": find_cargo, "/api/options": find_options,
                   "/api/compare": find_compare}.get(urllib.parse.urlparse(self.path).path)
        if handler is None:
            return self.send(404, "not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY_BYTES:
                raise BadRequest("Request body missing or too large.")
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise BadRequest("Request body must be a JSON object.")
            self.send_json(200, handler(body))
        except (BadRequest, ValueError) as e:
            self.send_json(400, {"error": str(e)})
        except Exception as e:
            self.send_json(500, {"error": "%s: %s" % (type(e).__name__, e)})


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
