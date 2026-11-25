"""
FALCON's data layer: the one place that knows where data comes from and what
shape it must have. Python 3, stdlib only.

    from falcon_data import STORE
    STORE.rules("lift")                  # a planning-factor file, as a dict (a fresh copy)
    STORE.ref_path("nodes", "ports.csv") # a reference-data file
    STORE.figure("lift:ships.tanker.capacity_st")   # a figure with its value, unit and source
    python falcon_data.py                # check every file; exits 1 on a problem

There are two kinds of data, kept apart on purpose:

  reference data    the world: ports, airfields, runways, sea lanes, passages.
                    The Neo4j data model (neo4j-datamodel/), built from NGA,
                    OurAirports and Eurostat. Large, graph-shaped, rebuilt by a
                    script, never edited by hand. The app reads its CSV export:
                    routing with closures runs in memory (see
                    neo4j-datamodel/README.md, "Sea routing"), so a database
                    isn't needed to run the demo.

  planning factors  figures a planner may change: ship and aircraft types,
                    payloads, port rates, allowances, which class flies.
                    cargo/*.json and cargo/port_rates.csv. Small, edited by
                    hand, reviewed line by line in git.

No other module opens these files itself: each asks the STORE. To read the
same data from a Neo4j database instead, write a store with the same methods
(rules, ref_path or the rows it serves, port_rates) and nothing else changes.

Every planning figure carries a source, under "_sources" in the block that
holds it (an underscore key is metadata, so code that walks a block skips it):

    {"status": "sourced" | "unverified" | "illustrative",
     "source": text,             what it is and where it came from
     "url": text,                optional; required when sourced
     "checked": "YYYY-MM-DD"}    optional; required when sourced

    sourced       read from a page that was opened
    unverified    a source is named, but its page wasn't opened
    illustrative  chosen for the demo; not a planning factor

A "_sources" key names a field of the block ("count"), a field of each entry
("*.draft_m"), or every entry ("*"). FIGURES lists the figures the screen can
show; check() makes sure each one exists, has a source, and agrees with the
other files.
"""
import copy
import csv
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATUSES = ("sourced", "unverified", "illustrative")
RULE_FILES = {"movement": "movement_rules.json", "air": "air_rules.json", "lift": "lift_rules.json",
              "cargo_classes": "cargo_classes.json"}

# The figures the screen can show: (file:path, label, unit). "*" stands for every entry of a block;
# {key} in a label is that entry's key, {name} its "name".
FIGURES = [
    ("lift:ships.*.capacity_st", "Capacity of one load, {name}", "short tons"),
    ("lift:ships.*.count", "On hand, {name}", "ships"),
    ("lift:aircraft.*.planning_payload_st", "Planning payload, {name}", "short tons"),
    ("lift:aircraft.*.ground_hours", "Ground time per stop, {name}", "hours"),
    ("lift:aircraft.*.count", "On hand, {name}", "aircraft"),
    ("lift:plan.normal_mode.*", "Normal mode, Class {key}", ""),
    ("lift:plan.fuel_lb_per_gal", "Fuel weight", "lb a gallon"),
    ("lift:ports.rate_st_per_day.*", "Port rate, {key} harbor", "short tons a day"),
    ("lift:airfields.working_mog.*", "Working MOG, {key}", "aircraft at once"),
    ("air:aircraft.*.payload_st", "Maximum payload, {name}", "short tons"),
    ("air:aircraft.*.range_nm", "Range with that payload, {name}", "nm"),
    ("air:aircraft.*.cruise_kt", "Cruise speed, {name}", "knots"),
    ("air:aircraft.*.min_runway_ft", "Shortest runway, {name}", "ft"),
    ("air:classes.*", "Who may fly Class {key}", ""),
    ("air:handling.ship_port_days", "Time in each port, ships", "days"),
    ("air:handling.airfield_hours", "Time at each airfield, cargo", "hours"),
    ("air:handling.refuel_stop_hours", "Refuelling stop", "hours"),
    ("air:handling.road_kt", "Road speed, port to airfield", "knots"),
    ("air:airfields.max_road_nm", "Furthest airfield from a port", "nm"),
    ("movement:carriers.*.knots", "Sailing speed, {label}", "knots"),
    ("movement:handover.window_nm", "Handover backup window", "nm"),
    ("movement:handover.countries", "Handover countries", ""),
    ("movement:manifest.*", "Default quantity, Class {key}", "short tons"),
    ("cargo_classes:vessels.*.draft_m", "Draft, {name}", "m"),
    ("cargo_classes:depth.marginal_m", "Depth margin before a port is ruled out", "m"),
]


class DataError(Exception):
    """A data file is missing a figure or a source, or disagrees with another file."""


def _public(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


class Store:
    """Planning factors and reference data from files under root."""

    def __init__(self, root=HERE):
        self.root = root
        self.rules_dir = os.path.join(root, "cargo")
        self.ref_dir = os.path.join(root, "neo4j-datamodel")
        self._rules = {}
        self._csv = {}
        self._figs = None

    # ------------------------------------------------------------ reading
    def rules_path(self, name):
        return os.path.join(self.rules_dir, RULE_FILES[name])

    def rules(self, name):
        """A planning-factor file as a dict. Each call gets its own copy, so one
        module changing it (a what-if, a test) can't change another's."""
        if name not in self._rules:
            with open(self.rules_path(name), encoding="utf-8") as f:
                self._rules[name] = json.load(f)
        return copy.deepcopy(self._rules[name])

    def ref_path(self, *parts):
        return os.path.join(self.ref_dir, *parts)

    def input_files(self):
        """Every file the planner reads, for a report's record of its inputs."""
        return ([self.ref_path("nodes", n) for n in ("ports.csv", "airfields.csv", "passages.csv")]
                + [self.rules_path(n) for n in RULE_FILES]
                + [os.path.join(self.rules_dir, n) for n in ("port_overrides.csv", "port_rates.csv")])

    def _rows(self, path):
        if path not in self._csv:
            with open(path, newline="", encoding="utf-8") as f:
                self._csv[path] = list(csv.DictReader(f))
        return self._csv[path]

    def passages(self):
        """{passage_id: name}."""
        return {r["passage_id"]: r["name"] for r in self._rows(self.ref_path("nodes", "passages.csv"))}

    def port_rates(self):
        """Sourced rates for single ports, which win over the rate for the harbor size:
        {port_id: {"rate_st_per_day", "name", "source": {...}}}."""
        out = {}
        for r in self._rows(os.path.join(self.rules_dir, "port_rates.csv")):
            src = {"status": r["status"], "source": r["source"]}
            src.update({k: r[k] for k in ("url", "checked") if r.get(k)})
            out[r["port_id"]] = {"rate_st_per_day": float(r["rate_st_per_day"]), "name": r["name"], "source": src}
        return out

    # ------------------------------------------------------------ figures
    def _resolve(self, fid):
        """(value, source or None) for one concrete figure id."""
        name, _, path = fid.partition(":")
        if name == "port_rates":
            r = self.port_rates().get(path)
            return (r["rate_st_per_day"], r["source"]) if r else (None, None)
        node, chain = self.rules(name), []
        for k in path.split("."):
            if not isinstance(node, dict) or k not in node:
                return None, None
            chain.append(node)
            node = node[k]
        keys = path.split(".")
        src = None
        for i in range(len(chain) - 1, -1, -1):          # the nearest block with a matching "_sources" key
            rel, srcs = keys[i:], chain[i].get("_sources", {})
            for cand in (".".join(rel), ".".join(["*"] + rel[1:]), rel[0], "*"):
                if cand in srcs:
                    src = srcs[cand]
                    break
            if src:
                break
        return node, src

    def _expand(self, pattern):
        """The concrete ids a FIGURES pattern stands for: [(id, key the * took)]."""
        name, _, path = pattern.partition(":")
        out, keys = [], path.split(".")

        def walk(node, i, done, star):
            if i == len(keys):
                out.append(("%s:%s" % (name, ".".join(done)), star))
                return
            k = keys[i]
            if not isinstance(node, dict):
                return
            for c in (sorted(_public(node)) if k == "*" else [k] if k in node else []):
                walk(node[c], i + 1, done + [c], c if k == "*" else star)
        walk(self.rules(name), 0, [], None)
        return out

    def figures(self):
        """Every figure the screen can show: {id: {"id", "label", "unit", "value", "source"}}."""
        if self._figs is None:
            self._figs = self._all_figures()
        return copy.deepcopy(self._figs)

    def _all_figures(self):
        out = {}
        for pattern, label, unit in FIGURES:
            for fid, key in self._expand(pattern):
                value, src = self._resolve(fid)
                parent = self.rules(fid.split(":")[0])
                entry = parent
                for k in fid.split(":")[1].split(".")[:-1]:
                    entry = entry[k]
                out[fid] = {"id": fid, "label": label.format(key="unsized" if key == "" else (key or "").replace("_", " "),
                                                             name=entry.get("name", key) if isinstance(entry, dict) else key,
                                                             label=entry.get("label", key) if isinstance(entry, dict) else key),
                            "unit": unit, "value": value, "source": src}
        for pid, r in self.port_rates().items():
            out["port_rates:" + pid] = {"id": "port_rates:" + pid, "label": "Port rate, %s" % r["name"],
                                        "unit": "short tons a day", "value": r["rate_st_per_day"], "source": r["source"]}
        return out

    def figure(self, fid):
        f = self.figures().get(fid)
        if f is None:
            raise KeyError("No figure %r; FIGURES in falcon_data.py lists them." % fid)
        return f

    # ------------------------------------------------------------ checks
    def problems(self):
        """Everything wrong with the data files, as plain sentences. Empty means all is well."""
        out = []
        out += self._source_problems()
        for fid, f in self.figures().items():
            if f["source"] is None:
                out.append("%s has no source." % fid)
            if isinstance(f["value"], (int, float)) and not isinstance(f["value"], bool) and f["value"] <= 0:
                out.append("%s is %s; it must be more than 0." % (fid, f["value"]))
        out += self._consistency_problems()
        return out

    def check(self):
        p = self.problems()
        if p:
            raise DataError("The data files have %d problem%s:\n  %s" % (len(p), "" if len(p) == 1 else "s",
                                                                         "\n  ".join(p)))

    @staticmethod
    def _bad_source(src):
        if not isinstance(src, dict):
            return "is not {status, source, url, checked}"
        extra = set(src) - {"status", "source", "url", "checked"}
        if extra:
            return "has unknown keys %s" % sorted(extra)
        if src.get("status") not in STATUSES:
            return "has status %r; use one of %s" % (src.get("status"), ", ".join(STATUSES))
        if not str(src.get("source") or "").strip():
            return "says nothing about where it came from"
        if src.get("url") and not re.match(r"https?://", src["url"]):
            return "has a url that isn't a web address"
        if src.get("checked") and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", src["checked"]):
            return "has checked %r; use YYYY-MM-DD" % src["checked"]
        if src["status"] == "sourced" and not (src.get("url") and src.get("checked")):
            return "is sourced but has no url or checked date"
        return None

    def _source_problems(self):
        out = []

        def walk(node, where):
            if not isinstance(node, dict):
                return
            for key, src in node.get("_sources", {}).items():
                bad = self._bad_source(src)
                if bad:
                    out.append("%s, source for %s, %s." % (where, key, bad))
                head = key.split(".")[0]
                if head != "*" and head not in node:
                    out.append("%s has a source for %s, which isn't there." % (where, key))
            for k, v in _public(node).items():
                walk(v, "%s.%s" % (where, k))
        for name in RULE_FILES:
            walk(self.rules(name), RULE_FILES[name])
        for pid, r in self.port_rates().items():
            bad = self._bad_source(r["source"])
            if bad:
                out.append("port_rates.csv, %s, %s." % (pid, bad))
        return out

    def _consistency_problems(self):
        out = []
        mv, air, lift, cc = (self.rules(n) for n in ("movement", "air", "lift", "cargo_classes"))
        classes = set(_public(cc["classes"]))
        flies = _public(air["classes"])
        normal = _public(lift["plan"]["normal_mode"])
        carriers = set(_public(mv["carriers"]))

        def same(what, keys):
            if set(keys) != classes:
                out.append("%s covers classes %s; cargo_classes.json has %s." % (what, sorted(keys), sorted(classes)))
        same("air_rules.json classes", flies)
        same("lift_rules.json plan.normal_mode", normal)
        for c, rule in flies.items():
            if rule not in ("any", "military", "none"):
                out.append("air_rules.json: Class %s flies %r; use any, military or none." % (c, rule))
        for c, m in normal.items():
            if m not in ("sea", "air"):
                out.append("lift_rules.json: Class %s's normal mode is %r; use sea or air." % (c, m))
            elif m == "air" and flies.get(c) == "none":
                out.append("lift_rules.json: Class %s normally goes by air, but air_rules.json says it never flies." % c)
        ships = _public(lift["ships"])
        for kind in ("ships", "aircraft"):
            for t, x in _public(lift[kind]).items():
                if not (x.get("name") and x.get("short")):
                    out.append("lift_rules.json: %s %s needs a name and a short name." % (kind, t))
        owner = {}
        for t, s in ships.items():
            if s["carrier"] not in carriers:
                out.append("lift_rules.json: ship %s has carrier %r; movement_rules.json has %s." % (t, s["carrier"], sorted(carriers)))
            for c in s["classes"]:
                if c in owner:
                    out.append("lift_rules.json: Class %s is carried by both %s and %s ships." % (c, owner[c], t))
                owner[c] = t
        same("lift_rules.json ships' classes", owner)
        for what, keys in (("lift_rules.json aircraft", _public(lift["aircraft"])),
                           ("air_rules.json aircraft", _public(air["aircraft"]))):
            if set(keys) != carriers:
                out.append("%s are %s; the carriers in movement_rules.json are %s." % (what, sorted(keys), sorted(carriers)))
        extra = set(_public(mv["manifest"])) - classes
        if extra:
            out.append("movement_rules.json manifest has classes %s that cargo_classes.json doesn't." % sorted(extra))
        ports = {r["port_id"]: r for r in self._rows(self.ref_path("nodes", "ports.csv"))}
        sizes = {r["harbor_size"] or "" for r in ports.values()}
        missing = sizes - set(lift["ports"]["rate_st_per_day"])
        if missing:
            out.append("lift_rules.json has no port rate for harbor sizes %s." % sorted(missing))
        for pid in self.port_rates():
            if pid not in ports:
                out.append("port_rates.csv: %s isn't a port in ports.csv." % pid)
        return out


STORE = Store()


def main():
    p = STORE.problems()
    figs = STORE.figures()
    n = {s: sum(1 for f in figs.values() if f["source"] and f["source"]["status"] == s) for s in STATUSES}
    print("%d figures: %s." % (len(figs), ", ".join("%d %s" % (n[s], s) for s in STATUSES)))
    if p:
        print("%d problem%s:" % (len(p), "" if len(p) == 1 else "s"))
        for x in p:
            print("  " + x)
        return 1
    print("No problems.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
