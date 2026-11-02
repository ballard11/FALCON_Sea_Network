"""
The routing core, sea_routing.py: geometry, areas of denial, the three
searches, and how a route is described. Most tests use the toy network in
toy_network.py, where every answer can be worked out by hand; the rest check
rules that must hold everywhere on the real network.

    python -m unittest tests.test_sea_routing
"""
import math
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
import sea_routing as sr  # noqa: E402
import toy_network as toy  # noqa: E402
from validate_data import DataError  # noqa: E402

NM_PER_DEGREE = sr.NM_RADIUS * math.pi / 180      # 60.04 nm


def destination(start, bearing_deg, dist_nm):
    """The point dist_nm from start along the given bearing, on the globe."""
    lon0, lat0 = map(math.radians, start)
    d, b = dist_nm / sr.NM_RADIUS, math.radians(bearing_deg)
    lat = math.asin(math.sin(lat0) * math.cos(d) + math.cos(lat0) * math.sin(d) * math.cos(b))
    lon = lon0 + math.atan2(math.sin(b) * math.sin(d) * math.cos(lat0), math.cos(d) - math.sin(lat0) * math.sin(lat))
    return ((math.degrees(lon) + 180) % 360 - 180, math.degrees(lat))


def circle(center, r):
    return sr.DenialArea({"type": "circle", "center": center, "radius_nm": r})


def polygon(*points):
    return sr.DenialArea({"type": "polygon", "points": [list(p) for p in points]})


# ------------------------------------------------------------------ geometry
class GreatCircle(unittest.TestCase):
    def test_same_point_is_zero(self):
        self.assertEqual(sr.great_circle_nm((12.5, -40), (12.5, -40)), 0)

    def test_one_degree_of_latitude(self):
        self.assertAlmostEqual(sr.great_circle_nm((0, 0), (0, 1)), NM_PER_DEGREE, places=6)

    def test_symmetric(self):
        a, b = (129.03, 35.1), (120.28, 14.8)
        self.assertEqual(sr.great_circle_nm(a, b), sr.great_circle_nm(b, a))

    def test_the_short_way_across_the_date_line(self):
        self.assertAlmostEqual(sr.great_circle_nm((179, 0), (-179, 0)), 2 * NM_PER_DEGREE, places=6)

    def test_antipodes_are_half_way_round(self):
        self.assertAlmostEqual(sr.great_circle_nm((0, 0), (180, 0)), math.pi * sr.NM_RADIUS, places=3)
        self.assertAlmostEqual(sr.great_circle_nm((30, 45), (-150, -45)), math.pi * sr.NM_RADIUS, places=3)


class Densify(unittest.TestCase):
    def test_points_are_never_further_apart_than_the_step(self):
        line = [(120, 10), (125, 14), (125.01, 14), (140, 30)]
        pts = sr.densify(line, 10.0)
        self.assertLessEqual(max(sr.great_circle_nm(p, q) for p, q in zip(pts, pts[1:])), 10.0 + 1e-9)

    def test_keeps_every_original_point_in_order(self):
        line = [(120, 10), (125, 14), (140, 30)]
        pts = sr.densify(line, 10.0)
        self.assertEqual([pts.index(p) for p in line], sorted(pts.index(p) for p in line))
        self.assertEqual((pts[0], pts[-1]), (line[0], line[-1]))

    def test_a_short_line_is_unchanged(self):
        self.assertEqual(sr.densify([(0, 0), (0, 0.1)], 10.0), [(0, 0), (0, 0.1)])

    def test_new_points_lie_on_the_great_circle(self):
        a, b = (-10, 50), (40, 60)
        total = sr.great_circle_nm(a, b)
        for p in sr.densify([a, b], 50.0)[1:-1]:
            self.assertAlmostEqual(sr.great_circle_nm(a, p) + sr.great_circle_nm(p, b), total, places=6)

    def test_across_the_date_line_stays_short(self):
        pts = sr.densify([(179.5, 0), (-179.5, 0)], 10.0)
        self.assertTrue(all(abs(abs(p[0]) - 180) <= 0.5 + 1e-9 for p in pts), pts)

    def test_interpolating_a_point_with_itself(self):
        self.assertEqual(sr._interpolate((5, 5), (5, 5), 0.5), (5, 5))


class Unwrap(unittest.TestCase):
    def test_moves_longitude_next_to_the_reference(self):
        self.assertEqual(sr._unwrap(-179, 179), 181)
        self.assertEqual(sr._unwrap(179, -179), -181)
        self.assertEqual(sr._unwrap(10, 0), 10)
        self.assertEqual(sr._unwrap(370, 0), 10)


class PointSetNear(unittest.TestCase):
    def test_near_and_far(self):
        ps = sr.PointSet([(0, 0), (10, 10)])
        self.assertTrue(ps.near((0, 0.5), 31))
        self.assertFalse(ps.near((0, 0.5), 29))

    def test_finds_a_point_across_the_date_line(self):
        self.assertTrue(sr.PointSet([(179.9, 0)]).near((-179.9, 0), 20))

    def test_near_the_pole_looks_all_the_way_round(self):
        self.assertTrue(sr.PointSet([(100, 89.5)]).near((-80, 89.5), 70))

    def test_an_empty_set_has_nothing_near(self):
        self.assertFalse(sr.PointSet([]).near((0, 0), 5000))


class ParseWkt(unittest.TestCase):
    def test_linestring(self):
        self.assertEqual(sr.parse_wkt("LINESTRING(1 2, -3.5 4.25)"), [(1.0, 2.0), (-3.5, 4.25)])


# ------------------------------------------------------------ denial areas
class Circles(unittest.TestCase):
    def test_contains_its_centre_and_its_edge(self):
        c = circle([124.2, 22.4], 240)
        self.assertTrue(c.contains((124.2, 22.4)))
        self.assertTrue(c.contains(destination((124.2, 22.4), 77, 239.9)))
        self.assertFalse(c.contains(destination((124.2, 22.4), 77, 240.1)))

    def test_across_the_date_line(self):
        c = circle([180, 30], 60)
        self.assertTrue(c.contains((-179.5, 30)))
        self.assertTrue(c.contains((179.5, 30)))
        self.assertTrue(c.crosses((178, 30.2), (-178, 30.2)))

    def test_a_circle_over_the_pole(self):
        c = circle([0, 85], 600)
        self.assertTrue(c.contains((180, 89)))
        self.assertFalse(c.contains((180, 70)))

    def test_a_piece_through_a_small_circle_with_both_ends_outside(self):
        c = circle([150, 20], 3)
        p, q = destination((150, 20), 270, 5), destination((150, 20), 90, 5)
        self.assertFalse(c.contains(p) or c.contains(q))
        self.assertTrue(c.crosses(p, q))

    def test_a_piece_passing_just_outside(self):
        c = circle([150, 20], 3)
        side = destination((150, 20), 0, 3.5)
        self.assertFalse(c.crosses(destination(side, 270, 5), destination(side, 90, 5)))

    def test_a_piece_that_is_a_single_point(self):
        self.assertFalse(circle([0, 0], 10).crosses((1, 1), (1, 1)))

    def test_large_circles_are_true_circles_at_high_latitude(self):
        # Before v1.1 a 3,000 nm circle at 50N closed lanes up to 426 nm outside it.
        c = circle([0, 50], 3000)
        p, q = (36.5, 10.0), (36.6, 10.1)
        self.assertGreater(min(sr.great_circle_nm(p, c.c), sr.great_circle_nm(q, c.c)), 3000)
        self.assertFalse(c.crosses(p, q))
        far = destination((0, 50), 120, 3300)
        self.assertFalse(c.crosses(far, destination(far, 30, 10)))

    def test_crosses_agrees_with_dense_sampling(self):
        """Random circles and short pieces near their edges, against a 0.05 nm sampling."""
        rng = random.Random(1450)
        checked = 0
        while checked < 400:
            center = (rng.uniform(-180, 180), rng.uniform(-80, 80))
            r = rng.choice([3, 25, 240, 1000, 3000]) * rng.uniform(0.5, 1)
            p = destination(center, rng.uniform(0, 360), r + rng.uniform(-8, 8))
            q = destination(p, rng.uniform(0, 360), rng.uniform(0.5, 10))
            nearest = min(sr.great_circle_nm(sr._interpolate(p, q, i / 200), center) for i in range(201))
            if abs(nearest - r) < 0.1:
                continue
            with self.subTest(center=center, r=r, p=p, q=q):
                self.assertEqual(circle(center, r).crosses(p, q), nearest <= r)
            checked += 1


class Polygons(unittest.TestCase):
    SQUARE = ((0, 0), (1, 0), (1, 1), (0, 1))

    def test_contains(self):
        sq = polygon(*self.SQUARE)
        self.assertTrue(sq.contains((0.5, 0.5)))
        self.assertFalse(sq.contains((1.5, 0.5)))
        self.assertFalse(sq.contains((0.5, -0.1)))

    def test_a_concave_polygon_leaves_its_notch_open(self):
        u = polygon((0, 0), (3, 0), (3, 3), (2, 3), (2, 1), (1, 1), (1, 3), (0, 3))
        self.assertTrue(u.contains((0.5, 2)))
        self.assertFalse(u.contains((1.5, 2)))

    def test_a_piece_through_it_with_both_ends_outside(self):
        self.assertTrue(polygon(*self.SQUARE).crosses((-1, 0.5), (2, 0.5)))

    def test_a_piece_beside_it(self):
        self.assertFalse(polygon(*self.SQUARE).crosses((-1, 2), (2, 2)))

    def test_a_piece_in_line_with_a_side_but_past_it(self):
        # Before v1.1 this counted as crossing, closing lanes that only lined up with a side.
        self.assertFalse(polygon(*self.SQUARE).crosses((2, 0), (3, 0)))
        self.assertFalse(sr._segments_cross((0, 0), (1, 0), (2, 0), (3, 0)))

    def test_a_piece_along_a_side_touches_it(self):
        self.assertTrue(polygon(*self.SQUARE).crosses((0.5, 0), (1.5, 0)))
        self.assertTrue(sr._segments_cross((0, 0), (2, 0), (1, 0), (3, 0)))

    def test_across_the_date_line(self):
        dl = polygon((179, -1), (-179, -1), (-179, 1), (179, 1))
        self.assertTrue(dl.contains((180, 0)))
        self.assertTrue(dl.contains((-179.5, 0.5)))
        self.assertFalse(dl.contains((178, 0)))
        self.assertTrue(dl.crosses((178, 0), (-178, 0)))

    def test_corners_given_the_long_way_round_still_take_the_short_way(self):
        dl = polygon((170, 0), (-170, 0), (-170, 10), (170, 10))
        self.assertTrue(dl.contains((180, 5)))
        self.assertFalse(dl.contains((0, 5)))

    def test_a_polygon_squashed_to_a_point_contains_nothing(self):
        self.assertFalse(polygon((5, 5), (5, 5), (5, 5)).contains((5, 5)))


# ------------------------------------------------------------- toy network
class Toy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = toy.ToyFolder()
        cls.g = sr.SeaGraph.load(cls.folder.__enter__())
        cls.eid = {}
        for eid, (a, b, *_rest) in cls.g.edges.items():
            cls.eid[frozenset((a, b))] = eid

    @classmethod
    def tearDownClass(cls):
        cls.folder.__exit__(None, None, None)

    def link(self, a, b):
        return self.eid[frozenset((a, b))]

    def assertRoute(self, route, *nodes):
        self.assertIsNotNone(route)
        self.assertEqual(route.nodes, list(nodes))
        self.assertAlmostEqual(route.length_nm, toy.nm(*nodes), places=3)


class Loading(Toy):
    def test_loads_every_node_and_link(self):
        self.assertEqual(set(self.g.ports), set(toy.PORTS))
        self.assertEqual(len(self.g.edges), len(toy.SEGMENTS) + len(toy.ACCESS) + len(toy.PORT_TO_PORT))
        self.assertEqual(self.g.coords["N2"], (1.0, 0.5))

    def test_refuses_data_that_fails_its_checks(self):
        with toy.ToyFolder() as folder:
            path = os.path.join(folder, "nodes", "sea_nodes.csv")
            with open(path, encoding="utf-8") as f:
                text = f.read()
            with open(path, "w", encoding="utf-8") as f:
                f.write(text.replace("N2,1,0.5", "N2,1,north"))
            with self.assertRaises(DataError) as ctx:
                sr.SeaGraph.load(folder)
            self.assertIn("nodes/sea_nodes.csv row 3", str(ctx.exception))

    def test_reads_files_saved_with_a_byte_order_mark(self):
        # Excel adds one; the checks accepted such files but loading didn't, before v1.1.
        with toy.ToyFolder() as folder:
            for name in ("nodes/ports.csv", "relationships/sea_segments.csv"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                with open(path, "w", encoding="utf-8-sig") as f:
                    f.write(text)
            g = sr.SeaGraph.load(folder)
            self.assertIn("PA", g.ports)
            self.assertEqual(g.shortest("PA", "PB").nodes, ["PA", "N1", "N2", "N3", "PB"])


class Shortest(Toy):
    def test_takes_the_shorter_lanes(self):
        self.assertRoute(self.g.shortest("PA", "PB"), "PA", "N1", "N2", "N3", "PB")

    def test_never_passes_through_another_port(self):
        # Through PM would be shorter (120 nm against 134 nm), but PM is not this route's port.
        self.assertGreater(toy.nm("N1", "N2", "N3"), toy.nm("N1", "PM", "N3"))
        self.assertNotIn("PM", self.g.shortest("PA", "PB").nodes)

    def test_a_port_uses_its_own_links(self):
        self.assertRoute(self.g.shortest("PM", "PB"), "PM", "N3", "PB")
        self.assertRoute(self.g.shortest("PA", "PM"), "PA", "N1", "PM")

    def test_a_coastal_hop_between_two_ports(self):
        self.assertRoute(self.g.shortest("PB", "PC"), "PB", "PC")

    def test_does_not_go_through_a_port_to_use_its_coastal_hop(self):
        self.assertRoute(self.g.shortest("PA", "PC"), "PA", "N1", "N2", "N3", "PC")

    def test_none_when_the_seas_do_not_connect(self):
        self.assertIsNone(self.g.shortest("PA", "PZ"))

    def test_banned_nodes_and_links(self):
        self.assertRoute(self.g.shortest("PA", "PB", banned_nodes=frozenset({"N2"})), "PA", "N1", "N4", "N3", "PB")
        self.assertRoute(self.g.shortest("PA", "PB", banned_edges=frozenset({self.link("N1", "N2")})),
                         "PA", "N1", "N4", "N3", "PB")

    def test_from_a_port_to_itself(self):
        self.assertRoute(self.g.shortest("PA", "PA"), "PA")


class KShortest(Toy):
    def test_ranks_every_loopless_route(self):
        routes = self.g.k_shortest("PA", "PB", k=5)
        self.assertEqual([r.nodes for r in routes], [["PA", "N1", "N2", "N3", "PB"], ["PA", "N1", "N4", "N3", "PB"]])
        self.assertLess(routes[0].length_nm, routes[1].length_nm)

    def test_k_of_one_is_the_shortest(self):
        self.assertEqual([r.nodes for r in self.g.k_shortest("PA", "PB", k=1)], [self.g.shortest("PA", "PB").nodes])

    def test_respects_closed_links(self):
        routes = self.g.k_shortest("PA", "PB", k=3, closed=frozenset({self.link("N4", "N3")}))
        self.assertEqual([r.nodes for r in routes], [["PA", "N1", "N2", "N3", "PB"]])

    def test_nothing_when_no_route_exists(self):
        self.assertEqual(self.g.k_shortest("PA", "PZ", k=3), [])
        self.assertEqual(self.g.k_shortest("PA", "PB", k=3, closed=frozenset(
            {self.link("N1", "N2"), self.link("N1", "N4")})), [])


class DistinctRoutes(Toy):
    def test_the_first_route_is_the_shortest(self):
        self.assertEqual(self.g.distinct_routes("PA", "PB", k=1)[0].nodes, ["PA", "N1", "N2", "N3", "PB"])

    def test_waters_near_the_two_ports_do_not_count_as_a_new_corridor(self):
        # Every junction in the toy is within 150 nm of PA or PB.
        self.assertEqual(len(self.g.distinct_routes("PA", "PB", k=3)), 1)

    def test_a_detour_is_not_a_corridor(self):
        north = self.g.shortest("PA", "PB")
        south = self.g.shortest("PA", "PB", banned_nodes=frozenset({"N2"}))
        ends = ("PA", "PB")
        self.assertTrue(self.g._straight_through(north, "N2", 20, ends))
        self.assertFalse(self.g._straight_through(south, "N4", 20, ends))
        self.assertEqual(len(self.g.distinct_routes("PA", "PB", k=3, separation_nm=20, endpoint_free_nm=0)), 1)

    def test_stops_at_the_stretch_limit(self):
        self.assertEqual(len(self.g.distinct_routes("PA", "PB", k=3, endpoint_free_nm=0, max_stretch=1.0)), 1)

    def test_respects_closed_links(self):
        routes = self.g.distinct_routes("PA", "PB", k=3, closed=frozenset({self.link("N1", "N2")}))
        self.assertEqual([r.nodes for r in routes], [["PA", "N1", "N4", "N3", "PB"]])

    def test_nothing_when_no_route_exists(self):
        self.assertEqual(self.g.distinct_routes("PA", "PZ", k=3), [])


class AreasOnTheToy(Toy):
    def closed(self, *areas):
        return {tuple(sorted(self.g.edges[e][:2])) for e in self.g.links_in_areas(list(areas))}

    def test_no_areas_close_nothing(self):
        self.assertEqual(self.g.links_in_areas([]), frozenset())

    def test_a_circle_closes_every_link_through_it(self):
        self.assertEqual(self.closed(circle([1, 0.5], 5)), {("N1", "N2"), ("N2", "N3")})

    def test_a_small_circle_between_two_sample_points_still_closes_the_link(self):
        n1, n4 = toy.SEA_NODES["N1"], toy.SEA_NODES["N4"]
        pieces = int(sr.great_circle_nm(n1, n4) // 10) + 1
        mid = sr._interpolate(n1, n4, 0.5 / pieces)
        self.assertTrue(all(sr.great_circle_nm(mid, p) > 2 for p in sr.densify([n1, n4])))
        self.assertEqual(self.closed(circle(mid, 2)), {("N1", "N4")})

    def test_a_polygon(self):
        self.assertEqual(self.closed(polygon((0.9, 0.4), (1.1, 0.4), (1.1, 0.6), (0.9, 0.6))), {("N1", "N2"), ("N2", "N3")})

    def test_empty_ocean_closes_nothing(self):
        self.assertEqual(self.closed(circle([-60, -40], 100)), set())

    def test_areas_add_up(self):
        both = self.closed(circle([1, 0.5], 5), circle([1, -1], 5))
        self.assertEqual(both, {("N1", "N2"), ("N2", "N3"), ("N1", "N4"), ("N3", "N4")})

    def test_a_port_inside_an_area(self):
        self.assertTrue(self.g.port_in_areas("PA", [circle([0, 0.2], 1)]))
        self.assertFalse(self.g.port_in_areas("PB", [circle([0, 0.2], 1)]))
        self.assertFalse(self.g.port_in_areas("PA", []))


class Describe(Toy):
    def test_one_line_from_origin_to_destination(self):
        d = self.g.describe(self.g.shortest("PB", "PA"))      # walks every link backwards
        self.assertEqual(d["line"], [(2.0, 0.2), (2.0, 0.0), (1.5, 0.3), (1.0, 0.5), (0.0, 0.0), (0.0, 0.2)])
        self.assertEqual(d["hops"], 4)
        self.assertEqual(d["length_nm"], round(toy.nm("PB", "N3", "N2", "N1", "PA"), 1))

    def test_each_passage_once_in_order(self):
        self.assertEqual(self.g.describe(self.g.shortest("PA", "PB"))["passages"], ["north"])
        south = self.g.shortest("PA", "PB", banned_nodes=frozenset({"N2"}))
        self.assertEqual(self.g.describe(south)["passages"], ["south"])


# ------------------------------------------------------------ real network
def real_graph():
    import server            # loads and checks the real data model once per test run
    return server.GRAPH


class RealNetwork(unittest.TestCase):
    """Rules that must hold everywhere, checked on sample routes."""

    @classmethod
    def setUpClass(cls):
        cls.g = real_graph()
        ports = sorted(cls.g.ports)
        rng = random.Random(23)
        cls.pairs = [tuple(rng.sample(ports, 2)) for _ in range(25)]

    def test_a_star_matches_a_full_dijkstra(self):
        for o, d in self.pairs:
            with self.subTest(origin=o, destination=d):
                route, tree = self.g.shortest(o, d), self.g._tree(o, (o, d))
                if d not in tree:
                    self.assertIsNone(route)
                else:
                    self.assertAlmostEqual(route.length_nm, tree[d][0], places=6)

    def test_routes_start_and_end_at_their_ports_and_match_their_length(self):
        for o, d in self.pairs[:10]:
            route = self.g.shortest(o, d)
            if route is None:
                continue
            with self.subTest(origin=o, destination=d):
                line = self.g.describe(route)["line"]
                self.assertEqual((tuple(line[0]), tuple(line[-1])), (self.g.coords[o], self.g.coords[d]))
                drawn = sum(sr.great_circle_nm(p, q) for p, q in zip(line, line[1:]))
                self.assertLessEqual(abs(drawn - route.length_nm), max(1.0, 0.02 * route.length_nm))
                self.assertGreaterEqual(route.length_nm, sr.great_circle_nm(self.g.coords[o], self.g.coords[d]) - 0.5)

    def test_yen_routes_are_ranked_different_and_loopless(self):
        for o, d in self.pairs[:4]:
            routes = self.g.k_shortest(o, d, k=4)
            with self.subTest(origin=o, destination=d):
                lengths = [r.length_nm for r in routes]
                self.assertEqual(lengths, sorted(lengths))
                self.assertEqual(len({tuple(r.edges) for r in routes}), len(routes))
                for r in routes:
                    self.assertEqual(len(set(r.nodes)), len(r.nodes))
                    self.assertNoPortWaypoint(r)

    def assertNoPortWaypoint(self, route):
        """A port inside a route is only ever reached and left by coastal hops between ports."""
        for i, n in enumerate(route.nodes[1:-1], 1):
            if n in self.g.ports:
                kinds = {self.g.edges[route.edges[i - 1]][4], self.g.edges[route.edges[i]][4]}
                self.assertEqual(kinds, {"port_to_port"}, "%s is a waypoint of %s" % (n, route.nodes))

    def test_distinct_corridors_busan_to_subic_bay(self):
        routes = self.g.distinct_routes("WPI-60390", "WPI-58395", k=4)
        self.assertEqual([round(r.length_nm) for r in routes], [1450, 1453, 1587, 1643])
        best = routes[0].length_nm
        self.assertTrue(all(r.length_nm <= 3 * best for r in routes))
        for r in routes:
            self.assertNoPortWaypoint(r)

    def test_the_area_index_finds_every_link_a_full_scan_finds(self):
        areas = [circle([124.2, 22.4], 240), circle([180, 30], 60), circle([-20, 65], 900),
                 polygon((175, -20), (-170, -20), (-170, -5), (175, -5))]
        self.g.links_in_areas([])
        for area in areas:
            with self.subTest(area=area.kind, box=area.box):
                scan = {eid for eid, pts in self.g._samples.items()
                        if any(area.crosses(p, q) for p, q in zip(pts, pts[1:]))}
                self.assertTrue(scan, "pick an area that closes something")
                self.assertEqual(self.g.links_in_areas([area]), scan)


if __name__ == "__main__":
    unittest.main()
