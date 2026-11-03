"""
A short brief of a scenario run, for readers outside the development team.

    python make_brief.py                              the newest run in reports/
    python make_brief.py reports/2026-11-03_105611    a chosen run
    python make_brief.py --note "Your own takeaways, in a paragraph."
    python make_brief.py --no-pdf                     HTML only

run_scenarios.py writes the QA report: every check, for the people who build
FALCON. This writes the same run's answers in plain terms (what each closure
costs in distance and time) with the assumptions and sources a reader needs,
and none of the test detail. It reads only the run's results.json and map
snapshots, so a brief always matches a tested run, and it refuses a run in
which any scenario failed.

Writes brief.html (one file, maps included) and brief.pdf next to the run's
report.html. Python 3, stdlib only; the PDF needs Edge or Chrome.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from html import escape

import map_snapshot

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "reports")
BUILD_REPORT = os.path.join(HERE, "neo4j-datamodel", "build-report.json")
KNOTS = 18                   # the nominal speed server.py uses (NOMINAL_KNOTS)

SOURCES = [
    ("Ports", "NGA World Port Index (Pub. 150)", "https://msi.nga.mil/Publications/WPI"),
    ("Shipping lanes", "Eurostat searoute, MARNET network", "https://github.com/eurostat/searoute"),
    ("Coastline (maps only)", "Natural Earth, 1:50m land", "https://www.naturalearthdata.com/"),
]

# What a reader must know before relying on a number. Edit as the model grows.
LIMITS = [
    "These results compare what-if routes. They do not predict or detect events.",
    "Distances are the shortest path along a published shipping-lane network (Eurostat MARNET, "
    "about 100 km detail), including the short leg from each port to its nearest lane. Real "
    "voyages also depend on weather, traffic and routing rules that are not modelled.",
    "Time is distance at a constant %d knots, for a rough sense of scale only." % KNOTS,
    "A denial area closes every shipping lane that enters it. How or for how long an area is "
    "closed is not modelled.",
    "Port depth, port size and vessel limits are not yet applied: any port can start or end a move.",
    "FALCON's distances have not yet been checked against published port-to-port distance tables.",
]

# Which movement modes this brief covers. Edit as the model grows.
MODES = "Sea. Air moves between airfields, along great-circle routes, are planned."


class BriefError(Exception):
    pass


# ------------------------------------------------------------------ words
def plain(text):
    """Test wording to reader wording: 'denial area' and 'denied' become 'closed'."""
    text = re.sub(r"\bareas of denial\b", "closed areas", text, flags=re.I)
    text = re.sub(r"\b(area of denial|denial area)\b", "closed area", text, flags=re.I)
    return re.sub(r"\bdenied\b", "closed", text, flags=re.I)


def nm(x):
    return format(round(x), ",")


def duration(distance_nm):
    """Rough sailing time at KNOTS: hours under a day, days after."""
    hours = distance_nm / KNOTS
    if hours < 1:
        return "under an hour"
    if hours < 23.5:
        return "%d hour%s" % (round(hours), "" if round(hours) == 1 else "s")
    return "%.1f days" % (hours / 24)


def via(passages):
    return "via " + ", ".join(passages) if passages else "through open water"


def percent(extra, base):
    p = 100.0 * extra / base if base else 0.0
    return "%.1f%%" % p if round(p, 1) < 10 else "%d%%" % round(p)


def extra_shown(r):
    """The extra distance as the difference of the two distances shown, so the
    page adds up (114.6 nm between 1,544.6 and 1,659.2 shows as 1,545 to 1,659,
    so +114). It is never more than 1 nm from the exact figure."""
    return round(r["routes"][0]["length_nm"]) - round(r["original"]["length_nm"])


def extra_pct(r):
    """The share a closure adds to the normal route, for sorting."""
    return r["extra_nm"] / r["original"]["length_nm"] if r.get("extra_nm") and r["original"]["length_nm"] else 0.0


def areas_phrase(r):
    n = len(r["areas"])
    return "the closed area" if n == 1 else "the %d closed areas" % n


def finding(r):
    """One or two sentences on what the scenario shows."""
    o = r["original"]
    if r.get("blocked"):
        text = "No route is possible. %s" % plain(r["blocked"])
        if r["areas"] and o.get("length_nm"):
            text += " With nothing closed, the route is %s nm %s." % (nm(o["length_nm"]), via(o["passages"]))
        return text
    best = r["routes"][0]
    if not r["areas"]:
        return "With nothing closed, the best route is %s nm %s, about %s at %d knots." % (
            nm(best["length_nm"]), via(best["passages"]), duration(best["length_nm"]), KNOTS)
    if not r.get("affected"):
        return "%s does not touch the normal route, which stays %s nm %s." % (
            areas_phrase(r).capitalize(), nm(best["length_nm"]), via(best["passages"]))
    extra = r["extra_nm"]
    if best["passages"] == o["passages"]:
        route = "the best route still goes %s, but is %s nm instead of %s nm." % (
            via(best["passages"]), nm(best["length_nm"]), nm(o["length_nm"]))
    else:
        route = "the best route is %s nm %s, instead of %s nm %s." % (
            nm(best["length_nm"]), via(best["passages"]), nm(o["length_nm"]), via(o["passages"]))
    return "With %s in place, %s That is %s nm (%s) longer: about %s more at %d knots." % (
        areas_phrase(r), route, nm(extra_shown(r)), percent(extra, o["length_nm"]), duration(extra), KNOTS)


def count(n, word, plural=None):
    return "%d %s" % (n, word if n == 1 else plural or word + "s")


def lane_network():
    """(lane segments, named straits and canals) from the data build, or None."""
    try:
        with open(BUILD_REPORT, encoding="utf-8") as f:
            build = json.load(f)
        return build["rows_written"]["relationships/sea_segments.csv"], len(build["network"]["segments_by_passage"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def capabilities(results):
    """What the run shows, as (capability, evidence) pairs read off its
    scenarios rather than claimed."""
    out = []
    k = max(r["k"] for r in results)
    distinct = any(r["method"] == "distinct" for r in results)
    ports = {r[end]["id"]: r[end]["country"] for r in results for end in ("from", "to")}
    out.append(("Origin to destination",
                "The best route between two ports, with up to %d routes per move%s. %s between %s in %s." % (
                    k, ", each through different waters" if distinct else ", the next shortest first",
                    count(len(results), "move"), count(len(ports), "port"),
                    count(len(set(ports.values())), "country", "countries"))))
    used = {p for r in results for route in [r["original"]] + r["routes"] for p in route["passages"]}
    network = lane_network()
    out.append(("Realistic sea routes",
                "Routes follow a published shipping-lane network through real straits and canals, not straight "
                "lines%s. This run's routes pass through %s." % (
                    " (Eurostat MARNET: %s lane segments, %s)" % (
                        format(network[0], ","), count(network[1], "named strait or canal", "named straits and canals"))
                    if network else "", count(len(used), "named strait"))))
    areas = [a for r in results for a in r["areas"]]
    if areas:
        radii = sorted(a["radius_nm"] for a in areas if a["type"] == "circle")
        shapes = []
        if radii:
            shapes.append("%s of %s nm radius" % (count(len(radii), "circle"), nm(radii[0]) if radii[0] == radii[-1]
                                                  else "%s to %s" % (nm(radii[0]), nm(radii[-1]))))
        polygons = len(areas) - len(radii)
        if polygons:
            shapes.append("%s drawn on the map" % count(polygons, "shape"))
        blocked = sum(1 for r in results if r.get("blocked"))
        out.append(("Area denial and what-if scenarios",
                    "%s in %s (%s). Every shipping lane that enters an area is closed, the routes are found again, "
                    "and the cost is measured in distance and in sailing time at %d knots.%s" % (
                        count(len(areas), "denial area"), count(sum(1 for r in results if r["areas"]), "scenario"),
                        " and ".join(shapes), KNOTS,
                        " %s FALCON recognising that no route remains, and saying why." % (
                            "1 scenario shows" if blocked == 1 else "%d scenarios show" % blocked) if blocked else "")))
    return out


# Checks run_scenarios.py makes of every answer, whatever the scenario
# (invariant_checks); the rest are each scenario's own expectations.
RULE_CHECKS = ("Every route starts at the origin", "No route is shorter than", "Routes are ranked shortest first",
               "Each route's drawn line", "No route is listed twice", "No route enters")


def tested(results):
    """What the run's automated checks cover."""
    checks = [c["check"] for r in results for c in r.get("checks", [])]
    rules = sum(1 for c in checks if c.startswith(RULE_CHECKS))
    out = ["All %s passed all %s." % (count(len(results), "scenario"), count(len(checks), "automated check"))]
    if len(checks) - rules:
        out.append("%s compare each answer with what its scenario expects: which straits the best route uses "
                   "or avoids, whether the closure touches the normal route, and how much longer the route gets."
                   % count(len(checks) - rules, "check"))
    if rules:
        out.append("%s hold every route to rules it must always keep: it starts and ends at its ports, never "
                   "enters a closed area, is never shorter than the straight-line distance, and its drawn line "
                   "matches its reported length." % count(rules, "check"))
    return out


def takeaways(results):
    """Facts read straight off the run; the reader's conclusions go in --note."""
    closures = [r for r in results if r["areas"]]
    costed = [r for r in closures if not r.get("blocked") and r.get("affected")]
    out = []
    if costed:
        top = max(costed, key=extra_pct)
        out.append("Largest relative cost: %s (%s longer, %s to %s nm)." % (
            plain(top["name"]), percent(top["extra_nm"], top["original"]["length_nm"]),
            nm(top["original"]["length_nm"]), nm(top["routes"][0]["length_nm"])))
        slow = max(costed, key=lambda r: r["extra_nm"])
        out.append("Longest delay: %s (about %s more at %d knots)." % (
            plain(slow["name"]), duration(slow["extra_nm"]), KNOTS))
        low = min(costed, key=extra_pct)
        if low is not top:
            out.append("Smallest effect: %s (%s longer)." % (
                plain(low["name"]), percent(low["extra_nm"], low["original"]["length_nm"])))
    blocked = [r for r in closures if r.get("blocked")]
    if blocked:
        out.append("%d closure%s leave%s no route at all: %s." % (
            len(blocked), "" if len(blocked) == 1 else "s", "s" if len(blocked) == 1 else "",
            "; ".join(plain(r["name"]) for r in blocked)))
    untouched = [r for r in closures if not r.get("blocked") and not r.get("affected")]
    if untouched:
        out.append("%d closure%s did not change the best route." % (len(untouched), "" if len(untouched) == 1 else "s"))
    return out


# ------------------------------------------------------------------- maps
LEGEND = [(">Area of denial<", ">Closed area<"), (">Lanes closed by the areas<", ">Lanes closed<"),
          (">Original, no areas ", ">Normal route ")]


def plain_map(svg, title, subtitle):
    """The QA snapshot, retitled and relabelled for readers, sized to its column."""
    svg = re.sub(r'(<text x="28" y="41"[^>]*>)[^<]*', lambda m: m.group(1) + escape(title), svg, count=1)
    svg = re.sub(r'(<text x="28" y="64"[^>]*>)[^<]*', lambda m: m.group(1) + escape(subtitle), svg, count=1)
    for old, new in LEGEND:
        svg = svg.replace(old, new)
    svg = re.sub(r">([^<]+)<", lambda m: ">" + plain(m.group(1)) + "<", svg)
    return svg.replace('width="%d" height="%d"' % (map_snapshot.WIDTH, map_snapshot.HEIGHT),
                       'class="map" width="100%" height="auto"', 1)


def map_for(run_dir, r):
    """The whole-route snapshot, or '' if the run has none."""
    snaps = r.get("snapshots") or []
    path = os.path.join(run_dir, snaps[0]["svg"]) if snaps else ""
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        svg = f.read()
    n = len(r["areas"])
    sub = "Nothing closed" if not n else "%d closed area%s" % (n, "" if n == 1 else "s")
    return plain_map(svg, "%s → %s" % (r["from"]["name"], r["to"]["name"]), sub)


# ----------------------------------------------------------------- brief
CSS = """
:root { --ink:#1b2430; --dim:#5d6b7a; --line:#dde3ea; --chip:#eef1f5; --accent:#0b6e4f; --bg:#fff; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 "Segoe UI", system-ui, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 32px 16px 60px; }
h1 { font-size: 28px; margin: 0 0 2px; }
h2 { font-size: 19px; margin: 0 0 8px; }
h3 { font-size: 13px; margin: 14px 0 6px; color: var(--dim); text-transform: uppercase; letter-spacing: .04em; }
.meta { color: var(--dim); font-size: 13.5px; }
.note { border-left: 4px solid var(--accent); padding: 4px 0 4px 14px; margin: 18px 0; }
h4 { font-size: 15px; margin: 12px 0 4px; }
ul.facts { padding-left: 20px; margin: 0 0 6px; }
ul.facts li { margin: 0 0 3px; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 7px 9px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--dim); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: .03em; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.wrap { overflow-x: auto; }
section.scenario { border-top: 1px solid var(--line); padding-top: 22px; margin-top: 26px; }
.finding { font-size: 15.5px; margin: 0 0 12px; }
.map { width: 100%; height: auto; display: block; border-radius: 8px; }
figure { margin: 0 0 6px; }
.limits li { margin: 0 0 5px; }
a { color: inherit; }
@page { size: letter; margin: 14mm 13mm; }
@media print {
  main { max-width: none; padding: 0; }
  body { font-size: 13px; }
  section.scenario { break-before: page; border-top: 0; margin-top: 0; padding-top: 0; }
  section.end { break-before: page; }
  figure, table, tr, .keep { break-inside: avoid; }
}
"""


def impact_rows(results):
    closures = [r for r in results if r["areas"]]
    closures.sort(key=lambda r: (bool(r.get("blocked")), -extra_pct(r)))
    rows = []
    for r in closures:
        o = r["original"]["length_nm"]
        if r.get("blocked"):
            cells = "<td class='num'>%s</td><td colspan='3'>No route: %s</td>" % (
                nm(o) if o else "—", escape(plain(r["blocked"])))
        elif not r.get("affected"):
            cells = "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>none</td><td class='num'>—</td>" % (
                nm(o), nm(r["routes"][0]["length_nm"]))
        else:
            cells = "<td class='num'>%s</td><td class='num'>%s</td><td class='num'>+%s nm (%s)</td><td class='num'>%s</td>" % (
                nm(o), nm(r["routes"][0]["length_nm"]), nm(extra_shown(r)), percent(r["extra_nm"], o),
                escape(duration(r["extra_nm"])))
        rows.append("<tr><td><a href='#%s'>%s</a></td>%s</tr>" % (escape(r["id"]), escape(plain(r["name"])), cells))
    return rows


def option_rows(r):
    rows = []
    if r["areas"] and r["original"].get("length_nm"):
        o = r["original"]
        rows.append(("Normal route, nothing closed", o))
    for x in r["routes"]:
        label = ("Best route with the closure" if r["areas"] else "Best route") if x["rank"] == 1 \
            else "Alternative %d" % x["rank"]
        rows.append((label, x))
    return "".join("<tr><td>%s</td><td class='num'>%s</td><td class='num'>%.1f</td><td>%s</td></tr>" % (
        escape(label), nm(x["length_nm"]), x["length_nm"] / KNOTS / 24,
        escape(", ".join(x["passages"]) or "open water")) for label, x in rows)


def port_access():
    """How far ports sit from their lanes, from the data build, or None."""
    try:
        with open(BUILD_REPORT, encoding="utf-8") as f:
            ports = json.load(f)["ports"]
        q = ports["by_access_quality"]
        return ("%s of %s ports reach their nearest lane by an approximate straight leg of 10 to 50 nm, "
                "and %s by one of more than 50 nm, so distances to and from those ports are rougher."
                % (format(q["approximate"], ","), format(ports["total"], ","), format(q["poor"], ",")))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_brief(run_dir, data, note=None):
    run, results = data["run"], data["scenarios"]
    sections = []
    for r in results:
        figure = map_for(run_dir, r)
        sections.append("""
<section class="scenario" id="%s">
  <h2>%s</h2>
  <p class="finding">%s</p>
  %s
  <div class="wrap"><table><tr><th>Option</th><th class="num">Distance (nm)</th><th class="num">Days at %d kt</th><th>Via</th></tr>%s</table></div>
</section>""" % (escape(r["id"]), escape(plain(r["name"])), escape(finding(r)),
                 "<figure>%s</figure>" % figure if figure else "", KNOTS, option_rows(r)))

    impact = impact_rows(results)
    impact_html = ("""<div class="keep"><h3>What each closure costs</h3>
<div class="wrap"><table><tr><th>Scenario</th><th class="num">Normal (nm)</th><th class="num">With closure (nm)</th>
<th class="num">Extra</th><th class="num">Extra time</th></tr>%s</table></div></div>""" % "\n".join(impact)) if impact else ""
    shows = "".join("<li><b>%s.</b> %s</li>" % (escape(what), escape(how)) for what, how in capabilities(results))
    glance = [(head, items) for head, items in (("How it was tested", tested(results)),
                                                 ("What it found", takeaways(results))) if items]
    facts_html = ("<h3>At a glance</h3><h4>What this run shows</h4><ul class='facts'>%s</ul>"
                  "<p class='meta'>Mode tested: %s</p>" % (shows, escape(MODES))) + "".join(
        "<h4>%s</h4><ul class='facts'>%s</ul>" % (escape(head), "".join("<li>%s</li>" % escape(t) for t in items))
        for head, items in glance)
    limits = LIMITS[:4] + [x for x in (port_access(),) if x] + LIMITS[4:]
    checks = sum(len(r.get("checks", [])) for r in results)

    html = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>FALCON Brief</title><style>%s</style></head>
<body><main>
<h1>FALCON · origin-to-destination scenarios</h1>
<div class="meta">%d scenarios · run of %s</div>
%s
%s
%s
%s
<section class="end">
<h3>Assumptions and limits</h3>
<ul class="limits">%s</ul>
<h3>Data sources</h3>
<div class="wrap"><table><tr><th>Data</th><th>Source</th></tr>%s</table></div>
<p class="meta">From the scenario run of %s, FALCON code %s. All %d scenarios passed %d automated checks; the full
QA report is report.html in the same folder.</p>
</section>
</main></body></html>""" % (
        CSS, len(results), escape(run["started"][:10]),
        "<p class='note'>%s</p>" % escape(note) if note else "", facts_html, impact_html, "\n".join(sections),
        "".join("<li>%s</li>" % escape(x) for x in limits),
        "".join("<tr><td>%s</td><td><a href='%s'>%s</a></td></tr>" % (escape(what), escape(url), escape(name))
                for what, name, url in SOURCES),
        escape(run["started"]), escape(run["code"]), len(results), checks)
    path = os.path.join(run_dir, "brief.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


# ------------------------------------------------------------------- run
def newest_run():
    runs = sorted(glob.glob(os.path.join(REPORTS, "*", "results.json")))
    if not runs:
        raise BriefError("No scenario runs in %s. Run python run_scenarios.py first." % REPORTS)
    return os.path.dirname(runs[-1])


def load_run(run_dir):
    path = os.path.join(run_dir, "results.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except OSError:
        raise BriefError("%s has no results.json. Point at a folder run_scenarios.py wrote." % run_dir)
    except ValueError:
        raise BriefError("%s is not valid JSON." % path)
    if not isinstance(data, dict) or not data.get("scenarios") or "run" not in data:
        raise BriefError("%s has no scenarios in it." % path)
    failed = [s.get("id", "?") for s in data["scenarios"] if not s.get("passed")]
    if failed:
        raise BriefError("Scenario%s %s failed QA in this run, so its numbers can't go in a brief. "
                         "Fix the failure and run the scenarios again." % ("" if len(failed) == 1 else "s",
                                                                          ", ".join(failed)))
    return data


def html_to_pdf(html_path, pdf_path, browser, wait_s=60):
    """Print the page with a headless browser. True if the PDF was made. As in
    map_snapshot.svg_to_png, waits for the file: on Windows the launcher can
    exit before it is written."""
    if os.path.exists(pdf_path):
        os.remove(pdf_path)
    profile = tempfile.mkdtemp(prefix="falcon-brief-")
    try:
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-pdf-header-footer",
                        "--user-data-dir=" + profile, "--print-to-pdf=" + os.path.abspath(pdf_path),
                        "file:///" + os.path.abspath(html_path).replace(os.sep, "/")],
                       capture_output=True, timeout=120)
        deadline = time.time() + wait_s
        while time.time() < deadline and not (os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 0):
            time.sleep(0.25)
        time.sleep(0.25)
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(profile, ignore_errors=True)
    return os.path.isfile(pdf_path) and os.path.getsize(pdf_path) > 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write a plain-language brief of a scenario run.")
    ap.add_argument("run", nargs="?", help="a run folder from run_scenarios.py (default: the newest)")
    ap.add_argument("--note", help="a paragraph of your own takeaways, shown at the top")
    ap.add_argument("--no-pdf", action="store_true", help="write the HTML only")
    args = ap.parse_args(argv)
    try:
        run_dir = args.run or newest_run()
        data = load_run(run_dir)
    except BriefError as e:
        print(e)
        return 1
    if "local changes" in data["run"].get("code", ""):
        print("Note: this run used code with uncommitted changes (%s)." % data["run"]["code"])
    html_path = write_brief(run_dir, data, args.note)
    print("Brief: %s" % html_path)
    if not args.no_pdf:
        browser = map_snapshot.find_browser()
        pdf_path = os.path.join(run_dir, "brief.pdf")
        if browser and html_to_pdf(html_path, pdf_path, browser):
            print("PDF:   %s" % pdf_path)
        else:
            print("No PDF: %s. Open the brief in a browser and print it to PDF instead." % (
                "the browser did not write one" if browser else "no Edge or Chrome found"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
