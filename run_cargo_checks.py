"""
Cargo checks: random moves through the cargo planner, closure and the Plan, each
checked against rules that must always hold. Python 3, stdlib only.

    python run_cargo_checks.py                 # the default run, under a minute
    python run_cargo_checks.py --moves 120 --seed 3

It complements the two scenario runners (run_scenarios.py, run_cargo_scenarios.py),
which check chosen cases against expected answers. These checks make up their own
cases, so they find what nobody thought to write a scenario for. A run with the
same seed makes the same cases.

1. Planner: economic and urgent plans, with and without a random area of denial
   and WEZ near the route. No errors; every rule check from run_cargo_scenarios.py
   passes; urgent is never slower than economic; an area never makes a move
   faster; the same input gives the same answer.
2. Closure (lift.py): the last ton never arrives before the first; more lift is
   never slower; a slower port is never faster; more cargo is never sooner; lift
   that starts later or further away is never sooner.
3. Plan (comparisons.py): the last ton of the plan is the latest class; every
   single change is listed, and each says what applying it gives; capacity
   used agrees with the limit; every figure shown has a source.
4. Data (falcon_data.py): the files pass their own check, and a file broken
   on purpose (a figure without a source, a bad status, classes that disagree)
   is caught.

Exits 1 if any check fails, listing up to three examples of each.
"""
import argparse
import collections
import contextlib
import io
import json
import os
import random
import shutil
import sys
import tempfile
import time

with contextlib.redirect_stdout(io.StringIO()):          # the runner prints while it loads the network
    import run_cargo_scenarios as R
import comparisons
import falcon_data
import server

P, G, L = R.PLANNER, R.GRAPH, comparisons.LIFT
COUNTRIES = P.rules["handover"]["countries"]


class Checks:
    def __init__(self):
        self.ran = collections.Counter()
        self.failed = collections.Counter()
        self.examples = collections.defaultdict(list)

    def check(self, part, name, ok, example=""):
        self.ran[part] += 1
        if not ok:
            self.failed[(part, name)] += 1
            if len(self.examples[(part, name)]) < 3:
                self.examples[(part, name)].append(example)


def area_near(rng, o, d):
    """A circle somewhere on the straight line between two ports."""
    (x1, y1), (x2, y2) = G.coords[o], G.coords[d]
    if abs(x2 - x1) > 180:
        x2 += 360 if x2 < x1 else -360
    t = rng.random()
    lon = ((x1 + t * (x2 - x1) + 180) % 360) - 180
    return {"type": "circle", "center": [round(lon, 3), round(y1 + t * (y2 - y1), 3)],
            "radius_nm": rng.choice([60, 120, 250, 400, 600])}


def scenario(o, d, cargo, raw_den=(), raw_wez=(), lift=None, modes=None, port_rates=None):
    return {"origin": o, "goal": d, "cargo": cargo, "countries": None, "lift": lift, "modes": modes,
            "port_rates": port_rates, "denial": server.parse_areas(list(raw_den)), "wez": server.parse_areas(list(raw_wez))}


def cells(result):
    return {(r["class"], k): c for r in result["rows"] for k, c in r["cells"].items()}


def planner(C, rng, moves, pool):
    for _ in range(moves):
        o, d = rng.sample(pool, 2)
        raw_den = [area_near(rng, o, d)] if rng.random() < 0.6 else []
        raw_wez = [area_near(rng, o, d)] if rng.random() < 0.6 else []
        den, wez = server.parse_areas(raw_den), server.parse_areas(raw_wez)
        tag = "%s -> %s, denial %s, WEZ %s" % (G.ports[o]["name"], G.ports[d]["name"], raw_den, raw_wez)
        try:
            base = P.plan(o, d, None, [], [], COUNTRIES, "economic")
            eco = P.plan(o, d, None, den, wez, COUNTRIES, "economic")
            urg = P.plan(o, d, None, den, wez, COUNTRIES, "urgent")
            again = P.plan(o, d, None, den, wez, COUNTRIES, "economic")
        except Exception as e:                            # noqa: BLE001 -- any error is a failure to report
            C.check("planner", "no errors", False, "%s: %r" % (tag, e))
            continue
        C.check("planner", "no errors", True)
        for name, pl in (("economic", eco), ("urgent", urg)):
            if pl["blocked"]:
                C.check("planner", "a blocked plan shows no sea days",
                        not any(c["days"] and c["mode"] == "sea" for c in pl["classes"]), tag)
                continue
            for chk in R.invariant_checks(pl, den, wez, COUNTRIES):
                C.check("planner", "%s: %s" % (name, chk["check"]), chk["passed"], "%s | %s" % (tag, chk["actual"]))
            for c in pl["classes"]:
                C.check("planner", "class days are positive", isinstance(c["days"], (int, float)) and c["days"] > 0,
                        "%s class %s: %s" % (tag, c["class"], c["days"]))
        if not eco["blocked"] and not urg["blocked"]:
            e_days = {c["class"]: c["days"] for c in eco["classes"]}
            for c in urg["classes"]:
                C.check("planner", "urgent is never slower than economic", c["days"] <= e_days[c["class"]] + 0.05,
                        "%s class %s: urgent %s, economic %s" % (tag, c["class"], c["days"], e_days[c["class"]]))
        if not eco["blocked"] and not base["blocked"]:
            b_days = {c["class"]: c["days"] for c in base["classes"]}
            for c in eco["classes"]:
                C.check("planner", "an area never makes a move faster", c["days"] >= b_days[c["class"]] - 0.05,
                        "%s class %s: %s with areas, %s without" % (tag, c["class"], c["days"], b_days[c["class"]]))
        same = json.dumps(eco, sort_keys=True, default=str) == json.dumps(again, sort_keys=True, default=str)
        C.check("planner", "the same input gives the same answer", same, tag)


def closure(C, rng, moves, pool):
    types = list(L.counts())
    for _ in range(moves):
        o, d = rng.sample(pool, 2)
        lens = rng.choice(["mode", "pod"])
        cargo = [dict(c, short_tons=c["short_tons"] * rng.choice([1, 5, 20])) for c in P.default_cargo()]
        raw_wez = [area_near(rng, o, d)] if rng.random() < 0.4 else []
        tag = "%s -> %s (%s)" % (G.ports[o]["name"], G.ports[d]["name"], lens)
        run = lambda cargo=cargo, lift=None: cells(comparisons.compare(P, lens, scenario(o, d, cargo, (), raw_wez, lift)))
        base = run()
        for k, c in base.items():
            C.check("closure", "the last ton never arrives before the first",
                    not c["available"] or c["closure"] is None or c["closure"] >= c["days"] - 1e-9,
                    "%s %s: first %s, last %s" % (tag, k, c["days"], c["closure"]))
        if lens == "pod":                                 # the soonest way can switch mode, so only Mode compares like with like
            continue
        t = rng.choice(types)
        saved = dict(L.port_rate)
        L.port_rate.update({k: v / 2 for k, v in saved.items()})
        try:
            slow_port = run()
        finally:
            L.port_rate.update(saved)
        place = rng.choice(pool) if t in L.ships else rng.choice(list(P.air.fields))
        for name, other, better in (
                ("more lift is never slower", run(lift={t: L.counts()[t] + 2}), True),
                ("a slower port is never faster", slow_port, False),
                ("more cargo is never sooner", run([dict(c, short_tons=c["short_tons"] * 2) for c in cargo]), False),
                ("lift that starts later or further away is never sooner",
                 run(lift={t: [{"count": L.counts()[t], "at": place, "ready": rng.choice([1, 3])}]}), False)):
            for k, c in base.items():
                o2 = other.get(k)
                if not c["available"] or not o2 or not o2["available"] or c["closure"] is None or o2["closure"] is None:
                    continue
                ok = o2["closure"] <= c["closure"] + 1e-9 if better else o2["closure"] >= c["closure"] - 1e-9
                C.check("closure", name, ok, "%s %s, %s: %s then %s" % (tag, k, t, c["closure"], o2["closure"]))


def plan(C, rng, moves, pool):
    for _ in range(moves):
        o, d = rng.sample(pool, 2)
        cargo = [dict(c, short_tons=c["short_tons"] * rng.choice([1, 5])) for c in P.default_cargo()]
        raw_wez = [area_near(rng, o, d)] if rng.random() < 0.4 else []
        tag = "%s -> %s" % (G.ports[o]["name"], G.ports[d]["name"])
        out = comparisons.compare(P, "plan", scenario(o, d, cargo, (), raw_wez))
        cells_ = {r["class"]: r["cells"]["plan"] for r in out["rows"]}
        days = [c["closure"] for c in cells_.values()]
        last = None if any(x is None for x in days) else max(days)
        C.check("plan", "the plan's last ton is its latest class", out["last"] == last,
                "%s: %s against %s" % (tag, out["last"], last))
        ids = {x["id"] for x in out["changes"]}
        want = {"mode:" + c for c, cell in cells_.items() if cell.get("switch")}
        want |= {"lift:" + cell["lift"]["type"] for cell in cells_.values() if cell.get("lift")}
        C.check("plan", "every single change is listed", want <= ids, "%s: missing %s" % (tag, sorted(want - ids)))
        lasts = [(x["last"] is None, x["last"] or 0) for x in out["changes"]]
        C.check("plan", "changes are listed soonest first", lasts == sorted(lasts), tag)
        for x in out["changes"]:
            if x["kind"] == "mode":
                applied = scenario(o, d, cargo, (), raw_wez, modes=dict(out["modes"], **{x["class"]: "sea" if x["to"] == "sea" else "air"}))
            elif x["kind"] == "lift":
                groups = L.groups()
                groups[x["type"]] = groups[x["type"]] + [{"count": 1, "at": None, "ready": 0.0}]
                applied = scenario(o, d, cargo, (), raw_wez, lift=groups)
            else:
                applied = scenario(o, d, cargo, (), raw_wez, port_rates={x["port_id"]: x["to"]})
                C.check("plan", "a port rate change is to a faster rate", x["to"] > x["from"], "%s: %s" % (tag, x))
            tried = comparisons.compare(P, "plan", applied)
            C.check("plan", "a change says what applying it gives", tried["last"] == x["last"],
                    "%s, %s: listed %s, applied %s" % (tag, x["id"], x["last"], tried["last"]))
        ports_used = {(u["port_id"], u["work"]): u for u in out["usage"] if u["kind"] == "port"}
        for u in ports_used.values():
            C.check("plan", "a port's days of work are its tons over its rate",
                    abs(u["days"] - u["demand_st"] / u["rate_st_per_day"]) <= 0.05 + 1e-9, "%s: %s" % (tag, u))
        for cid, c in cells_.items():
            lim = c.get("limit")
            if lim and lim["kind"] == "port":
                u = ports_used.get((lim["port_id"], lim["work"]))
                C.check("plan", "a port limit is in the capacity used, at the same rate and tons",
                        u is not None and u["rate_st_per_day"] == lim["rate_st_per_day"] and u["demand_st"] == lim["demand_st"],
                        "%s class %s: %s against %s" % (tag, cid, lim, u))
            C.check("plan", "a limit is fields, not words", lim is None or isinstance(lim, dict) and "kind" in lim,
                    "%s class %s: %r" % (tag, cid, lim))
        for f in out["figures"]:
            C.check("plan", "every figure shown has a source",
                    f["source"] is not None and f["source"]["status"] in falcon_data.STATUSES, "%s: %s" % (tag, f["id"]))
        rates = {u["figure"] for u in ports_used.values() if u["figure"]}
        C.check("plan", "every port rate used is among the figures shown", rates <= {f["id"] for f in out["figures"]},
                "%s: %s" % (tag, sorted(rates - {f["id"] for f in out["figures"]})))


def data(C, rng, moves, pool):
    """The data files pass their own check, and files broken on purpose don't."""
    C.check("data", "the data files have no problems", not falcon_data.STORE.problems(),
            "; ".join(falcon_data.STORE.problems()[:3]))
    breaks = [
        ("a figure without a source", "lift_rules.json", lambda r: r["ships"]["tanker"]["_sources"].pop("capacity_st")),
        ("a status that isn't one of the three", "lift_rules.json",
         lambda r: r["ships"]["tanker"]["_sources"]["count"].update(status="probably")),
        ("sourced without a url", "air_rules.json",
         lambda r: r["aircraft"]["commercial"]["_sources"]["payload_st"].pop("url")),
        ("a class with no normal mode", "lift_rules.json", lambda r: r["plan"]["normal_mode"].pop("IV")),
        ("a class that never flies, set to go by air", "lift_rules.json",
         lambda r: r["plan"]["normal_mode"].update(III="air")),
        ("a class carried by two ship types", "lift_rules.json", lambda r: r["ships"]["tanker"]["classes"].append("I")),
        ("a harbor size with no port rate", "lift_rules.json", lambda r: r["ports"]["rate_st_per_day"].pop("Medium")),
        ("a source for a field that isn't there", "lift_rules.json",
         lambda r: r["ports"]["_sources"].update(rate_per_hour={"status": "illustrative", "source": "x"})),
        ("a rate of zero", "lift_rules.json", lambda r: r["ports"]["rate_st_per_day"].update(Large=0)),
    ]
    src = falcon_data.STORE
    for name, fname, breaker in breaks:
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "cargo"))
            for f in os.listdir(src.rules_dir):
                if f.endswith((".json", ".csv")) and f != "port_cargo_capability.csv":
                    shutil.copy(os.path.join(src.rules_dir, f), os.path.join(tmp, "cargo", f))
            with open(os.path.join(tmp, "cargo", fname), encoding="utf-8") as f:
                rules = json.load(f)
            breaker(rules)
            with open(os.path.join(tmp, "cargo", fname), "w", encoding="utf-8") as f:
                json.dump(rules, f)
            st = falcon_data.Store(tmp)
            st.ref_dir = src.ref_dir                     # the reference data isn't copied: it isn't broken
            C.check("data", "a file broken on purpose is caught", bool(st.problems()), name)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--moves", type=int, default=30, help="random moves for each part (default 30)")
    ap.add_argument("--seed", type=int, default=7, help="the same seed makes the same moves (default 7)")
    a = ap.parse_args()
    rng = random.Random(a.seed)
    ports = list(G.ports)
    # Moves within the region the cargo rules are written for (handover and refuelling countries), and
    # between larger harbors anywhere, so the run is quick and the cases are ones the rules cover.
    region = [p for p in ports if -15 <= G.coords[p][1] <= 45 and (95 <= G.coords[p][0] <= 180 or G.coords[p][0] <= -150)]
    region_big = [p for p in region if G.ports[p]["harbor_size"] in ("Large", "Medium")]
    C = Checks()
    started = time.perf_counter()
    for name, part, pool in (("planner", planner, region), ("closure", closure, region_big), ("plan", plan, region_big),
                             ("data", data, None)):
        t = time.perf_counter()
        part(C, rng, a.moves, pool)
        print("  %-8s %5d checks, %d failed  (%.0f s)" % (name, C.ran[name], sum(v for (p, _), v in C.failed.items() if p == name),
                                                    time.perf_counter() - t))
    print()
    if not C.failed:
        print("All %d checks passed: %d moves a part, seed %d, %.0f s." % (sum(C.ran.values()), a.moves, a.seed,
                                                                        time.perf_counter() - started))
        return 0
    print("FAILED: %d checks." % sum(C.failed.values()))
    for (part, name), n in C.failed.most_common():
        print("  %s: %s (%d)" % (part, name, n))
        for ex in C.examples[(part, name)]:
            print("      e.g. %s" % ex[:300])
    return 1


if __name__ == "__main__":
    sys.exit(main())
