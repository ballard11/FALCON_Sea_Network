"""
Rates every port for every supply class: Confirmed, Inferred, Unknown or Ruled out.

    python cargo_capability.py                  # rate and write the CSV
    python cargo_capability.py --no-overrides   # World Port Index only; writes nothing

Reads   neo4j-datamodel/nodes/ports.csv   (NGA World Port Index fields)
        cargo/cargo_classes.json          (what each class needs)
        cargo/port_overrides.csv          (sourced corrections, these win)
Writes  cargo/port_cargo_capability.csv   (one row per port and class)

Stdlib only. The rules are explained in cargo/README.md.
"""
import csv
import hashlib
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
PORTS = os.path.join(HERE, "neo4j-datamodel", "nodes", "ports.csv")
RULES = os.path.join(HERE, "cargo", "cargo_classes.json")
OVERRIDES = os.path.join(HERE, "cargo", "port_overrides.csv")
OUT = os.path.join(HERE, "cargo", "port_cargo_capability.csv")

HARBOR_RANK = {"Very Small": 0, "Small": 1, "Medium": 2, "Large": 3}
STATUSES = ("confirmed", "inferred", "unknown", "ruled_out")
KEY_PORTS = ["WPI-61400", "WPI-62380", "WPI-62500", "WPI-56550", "WPI-58395", "WPI-58370",
             "WPI-57920", "WPI-60390", "WPI-50000", "WPI-54670", "WPI-47800"]


def flag(v):
    """ports.csv stores flags as true / false / blank (blank = Unknown)."""
    return {"true": True, "false": False}.get((v or "").strip().lower())


def measure(v):
    v = (v or "").strip()
    return float(v) if v else None


def load_ports():
    with open(PORTS, newline="", encoding="utf-8") as f:
        return {r["port_id"]: r for r in csv.DictReader(f)}


def apply_overrides(ports):
    """Overrides replace a field's value; the port remembers which fields came
    from an override so the rating can say so."""
    with open(OVERRIDES, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    columns = set(next(iter(ports.values())))
    for o in rows:
        pid, field = o["port_id"], o["field"]
        if pid not in ports:
            sys.exit(f"port_overrides.csv: unknown port {pid}")
        if field not in columns:
            sys.exit(f"port_overrides.csv: unknown field {field} for {pid}")
        ports[pid][field] = o["value"]
        ports[pid].setdefault("_overridden", set()).add(field)
    return len(rows)


def requirement(port, alternatives):
    """A requirement is met if any alternative is Yes, failed if all are No."""
    values = [flag(port[f]) for f in alternatives]
    if any(v is True for v in values):
        return True, next(f for f, v in zip(alternatives, values) if v)
    if all(v is False for v in values):
        return False, " and ".join(alternatives)
    return None, " / ".join(alternatives)


def depth_check(port, draft, marginal):
    """Max vessel draft where NGA records it, otherwise the deepest cargo berth.
    Channel depth is reported but does not rule a port out: it is banded, and
    for large ports often describes an older or secondary channel."""
    limit, basis = measure(port["max_vessel_draft_m"]), "max vessel draft"
    if limit is None:
        limit, basis = measure(port["cargo_pier_depth_m"]), "cargo pier depth"
    if limit is None:
        return "unknown", None, "no depth recorded"
    if limit >= draft:
        return "ok", limit, basis
    if limit >= draft - marginal:
        return "marginal", limit, basis
    return "short", limit, basis


def can_infer(port, rule):
    if not rule:
        return False
    size = HARBOR_RANK.get(port["harbor_size"], -1)
    pier = measure(port["cargo_pier_depth_m"]) or 0
    return (size >= HARBOR_RANK[rule["min_harbor_size"]]
            and pier >= rule["min_pier_m"]
            and any(flag(port[f]) for f in rule["any_yes"]))


def rate(port, cls, rules):
    vessel = rules["vessels"][cls["vessel"]]
    depth, limit, basis = depth_check(port, vessel["draft_m"], rules["depth"]["marginal_m"])
    infer_rule = cls["infer"] if "infer" in cls else rules["inference_defaults"]

    met, failed, unknown = [], [], []
    for alts in cls["requires"]:
        ok, label = requirement(port, alts)
        (met if ok else failed if ok is False else unknown).append(label)

    # decided_by names the rule that set the status, for summaries and audit.
    reasons = []
    if failed:
        status, decided_by = "ruled_out", "facility recorded No"
        reasons.append("no " + "; no ".join(failed))
    elif depth == "short":
        status, decided_by = "ruled_out", "too shallow"
        reasons.append(f"{basis} {limit:g} m, ship needs {vessel['draft_m']:g} m")
    elif not unknown:
        status, decided_by = "confirmed", "all recorded Yes"
        reasons.append("has " + ", ".join(met))
    elif can_infer(port, infer_rule):
        status, decided_by = "inferred", "facility inferred"
        reasons.append("unknown " + ", ".join(unknown) + "; inferred from harbor size, pier depth and other facilities")
    else:
        status, decided_by = "unknown", "facility unknown" if infer_rule else "never inferred"
        reasons.append("unknown " + ", ".join(unknown)
                       + ("" if infer_rule else "; this class is never inferred"))

    if status == "confirmed" and depth in ("marginal", "unknown"):
        status = "inferred"
        decided_by = "depth marginal" if depth == "marginal" else "depth not recorded"
    if status != "ruled_out" and depth == "marginal":
        reasons.append(f"depth marginal: {basis} {limit:g} m, ship needs {vessel['draft_m']:g} m")
    elif status != "ruled_out" and depth == "unknown":
        reasons.append("no depth recorded")

    used = {f for alts in cls["requires"] for f in alts} | {"max_vessel_draft_m", "cargo_pier_depth_m"}
    overridden = sorted(port.get("_overridden", set()) & used)
    return {
        "status": status,
        "decided_by": decided_by,
        "vessel": cls["vessel"],
        "draft_m": vessel["draft_m"],
        "depth_limit_m": limit,
        "depth_basis": basis if limit is not None else None,
        "supports_yes": ";".join(f for f in cls["supports"] if flag(port[f])),
        "overridden": ";".join(overridden),
        "reason": "; ".join(reasons),
    }


def main():
    with open(RULES, encoding="utf-8") as f:
        rules = json.load(f)
    ports = load_ports()
    # --no-overrides rates from the World Port Index alone and writes nothing,
    # to show what the overrides change.
    no_overrides = "--no-overrides" in sys.argv[1:]
    n_overrides = 0 if no_overrides else apply_overrides(ports)

    rows = []
    for pid, port in ports.items():
        for cid, cls in rules["classes"].items():
            rows.append({"port_id": pid, "name": port["name"], "harbor_size": port["harbor_size"],
                         "class_id": cid, "class_name": cls["name"], **rate(port, cls, rules)})

    if not no_overrides:
        cols = list(rows[0])
        with open(OUT, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows({k: ("" if v is None else v) for k, v in r.items()} for r in rows)

    print(f"{len(ports):,} ports x {len(rules['classes'])} classes = {len(rows):,} ratings, "
          + ("no overrides, nothing written\n" if no_overrides
             else f"{n_overrides} overrides -> {os.path.relpath(OUT, HERE)}\n"))
    print("Inputs (SHA-256, first 16 hex digits):\n")
    for path in (PORTS, RULES) + (() if no_overrides else (OVERRIDES, OUT)):
        with open(path, "rb") as f:
            print(f"- `{os.path.relpath(path, HERE)}`: `{hashlib.sha256(f.read()).hexdigest()[:16]}`")

    big = [r for r in rows if r["harbor_size"] in ("Large", "Medium")]
    print(f"\nLarge and Medium ports ({len(big) // len(rules['classes'])}):\n")
    print("| Class | " + " | ".join(s.replace("_", " ").capitalize() for s in STATUSES) + " |")
    print("|---|" + "---|" * len(STATUSES))
    for cid, cls in rules["classes"].items():
        c = Counter(r["status"] for r in big if r["class_id"] == cid)
        print(f"| {cid} {cls['name']} | " + " | ".join(str(c[s]) for s in STATUSES) + " |")

    print("\nWhat decided each status (Large and Medium ports):\n")
    decided = sorted({r["decided_by"] for r in big})
    print("| Class | " + " | ".join(decided) + " |")
    print("|---|" + "---|" * len(decided))
    for cid, cls in rules["classes"].items():
        c = Counter(r["decided_by"] for r in big if r["class_id"] == cid)
        print(f"| {cid} | " + " | ".join(str(c[d]) for d in decided) + " |")

    print("\nKey ports:\n")
    print("| Port | " + " | ".join(rules["classes"]) + " |")
    print("|---|" + "---|" * len(rules["classes"]))
    short = {"confirmed": "C", "inferred": "I", "unknown": "?", "ruled_out": "x"}
    for pid in KEY_PORTS:
        mine = [r for r in rows if r["port_id"] == pid]
        print(f"| {mine[0]['name']} | " + " | ".join(short[r["status"]] for r in mine) + " |")

    print("\nKey ports, why:\n")
    print("| Port | Class | Status | Decided by | Depth used | Overridden | Reason |")
    print("|---|---|---|---|---|---|---|")
    for pid in KEY_PORTS:
        for r in (r for r in rows if r["port_id"] == pid):
            depth = f"{r['depth_limit_m']:g} m ({r['depth_basis']})" if r["depth_limit_m"] else "none"
            print(f"| {r['name']} | {r['class_id']} | {r['status']} | {r['decided_by']} | {depth} "
                  f"| {r['overridden'] or '-'} | {r['reason']} |")


if __name__ == "__main__":
    main()
