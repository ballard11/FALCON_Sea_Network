"""
The planner screen's comparisons (Planner screen spec, step 2). Python 3, stdlib only.

    from comparisons import compare
    result = compare(planner, "mode", scenario)

A scenario is one move in one situation:

    {"origin": port_id, "goal": port_id, "cargo": [{"class": "I", "short_tons": 500}, ...],
     "denial": [DenialArea], "wez": [DenialArea], "countries": [handover country names] | None}

A lens turns the scenario into the variants to compare: the columns of a grid
whose rows are the classes of supply. Every cell has the same shape, whatever
the lens, so the screen draws any lens the same way:

    {"available": bool, "why": text or None,     why it can't go, or why it goes the way it does
     "days": float or None,                      port to port, handling included
     "mode": "sea" | "air" | "air_sea" | "sea_air",
     "plan": "commercial" | "military" | "commercial_military" | ...,
     "legs": ["mode|carrier|from|to", ...],      keys into the result's "legs"
     "handover": {...} or None, "transfer": {...} or None,
     "sorties": {"commercial": n, "military": n},
     "flags": [{"level", "code", "name", "text"}],
     "alt": {"mode", "days"} or None}            the other way there, when a lens shows only the soonest

A lens compares; it never ranks or picks. The screen marks which cells make the
arrive-by date. To add a comparison, write lens(planner, scenario) returning
{"columns": [{"id", "label", "sub"}], "rows": [{"class", "name", "short_tons",
"cells": {column_id: cell}}], "legs": {key: leg}} and add it to LENSES.
"""
import math

from cargo_movement import FLAG_NAMES
from sea_routing import great_circle_nm

POD_RADIUS_NM = 300          # alternate ports of debarkation: within this distance of the destination ...
POD_ALTERNATES = 3           # ... the nearest few Medium or Large harbors
POD_SIZES = ("Medium", "Large")


def leg_key(leg):
    return "%s|%s|%s|%s" % (leg["mode"], leg["carrier"], leg["from"], leg["to"])


def _flags(flags):
    return [dict(f, name=FLAG_NAMES.get(f["code"], f["code"])) for f in flags]


def _ways(P, s, goal):
    """Each class's way by sea and way by air from the scenario's origin to goal:
    ([(item, sea_cell, air_cell)], legs). Both use the planner's own rules."""
    g, den, wez, countries = P.g, s["denial"], s["wez"], s["countries"]
    out = P.plan(s["origin"], goal, s["cargo"], den, wez, countries, "economic")
    dc = g.links_in_areas(den) if den else frozenset()
    closed = {"military": dc, "commercial": dc | (g.links_in_areas(wez) if wez else frozenset())}
    legs, full = {}, {leg_key(x): x for x in out["legs"]}
    payload = {k: v["payload_st"] for k, v in P.air_rules["aircraft"].items() if not k.startswith("_")}

    def keep(ls):
        for x in ls:
            legs[leg_key(x)] = x
        return [leg_key(x) for x in ls]

    direct, cache, rows = {}, {}, []
    for item, c in zip(s["cargo"], out["classes"]):
        blocked = c["plan"] == "blocked"
        sea = {"available": not blocked, "why": out["blocked"] if blocked else None, "days": None if blocked else c["days"],
               "mode": "sea", "plan": c["plan"], "legs": keep([full[leg_key(x)] for x in c["legs"]]),
               "handover": c["handover"], "transfer": None, "sorties": {}, "flags": _flags(c["flags"]), "alt": None}
        opt, reason = P.air_way(out, c["class"], den, wez, countries, closed, direct, cache)
        if opt is None:
            air = {"available": False, "why": reason[0].upper() + reason[1:] + ".", "days": None, "mode": "air",
                   "plan": None, "legs": [], "handover": None, "transfer": None, "sorties": {}, "flags": [], "alt": None}
        else:
            tons = item.get("short_tons") or 0
            sorties = {}
            for x in opt["legs"]:
                if x["mode"] == "air" and tons:
                    sorties[x["carrier"]] = math.ceil(tons / payload[x["carrier"]])
            why = None
            if opt["mode"] != "air":
                why = "No flight the whole way: %s." % opt["why"]
            elif P.air_rules["classes"][c["class"]] == "military":
                why = "Military aircraft only."
            air = {"available": True, "why": why, "days": opt["days"], "mode": opt["mode"], "plan": opt["plan"],
                   "legs": keep(opt["legs"]), "handover": opt.get("handover"), "transfer": opt.get("transfer"),
                   "sorties": sorties, "flags": [], "alt": None}
        rows.append((item, sea, air))
    return rows, legs


def _row(P, item, cells):
    cid = item["class"]
    return {"class": cid, "name": P.classes[cid]["name"], "short_tons": item.get("short_tons"), "cells": cells}


def by_mode(P, s):
    """By sea against by air (air only, or air then sea when no flight reaches)."""
    rows, legs = _ways(P, s, s["goal"])
    return {"columns": [{"id": "sea", "label": "By sea", "sub": "port to port"},
                        {"id": "air", "label": "By air", "sub": "or air, then sea"}],
            "rows": [_row(P, item, {"sea": sea, "air": air}) for item, sea, air in rows], "legs": legs}


def alternate_ports(P, s, radius_nm=POD_RADIUS_NM, n=POD_ALTERNATES):
    """The nearest Medium or Large harbors within radius_nm of the destination,
    outside the areas of denial: [(port_id, nm from the destination)]."""
    g, goal = P.g, s["goal"]
    here = g.coords[goal]
    near = []
    for p, port in g.ports.items():
        if p in (goal, s["origin"]) or port["harbor_size"] not in POD_SIZES:
            continue
        d = great_circle_nm(here, g.coords[p])
        if d <= radius_nm and not g.port_in_areas(p, s["denial"]):
            near.append((d, p))
    return [(p, d) for d, p in sorted(near)[:n]]


def _soonest(sea, air):
    ways = [w for w in (sea, air) if w["available"]]
    if not ways:
        return dict(sea, why="%s %s" % (sea["why"] or "No sea route.", air["why"] or ""))
    best = min(ways, key=lambda w: w["days"])
    other = air if best is sea else sea
    return dict(best, alt={"mode": other["mode"], "days": other["days"]} if other["available"] else None)


def by_port(P, s):
    """The destination against nearby ports of debarkation. Each cell is the
    soonest way to that port; days stop there (no onward land leg yet)."""
    g, goal = P.g, s["goal"]
    ports = [(goal, 0.0)] + alternate_ports(P, s)
    columns, cells, legs = [], {}, {}
    for p, d in ports:
        columns.append({"id": p, "label": g.ports[p]["name"],
                        "sub": "the destination" if p == goal else "%s nm away" % format(round(d), ",")})
        rows, more = _ways(P, s, p)
        legs.update(more)
        for item, sea, air in rows:
            cells.setdefault(item["class"], (item, {}))[1][p] = _soonest(sea, air)
    return {"columns": columns, "rows": [_row(P, item, c) for item, c in cells.values()], "legs": legs}


LENSES = {
    "mode": {"label": "Mode", "run": by_mode,
             "note": "By sea against by air. By air flies the whole way, or flies part way and goes on by sea "
                     "when no flight reaches the far end."},
    "pod": {"label": "Port of debarkation", "run": by_port,
            "note": "The destination against the nearest %d Medium or Large harbors within %d nm. Each cell is the "
                    "soonest way to that port." % (POD_ALTERNATES, POD_RADIUS_NM)},
}


def compare(P, lens, s):
    if lens not in LENSES:
        raise ValueError("Unknown comparison %r; use one of %s." % (lens, ", ".join(LENSES)))
    return dict(LENSES[lens]["run"](P, s), lens=lens)
