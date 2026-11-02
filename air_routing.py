"""
Air routes between airfields, for cargo. Python 3, stdlib only.

    from air_routing import AirNetwork
    air = AirNetwork.load()
    air.nearest(lonlat, min_runway_ft, max_nm, avoid=areas)    # the airfield for a port
    air.fly(a, b, aircraft, closed=areas, stop_ok=fn)            # [airfield ids] or None

A flight is a great circle between two airfields, no longer than the
aircraft's range with its payload, and it may not enter any area in `closed`.
When the direct flight is too long or would cross an area, the route lands
at other airfields on the way to refuel (or to go around the area), chosen to
arrive soonest: each stop costs `stop_hours` on the ground. Airfields come
from neo4j-datamodel/nodes/airfields.csv (OurAirports; paved runways of
8,000 ft or more).
"""
import csv
import heapq
import math
import os

from sea_routing import NM_RADIUS, densify, great_circle_nm

HERE = os.path.dirname(os.path.abspath(__file__))
AIRFIELDS = os.path.join(HERE, "neo4j-datamodel", "nodes", "airfields.csv")
SEARCH_STRETCH = 2.0        # stops are looked for within this many times the direct distance ...
SEARCH_EXTRA_NM = 1500.0    # ... or this much further, whichever is more


def _bearing(a, b):
    l1, p1, l2, p2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    return math.atan2(math.sin(l2 - l1) * math.cos(p2),
                      math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(l2 - l1))


def arc_distance_nm(a, b, c):
    """Shortest distance from point c to the great-circle arc a-b."""
    d12, d13 = great_circle_nm(a, b) / NM_RADIUS, great_circle_nm(a, c) / NM_RADIUS
    if d12 == 0:
        return d13 * NM_RADIUS
    turn = _bearing(a, c) - _bearing(a, b)
    if math.cos(turn) < 0:                       # c is behind a
        return d13 * NM_RADIUS
    xt = math.asin(max(-1.0, min(1.0, math.sin(d13) * math.sin(turn))))
    along = math.acos(max(-1.0, min(1.0, math.cos(d13) / max(math.cos(xt), 1e-12))))
    if along > d12:                              # c is beyond b
        return great_circle_nm(b, c)
    return abs(xt) * NM_RADIUS


def flight_enters(a, b, area):
    """Does the great circle a-b enter the area (a DenialArea)? Exact for a
    circle; for a polygon, checked every 10 nm once it comes near."""
    if area.kind == "circle":
        return arc_distance_nm(a, b, area.c) <= area.r
    if not hasattr(area, "_hub"):
        hub = (sum(p[0] for p in area.poly) / len(area.poly), sum(p[1] for p in area.poly) / len(area.poly))
        area._hub = (hub, max(great_circle_nm(hub, p) for p in area.poly) + 1.0)
    hub, reach = area._hub
    if arc_distance_nm(a, b, hub) > reach:
        return False
    pts = densify([a, b], 10.0)
    return any(area.crosses(p, q) for p, q in zip(pts, pts[1:]))


class AirNetwork:
    def __init__(self, rows):
        self.fields = {r["airfield_id"]: r for r in rows}
        self.coords = {k: (float(r["lon"]), float(r["lat"])) for k, r in self.fields.items()}
        self.runway = {k: float(r["longest_paved_runway_ft"] or 0) for k, r in self.fields.items()}

    @classmethod
    def load(cls, path=AIRFIELDS):
        with open(path, encoding="utf-8") as f:
            return cls(list(csv.DictReader(f)))

    def name(self, fid):
        r = self.fields[fid]
        code = r["icao_code"] or r["iata_code"] or fid
        return "%s (%s)" % (r["name"], code)

    def inside(self, fid, areas):
        return any(a.contains(self.coords[fid]) for a in areas)

    def nearest(self, point, min_runway_ft, max_nm, avoid=()):
        """The nearest airfield to point with a long enough runway, outside
        the areas in avoid, no more than max_nm away: (id, nm) or (None, None)."""
        best = None
        for fid, c in self.coords.items():
            if self.runway[fid] < min_runway_ft:
                continue
            d = great_circle_nm(point, c)
            if d <= max_nm and (best is None or d < best[1]) and not self.inside(fid, avoid):
                best = (fid, d)
        return best or (None, None)

    def fly(self, a, b, plane, closed=(), stop_ok=lambda fid: True, stop_hours=0.0):
        """Soonest route from airfield a to airfield b for the aircraft `plane`
        ({range_nm, cruise_kt, min_runway_ft}): the list of airfields landed
        at, a first and b last, or None. A* on hours in the air and on the ground."""
        rng, kt = plane["range_nm"], plane["cruise_kt"]
        pa, pb = self.coords[a], self.coords[b]
        direct = great_circle_nm(pa, pb)
        if direct <= rng and not any(flight_enters(pa, pb, x) for x in closed):
            return [a, b]
        budget = max(SEARCH_STRETCH * direct, direct + SEARCH_EXTRA_NM)
        stops = [f for f, c in self.coords.items()
                 if f not in (a, b) and self.runway[f] >= plane["min_runway_ft"] and stop_ok(f)
                 and great_circle_nm(pa, c) + great_circle_nm(c, pb) <= budget and not self.inside(f, closed)]
        nodes = stops + [b]
        best, prev = {a: 0.0}, {}
        heap = [(direct / kt, 0.0, a)]
        while heap:
            _, hours, n = heapq.heappop(heap)
            if n == b:
                path = [b]
                while path[-1] != a:
                    path.append(prev[path[-1]])
                return path[::-1]
            if hours > best[n]:
                continue
            pn = self.coords[n]
            for m in nodes:
                if m == n:
                    continue
                pm = self.coords[m]
                d = great_circle_nm(pn, pm)
                if d > rng:
                    continue
                h = hours + d / kt + (0.0 if m == b else stop_hours)
                if h >= best.get(m, math.inf):
                    continue
                if any(flight_enters(pn, pm, x) for x in closed):   # the dear test last
                    continue
                best[m], prev[m] = h, n
                heapq.heappush(heap, (h + great_circle_nm(pm, pb) / kt, h, m))
        return None

    def line(self, path, step_nm=20.0):
        """The route drawn as one line along its great circles."""
        out = []
        for a, b in zip(path, path[1:]):
            pts = densify([self.coords[a], self.coords[b]], step_nm)
            out.extend(pts if not out else pts[1:])
        return [list(p) for p in out]
