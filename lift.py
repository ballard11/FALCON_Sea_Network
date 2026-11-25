"""
Closure: when the last ton arrives, given the lift on hand, where it starts,
and how fast ports and airfields work cargo (Lift layer spec, stages 1 to 3).
Python 3, stdlib only.

    from lift import Lift
    lift = Lift.load()
    used = lift.close(planner, columns_of_cells, legs, lift_on_hand, closed, port_rates)

Lift on hand is {type: count} or {type: [{"count", "at", "ready"}]}: groups of
one type, each starting at a port (ships) or an airfield (aircraft), or at the
origin when "at" is None, and free to move from day "ready". A plain count is
one group at the origin, ready on D+0. port_rates is a what-if's {port_id:
short tons a day}, which wins over the data.

A cell's "days" stays the first arrival as if lift were waiting at the origin:
the first load, with the planner's handling allowances. Closure adds the
longest of the waits that more cargo, and lift that isn't there yet, bring:

    by sea:  each load goes on the first ship free: a ship is first free when
             it reaches the loading port (its ready day plus the sail there),
             then again one round trip later. Or the loading port working the
             rest of the cargo, or the receiving port discharging it, from the
             first ship's arrival;
    by air:  the remaining sorties, flown at the lowest of what the aircraft
             there so far and the working MOG at each end can handle a day.
             Aircraft join as they reach the loading airfield.

Classes that use the same ship type (or aircraft) on the same legs share it:
their tons are pooled. A port or airfield works all the cargo through it, so
its rate is shared too.

Results are data, never sentences; the screen words them. Each cell gets
"closure", "lift" (what carries it) and "limit" (what sets the closure, or
None when nothing makes it wait):

    {"kind": "port", "place", "port_id", "work": "loading" | "discharging", "demand_st", "rate_st_per_day"}
    {"kind": "ships", "type", "name", "count", "loads"}            too few ships for the loads
    {"kind": "ships_arriving", "type", "name", "at", "from": [places]}
    {"kind": "no_ships", "type", "name", "at"}                     none can reach the loading port
    {"kind": "aircraft", "type", "name", "count", "sorties_per_day", "from": [places]}
    {"kind": "mog", "place", "type", "name", "sorties_per_day"}    an airfield's working MOG
    {"kind": "aircraft_arriving", "type", "name", "at", "from": [places]}
    {"kind": "no_aircraft", "type", "name"}

close() returns, for each column, what it used: "usage" (each port's work and
each pool of ships or aircraft, with the days it takes) and "figures" (the ids
of the figures in falcon_data.FIGURES that the answer rests on).

Simplifications: sailing time is the route's; a ship sails to the loading port
by the shortest sea route its carrier may use; an aircraft flies there direct,
on a great circle; the sea part of air-then-sea isn't limited by ships.
"""
import heapq
import math

from falcon_data import STORE
from sea_routing import great_circle_nm


def _public(d):
    return {k: v for k, v in d.items() if not k.startswith("_")}


class Lift:
    def __init__(self, rules, port_rates=None):
        self.rules = rules
        self.ships = _public(rules["ships"])
        self.aircraft = _public(rules["aircraft"])
        self.ship_for = {c: t for t, s in self.ships.items() for c in s["classes"]}
        self.port_rate = rules["ports"]["rate_st_per_day"]
        self.rate_by_port = {pid: r["rate_st_per_day"] for pid, r in (port_rates or {}).items()}
        self.normal_mode = _public(rules["plan"]["normal_mode"])
        self.fuel_lb_per_gal = rules["plan"]["fuel_lb_per_gal"]
        self.mog = rules["airfields"]["working_mog"]

    @classmethod
    def load(cls, store=STORE):
        return cls(store.rules("lift"), store.port_rates())

    def counts(self, override=None):
        """How many of each type are on hand: the starting set, with any override."""
        return {k: sum(g["count"] for g in gs) for k, gs in self.groups(override).items()}

    def groups(self, override=None):
        """The lift on hand as groups: {type: [{"count", "at", "ready"}]}."""
        out = {t: [{"count": s["count"], "at": None, "ready": 0.0}] for t, s in self.ships.items()}
        out.update({k: [{"count": a["count"], "at": None, "ready": 0.0}] for k, a in self.aircraft.items()})
        for k, v in (override or {}).items():
            if k in out:
                out[k] = ([{"count": v, "at": None, "ready": 0.0}] if isinstance(v, int) else
                          [{"count": g["count"], "at": g.get("at"), "ready": float(g.get("ready") or 0)} for g in v])
        return out

    def rate(self, port, pid, whatif=None):
        """A port's short tons a day and where that figure comes from: (rate, figure id),
        the figure id None for a what-if's rate."""
        if whatif and pid in whatif:
            return whatif[pid], None
        if pid in self.rate_by_port:
            return self.rate_by_port[pid], "port_rates:" + pid
        size = port["harbor_size"] or ""
        return self.port_rate[size], "lift:ports.rate_st_per_day." + size

    def next_rate(self, rate):
        """The next harbor size's rate above this one: (size, rate), or None at the top."""
        up = sorted((r, s) for s, r in self.port_rate.items() if r > rate)
        return (up[0][1], up[0][0]) if up else None

    def summary(self):
        return {"ships": {t: {k: s[k] for k in ("name", "short", "classes", "capacity_st", "count", "carrier")}
                          for t, s in self.ships.items()},
                "aircraft": {t: {k: a[k] for k in ("name", "short", "planning_payload_st", "ground_hours", "count")}
                             for t, a in self.aircraft.items()},
                "port_rate_st_per_day": self.port_rate, "working_mog": self.mog,
                "normal_mode": self.normal_mode, "fuel_lb_per_gal": self.fuel_lb_per_gal}

    # ------------------------------------------------------------------ close
    def close(self, P, columns, legs, lift=None, closed=None, port_rates=None):
        """columns: {column_id: [(class_id, short_tons, cell)]}, one entry per class
        in that column, each cell as comparisons.py makes them. closed is the sea
        links each carrier may not use ({"commercial", "military"}). Adds closure
        to every available cell, pooling within a column (one column is one way
        of moving all the cargo). Returns {column_id: {"usage", "figures"}}."""
        groups = self.groups(lift)
        closed = closed or {"commercial": frozenset(), "military": frozenset()}
        arrivals = {}            # (type, loading point) -> [(day it can start there, origin name)], one per unit
        used = {}
        for col, entries in columns.items():
            u = used[col] = {"usage": [], "figures": set()}
            self._close_sea(P, [e for e in entries if e[2]["available"] and e[2]["mode"] == "sea"], legs, groups,
                            closed, arrivals, port_rates, u)
            self._close_air(P, [e for e in entries if e[2]["available"] and e[2]["mode"] != "sea"], legs, groups,
                            arrivals, u)
            for _, _, c in entries:
                if not c["available"]:
                    c.update(closure=None, lift=None, limit=None)
            u["figures"] = sorted(u["figures"])
        return used

    def _ships_at(self, P, stype, port, groups, closed, arrivals):
        """Each ship of the type: (the day it can start loading at port, where it came from)."""
        key = (stype, port)
        if key not in arrivals:
            ship, out = self.ships[stype], []
            knots = P.rules["carriers"][ship["carrier"]]["knots"]
            for g in groups[stype]:
                if g["at"] in (None, port):
                    sail, came = 0.0, None
                else:
                    r = P.g.shortest(g["at"], port, banned_edges=closed[ship["carrier"]])
                    if r is None:
                        continue                     # no sea route there: this group can't take part
                    sail, came = r.length_nm / knots / 24, P.g.ports[g["at"]]["name"]
                out += [(g["ready"] + sail, came)] * g["count"]
            arrivals[key] = sorted(out)
        return arrivals[key]

    def _close_sea(self, P, entries, legs, groups, closed, arrivals, whatif, u):
        hd = P.air_rules["handling"]["ship_port_days"]
        ports = P.g.ports
        at_origin, at_goal, pools = {}, {}, {}
        for cid, tons, c in entries:
            sea = [legs[k] for k in c["legs"] if legs[k]["mode"] == "sea"]
            o, d = sea[0]["from"], sea[-1]["to"]
            at_origin[o] = at_origin.get(o, 0) + (tons or 0)
            at_goal[d] = at_goal.get(d, 0) + (tons or 0)
            pools.setdefault((self.ship_for[cid], tuple(c["legs"])), []).append((cid, tons, c, sea, o, d))
        if entries:
            u["figures"].add("air:handling.ship_port_days")
        rates = {}
        for work, totals in (("loading", at_origin), ("discharging", at_goal)):
            for p, total in totals.items():
                rate, fid = rates[p] = self.rate(ports[p], p, whatif)
                if fid:
                    u["figures"].add(fid)
                u["usage"].append({"kind": "port", "port_id": p, "place": ports[p]["name"], "work": work,
                                   "demand_st": total, "rate_st_per_day": rate, "figure": fid,
                                   "days": round(total / rate, 1)})
        for (stype, _), members in pools.items():
            ship = self.ships[stype]
            tons = sum(m[1] or 0 for m in members)
            _, _, c0, sea, o, d = members[0]
            fleet = self._ships_at(P, stype, o, groups, closed, arrivals)
            count = len(fleet)
            loads = math.ceil(tons / ship["capacity_st"]) if tons else 0
            lift = {"kind": "ship", "type": stype, "name": ship["name"], "count": count, "loads": loads,
                    "capacity_st": ship["capacity_st"], "pool_st": tons}
            u["figures"].update({"lift:ships.%s.capacity_st" % stype, "lift:ships.%s.count" % stype,
                                 "movement:carriers.%s.knots" % ship["carrier"]})
            row = {"kind": "ships", "type": stype, "name": ship["name"], "classes": [m[0] for m in members],
                   "count": count, "loads": loads, "capacity_st": ship["capacity_st"], "pool_st": tons,
                   "fill": round(tons / (loads * ship["capacity_st"]), 2) if loads else 0.0, "days": None}
            u["usage"].append(row)
            if loads and not count:
                for m in members:
                    m[2].update(closure=None, lift=lift,
                                limit={"kind": "no_ships", "type": stype, "name": ship["name"], "at": ports[o]["name"]})
                continue
            r_o, r_d = rates[o][0], rates[d][0]
            sail = sum(x["days"] for x in sea)
            # A ship's round trip: load and work a full load at each end (at least the day allowed in each
            # port), sail there and back, and a day at each handover port on the way.
            work = lambda rate: max(hd, ship["capacity_st"] / rate)
            cycle = work(r_o) + 2 * sail + (len(sea) - 1) * hd + work(r_d)
            # Each load goes on the first ship free; the last load's start is the ships' wait.
            free = [t for t, _ in fleet]
            heapq.heapify(free)
            last = 0.0
            for _ in range(loads):
                t = heapq.heappop(free)
                last = max(last, t)
                heapq.heappush(free, t + cycle)
            row["days"] = round(last, 1)
            first_in = fleet[0][0] if fleet else 0.0
            # The first arrival already allows a day in each port. A port works all the cargo through it,
            # every class, from D+0; this pool's own cargo can't start before its first ship is there.
            port = lambda total, own, rate: max(max(0.0, (total - rate * hd) / rate),
                                                first_in + max(0.0, (own - rate * hd) / rate))
            waits = {"ships": last, "origin": port(at_origin[o], tons, r_o), "goal": port(at_goal[d], tons, r_d)}
            key = max(waits, key=waits.get)
            late_ships = first_in > 0 and key != "ships" and waits[key] > max(
                0.0, ((at_origin[o] - r_o * hd) / r_o) if key == "origin" else (at_goal[d] - r_d * hd) / r_d)
            limit = None
            if waits[key] > 0:
                if (key == "ships" and loads <= count) or late_ships:
                    limit = {"kind": "ships_arriving", "type": stype, "name": ship["name"], "at": ports[o]["name"],
                             "from": sorted({came for t, came in fleet[:loads] if came})}
                elif key == "ships":
                    limit = {"kind": "ships", "type": stype, "name": ship["name"], "count": count, "loads": loads}
                else:
                    p, rate, work_ = (o, r_o, "loading") if key == "origin" else (d, r_d, "discharging")
                    limit = {"kind": "port", "port_id": p, "place": ports[p]["name"], "work": work_,
                             "demand_st": (at_origin if key == "origin" else at_goal)[p], "rate_st_per_day": rate}
            for _, _, c, *_ in members:
                c.update(closure=round(c["days"] + waits[key], 1), lift=lift, limit=limit)

    def _planes_at(self, P, carrier, field, groups, arrivals):
        """Each aircraft of the type: (the day it can start at the loading airfield, where it came from)."""
        key = (carrier, field)
        if key not in arrivals:
            air, kt, out = P.air, P.air_rules["aircraft"][carrier]["cruise_kt"], []
            for g in groups[carrier]:
                if g["at"] in (None, field):
                    fly, came = 0.0, None
                else:
                    fly, came = great_circle_nm(air.coords[g["at"]], air.coords[field]) / kt / 24, air.name(g["at"])
                out += [(g["ready"] + fly, came)] * g["count"]
            arrivals[key] = sorted(out)
        return arrivals[key]

    def _close_air(self, P, entries, legs, groups, arrivals, u):
        air = P.air
        pools = {}
        for cid, tons, c in entries:
            for carrier in {legs[k]["carrier"] for k in c["legs"] if legs[k]["mode"] == "air"}:
                flights = [legs[k] for k in c["legs"] if legs[k]["mode"] == "air" and legs[k]["carrier"] == carrier]
                pools.setdefault((carrier, tuple(c["legs"])), []).append((cid, tons, c, flights))
        if entries:
            u["figures"].add("air:handling.airfield_hours")
        waits = {}               # cell id -> (days, limit, lift, cell) of the slowest carrier in it
        for (carrier, _), members in pools.items():
            plane = self.aircraft[carrier]
            tons = sum(m[1] or 0 for m in members)
            flights = members[0][3]
            sorties = math.ceil(tons / plane["planning_payload_st"]) if tons else 0
            fly_h = sum(x["hours"] for x in flights)
            o, d = flights[0]["from"], flights[-1]["to"]
            fleet = self._planes_at(P, carrier, o, groups, arrivals)
            each = 24 / (2 * fly_h + 2 * plane["ground_hours"])        # sorties a day one aircraft flies
            types = {"origin": air.fields[o]["airport_type"], "goal": air.fields[d]["airport_type"]}
            mog = {k: self.mog.get(t, 1) * 24 / plane["ground_hours"] for k, t in types.items()}
            cap_key = min(mog, key=mog.get)
            u["figures"].update({"lift:aircraft.%s.planning_payload_st" % carrier, "lift:aircraft.%s.ground_hours" % carrier,
                                 "lift:aircraft.%s.count" % carrier, "air:aircraft.%s.cruise_kt" % carrier})
            u["figures"].update("lift:airfields.working_mog." + t for t in types.values() if t in self.mog)
            lift = {"kind": "aircraft", "type": carrier, "name": plane["name"], "count": len(fleet), "sorties": sorties,
                    "planning_payload_st": plane["planning_payload_st"], "pool_st": tons}
            row = {"kind": "aircraft", "type": carrier, "name": plane["name"], "classes": [m[0] for m in members],
                   "count": len(fleet), "sorties": sorties, "pool_st": tons, "sorties_per_day": None, "days": None,
                   "bound": None}
            u["usage"].append(row)
            if sorties and not fleet:
                wait, limit = None, {"kind": "no_aircraft", "type": carrier, "name": plane["name"]}
            elif sorties <= 1:
                wait, limit = (fleet[0][0] if fleet else 0.0), None
            else:
                # The first sortie leaves when the first aircraft is there; the rest at the rate of the aircraft
                # there so far, capped by working MOG, as more join.
                t, left, i, bound = fleet[0][0], sorties - 1.0, 0, "aircraft"
                while True:
                    while i < len(fleet) and fleet[i][0] <= t + 1e-12:
                        i += 1
                    rate = min(i * each, mog[cap_key])
                    bound = "aircraft" if i * each < mog[cap_key] else cap_key
                    nxt = fleet[i][0] if i < len(fleet) else math.inf
                    if t + left / rate <= nxt:
                        t += left / rate
                        break
                    left -= (nxt - t) * rate
                    t = nxt
                wait = t
                lift["sorties_per_day"] = row["sorties_per_day"] = round(rate, 1)
                row["days"], row["bound"] = round(wait, 1), bound
                if fleet[0][0] > 0 and wait == fleet[0][0]:
                    limit = {"kind": "aircraft_arriving", "type": carrier, "name": plane["name"], "at": air.name(o),
                             "from": [fleet[0][1]] if fleet[0][1] else []}
                elif bound == "aircraft":
                    limit = {"kind": "aircraft", "type": carrier, "name": plane["name"], "count": i,
                             "sorties_per_day": round(rate, 1), "from": sorted({came for _, came in fleet[:i] if came})}
                else:
                    limit = {"kind": "mog", "place": air.name(o if bound == "origin" else d), "type": carrier,
                             "name": plane["name"], "sorties_per_day": round(rate, 1)}
            if sorties == 1 and fleet and fleet[0][0] > 0:
                limit = {"kind": "aircraft_arriving", "type": carrier, "name": plane["name"], "at": air.name(o),
                         "from": [fleet[0][1]] if fleet[0][1] else []}
            for _, _, c, _ in members:
                prev = waits.get(id(c))
                if prev is None or prev[0] is not None and (wait is None or wait > prev[0]):
                    waits[id(c)] = (wait, limit, lift, c)
        for wait, limit, lift, c in waits.values():
            c.update(closure=None if wait is None else round(c["days"] + wait, 1), lift=lift, limit=limit)
