"""
A tiny data model, small enough to work out every answer by hand, written in
the same files and columns as neo4j-datamodel/. Tests load it with the real
SeaGraph.load, so the validation and loading code runs too.

    Sea lanes                              Ports (start and end only)
    N1 (0, 0)  -- N2 (1, 0.5) -- N3 (2, 0)    PA (0, 0.2) on N1
    N1 (0, 0)  -- N4 (1, -1)  -- N3 (2, 0)    PB (2, 0.2) on N3
    N6 (10, 0) -- N7 (11, 0), a separate sea  PC (2.2, 0.3) on N3, plus a coastal hop to PB
                                              PM (1, 0) on N1 and on N3
    North Strait: N1-N2 and N2-N3             PZ (10, 0.1) on N6
    South Strait: N4-N3

The north lanes are shorter than the south. PM's two links make a shortcut
(120 nm against 134 nm by the north lanes) that only PM's own routes may use.
"""
import csv
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from sea_routing import great_circle_nm  # noqa: E402

SEA_NODES = {"N1": (0, 0), "N2": (1, 0.5), "N3": (2, 0), "N4": (1, -1), "N6": (10, 0), "N7": (11, 0)}
PORTS = {"PA": ("Port A", (0, 0.2)), "PB": ("Port B", (2, 0.2)), "PC": ("Port C", (2.2, 0.3)),
         "PM": ("Port M", (1, 0)), "PZ": ("Port Z", (10, 0.1))}
PASSAGES = {"north": "North Strait", "south": "South Strait"}
# (id, from, to, passage, points between the two ends)
SEGMENTS = [("SEG-1", "N1", "N2", "north", []), ("SEG-2", "N2", "N3", "north", [(1.5, 0.3)]),
            ("SEG-3", "N1", "N4", "", []), ("SEG-4", "N4", "N3", "south", []), ("SEG-5", "N6", "N7", "", [])]
ACCESS = [("ACC-1", "PA", "N1"), ("ACC-2", "PB", "N3"), ("ACC-3", "PC", "N3"), ("ACC-4", "PM", "N1"),
          ("ACC-5", "PM", "N3"), ("ACC-6", "PZ", "N6")]
PORT_TO_PORT = [("P2P-1", "PB", "PC")]


def where(node):
    return SEA_NODES[node] if node in SEA_NODES else PORTS[node][1]


def link_row(seg_id, a, b, passage="", via=()):
    pts = [where(a)] + list(via) + [where(b)]
    length = sum(great_circle_nm(p, q) for p, q in zip(pts, pts[1:]))
    return {"segment_id": seg_id, "length_nm": "%.4f" % length, "passage": passage, "tag_method": "",
            "geometry_wkt": "LINESTRING(" + ", ".join("%s %s" % p for p in pts) + ")"}


def write(path, columns, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def make(folder):
    """Write the toy data model into folder; returns folder."""
    counts = {}
    counts["nodes/sea_nodes.csv"] = write(os.path.join(folder, "nodes", "sea_nodes.csv"), ["sea_node_id", "lon", "lat"],
                                          [{"sea_node_id": n, "lon": x, "lat": y} for n, (x, y) in SEA_NODES.items()])
    counts["nodes/ports.csv"] = write(
        os.path.join(folder, "nodes", "ports.csv"), ["port_id", "name", "country", "lat", "lon", "harbor_size"],
        [{"port_id": p, "name": name, "country": "Toyland", "lat": y, "lon": x, "harbor_size": "Small"}
         for p, (name, (x, y)) in PORTS.items()])
    counts["nodes/passages.csv"] = write(os.path.join(folder, "nodes", "passages.csv"), ["passage_id", "name"],
                                         [{"passage_id": k, "name": v} for k, v in PASSAGES.items()])
    counts["nodes/airfields.csv"] = write(
        os.path.join(folder, "nodes", "airfields.csv"), ["airfield_id", "name", "lat", "lon", "longest_paved_runway_ft"],
        [{"airfield_id": "AF-1", "name": "Toy Field", "lat": 0.3, "lon": 0.1, "longest_paved_runway_ft": 9000}])
    counts["nodes/runways.csv"] = write(os.path.join(folder, "nodes", "runways.csv"), ["runway_id", "airfield_id"],
                                        [{"runway_id": "RW-1", "airfield_id": "AF-1"}])
    link_cols = ["segment_id", "length_nm", "passage", "tag_method", "geometry_wkt"]
    counts["relationships/sea_segments.csv"] = write(
        os.path.join(folder, "relationships", "sea_segments.csv"), ["from_sea_node_id", "to_sea_node_id"] + link_cols,
        [dict(link_row(s, a, b, p, via), from_sea_node_id=a, to_sea_node_id=b) for s, a, b, p, via in SEGMENTS])
    counts["relationships/port_to_sea_node.csv"] = write(
        os.path.join(folder, "relationships", "port_to_sea_node.csv"), ["port_id", "sea_node_id"] + link_cols,
        [dict(link_row(s, p, n), port_id=p, sea_node_id=n) for s, p, n in ACCESS])
    counts["relationships/port_to_port.csv"] = write(
        os.path.join(folder, "relationships", "port_to_port.csv"), ["from_port_id", "to_port_id"] + link_cols,
        [dict(link_row(s, a, b), from_port_id=a, to_port_id=b) for s, a, b in PORT_TO_PORT])
    with open(os.path.join(folder, "build-report.json"), "w", encoding="utf-8") as f:
        json.dump({"rows_written": counts}, f)
    return folder


class ToyFolder:
    """with ToyFolder() as folder: a fresh toy data model, removed afterwards."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="falcon-toy-")
        return make(os.path.join(self.tmp, "neo4j-datamodel"))

    def __exit__(self, *exc):
        shutil.rmtree(self.tmp, ignore_errors=True)


def nm(*nodes):
    """Length of the path through these nodes in the toy, along each link's line."""
    lines = {frozenset((a, b)): [where(a)] + list(via) + [where(b)] for _, a, b, _, via in SEGMENTS}
    total = 0.0
    for a, b in zip(nodes, nodes[1:]):
        pts = lines.get(frozenset((a, b)), [where(a), where(b)])
        total += sum(great_circle_nm(p, q) for p, q in zip(pts, pts[1:]))
    return total
