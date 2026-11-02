"""
Cargo scenarios: LOGCAP cargo moved through the sea-network scenarios, with a
report on who carries each class of supply, where it changes hands, and the
bottlenecks each class runs into.

    python run_cargo_scenarios.py                              every cargo_scenarios/*.json
    python run_cargo_scenarios.py cargo_scenarios/C05_*.json   just these
    python run_cargo_scenarios.py --out reports/cargo/demo     choose the report folder
    python run_cargo_scenarios.py --no-png                     SVG snapshots only

Each file in cargo_scenarios/ builds on one route scenario in scenarios/ (its
"base") or names its own ports, adds weapons engagement zones, cargo and when
it is needed, and says what should happen (see cargo_scenarios/README.md). The run asks cargo_movement.py for a plan,
checks it against the scenario's expectations and against rules every plan
must keep, and writes

    reports/cargo/<date>_<time>/report.html      open in a browser; one file, maps included
    reports/cargo/<date>_<time>/movements.csv    one row per scenario and class, for planners
    reports/cargo/<date>_<time>/results.json     the same results, for other tools
    reports/cargo/<date>_<time>/snapshots/       two maps per scenario, SVG (and PNG)

The exit code is 0 when every check passes and 1 when any fails.
Python 3, stdlib only; PNGs need Edge or Chrome, and are skipped without.
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from html import escape

import air_routing
import cargo_capability
import cargo_movement
import map_snapshot
import run_scenarios as routes
import server
from run_scenarios import ScenarioError, check, in_range, range_text
from sea_routing import densify, great_circle_nm

HERE = os.path.dirname(os.path.abspath(__file__))
SCENARIOS = os.path.join(HERE, "cargo_scenarios")
REPORTS = os.path.join(HERE, "reports", "cargo")
GRAPH = server.GRAPH
PLANNER = server.PLANNER
CLASSES = list(PLANNER.classes)

PLANS = {"commercial": "Commercial the whole way", "commercial_military": "Commercial, then military",
         "military_commercial": "Military, then commercial", "military": "Military the whole way",
         "blocked": "Blocked"}


def plan_text(plan):
    """Any plan id as words: "commercial_military_commercial" -> "Commercial, then military, then commercial"."""
    return PLANS.get(plan) or ", then ".join(plan.split("_")).capitalize()


LEVELS, FLAG_NAMES = cargo_movement.LEVELS, cargo_movement.FLAG_NAMES
INPUTS = (cargo_capability.PORTS, cargo_capability.RULES, cargo_capability.OVERRIDES, cargo_movement.RULES,
          cargo_movement.AIR_RULES, air_routing.AIRFIELDS)
AIR = PLANNER.air


def coords(place):
    """A port's or an airfield's (lon, lat)."""
    return GRAPH.coords.get(place) or AIR.coords[place]


# ----------------------------------------------------------------- loading
def load_bases():
    bases = {}
    for path in sorted(glob.glob(os.path.join(routes.SCENARIOS, "*.json"))):
        s = routes.load_scenario(path)
        bases[s["id"]] = s
    return bases


def load_scenario(path, bases):
    with open(path, encoding="utf-8") as f:
        s = json.load(f)
    for key in ("id", "name") + (() if "from" in s and "to" in s else ("base",)):
        if key not in s:
            raise ScenarioError("%s has no %r." % (os.path.basename(path), key))
    if s.get("base") and s["base"] not in bases:
        raise ScenarioError("Base scenario %s is not in scenarios/." % s["base"])
    base = bases.get(s.get("base"), {"areas": []})
    for key in ("from", "to", "areas"):
        if key not in s:
            s[key] = base[key]             # the move and its areas of denial come from the base
    s.setdefault("base", "")
    s["file"] = os.path.relpath(path, HERE).replace(os.sep, "/")
    s.setdefault("purpose", "")
    s.setdefault("wez", [])
    s.setdefault("cargo", None)
    s.setdefault("handover_countries", None)
    s.setdefault("priority", "economic")
    s.setdefault("expect", {})
    return s


# ------------------------------------------------------------------ checks
def by_class(spec, present):
    """{"*": a, "V": b} -> {class: value}; "*" covers every class in the
    scenario's cargo that is not named."""
    out = {c: spec["*"] for c in present} if "*" in spec else {}
    out.update({c: v for c, v in spec.items() if c != "*"})
    return out


def grouped(values):
    """{class: text} -> "I, II: text; V: other", classes in order."""
    groups = {}
    for c in CLASSES:
        if c in values:
            groups.setdefault(values[c], []).append(c)
    return "; ".join("%s: %s" % (", ".join(cs), v) for v, cs in groups.items())


def expectation_checks(plan, expect):
    out, rows = [], {c["class"]: c for c in plan["classes"]}
    known = {"blocked_contains", "mode", "plan", "handover", "military_nm", "bottleneck_classes",
             "commercial_extra_nm"}
    for key in sorted(set(expect) - known):
        out.append(check("Unknown expectation %r" % key, "one of: " + ", ".join(sorted(known)), key, False))
    if "blocked_contains" in expect:
        reason = plan.get("blocked") or ""
        out.append(check("The blocked reason says why", "mentions %r" % expect["blocked_contains"],
                         reason or "not blocked", expect["blocked_contains"].lower() in reason.lower()))
    if "mode" in expect:
        want = by_class(expect["mode"], rows)
        got = {c: rows[c]["mode"] for c in want if c in rows}
        out.append(check("Sea, air, or air then sea, for each class", grouped(want), grouped(got) or "no such classes", want == got))
    if "plan" in expect:
        want = {c: plan_text(v) for c, v in by_class(expect["plan"], rows).items()}
        got = {c: plan_text(rows[c]["plan"]) for c in want if c in rows}
        out.append(check("Who carries each class", grouped(want), grouped(got) or "no such classes", want == got))
    if "handover" in expect:
        want = {c: v or "none" for c, v in by_class(expect["handover"], rows).items()}
        got = {}
        for c in want:
            h = rows[c]["handover"] if c in rows else None
            got[c] = "none" if not h else h["id"] if want[c].startswith("WPI-") or want[c] in AIR.fields \
                else h["name"]
        out.append(check("Handover port for each class", grouped(want), grouped(got), want == got))
    if "military_nm" in expect:
        for c, rng in by_class(expect["military_nm"], rows).items():
            nm = rows[c]["military_nm"] if c in rows else None
            out.append(check("Class %s military distance" % c, range_text(rng),
                             "%s nm" % format(round(nm), ",") if nm is not None else "no such class", in_range(nm, rng)))
    if "bottleneck_classes" in expect:
        want = sorted(expect["bottleneck_classes"], key=CLASSES.index)
        got = [c["class"] for c in plan["classes"] if c["bottlenecks"]]
        out.append(check("Classes with a bottleneck", ", ".join(want) or "none", ", ".join(got) or "none", want == got))
    if "commercial_extra_nm" in expect:
        extra = plan.get("commercial_extra_nm")
        out.append(check("Extra distance the WEZ adds for commercial carriers", range_text(expect["commercial_extra_nm"]),
                         "%s nm" % format(round(extra), ",") if extra is not None else "none",
                         in_range(extra, expect["commercial_extra_nm"])))
    return out


def invariant_checks(plan, denial, wez, countries):
    """Rules every plan must keep, whatever the scenario."""
    out = []
    if plan["blocked"]:
        return out
    legs = {cargo_movement._key(x): x for x in plan["legs"]}
    broken = []
    for c in plan["classes"]:
        stops = [plan["origin"]] + [x["to"] for x in c["legs"]]
        if [x["from"] for x in c["legs"]] != stops[:-1] or stops[-1] != plan["goal"]:
            broken.append(c["class"])
    out.append(check("Each class's legs run from the origin to the destination, end to end",
                     "every class", "broken: " + ", ".join(broken) if broken else "every class", not broken))

    def entering(carrier, areas):
        bad = []
        for x in legs.values():
            if x["carrier"] != carrier or not areas:
                continue
            pts = densify([tuple(p) for p in x["line"]], routes.AREA_SAMPLE_NM)
            if any(a.crosses(p, q) for a in areas for p, q in zip(pts, pts[1:])):
                bad.append("%s to %s" % (x["from_name"], x["to_name"]))
        return bad
    bad = entering("commercial", wez + denial)
    out.append(check("No commercial leg enters a WEZ or an area of denial",
                     "none, checked every %g nm" % routes.AREA_SAMPLE_NM, ", ".join(bad) or "none enter", not bad))
    bad = entering("military", denial)
    out.append(check("No military leg enters an area of denial",
                     "none, checked every %g nm" % routes.AREA_SAMPLE_NM, ", ".join(bad) or "none enter", not bad))

    statuses = PLANNER.rules["handover"]["statuses"]
    codes = {PLANNER.air_rules["airfields"]["country_codes"].get(n) for n in countries}
    bad = []
    for c in plan["classes"]:
        h = c["handover"]
        if not h:
            continue
        inside = any(a.contains(coords(h["id"])) for a in wez + denial)
        if h["status"] == "airfield":
            wrong = (AIR.runway[h["id"]] < max(p["min_runway_ft"] for p in planes())
                     or (countries and AIR.fields[h["id"]]["country_code"] not in codes))
        else:
            wrong = (h["status"] not in statuses
                     or (countries and GRAPH.ports[h["id"]]["country"] not in countries))
        if inside or wrong:
            bad.append("%s at %s" % (c["class"], h["name"]))
    out.append(check("Handover ports are outside every area and in a handover country; sea ones are rated "
                     + " or ".join(s.capitalize() for s in statuses) + " for their class, air ones have a runway "
                     "for both aircraft", "all", ", ".join(bad) or "all", not bad))

    flights = [x for x in legs.values() if x["mode"] == "air"]
    if flights:
        bad = []
        for x in flights:
            plane = PLANNER.air_rules["aircraft"][x["carrier"]]
            hops = x["hops"]
            if (any(great_circle_nm(AIR.coords[a], AIR.coords[b]) > plane["range_nm"] for a, b in zip(hops, hops[1:]))
                    or any(AIR.runway[f] < plane["min_runway_ft"] for f in hops)):
                bad.append("%s to %s" % (x["from_name"], x["to_name"]))
        out.append(check("Every flight is within its aircraft's range, between runways long enough for it",
                         "all", ", ".join(bad) or "all", not bad))

    ends, worst, short = 0.0, 0.0, []
    for x in legs.values():
        ends = max(ends, great_circle_nm(x["line"][0], coords(x["from"])),
                   great_circle_nm(x["line"][-1], coords(x["to"])))
        worst = max(worst, abs(routes.line_nm(x["line"]) - x["length_nm"]) / max(x["length_nm"], 1.0))
        if x["length_nm"] < great_circle_nm(coords(x["from"]), coords(x["to"])) - 0.5:
            short.append("%s to %s" % (x["from_name"], x["to_name"]))
    out.append(check("Every leg starts and ends at its ports or airfields", "within %g nm" % routes.END_TOLERANCE_NM,
                     "%.2f nm off at worst" % ends, ends <= routes.END_TOLERANCE_NM))
    out.append(check("Each leg's drawn line matches its reported length",
                     "within %g%%" % (routes.LENGTH_TOLERANCE * 100), "%.2f%% at worst" % (worst * 100),
                     worst <= routes.LENGTH_TOLERANCE))
    out.append(check("No leg is shorter than the great-circle distance", "none", ", ".join(short) or "none", not short))
    return out


def planes():
    return [v for k, v in PLANNER.air_rules["aircraft"].items() if not k.startswith("_")]


# --------------------------------------------------------------------- run
def run_one(s, snap_dir, browser):
    origin, goal = routes.resolve_port(s["from"]), routes.resolve_port(s["to"])
    try:
        denial, wez = server.parse_areas(s["areas"]), server.parse_areas(s["wez"])
    except server.BadRequest as e:
        raise ScenarioError("Bad area: %s" % e)
    countries = s["handover_countries"] if s["handover_countries"] is not None \
        else PLANNER.rules["handover"]["countries"]
    started = time.perf_counter()
    plan = PLANNER.plan(origin, goal, s["cargo"], denial, wez, countries, s["priority"])
    seconds = time.perf_counter() - started
    checks = expectation_checks(plan, s["expect"]) + invariant_checks(plan, denial, wez, countries)

    closed = GRAPH.links_in_areas(denial) if denial else frozenset()
    title = "%s  ·  %s → %s" % (s["id"], plan["origin_name"], plan["goal_name"])
    sub = "Cargo, %d classes%s%s%s" % (len(plan["classes"]),
                                       ", urgent" if s["priority"] == "urgent" else "",
                                     ", %d WEZ" % len(wez) if wez else ", no WEZ",
                                     ", %d area%s of denial" % (len(denial), "" if len(denial) == 1 else "s")
                                     if denial else "")
    stem = os.path.splitext(os.path.basename(s["file"]))[0]
    snapshots = []
    for name, closeup, label in ((stem, False, "Whole move"),
                                 (stem + "_closeup", True, "Close-up of the WEZ and handover ports" if wez
                                  else "Close-up of the areas" if denial else "Close-up of the destination")):
        svg = map_snapshot.render_cargo(GRAPH, plan, s["areas"], s["wez"], closed, title,
                                        sub + ("  ·  close-up" if closeup else ""), closeup=closeup)
        svg_path = os.path.join(snap_dir, name + ".svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg)
        png = bool(browser) and map_snapshot.svg_to_png(svg_path, os.path.join(snap_dir, name + ".png"), browser)
        snapshots.append({"label": label, "svg": "snapshots/" + name + ".svg",
                          "png": "snapshots/" + name + ".png" if png else None, "_svg": svg})

    return {"id": s["id"], "base": s["base"], "name": s["name"], "purpose": s["purpose"], "file": s["file"],
            "from": {"id": origin, "name": plan["origin_name"], "country": GRAPH.ports[origin]["country"]},
            "to": {"id": goal, "name": plan["goal_name"], "country": GRAPH.ports[goal]["country"]},
            "areas": s["areas"], "wez": s["wez"], "handover_countries": countries,
            "priority": s["priority"], "passed": all(c["passed"] for c in checks), "checks": checks,
            "blocked": plan["blocked"], "handover_side": plan.get("handover_side"),
            "commercial_direct": plan["commercial_direct"], "military_direct": plan["military_direct"],
            "commercial_extra_nm": plan["commercial_extra_nm"],
            "classes": plan["classes"], "routes": plan["routes"],
            "legs": [{k: v for k, v in x.items() if k != "line"} for x in plan.get("legs", [])],
            "seconds": round(seconds, 3), "snapshots": snapshots}


def failed_run(path, error):
    stem = os.path.splitext(os.path.basename(path))[0]
    return {"id": stem, "base": "", "name": stem, "purpose": "This scenario could not run.",
            "file": os.path.relpath(path, HERE).replace(os.sep, "/"),
            "from": {"id": "", "name": "?", "country": ""}, "to": {"id": "", "name": "?", "country": ""},
            "areas": [], "wez": [], "handover_countries": [], "priority": "economic", "passed": False,
            "checks": [check("The scenario runs", "no errors", str(error), False)], "blocked": str(error),
            "handover_side": None, "commercial_direct": None, "military_direct": None,
            "commercial_extra_nm": None, "classes": [], "routes": [], "legs": [], "seconds": 0, "snapshots": []}


def inputs():
    out = []
    for path in INPUTS:
        with open(path, "rb") as f:
            out.append({"file": os.path.relpath(path, HERE).replace(os.sep, "/"),
                        "sha256": hashlib.sha256(f.read()).hexdigest()[:16]})
    return out


# ------------------------------------------------------------------ output
def full_leg(r, x):
    return next(l for l in r["legs"] if cargo_movement._key(l) == cargo_movement._key(x))


def leg_text(r, x):
    leg = full_leg(r, x)
    return "%s by %s: %s to %s" % (leg["carrier_label"], leg["mode"], leg["from_name"], leg["to_name"])


def write_csv(path, results):
    cols = ["scenario", "base", "class_id", "class_name", "route", "short_tons", "priority", "mode",
            "sea_days", "air_days", "sorties", "plan", "transfer_port", "carried_by", "handover_port",
            "handover_country", "handover_status", "handover_options", "backups", "origin_status",
            "destination_status", "commercial_nm", "military_nm", "wez_nm", "days", "military_ton_nm",
            "bottlenecks", "risks", "checks", "flags"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            for c in r["classes"]:
                h = c["handover"] or {}
                w.writerow({"scenario": r["id"], "base": r["base"], "class_id": c["class"],
                            "class_name": c["class_name"], "route": c["route"], "short_tons": c["short_tons"] or "",
                            "priority": c["priority"], "mode": c["mode"], "sea_days": c["sea_days"] or "",
                            "air_days": c["air_days"] or "",
                            "sorties": "; ".join("%d %s" % (n, PLANNER.air_rules["aircraft"][k]["name"])
                                                 for k, n in (c["sorties"] or {}).items()),
                            "plan": plan_text(c["plan"]), "transfer_port": (c["transfer"] or {}).get("name", ""),
                            "carried_by": "; ".join(leg_text(r, x) for x in c["legs"]),
                            "handover_port": h.get("name", ""), "handover_country": h.get("country", ""),
                            "handover_status": h.get("status", ""), "handover_options": c["handover_options"],
                            "backups": c["backups"], "origin_status": c["origin_status"],
                            "destination_status": c["destination_status"], "commercial_nm": c["commercial_nm"],
                            "military_nm": c["military_nm"], "wez_nm": c["wez_nm"], "days": c["days"],
                            "military_ton_nm": c["military_ton_nm"] or "", "bottlenecks": c["bottlenecks"],
                            "risks": c["risks"], "checks": c["checks"],
                            "flags": " | ".join("%s: %s" % (x["level"], x["text"]) for x in c["flags"])})


CSS = routes.CSS + """
.lv { display: inline-block; padding: 0 7px; border-radius: 99px; font-size: 11.5px; font-weight: 700;
      text-transform: uppercase; letter-spacing: .03em; margin-right: 6px; }
.lv-bottleneck { color: var(--fail); background: var(--fail-bg); }
.lv-risk { color: #9a5b00; background: #fdf0d9; }
.lv-check { color: #2457a6; background: #e3edfb; }
.lv-note { color: var(--dim); background: var(--chip); }
@media (prefers-color-scheme: dark) {
  .lv-risk { color: #f0c27a; background: #3a2c14; } .lv-check { color: #9cc2f5; background: #1b2b44; } }
.flags { margin: 0; padding: 0; list-style: none; font-size: 13px; }
.flags li { margin: 0 0 4px; }
.matrix td, .matrix th { text-align: center; white-space: nowrap; }
.matrix td:first-child, .matrix th:first-child, .matrix .what { text-align: left; }
.matrix th, .matrix .what { white-space: normal; }
.cell-bottleneck { background: var(--fail-bg); }
.cell-risk { background: #fdf0d9; }
@media (prefers-color-scheme: dark) { .cell-risk { background: #3a2c14; } }
.carrier { display: inline-block; width: 18px; height: 4px; border-radius: 2px; vertical-align: middle; margin-right: 6px; }
"""
SHORT = {"commercial": "C", "commercial_military": "C→M", "military_commercial": "M→C", "military": "M",
         "blocked": "—"}
MODE = {"sea": "Sea", "air": "Air", "air_sea": "Air, then sea", "sea_air": "Sea, then air", "road": "Road"}


def _flags_html(flags):
    """A route's merged flags: each names the classes it applies to."""
    if not flags:
        return "<span class='meta'>none</span>"
    return "<ul class='flags'>%s</ul>" % "".join(
        "<li><span class='lv lv-%s'>%s</span><b>%s.</b> %s</li>" % (f["level"], f["level"], escape(f["name"]),
                                                                  escape(f["text"])) for f in flags)


def _legs_html(r, route):
    out = []
    for x in route["legs"]:
        leg = full_leg(r, x)
        via = ("by sea, via " + (", ".join(leg["passages"]) or "open water") if leg["mode"] == "sea"
               else "by road" if leg["mode"] == "road"
               else "by air, %s%s" % (leg["aircraft"], ", stopping at " + ", ".join(leg["stops"]) if leg["stops"] else ""))
        out.append("<span class='carrier' style='background:%s'></span><b>%s</b> %s → %s, %s nm%s · %s" % (
            map_snapshot.COMMERCIAL if leg["carrier"] == "commercial" else map_snapshot.MILITARY,
            escape(leg["carrier_label"]), escape(leg["from_name"]), escape(leg["to_name"]), routes._nm(leg["length_nm"]),
            ", %s in WEZ" % routes._nm(leg["wez_nm"]) if leg["wez_nm"] >= 1 else "", escape(via)))
    return "<br>".join(out) or "—"


def _timing_html(route):
    """Days door to door, and for urgent cargo how sea and air compare."""
    out = "%.1f" % route["days"]
    if route["priority"] != "economic":
        sea = route["sea_days"]
        out += "<br><span class='meta'>%s; sea %s%s</span>" % (
            "urgent" if route["priority"] == "urgent" else "partly urgent",
            "%.1f" % sea[0] if sea and sea[0] == sea[1] else "%.1f to %.1f" % tuple(sea) if sea else "blocked",
            "; %s %.1f" % ("air" if route["mode"] == "sea" else MODE[route["mode"]].lower(), route["air_days"])
            if route["air_days"] is not None else "; cannot fly")
    if route["sorties"]:
        out += "<br><span class='meta'>%s</span>" % ", ".join(
            "%d %s sortie%s" % (n, PLANNER.air_rules["aircraft"][k]["name"], "" if n == 1 else "s")
            for k, n in route["sorties"].items())
    return out


def _handover_html(route):
    h = route["handover"]
    if not h:
        return "—"
    rated = "; ".join("%s for %s" % (st.capitalize(), ", ".join(cs)) for st, cs in route["handover_status"].items())
    return "%s, %s<br><span class='meta'>%s</span>" % (escape(h["name"]), escape(h["country"]), escape(rated))


def _worst(flags):
    return next((lv for lv in LEVELS if any(f["level"] == lv for f in flags)), None)


def class_matrix(results):
    """Rows are classes, columns scenarios: who carries it, how far the
    military takes it, and the worst flag it raised."""
    head = "".join("<th><a href='#%s'>%s</a></th>" % (escape(r["id"]), escape(r["id"])) for r in results)
    rows = []
    for cid in CLASSES:
        cells, hit, worst_nm = [], 0, 0.0
        for r in results:
            c = next((x for x in r["classes"] if x["class"] == cid), None)
            if not c:
                cells.append("<td class='meta'>·</td>")
                continue
            lv = _worst(c["flags"])
            hit += bool(c["bottlenecks"])
            worst_nm = max(worst_nm, c["military_nm"])
            tip = " | ".join("%s: %s" % (f["level"], f["text"]) for f in c["flags"]) or "no flags"
            nm = (" %s" % format(round(c["military_nm"]), ",")) if c["military_nm"] else ""
            cells.append("<td class='cell-%s' title='%s'>%s%s%s</td>" % (
                lv or "none", escape(tip, quote=True), {"air": "Air ", "air_sea": "Air+sea ", "sea_air": "Sea+air "}.get(c["mode"], ""),
                SHORT.get(c["plan"], c["plan"].replace("commercial", "C").replace("military", "M").replace("_", "→")), nm))
        rows.append("<tr><td>%s %s</td>%s<td class='num'>%d</td><td class='num'>%s</td></tr>" % (
            cid, escape(PLANNER.classes[cid]["name"]), "".join(cells), hit, format(round(worst_nm), ",")))
    return ("<table class='matrix'><tr><th>Class</th>%s<th class='num'>Scenarios with a bottleneck</th>"
            "<th class='num'>Longest military leg, nm</th></tr>%s</table>" % (head, "".join(rows)))


def flag_summary(results):
    """How often each kind of flag comes up, by class."""
    codes = {}
    for r in results:
        for c in r["classes"]:
            for f in c["flags"]:
                if f["level"] != "note":
                    codes.setdefault((f["level"], f["code"]), {}).setdefault(c["class"], set()).add(r["id"])
    rows = []
    for (lv, code), per in sorted(codes.items(), key=lambda kv: (LEVELS.index(kv[0][0]), kv[0][1])):
        rows.append("<tr><td><span class='lv lv-%s'>%s</span></td><td class='what'>%s</td>%s</tr>" % (
            lv, lv, escape(FLAG_NAMES.get(code, code)),
            "".join("<td class='num'>%s</td>" % (len(per[c]) if c in per else "") for c in CLASSES)))
    return ("<table class='matrix'><tr><th>Level</th><th class='what'>What</th>%s</tr>%s</table>"
            % ("".join("<th>%s</th>" % c for c in CLASSES), "".join(rows)))


def write_report(out_dir, run, results, prev_when, prev):
    passed = sum(r["passed"] for r in results)
    rows = []
    for r in results:
        plans = ["%d. %s, %s%s (%s)" % (x["route"], MODE[x["mode"]], plan_text(x["plan"]).lower(),
                                        ", via " + x["handover"]["name"] if x["handover"] else "",
                                        ", ".join(x["classes"])) for x in r["routes"]]
        hand = sorted({c["handover"]["name"] for c in r["classes"] if c["handover"]})
        change = "new"
        before = prev.get(r["id"])
        if before:
            old = {c["class"]: (c["plan"], (c.get("handover") or {}).get("id")) for c in before.get("classes", [])}
            new = {c["class"]: (c["plan"], (c.get("handover") or {}).get("id")) for c in r["classes"]}
            changed = [c for c in new if old.get(c) != new[c]]
            change = "same" if not changed else "changed: " + ", ".join(changed)
            if before.get("passed") != r["passed"]:
                change += ", was " + ("PASS" if before.get("passed") else "FAIL")
        rows.append("<tr><td><a href='#%s'>%s</a></td><td>%s</td><td>%s → %s</td><td>%s</td><td>%s</td>"
                    "<td>%s</td><td>%s</td><td>%s</td></tr>" % (
                        escape(r["id"]), escape(r["id"]), escape(r["name"]), escape(r["from"]["name"]),
                        escape(r["to"]["name"]),
                        "<br>".join(escape(p) for p in plans) or "—",
                        escape(", ".join(hand) or "—"),
                        escape(", ".join(c["class"] for c in r["classes"] if c["bottlenecks"]) or "none"),
                        escape(change), routes._badge(r["passed"])))

    sections = []
    for r in results:
        route_rows = "".join(
            "<tr><td>%d</td><td>%s</td><td class='num'>%s</td><td>%s</td><td>%s</td><td class='num'>%s</td>"
            "<td>%s</td></tr>" % (
                x["route"], "<br>".join("%s %s" % (c, escape(PLANNER.classes[c]["name"])) for c in x["classes"]),
                format(x["short_tons"], ",") if x["short_tons"] else "—",
                "<b>%s</b> · %s<br>" % (MODE[x["mode"]], escape(plan_text(x["plan"])))
                + ("<span class='meta'>changes from %s at %s</span><br>" % (
                    "air to sea" if x["mode"] == "air_sea" else "sea to air", escape(x["transfer"]["name"]))
                   if x["transfer"] else "") + _legs_html(r, x),
                _handover_html(x), _timing_html(x), _flags_html(x["flags"]))
            for x in r["routes"])
        check_rows = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                escape(c["check"]), escape(str(c["expected"])), escape(str(c["actual"])), routes._badge(c["passed"]))
            for c in r["checks"])
        chips = ["<span>%s</span>" % escape(t) for t in (
            "From %s (%s, %s)" % (r["from"]["name"], r["from"]["country"], r["from"]["id"]),
            "To %s (%s, %s)" % (r["to"]["name"], r["to"]["country"], r["to"]["id"]),
            "Builds on %s" % r["base"])]
        chips += ["<span>WEZ: %s</span>" % escape(routes._area_text(a)) for a in r["wez"]]
        chips += ["<span>Denial: %s</span>" % escape(routes._area_text(a)) for a in r["areas"]]
        if r["commercial_extra_nm"]:
            chips.append("<span>The WEZ adds %s nm to the commercial route</span>" % routes._nm(r["commercial_extra_nm"]))
        urgent = [c["class"] for c in r["classes"] if c["priority"] == "urgent"]
        if urgent:
            chips.append("<span>Urgent: %s</span>" % ("every class" if len(urgent) == len(r["classes"])
                                                     else ", ".join(urgent)))
        chips.append("<span title='%s'>Handover countries: %d</span>" % (
            escape(", ".join(r["handover_countries"]), quote=True), len(r["handover_countries"])))
        maps = "".join('<figure><figcaption>%s</figcaption>%s</figure>' % (
            escape(m["label"]), m["_svg"].replace('width="%d" height="%d"' % (map_snapshot.WIDTH, map_snapshot.HEIGHT),
                                                  'class="map" width="100%" height="auto"', 1))
            for m in r["snapshots"])
        files = " · ".join('<a href="%s">%s</a>' % (m[kind], m[kind].rsplit("/", 1)[1])
                           for m in r["snapshots"] for kind in ("svg", "png") if m[kind]) or "none"
        sections.append("""
<section class="card" id="%s">
  <div class="head"><h2>%s · %s</h2><div>%s</div></div>
  <p class="purpose">%s</p>
  <div class="chips">%s</div>
  %s
  <h3>Routes</h3>
  <div class="wrap"><table><tr><th>Route</th><th>Classes</th><th class="num">Short tons</th><th>Carried by</th>
    <th>Handover</th><th class="num">Days, door to door</th><th>Bottlenecks, risks and checks</th></tr>%s</table></div>
  <h3>Checks</h3>
  <div class="wrap"><table><tr><th>Check</th><th>Expected</th><th>Actual</th><th></th></tr>%s</table></div>
  <p class="meta">Scenario file %s · snapshots %s · %.2f s</p>
</section>""" % (escape(r["id"]), escape(r["id"]), escape(r["name"]), routes._badge(r["passed"]), escape(r["purpose"]),
                 "".join(chips), maps, route_rows, check_rows, escape(r["file"]), files, r["seconds"]))

    hand = PLANNER.rules["handover"]
    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cargo Scenarios</title><style>%s</style></head>
<body><main>
<h1>FALCON sea network · cargo scenario report</h1>
<div class="meta">Run on %s · code %s · %d scenarios · %s</div>
<div class="meta">Inputs (SHA-256, first 16 hex digits): %s · %d port overrides applied</div>
<div class="tally"><div><b>%d</b>scenarios</div><div><b class="%s" style="background:none">%d</b>passed</div>
<div><b class="%s" style="background:none">%d</b>failed</div><div><b>%d</b>checks</div></div>

<section class="card">
<h2>How cargo moves</h2>
<p class="purpose">Every leg is carried by a <b>%s</b> or by the <b>%s</b>. Commercial carriers never enter a
weapons engagement zone (WEZ); the military may. Neither enters an area of denial. When commercial carriers cannot
reach the destination, they carry each class to its <b>handover port</b>: the port outside every area, in the handover
countries, rated %s for that class, with the shortest military leg to the destination. The military takes it from
there. A handover is used only if it saves the military at least %d nm; otherwise the military carries it the whole
way. When the origin is the end inside a WEZ, the same happens in reverse. Port ratings come from the NGA World Port
Index through <code>cargo_capability.py</code>, with the sourced overrides in <code>cargo/port_overrides.csv</code>.
Unknown in the World Port Index means no information, not No.</p>
<p class="purpose">Each class is <b>economic</b> (cheapest) or <b>urgent</b> (fastest). Economic cargo goes <b>by
sea</b>. Urgent cargo takes the soonest way it may: by sea; <b>by air</b>, between the airfields nearest the two
ports; or, when no flight reaches the far end, <b>by air, then sea</b>: it flies to the airfield nearest a port close
to the destination and goes on by sea from there. Commercial aircraft follow the sea rules: they stay out of a WEZ
and hand over to military airlift at an airfield outside it. Flights are great circles within each aircraft's range,
refuelling at airfields on the way when needed (<code>cargo/air_rules.json</code>). Bulk fuel never flies, so urgent
fuel is a bottleneck. There is no cost model yet: the sortie count is the cost of flying to look at.</p>
<p class="meta"><b>C</b> commercial the whole way · <b>C→M</b> commercial, then military · <b>M→C</b> military, then
commercial · <b>M</b> military the whole way · <b>Air</b> in front when it flies · the number is the military's
distance in nm. A red cell has a bottleneck, an amber cell a risk; hover for the details. Quantities are
illustrative, not planning factors.</p>
</section>

<section class="card wrap"><h2>Bottlenecks by class of supply</h2>%s</section>
<section class="card wrap"><h2>What the flags are, by class</h2><p class="meta">The number of scenarios in which
each class raised each flag.</p>%s</section>
<section class="card wrap"><table>
<tr><th>ID</th><th>Scenario</th><th>Move</th><th>Routes, and the classes on each</th><th>Handover ports</th><th>Bottleneck classes</th>
<th>vs last run</th><th></th></tr>
%s</table></section>
%s
<p class="meta">Sea distances are along the Eurostat MARNET shipping-lane network (100 km); air distances are
great circles between OurAirports airfields. Days are door to door: ships at a nominal %g knots (commercial) and
%g knots (military) plus %g day in each port where the cargo is worked; aircraft at their cruise speeds plus %g hours
at each airfield where the cargo is worked and %g at each refuelling stop. Air days assume enough aircraft to fly
every sortie at once. These allowances, WEZs, handover countries and cargo quantities are illustrative inputs for
the demo.</p>
</main></body></html>""" % (
        CSS, escape(run["started"]), escape(run["code"]), len(results),
        "compared with the run of %s" % escape(prev_when) if prev_when else "no earlier run to compare with",
        " · ".join("%s <code>%s</code>" % (escape(i["file"]), i["sha256"]) for i in run["inputs"]), run["overrides"],
        len(results), "pass", passed, "fail" if passed < len(results) else "", len(results) - passed,
        sum(len(r["checks"]) for r in results),
        escape(PLANNER.rules["carriers"]["commercial"]["label"]), escape(PLANNER.rules["carriers"]["military"]["label"]),
        " or ".join(s.capitalize() for s in hand["statuses"]), hand["window_nm"],
        class_matrix(results), flag_summary(results), "\n".join(rows), "\n".join(sections),
        PLANNER.rules["carriers"]["commercial"]["knots"], PLANNER.rules["carriers"]["military"]["knots"],
        PLANNER.air_rules["handling"]["ship_port_days"], PLANNER.air_rules["handling"]["airfield_hours"],
        PLANNER.air_rules["handling"]["refuel_stop_hours"])
    with open(os.path.join(out_dir, "report.html"), "w", encoding="utf-8") as f:
        f.write(html)


def previous_results(out_dir):
    earlier = sorted(p for p in glob.glob(os.path.join(REPORTS, "*", "results.json"))
                     if os.path.dirname(os.path.abspath(p)) != os.path.abspath(out_dir))
    if not earlier:
        return None, {}
    with open(earlier[-1], encoding="utf-8") as f:
        data = json.load(f)
    return data.get("run", {}).get("started", os.path.basename(os.path.dirname(earlier[-1]))), \
        {s["id"]: s for s in data.get("scenarios", [])}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run the cargo scenarios and write a report.")
    ap.add_argument("files", nargs="*", help="cargo scenario files (default: every cargo_scenarios/*.json)")
    ap.add_argument("--out", help="report folder (default: reports/cargo/<date>_<time>)")
    ap.add_argument("--no-png", action="store_true", help="skip the PNG snapshots")
    args = ap.parse_args(argv)

    files = args.files or sorted(glob.glob(os.path.join(SCENARIOS, "*.json")))
    if not files:
        raise SystemExit("No cargo scenario files found in %s." % SCENARIOS)
    started = datetime.now().astimezone()
    out_dir = args.out or os.path.join(REPORTS, started.strftime("%Y-%m-%d_%H%M%S"))
    snap_dir = os.path.join(out_dir, "snapshots")
    os.makedirs(snap_dir, exist_ok=True)
    browser = None if args.no_png else map_snapshot.find_browser()
    if not args.no_png and not browser:
        print("No Edge or Chrome found, so snapshots are SVG only (set FALCON_BROWSER to a browser to add PNGs).")

    bases, results, seen = load_bases(), [], set()
    for path in files:
        try:
            s = load_scenario(path, bases)
            if s["id"] in seen:
                raise ScenarioError("Scenario id %s is used twice." % s["id"])
            seen.add(s["id"])
            r = run_one(s, snap_dir, browser)
        except (ScenarioError, ValueError, server.BadRequest) as e:
            print("%-6s ERROR  %s: %s" % ("", os.path.basename(path), e))
            results.append(failed_run(path, e))
            continue
        results.append(r)
        fails = [c["check"] for c in r["checks"] if not c["passed"]]
        print("%-6s %s  %s -> %s  %s%s" % (
            r["id"], "PASS" if r["passed"] else "FAIL", r["from"]["name"], r["to"]["name"],
            r["blocked"] or "bottlenecks: " + (", ".join(c["class"] for c in r["classes"] if c["bottlenecks"]) or "none"),
            "".join("\n         failed: " + f for f in fails)))

    run = {"started": started.strftime("%Y-%m-%d %H:%M:%S UTC%z"), "code": routes.git_version(),
           "data": "neo4j-datamodel (MARNET 100 km)", "inputs": inputs(), "overrides": PLANNER.n_overrides,
           "passed": sum(r["passed"] for r in results), "failed": sum(not r["passed"] for r in results)}
    prev_when, prev = previous_results(out_dir)
    write_report(out_dir, run, results, prev_when, prev)
    write_csv(os.path.join(out_dir, "movements.csv"), results)
    with open(os.path.join(out_dir, "results.json"), "w", encoding="utf-8") as f:
        json.dump({"run": run, "scenarios": [dict(r, snapshots=[{k: v for k, v in m.items() if k != "_svg"}
                                                                for m in r["snapshots"]]) for r in results]},
                  f, indent=2)
    print("\n%d of %d cargo scenarios passed.  Report: %s" % (run["passed"], len(results),
                                                             os.path.join(out_dir, "report.html")))
    return 0 if run["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
