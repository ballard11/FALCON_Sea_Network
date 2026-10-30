"""
Cargo through a scenario: for each supply class, who carries it and where it
changes hands. Python 3, stdlib only.

    from cargo_movement import CargoPlanner
    planner = CargoPlanner(graph)               # rates every port once
    plan = planner.plan(origin, goal, cargo, denial=[...], wez=[...])

There are two kinds of area, both DenialArea shapes:

    an area of denial closes the water to every carrier;
    a weapons engagement zone (WEZ) closes it to LOGCAP commercial contractors
    only. The military may sail inside it.

Each class in the cargo is moved like this:

    1. If a commercial carrier can reach the destination without entering a
       WEZ, it carries the class the whole way.
    2. Otherwise it carries the class to the handover port: the port outside
       every area, in the scenario's handover countries, rated Confirmed or
       Inferred for that class, with the shortest military leg to the
       destination. The military carries it on.
    3. If the origin is the end inside a WEZ (retrograde, say), the same in
       reverse: the military carries it out to the handover port nearest the
       origin, and a commercial carrier takes it on.
    4. The military carries it the whole way when no handover port saves it
       at least window_nm of sailing, or when both ends are inside a WEZ.

The port ratings come from cargo_capability.py, so the overrides in
cargo/port_overrides.csv apply. Carriers, speeds, the handover rule and the
default cargo are in cargo/movement_rules.json. Each class's plan lists the
bottlenecks and data checks it runs into; see cargo_scenarios/README.md.
Classes that travel the same legs are also gathered into routes, so a plan
reads as a few routes, each carrying several classes.
"""
import csv
import json
import os
import re

import cargo_capability
from sea_routing import _unwrap, densify, great_circle_nm

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "cargo", "movement_rules.json")
PASSAGES = os.path.join(HERE, "neo4j-datamodel", "nodes", "passages.csv")
SAMPLE_NM = 2.0              # legs are measured inside a WEZ every 2 nm
LEVELS = ("bottleneck", "risk", "check", "note")
FLAG_NAMES = {
    "blocked": "No route at all",
    "no_handover": "No handover port can take it",
    "inside_wez": "Both ends inside a WEZ",
    "longer_military_leg": "Military carries it much further than other classes",
    "origin_ruled_out": "Origin cannot load it",
    "destination_ruled_out": "Destination cannot receive it",
    "single_handover": "Only one handover port within reach",
    "handover_inferred": "Handover port is only Inferred",
    "nearer_unknown": "A nearer port is Unknown in the WPI",
    "origin_unknown": "WPI cannot say if the origin can load it",
    "destination_unknown": "WPI cannot say if the destination can receive it",
    "end_is_handover": "No handover port is nearer than the far end",
    "handover_not_worth": "A handover would save too little",
}


def class_list(ids):
    """["I"] -> "Class I"; ["I", "II", "VI"] -> "Classes I, II and VI"."""
    return "Class " + ids[0] if len(ids) == 1 else "Classes %s and %s" % (", ".join(ids[:-1]), ids[-1])


def nm_inside(line, areas):
    """How much of a line lies inside any of the areas."""
    if not areas:
        return 0.0
    pts = densify([tuple(p) for p in line], SAMPLE_NM)
    total = 0.0
    for p, q in zip(pts, pts[1:]):
        mid = ((p[0] + _unwrap(q[0], p[0])) / 2, (p[1] + q[1]) / 2)
        if any(a.contains(mid) for a in areas):
            total += great_circle_nm(p, q)
    return total


class CargoPlanner:
    def __init__(self, graph, overrides=True):
        self.g = graph
        with open(RULES, encoding="utf-8") as f:
            self.rules = json.load(f)
        self.class_rules, _, rows, self.n_overrides = cargo_capability.rate_all(overrides)
        self.classes = self.class_rules["classes"]
        self.rating = {(r["port_id"], r["class_id"]): r for r in rows}
        with open(PASSAGES, encoding="utf-8") as f:
            self.passage_names = {r["passage_id"]: r["name"] for r in csv.DictReader(f)}

    def default_cargo(self):
        return [{"class": c, "short_tons": t} for c, t in self.rules["manifest"].items() if not c.startswith("_")]

    def _name(self, pid):
        return self.g.ports[pid]["name"]

    # ------------------------------------------------------------------ legs
    def _leg(self, carrier, a, b, closed, wez):
        r = self.g.shortest(a, b, banned_edges=closed)
        d = self.g.describe(r)
        knots = self.rules["carriers"][carrier]["knots"]
        return {"carrier": carrier, "carrier_label": self.rules["carriers"][carrier]["label"],
                "from": a, "from_name": self._name(a), "to": b, "to_name": self._name(b),
                "length_nm": d["length_nm"], "days": round(d["length_nm"] / knots / 24, 1),
                "passages": [self.passage_names.get(p, p) for p in d["passages"]],
                "wez_nm": round(nm_inside(d["line"], wez), 1), "line": d["line"]}

    # ------------------------------------------------------------------ plan
    def plan(self, origin, goal, cargo=None, denial=(), wez=(), handover_countries=None):
        """cargo is [{"class": "I", "short_tons": 500}, ...] (default: the
        manifest in movement_rules.json); denial and wez are DenialArea lists.
        handover_countries are the only countries whose ports may be handover
        ports (default: the list in movement_rules.json; [] allows any)."""
        g, cargo = self.g, cargo if cargo is not None else self.default_cargo()
        for item in cargo:
            if item.get("class") not in self.classes:
                raise ValueError("Unknown supply class %r; use one of %s." % (item.get("class"), ", ".join(self.classes)))
        hand_rule = self.rules["handover"]
        window = hand_rule["window_nm"]
        if handover_countries is None:
            handover_countries = hand_rule["countries"]
        den_closed = g.links_in_areas(denial) if denial else frozenset()
        comm_closed = den_closed | (g.links_in_areas(wez) if wez else frozenset())

        out = {"origin": origin, "origin_name": self._name(origin), "goal": goal, "goal_name": self._name(goal),
               "blocked": None, "commercial_direct": None, "military_direct": None, "commercial_extra_nm": None,
               "origin_in_wez": g.port_in_areas(origin, wez), "goal_in_wez": g.port_in_areas(goal, wez),
               "classes": []}
        for role, pid in (("Origin", origin), ("Destination", goal)):
            if g.port_in_areas(pid, denial):
                out["blocked"] = "%s port %s is inside an area of denial." % (role, self._name(pid))
        mil_direct = None if out["blocked"] else g.shortest(origin, goal, banned_edges=den_closed)
        if not out["blocked"] and mil_direct is None:
            out["blocked"] = "No sea route avoids the areas of denial."
        if out["blocked"]:
            for item in cargo:
                out["classes"].append(self._row(item, "blocked", [], None, origin, goal,
                                                [("bottleneck", "blocked", out["blocked"])], 0, 0))
            out["legs"] = []
            return self._tally(out)
        out["military_direct"] = round(mil_direct.length_nm, 1)

        legs = {}

        def leg(carrier, a, b):
            if (carrier, a, b) not in legs:
                legs[(carrier, a, b)] = self._leg(carrier, a, b, comm_closed if carrier == "commercial"
                                                  else den_closed, wez)
            return legs[(carrier, a, b)]

        comm = None
        if not out["origin_in_wez"] and not out["goal_in_wez"]:
            comm = g.shortest(origin, goal, banned_edges=comm_closed)
        if comm is not None:
            out["commercial_direct"] = round(comm.length_nm, 1)
            out["commercial_extra_nm"] = round(comm.length_nm - mil_direct.length_nm, 1)
        # When commercial carriers cannot make the whole trip, the cargo changes
        # hands once: near the destination when it is the end inside a WEZ (or
        # cut off by one), near the origin when the origin is inside a WEZ.
        o_in, g_in = out["origin_in_wez"], out["goal_in_wez"]
        side = None if comm is not None or (o_in and g_in) else "outbound" if o_in else "inbound"
        out["handover_side"] = side
        candidates, far = {}, None
        if side:
            near, far = (origin, goal) if side == "outbound" else (goal, origin)
            d_mil = g.port_distances(near, den_closed)
            d_comm = g.port_distances(far, comm_closed)
            allowed = set(handover_countries) if handover_countries else None
            candidates = {p: (d_mil[p], d_comm[p]) for p in d_comm
                          if p in d_mil and p not in (near, far) and not g.port_in_areas(p, wez)
                          and not g.port_in_areas(p, denial)
                          and (allowed is None or g.ports[p]["country"] in allowed)}
            # The far end is always a fallback: the military can carry the
            # whole way from (or to) it, whatever its rating.
            candidates[far] = (d_mil[far], 0.0)

        for item in cargo:
            cid = item["class"]
            flags = []
            status = lambda p: self.rating[(p, cid)]["status"]
            hand, options, backups = None, 0, 0
            if comm is not None:
                plan, route = "commercial", [leg("commercial", origin, goal)]
            elif not side:
                plan, route = "military", [leg("military", origin, goal)]
                flags.append(("bottleneck", "inside_wez", "%s and %s are both inside a WEZ, so commercial carriers "
                              "cannot take part. The military carries it the whole way." % (self._name(origin),
                                                                                           self._name(goal))))
            else:
                ok = sorted((p for p in candidates if p == far or status(p) in hand_rule["statuses"]),
                            key=candidates.get)
                options = len(ok) - 1
                best = ok[0]
                # A handover costs a transfer, so it must save the military real sailing.
                saving = candidates[far][0] - candidates[best][0]
                if best != far and saving < window:
                    flags.append(("note", "handover_not_worth", "A handover at %s would save the military only %s nm, "
                                  "less than %d nm, so the military carries it the whole way."
                                  % (self._name(best), format(round(saving), ","), window)))
                    best = far
                backups = sum(1 for p in ok if p != best and candidates[p][0] <= candidates[best][0] + window)
                if best == far:
                    plan, route = "military", [leg("military", origin, goal)]
                    if flags:
                        pass                          # handover_not_worth says why
                    elif not options:
                        flags.append(("bottleneck", "no_handover", "No port that commercial carriers can reach "
                                      "outside the WEZ, in the scenario's handover countries, is rated Confirmed or "
                                      "Inferred for Class %s, so the military carries it the whole way." % cid))
                    else:
                        flags.append(("note", "end_is_handover", "Every port outside the WEZ that can take Class %s "
                                      "is further from %s than %s is, so the military carries it the whole way."
                                      % (cid, self._name(near), self._name(far))))
                else:
                    hand = best
                    if side == "inbound":
                        plan, route = "commercial_military", [leg("commercial", origin, hand), leg("military", hand, goal)]
                    else:
                        plan, route = "military_commercial", [leg("military", origin, hand), leg("commercial", hand, goal)]
                    r = self.rating[(hand, cid)]
                    if r["status"] == "inferred":
                        flags.append(("check", "handover_inferred", "%s is only Inferred for Class %s: %s."
                                      % (self._name(hand), cid, r["reason"])))
                    if backups == 0:
                        flags.append(("risk", "single_handover", "No other port within %d nm of %s's military leg "
                                      "can take Class %s, so the whole handover depends on one port."
                                      % (window, self._name(hand), cid)))
                # Ports the World Port Index knows too little about may be the
                # real answer: name the nearest one, for a planner to check.
                nearer = sorted((p for p in candidates if p != far and status(p) == "unknown"
                                 and g.ports[p]["harbor_size"] in ("Medium", "Large")
                                 and candidates[p][0] < candidates[best][0] - window), key=candidates.get)
                if nearer:
                    p = nearer[0]
                    flags.append(("check", "nearer_unknown", "%d Medium or Large port%s nearer %s %s Unknown for "
                                  "Class %s. The nearest, %s (%s), is %s nm away; confirming it in "
                                  "cargo/port_overrides.csv would shorten the military leg." % (
                                      len(nearer), "" if len(nearer) == 1 else "s", self._name(near),
                                      "is" if len(nearer) == 1 else "are", cid, self._name(p),
                                      g.ports[p]["country"], format(round(candidates[p][0]), ","))))
            for role, pid, verb in (("origin", origin, "load"), ("destination", goal, "receive")):
                r = self.rating[(pid, cid)]
                if r["status"] == "ruled_out":
                    flags.append(("bottleneck", role + "_ruled_out", "%s is Ruled out for Class %s (%s). It cannot "
                                  "%s it from the class's usual ship without a smaller ship or another way to work "
                                  "cargo." % (self._name(pid), cid, r["reason"], verb)))
                elif r["status"] == "unknown":
                    flags.append(("check", role + "_unknown", "The World Port Index cannot say whether %s can %s "
                                  "Class %s: %s." % (self._name(pid), verb, cid, r["reason"])))
            out["classes"].append(self._row(item, plan, route, hand, origin, goal, flags, options, backups))

        # A class whose military leg is much longer than the scenario's shortest
        # is held back by its handover port, not by the geography.
        handed = [c for c in out["classes"] if c["military_nm"]]
        if handed:
            best = min(c["military_nm"] for c in handed)
            shortest = [c["class"] for c in handed if c["military_nm"] == best]
            others = len(shortest) == len(out["classes"]) - 1
            for c in handed:
                forced = {f["code"] for f in c["flags"]} & {"no_handover", "inside_wez"}
                if c["military_nm"] > best + window and not forced:
                    c["flags"].insert(0, {"level": "bottleneck", "code": "longer_military_leg", "text":
                                          "The military carries it %s nm, %s nm more than %s, because the "
                                          "nearest port that can take Class %s is further away." % (
                                              format(round(c["military_nm"]), ","),
                                              format(round(c["military_nm"] - best), ","),
                                              "the other classes" if others else "Class " + "/".join(shortest),
                                              c["class"])})
        out["legs"] = list(legs.values())
        return self._tally(out)

    @staticmethod
    def _tally(out):
        """Each class's count of bottlenecks, risks and checks, and the routes:
        classes that travel the same legs, gathered into one. A route's flags
        are merged, one line per flag, naming every class it applies to."""
        groups = {}
        for c in out["classes"]:
            for level in ("bottleneck", "risk", "check"):
                c[level + "s"] = sum(f["level"] == level for f in c["flags"])
            key = (c["plan"], tuple((x["carrier"], x["from"], x["to"]) for x in c["legs"]))
            groups.setdefault(key, []).append(c)
        out["routes"] = []
        for n, ((plan, _), cs) in enumerate(groups.items(), 1):
            first, ids, statuses, flags = cs[0], [c["class"] for c in cs], {}, {}
            for c in cs:
                c["route"] = n
                if c["handover"]:
                    statuses.setdefault(c["handover"]["status"], []).append(c["class"])
                for f in c["flags"]:
                    text = re.sub(r"\bClass %s\b" % c["class"], "Class {}", f["text"])
                    flags.setdefault((f["level"], f["code"], text), []).append(c["class"])
            merged = sorted(({"level": lv, "code": code, "name": FLAG_NAMES.get(code, code), "classes": who,
                              "text": text.replace("Class {}", class_list(who))}
                             for (lv, code, text), who in flags.items()), key=lambda f: LEVELS.index(f["level"]))
            tons = [c["short_tons"] for c in cs if c["short_tons"]]
            route = {"route": n, "plan": plan, "classes": ids, "legs": first["legs"],
                     "handover": first["handover"] and {k: first["handover"][k] for k in ("id", "name", "country")},
                     "handover_status": statuses, "short_tons": sum(tons) if tons else None,
                     "commercial_nm": first["commercial_nm"], "military_nm": first["military_nm"],
                     "wez_nm": first["wez_nm"], "days": first["days"], "flags": merged}
            for level in ("bottleneck", "risk", "check"):
                route[level + "s"] = sum(f["level"] == level for f in merged)
            out["routes"].append(route)
        return out

    def _row(self, item, plan, route, hand, origin, goal, flags, options, backups):
        cid, tons = item["class"], item.get("short_tons")
        mil = sum(x["length_nm"] for x in route if x["carrier"] == "military")
        com = sum(x["length_nm"] for x in route if x["carrier"] == "commercial")
        return {"class": cid, "class_name": self.classes[cid]["name"], "short_tons": tons, "plan": plan,
                "legs": [{"carrier": x["carrier"], "from": x["from"], "to": x["to"]} for x in route],
                "handover": None if hand is None else {
                    "id": hand, "name": self._name(hand), "country": self.g.ports[hand]["country"],
                    "status": self.rating[(hand, cid)]["status"], "reason": self.rating[(hand, cid)]["reason"]},
                "handover_options": options, "backups": backups,
                "origin_status": self.rating[(origin, cid)]["status"],
                "destination_status": self.rating[(goal, cid)]["status"],
                "commercial_nm": round(com, 1), "military_nm": round(mil, 1),
                "wez_nm": round(sum(x["wez_nm"] for x in route), 1),
                "days": round(sum(x["days"] for x in route), 1),
                "military_ton_nm": round(tons * mil) if tons else None,
                "flags": [{"level": lv, "code": code, "text": text} for lv, code, text in flags]}
