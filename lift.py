"""
Closure: when the last ton arrives, given the lift on hand and how fast ports
and airfields work cargo (Lift layer spec, stage 1). Python 3, stdlib only.

    from lift import Lift
    lift = Lift.load()
    lift.close(planner, columns_of_cells, legs, counts=None)

A cell's "days" stays the first arrival: the first load, with the planner's
handling allowances. Closure adds the longest of the waits that more cargo
brings:

    by sea:  the ships coming back for another load (when the cargo needs more
             loads than there are ships), the loading port working the rest of
             the cargo, or the receiving port discharging it;
    by air:  the remaining sorties, at the lowest of what the aircraft on hand,
             the loading airfield and the receiving airfield can handle a day.

Classes that use the same ship type (or aircraft) on the same legs share it:
their tons are pooled. A port or airfield works all the cargo through it, so
its rate is shared too. Each cell gets "closure", "lift" (what carries it) and
"limit" (what sets the closure). Figures come from cargo/lift_rules.json; every
one there is marked sourced or illustrative.

Stage 1 simplifications: the starting set is at the origin, ready on D+0;
sailing time is the route's; the sea part of air-then-sea isn't limited by ships.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RULES = os.path.join(HERE, "cargo", "lift_rules.json")


def _public(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


class Lift:
    def __init__(self, rules):
        self.rules = rules
        self.ships = _public(rules["ships"])
        self.aircraft = _public(rules["aircraft"])
        self.ship_for = {c: t for t, s in self.ships.items() for c in s["classes"]}
        self.port_rate = rules["ports"]["rate_st_per_day"]
        self.mog = rules["airfields"]["working_mog"]

    @classmethod
    def load(cls, path=RULES):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    def counts(self, override=None):
        """How many of each type are on hand: the starting set, with any override."""
        out = {t: s["count"] for t, s in self.ships.items()}
        out.update({k: a["count"] for k, a in self.aircraft.items()})
        for k, v in (override or {}).items():
            if k in out:
                out[k] = v
        return out

    def summary(self):
        return {"ships": {t: {k: s[k] for k in ("name", "classes", "capacity_st", "count")} for t, s in self.ships.items()},
                "aircraft": {t: {k: a[k] for k in ("name", "planning_payload_st", "ground_hours", "count")}
                             for t, a in self.aircraft.items()},
                "port_rate_st_per_day": self.port_rate, "working_mog": self.mog}

    # ------------------------------------------------------------------ close
    def close(self, P, columns, legs, counts=None):
        """columns: {column_id: [(class_id, short_tons, cell)]}, one entry per class
        in that column, each cell as comparisons.py makes them. Adds closure to
        every available cell, pooling within a column (one column is one way of
        moving all the cargo)."""
        n = self.counts(counts)
        for entries in columns.values():
            self._close_sea(P, [e for e in entries if e[2]["available"] and e[2]["mode"] == "sea"], legs, n)
            self._close_air(P, [e for e in entries if e[2]["available"] and e[2]["mode"] != "sea"], legs, n)
            for _, _, c in entries:
                if not c["available"]:
                    c.update(closure=None, lift=None, limit=None)

    def _close_sea(self, P, entries, legs, n):
        hd = P.air_rules["handling"]["ship_port_days"]
        ports = P.g.ports
        at_origin, at_goal, pools = {}, {}, {}
        for cid, tons, c in entries:
            sea = [legs[k] for k in c["legs"] if legs[k]["mode"] == "sea"]
            o, d = sea[0]["from"], sea[-1]["to"]
            at_origin[o] = at_origin.get(o, 0) + (tons or 0)
            at_goal[d] = at_goal.get(d, 0) + (tons or 0)
            pools.setdefault((self.ship_for[cid], tuple(c["legs"])), []).append((cid, tons, c, sea, o, d))
        for (stype, _), members in pools.items():
            ship, count = self.ships[stype], n[stype]
            tons = sum(m[1] or 0 for m in members)
            _, _, c0, sea, o, d = members[0]
            r_o, r_d = self.port_rate[ports[o]["harbor_size"] or ""], self.port_rate[ports[d]["harbor_size"] or ""]
            loads = math.ceil(tons / ship["capacity_st"]) if tons else 0
            sail = sum(x["days"] for x in sea)
            # A ship's round trip: load and work a full load at each end (at least the day allowed in each
            # port), sail there and back, and a day at each handover port on the way.
            work = lambda rate: max(hd, ship["capacity_st"] / rate)
            cycle = work(r_o) + 2 * sail + (len(sea) - 1) * hd + work(r_d)
            waits = {"ships": 0.0 if count == 0 else (math.ceil(loads / count) - 1) * cycle if loads else 0.0,
                     # The first arrival already allows a day in each port; the rest of the port's cargo,
                     # every class through it, waits its turn after that.
                     "origin": max(0.0, (at_origin[o] - r_o * hd) / r_o),
                     "goal": max(0.0, (at_goal[d] - r_d * hd) / r_d)}
            if count == 0 and loads:
                lift = {"kind": "ship", "type": stype, "name": ship["name"], "count": 0, "loads": loads,
                        "capacity_st": ship["capacity_st"]}
                for m in members:
                    m[2].update(closure=None, lift=lift, limit="No %s on hand" % ship["name"].lower())
                continue
            key = max(waits, key=waits.get)
            limit = (None if waits[key] <= 0 else
                     "%d %s%s for %d load%s" % (count, ship["name"], "" if count == 1 else "s", loads, "" if loads == 1 else "s")
                     if key == "ships" else
                     "%s loading %s short tons at %s a day" % (ports[o]["name"], format(round(at_origin[o]), ","),
                                                                format(r_o, ","))
                     if key == "origin" else
                     "%s discharging %s short tons at %s a day" % (ports[d]["name"], format(round(at_goal[d]), ","),
                                                                    format(r_d, ",")))
            lift = {"kind": "ship", "type": stype, "name": ship["name"], "count": count, "loads": loads,
                    "capacity_st": ship["capacity_st"], "pool_st": tons}
            for _, _, c, *_ in members:
                c.update(closure=round(c["days"] + waits[key], 1), lift=lift, limit=limit)

    def _close_air(self, P, entries, legs, n):
        air = P.air
        fields = {}
        pools = {}
        for cid, tons, c in entries:
            for carrier in {legs[k]["carrier"] for k in c["legs"] if legs[k]["mode"] == "air"}:
                flights = [legs[k] for k in c["legs"] if legs[k]["mode"] == "air" and legs[k]["carrier"] == carrier]
                for f in (flights[0]["from"], flights[-1]["to"]):
                    fields[f] = fields.get(f, 0) + (tons or 0)
                pools.setdefault((carrier, tuple(c["legs"])), []).append((tons, c, flights))
        waits = {}               # cell id -> (days, limit, lift) of the slowest carrier in it
        for (carrier, _), members in pools.items():
            plane, count = self.aircraft[carrier], n[carrier]
            tons = sum(m[0] or 0 for m in members)
            flights = members[0][2]
            sorties = math.ceil(tons / plane["planning_payload_st"]) if tons else 0
            fly_h = sum(x["hours"] for x in flights)
            o, d = flights[0]["from"], flights[-1]["to"]
            per_day = {"aircraft": count * 24 / (2 * fly_h + 2 * plane["ground_hours"]) if count else 0.0,
                       "origin": self.mog.get(air.fields[o]["airport_type"], 1) * 24 / plane["ground_hours"],
                       "goal": self.mog.get(air.fields[d]["airport_type"], 1) * 24 / plane["ground_hours"]}
            key = min(per_day, key=per_day.get)
            lift = {"kind": "aircraft", "type": carrier, "name": plane["name"], "count": count, "sorties": sorties,
                    "planning_payload_st": plane["planning_payload_st"], "pool_st": tons,
                    "sorties_per_day": round(per_day[key], 1)}
            if sorties and not per_day["aircraft"]:
                wait, limit = None, "No %s on hand" % plane["name"]
            else:
                wait = (sorties - 1) / per_day[key] if sorties > 1 else 0.0
                name = {"aircraft": "%d %s" % (count, plane["name"]), "origin": air.name(o), "goal": air.name(d)}[key]
                limit = None if wait <= 0 else ("%s: %s sorties a day" % (name, format(per_day[key], ".1f")) +
                                                ("" if key == "aircraft" else " (working MOG)"))
            for _, c, _ in members:
                prev = waits.get(id(c))
                if prev is None or prev[0] is not None and (wait is None or wait > prev[0]):
                    waits[id(c)] = (wait, limit, lift, c)
        for wait, limit, lift, c in waits.values():
            c.update(closure=None if wait is None else round(c["days"] + wait, 1), lift=lift, limit=limit)
