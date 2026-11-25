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

Each class is either economic (the default) or urgent. Economic cargo goes
by sea. Urgent cargo takes whichever is soonest of sea, air, and air for part
of the way then sea for the rest, when no flight reaches the far end (no
airfield near it, say). See cargo/air_rules.json for what may fly.

The port ratings come from cargo_capability.py, so the overrides in
cargo/port_overrides.csv apply. Carriers, speeds, the handover rule and the
default cargo are in cargo/movement_rules.json. Each class's plan lists the
bottlenecks and data checks it runs into; see cargo_scenarios/README.md.
Classes that travel the same legs are also gathered into routes, so a plan
reads as a few routes, each carrying several classes.
"""
import csv
import json
import math
import os
import re

import cargo_capability
from air_routing import AirNetwork
from sea_routing import _unwrap, densify, great_circle_nm

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "cargo", "movement_rules.json")
AIR_RULES = os.path.join(HERE, "cargo", "air_rules.json")
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
    "flies": "Flies because it is urgent",
    "air_sea": "Flies part of the way, then goes by sea",
    "no_air": "Urgent, but cannot fly",
    "sea_sooner": "Urgent, but sea is sooner",
}
PRIORITIES = ("economic", "urgent")


def _key(leg):
    return (leg["mode"], leg["carrier"], leg["from"], leg["to"])


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
        with open(AIR_RULES, encoding="utf-8") as f:
            self.air_rules = json.load(f)
        self.air = AirNetwork.load()

    def default_cargo(self):
        return [{"class": c, "short_tons": t} for c, t in self.rules["manifest"].items() if not c.startswith("_")]

    def _name(self, pid):
        return self.g.ports[pid]["name"]

    # ------------------------------------------------------------------ legs
    def _leg(self, carrier, a, b, closed, wez):
        r = self.g.shortest(a, b, banned_edges=closed)
        d = self.g.describe(r)
        knots = self.rules["carriers"][carrier]["knots"]
        return {"mode": "sea", "carrier": carrier, "carrier_label": self.rules["carriers"][carrier]["label"],
                "from": a, "from_name": self._name(a), "to": b, "to_name": self._name(b),
                "length_nm": d["length_nm"], "days": round(d["length_nm"] / knots / 24, 1),
                "passages": [self.passage_names.get(p, p) for p in d["passages"]],
                "wez_nm": round(nm_inside(d["line"], wez), 1), "line": d["line"]}

    # ------------------------------------------------------------------ plan
    def plan(self, origin, goal, cargo=None, denial=(), wez=(), handover_countries=None, priority="economic"):
        """cargo is [{"class": "I", "short_tons": 500}, ...] (default: the
        manifest in movement_rules.json); denial and wez are DenialArea lists.
        handover_countries are the only countries whose ports may be handover
        ports (default: the list in movement_rules.json; [] allows any).
        priority, for the whole cargo or per item, is "economic" (by sea) or
        "urgent" (the soonest of sea, air, or air then sea)."""
        g, cargo = self.g, cargo if cargo is not None else self.default_cargo()
        for item in cargo:
            if item.get("class") not in self.classes:
                raise ValueError("Unknown supply class %r; use one of %s." % (item.get("class"), ", ".join(self.classes)))
            if item.get("priority", priority) not in PRIORITIES:
                raise ValueError("Priority must be economic or urgent, not %r." % item.get("priority", priority))
        hand_rule = self.rules["handover"]
        window = hand_rule["window_nm"]
        if handover_countries is None:
            handover_countries = hand_rule["countries"]
        den_closed = g.links_in_areas(denial) if denial else frozenset()
        comm_closed = den_closed | (g.links_in_areas(wez) if wez else frozenset())
        closed = {"commercial": comm_closed, "military": den_closed}

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
            self._choose_modes(out, cargo, priority, denial, wez, handover_countries, closed)
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
        self._choose_modes(out, cargo, priority, denial, wez, handover_countries, closed)
        return self._tally(out)

    # --------------------------------------------------------------- options
    def options(self, origin, goal, denial=(), wez=(), handover_countries=None, cls="I"):
        """The ways to make one move, side by side, for the planner screen:
        {"sea": option, "air": option}. The air option flies the whole way, or,
        when no flight reaches the far end, flies part way and goes on by sea
        (or the reverse). Each option is {"available", "reason", "days",
        "plan", "legs"}, and air adds "mode" and "transfer". Uses class cls's
        rules (default I, general cargo that any aircraft may carry), by the
        same rules as plan()."""
        g = self.g
        if handover_countries is None:
            handover_countries = self.rules["handover"]["countries"]
        out = self.plan(origin, goal, [{"class": cls}], denial, wez, handover_countries, "economic")
        row = out["classes"][0]
        sea = {"available": not out["blocked"], "reason": out["blocked"], "days": row["sea_days"],
               "plan": row["plan"], "legs": out["legs"]}
        den = g.links_in_areas(denial) if denial else frozenset()
        closed = {"military": den, "commercial": den | (g.links_in_areas(wez) if wez else frozenset())}
        opt, reason = self.air_way(out, cls, denial, wez, handover_countries, closed, {}, {})
        air = dict(opt, reason=reason) if opt else {"available": False, "reason": reason}
        return {"sea": sea, "air": air}

    # ------------------------------------------------------------------- air
    def _choose_modes(self, out, cargo, priority, denial, wez, countries, closed):
        """Economic cargo goes by sea. Urgent cargo takes the soonest of sea,
        air, and (when no flight reaches the far end) air then sea."""
        direct, cache = {}, {}
        for item, c in zip(cargo, out["classes"]):
            pr = item.get("priority", priority)
            blocked = c["plan"] == "blocked"
            c.update(mode="sea", priority=pr, sea_days=None if blocked else c["days"], air_days=None, transfer=None)
            if pr != "urgent":
                continue
            sea_text = "with no sea route" if blocked else "against %s by sea" % format(c["days"], ".1f")
            rule = self.air_rules["classes"][c["class"]]
            if rule == "none":
                c["flags"].insert(0, {"level": "bottleneck", "code": "no_air", "text":
                                      "Urgent cargo of Class %s does not fly, so %s."
                                      % (c["class"], "it has no way there" if blocked else
                                         "it goes by sea: %s days" % format(c["days"], ".1f"))})
                continue
            opt, reason = self.air_way(out, c["class"], denial, wez, countries, closed, direct, cache)
            if opt:
                c["air_days"] = opt["days"]
            if opt is None:
                c["flags"].insert(0, {"level": "bottleneck", "code": "no_air", "text":
                                      "Urgent cargo of Class %s cannot fly: %s. %s." % (
                                          c["class"], reason,
                                          "It has no way there" if blocked else
                                          "It goes by sea: %s days" % format(c["days"], ".1f"))})
            elif not blocked and opt["days"] >= c["days"]:
                c["flags"].insert(0, {"level": "note", "code": "sea_sooner", "text":
                                      "Urgent cargo of Class %s goes by sea: %s days, against %s by air."
                                      % (c["class"], format(c["days"], ".1f"), format(opt["days"], ".1f"))})
            else:
                self._fly(out, c, item, opt, sea_text)

    def air_way(self, out, cid, denial, wez, countries, closed, direct, cache):
        """Class cid's way by air for the move in out (a plan() result): the direct
        flight, or, when no flight reaches the far end, air then sea (or sea then air).
        Returns (option or None, why the direct flight fails or None). direct and cache
        are dicts kept between classes of one move, so each flight is worked out once."""
        rule = self.air_rules["classes"][cid]
        if rule == "none":
            return None, "Class %s never flies" % cid
        if rule not in direct:
            direct[rule] = self._air_option(out["origin"], out["goal"], denial, wez, countries, rule == "military")
        air = direct[rule]
        if air["available"]:
            return dict(air, mode="air", transfer=None), None
        opt = self._mixed_option(out, cid, rule, denial, wez, countries, closed, cache)
        return (dict(opt, why=air["reason"]) if opt else None), air["reason"]

    def _fly(self, out, c, item, opt, sea_text):
        """Turn class row c into its air plan, or its air-then-sea plan."""
        tons = item.get("short_tons")
        for leg in opt["legs"]:
            if not any(_key(leg) == _key(x) for x in out["legs"]):
                out["legs"].append(leg)
        moved = [x for x in opt["legs"] if x["mode"] != "road"]
        mil = sum(x["length_nm"] for x in moved if x["carrier"] == "military")
        com = sum(x["length_nm"] for x in moved if x["carrier"] == "commercial")
        sorties = {}
        for x in moved:
            if x["mode"] == "air" and tons:
                sorties[x["carrier"]] = math.ceil(tons / self.air_rules["aircraft"][x["carrier"]]["payload_st"])
        mode = opt.get("mode", "air")
        if mode == "air":
            flag = {"level": "note", "code": "flies", "text": "Urgent cargo of Class %s goes by air: %s days, %s."
                    % (c["class"], format(opt["days"], ".1f"), sea_text)}
        else:
            t = opt["transfer"]["name"]
            flag = {"level": "note", "code": "air_sea", "text":
                    "Urgent cargo of Class %s cannot fly the whole way: %s. It %s: %s days door to door, %s." % (
                        c["class"], opt["why"],
                        "flies to the airfield nearest %s and goes on by sea from there" % t if mode == "air_sea"
                        else "goes by sea to %s and flies on from the airfield nearest it" % t,
                        format(opt["days"], ".1f"), sea_text)}
        c.update(mode=mode, plan=opt["plan"], legs=[{k: x[k] for k in ("mode", "carrier", "from", "to")}
                                                   for x in opt["legs"]],
                 handover=opt["handover"], handover_options=0, backups=0, transfer=opt.get("transfer"),
                 commercial_nm=round(com, 1), military_nm=round(mil, 1),
                 wez_nm=round(sum(x["wez_nm"] for x in opt["legs"]), 1), days=opt["days"],
                 military_ton_nm=round(tons * mil) if tons else None, sorties=sorties, flags=[flag])

    def _mixed_option(self, out, cid, rule, denial, wez, countries, closed, cache):
        """When no flight reaches the far end, fly as far as a port near it
        and go by sea for the rest (or, out of a port with no airfield, by sea
        to one and fly on). The transfer port must be outside the areas of
        denial, in the handover countries, rated for the class, and within
        road distance of an airfield; of the nearest few to the far end by
        sea, the one that gets the cargo there soonest wins."""
        g, hd = self.g, self.air_rules["handling"]
        origin, goal = out["origin"], out["goal"]
        statuses = self.rules["handover"]["statuses"]
        allowed = set(countries) if countries else None
        runway = self.air_rules["aircraft"]["military"]["min_runway_ft"]
        reach = self.air_rules["airfields"]["max_road_nm"]
        best = None
        for inbound in (True, False):
            end = goal if inbound else origin
            if g.port_in_areas(end, denial):
                continue
            d = g.port_distances(end, closed["military"])
            ports = sorted((p for p, nm in d.items() if math.isfinite(nm) and p not in (origin, goal)
                            and self.rating[(p, cid)]["status"] in statuses
                            and (allowed is None or g.ports[p]["country"] in allowed)
                            and not g.port_in_areas(p, denial)), key=d.get)
            tried = 0
            for p in ports:
                if tried == 8:                # the nearest few with an airfield
                    break
                if self.air.nearest(g.coords[p], runway, reach, denial)[0] is None:
                    continue
                tried += 1
                key = (rule, p, inbound)
                if key not in cache:
                    cache[key] = (self._air_option(origin, p, denial, wez, countries, rule == "military") if inbound
                                  else self._air_option(p, goal, denial, wez, countries, rule == "military"))
                air = cache[key]
                if not air["available"]:
                    continue
                a, b = (p, goal) if inbound else (origin, p)
                # Commercial carriers take the sea leg unless it starts, ends or runs inside a WEZ.
                in_wez = g.port_in_areas(p, wez) or g.port_in_areas(end, wez)
                carrier = ("commercial" if not in_wez and g.shortest(a, b, banned_edges=closed["commercial"])
                           else "military")
                if ("sea", carrier, a, b) not in cache:
                    cache[("sea", carrier, a, b)] = self._leg(carrier, a, b, closed[carrier], wez)
                sea = cache[("sea", carrier, a, b)]
                # loaded onto a ship at the transfer port, unloaded at the far end
                days = round(air["days"] + sea["days"] + 2 * hd["ship_port_days"], 1)
                if best is None or days < best["days"]:
                    legs = air["legs"] + [sea] if inbound else [sea] + air["legs"]
                    carriers = []
                    for x in legs:
                        if x["mode"] != "road" and (not carriers or carriers[-1] != x["carrier"]):
                            carriers.append(x["carrier"])
                    hand = air["handover"]
                    if hand is None and len(carriers) > 1:
                        r = self.rating[(p, cid)]
                        hand = {"id": p, "name": self._name(p), "country": g.ports[p]["country"],
                                "status": r["status"], "reason": r["reason"],
                                "lon": g.coords[p][0], "lat": g.coords[p][1]}
                    best = {"available": True, "mode": "air_sea" if inbound else "sea_air",
                            "plan": "_".join(carriers), "legs": legs, "handover": hand, "days": days,
                            "transfer": {"id": p, "name": self._name(p), "country": g.ports[p]["country"],
                                         "lon": g.coords[p][0], "lat": g.coords[p][1]}}
        return best

    def _air_option(self, origin, goal, denial, wez, countries, military_only):
        """Flying between the airfields nearest the two ports, by the same
        rules as at sea: commercial aircraft stay out of a WEZ, and hand over
        to military airlift at the airfield outside it nearest the end inside
        it. {"available", "reason", "plan", "legs", "handover", "days"}"""
        # A port inside an area of denial is closed to every mode: no flying to an airfield
        # outside the area and going in by road.
        for role, pid in (("origin", origin), ("destination", goal)):
            if self.g.port_in_areas(pid, denial):
                return {"available": False, "reason": "the %s port %s is inside an area of denial" % (role, self._name(pid))}
        air, ar = self.air, self.air_rules
        hd, fields, planes = ar["handling"], ar["airfields"], ar["aircraft"]
        stops = set(fields["stop_countries"])
        shut = list(denial) + list(wez)

        def fly(a, b, carrier, closed):
            return air.fly(a, b, planes[carrier], closed, lambda f: air.fields[f]["country_code"] in stops,
                           hd["refuel_stop_hours"])

        def road_clear(port, field, areas):
            """The road leg is a straight line; it may not pass through an area either."""
            if not areas:
                return True
            pts = densify([self.g.coords[port], air.coords[field]], 2.0)
            return not any(a.crosses(p, q) for a in areas for p, q in zip(pts, pts[1:]))

        def end(port, carrier, avoid):
            return air.nearest(self.g.coords[port], planes[carrier]["min_runway_ft"], fields["max_road_nm"], avoid,
                               ok=lambda f: road_clear(port, f, avoid))[0]

        def hours(path, carrier):
            nm = sum(great_circle_nm(air.coords[a], air.coords[b]) for a, b in zip(path, path[1:]))
            return nm / planes[carrier]["cruise_kt"] + (len(path) - 2) * hd["refuel_stop_hours"]

        om, gm = end(origin, "military", denial), end(goal, "military", denial)
        if om is None or gm is None:
            return {"available": False, "reason": "no airfield with a long enough runway%s is within %d nm of %s"
                    % (", reached by a road that keeps out of the areas of denial," if denial else "",
                       fields["max_road_nm"],
                       self._name(origin if om is None else goal))}
        whole = fly(om, gm, "military", denial)
        if not military_only:
            oc, gc = end(origin, "commercial", shut), end(goal, "commercial", shut)
            path = fly(oc, gc, "commercial", shut) if oc and gc else None
            if path:
                return self._air_result("commercial", [("commercial", path)], origin, goal, None, wez)
            if oc or gc:
                side = "inbound" if oc else "outbound"
                near, far = (gm, oc) if side == "inbound" else (om, gc)
                codes = None if not countries else {fields["country_codes"].get(n) for n in countries}
                runway = max(planes["commercial"]["min_runway_ft"], planes["military"]["min_runway_ft"])
                cands = sorted((f for f in air.fields if f not in (near, far) and air.runway[f] >= runway
                                and (codes is None or air.fields[f]["country_code"] in codes)
                                and not air.inside(f, shut)),
                               key=lambda f: great_circle_nm(air.coords[f], air.coords[near]))
                best = None
                for f in cands[:15]:          # the nearest few; the best military leg among them wins
                    mpath = fly(f, near, "military", denial) if side == "inbound" else fly(near, f, "military", denial)
                    cpath = mpath and (fly(far, f, "commercial", shut) if side == "inbound"
                                       else fly(f, far, "commercial", shut))
                    if cpath and (best is None or hours(mpath, "military") < best[0]):
                        best = (hours(mpath, "military"), f, mpath, cpath)
                window_h = self.rules["handover"]["window_nm"] / planes["military"]["cruise_kt"]
                if best and (whole is None or hours(whole, "military") - best[0] >= window_h):
                    _, f, mpath, cpath = best
                    if side == "inbound":
                        return self._air_result("commercial_military", [("commercial", cpath), ("military", mpath)],
                                                origin, goal, f, wez)
                    return self._air_result("military_commercial", [("military", mpath), ("commercial", cpath)],
                                            origin, goal, f, wez)
        if whole:
            return self._air_result("military", [("military", whole)], origin, goal, None, wez)
        return {"available": False, "reason": "no air route avoids the areas of denial"}

    def _air_result(self, plan, flights, origin, goal, handover, wez):
        air, ar = self.air, self.air_rules
        hd = ar["handling"]
        labels = {k: v["label"] for k, v in self.rules["carriers"].items() if not k.startswith("_")}

        def road(carrier, port, field, outward):
            p, f = self.g.coords[port], air.coords[field]
            nm = great_circle_nm(p, f)
            a, b = (port, field) if outward else (field, port)
            return {"mode": "road", "carrier": carrier, "carrier_label": labels[carrier], "from": a, "to": b,
                    "from_name": self._name(port) if outward else air.name(field),
                    "to_name": air.name(field) if outward else self._name(port),
                    "length_nm": round(nm, 1), "hours": round(nm / hd["road_kt"], 1),
                    "days": round(nm / hd["road_kt"] / 24, 2), "passages": [], "stops": [],
                    "wez_nm": round(nm_inside([p, f] if outward else [f, p], wez), 1),
                    "line": [list(p), list(f)] if outward else [list(f), list(p)]}

        legs = [road(flights[0][0], origin, flights[0][1][0], True)]
        for carrier, path in flights:
            plane = ar["aircraft"][carrier]
            nm = sum(great_circle_nm(air.coords[a], air.coords[b]) for a, b in zip(path, path[1:]))
            h = nm / plane["cruise_kt"] + (len(path) - 2) * hd["refuel_stop_hours"]
            line = air.line(path)
            legs.append({"mode": "air", "carrier": carrier, "carrier_label": labels[carrier], "aircraft": plane["name"],
                         "from": path[0], "to": path[-1], "from_name": air.name(path[0]), "to_name": air.name(path[-1]),
                         "hops": path, "stops": [air.name(f) for f in path[1:-1]], "length_nm": round(nm, 1),
                         "hours": round(h, 1), "days": round(h / 24, 2), "passages": [],
                         "wez_nm": round(nm_inside(line, wez), 1), "line": line})
        legs.append(road(flights[-1][0], goal, flights[-1][1][-1], False))
        hours = sum(x["hours"] for x in legs) + hd["airfield_hours"] * (2 + (1 if handover else 0))
        hand = None if handover is None else {
            "id": handover, "name": air.name(handover), "country": air.fields[handover]["country_code"],
            "status": "airfield", "reason": "runway %d ft" % air.runway[handover],
            "lon": air.coords[handover][0], "lat": air.coords[handover][1]}
        return {"available": True, "reason": None, "plan": plan, "legs": legs, "handover": hand,
                "days": round(hours / 24, 1)}

    def _tally(self, out):
        """Each class's count of bottlenecks, risks and checks, and the routes:
        classes that travel the same legs, gathered into one. A route's flags
        are merged, one line per flag, naming every class it applies to."""
        used = {_key(x) for c in out["classes"] for x in c["legs"]}
        out["legs"] = [x for x in out["legs"] if _key(x) in used]     # a class that flies leaves its sea legs
        groups = {}
        for c in out["classes"]:
            for level in ("bottleneck", "risk", "check"):
                c[level + "s"] = sum(f["level"] == level for f in c["flags"])
            key = (c["mode"], c["plan"], tuple(_key(x) for x in c["legs"]))
            groups.setdefault(key, []).append(c)
        out["routes"] = []
        for n, ((mode, plan, _), cs) in enumerate(groups.items(), 1):
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
            # Sorties for the route's whole load: classes on one route can share aircraft.
            sorties = {}
            for x in first["legs"]:
                if x["mode"] == "air" and tons:
                    sorties[x["carrier"]] = math.ceil(sum(tons) / self.air_rules["aircraft"][x["carrier"]]["payload_st"])
            sea = [c["sea_days"] for c in cs if c["sea_days"]]
            prio = {c["priority"] for c in cs}
            route = {"route": n, "mode": mode, "plan": plan, "classes": ids, "legs": first["legs"],
                     "handover": first["handover"] and {k: first["handover"][k]
                                                        for k in ("id", "name", "country", "lon", "lat")},
                     "priority": prio.pop() if len(prio) == 1 else "mixed", "transfer": first["transfer"],
                     "sea_days": [min(sea), max(sea)] if sea else None,
                     "air_days": first["air_days"], "sorties": sorties,
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
                "legs": [{k: x[k] for k in ("mode", "carrier", "from", "to")} for x in route],
                "handover": None if hand is None else {
                    "id": hand, "name": self._name(hand), "country": self.g.ports[hand]["country"],
                    "status": self.rating[(hand, cid)]["status"], "reason": self.rating[(hand, cid)]["reason"],
                    "lon": self.g.coords[hand][0], "lat": self.g.coords[hand][1]},
                "handover_options": options, "backups": backups,
                "origin_status": self.rating[(origin, cid)]["status"],
                "destination_status": self.rating[(goal, cid)]["status"],
                "commercial_nm": round(com, 1), "military_nm": round(mil, 1),
                "wez_nm": round(sum(x["wez_nm"] for x in route), 1),
                # door to door: sailing, plus a day in each port where the cargo is worked
                "days": round(sum(x["days"] for x in route) + (len(route) + 1 if route else 0)
                              * self.air_rules["handling"]["ship_port_days"], 1),
                "military_ton_nm": round(tons * mil) if tons else None, "sorties": None,
                "flags": [{"level": lv, "code": code, "text": text} for lv, code, text in flags]}
