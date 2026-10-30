# Cargo scenarios

LOGCAP cargo moved through the route scenarios in `scenarios/`. Each leg of a
move is carried either by a **LOGCAP commercial contractor** or by the
**military**. Commercial contractors do not operate inside a **weapons
engagement zone (WEZ)**, so they carry each class of supply to the nearest
port outside it that can take that class, and the military takes it on from
there. The report shows who carries what, where it changes hands, and the
bottlenecks each class of supply runs into.

```bash
python run_cargo_scenarios.py
```

It takes a few seconds. Each run writes a new report to
`reports/cargo/<date>_<time>/`:

- `report.html` is one file with the maps inside, ready to send as it is.
  It opens with a table of every class against every scenario. Each scenario
  then lists its **routes**: classes that travel the same legs make one route,
  and its bottlenecks, risks and checks are merged, each naming the classes it
  applies to.
- `movements.csv` has one row per scenario and class, with its route number,
  for a planner to filter, sort or take into a spreadsheet.
- `results.json` holds the same results for other tools.
- `snapshots/` holds two maps per scenario, as SVG (and PNG with Edge or Chrome).

The command exits with 1 if any check fails. The same planning is on the web
map: run `python server.py`, choose **Cargo**, and pick a scenario or draw
your own areas. The results show one card per route; hover a card to see its
legs on the map, and click it for the details.

## How cargo moves

There are two kinds of area. Both are drawn the same way, as circles or polygons.

| Area | LOGCAP commercial contractor | Military |
|---|---|---|
| **Area of denial** | Cannot enter | Cannot enter |
| **Weapons engagement zone (WEZ)** | Cannot enter | May enter |

For each class of supply in the cargo:

1. **Commercial the whole way** if a commercial carrier can reach the
   destination without entering a WEZ. A WEZ may still make it go further.
2. **Commercial, then military** if it cannot. The commercial carrier takes
   the class to its **handover port**: the port outside every area, in the
   scenario's handover countries, rated Confirmed or Inferred for that class,
   with the shortest military leg to the destination. The military carries it
   from there.
3. **Military, then commercial** when the origin is the end inside a WEZ
   (retrograde, say). The military carries the class out to the handover port
   nearest the origin, and a commercial carrier takes it on.
4. **Military the whole way** when no handover port saves the military at
   least 250 nm of sailing, when no port can take the class, or when both
   ends are inside a WEZ.

The port ratings are the ones in `cargo/port_cargo_capability.csv`, from
`cargo_capability.py`, with the sourced corrections in
`cargo/port_overrides.csv` applied. **Unknown in the World Port Index means no
information, not No**: a port rated Unknown is never used as a handover port,
but the report names it when it would be nearer, so a planner can check it
and add an override.

The rules and nominal values are in `cargo/movement_rules.json`: the carrier
names and speeds, the statuses a handover port needs, the 250 nm window, the
default handover countries and the default cargo.

## What each class runs into

Every class in every scenario gets a list of flags, at four levels:

| Level | Meaning | Flags |
|---|---|---|
| **Bottleneck** | Forces a worse plan for this class | No handover port can take it · the military carries it much further than other classes · the origin cannot load it or the destination cannot receive it (Ruled out) · both ends inside a WEZ · no route at all |
| **Risk** | The plan depends on one thing | Only one handover port within 250 nm of the best |
| **Check** | The data is thin | The handover port is only Inferred · the World Port Index cannot say whether the origin or destination can handle it · a nearer Medium or Large port is Unknown for it |
| **Note** | Explains the plan | The far end is the nearest fallback · a handover would save too little |

"Much further" means more than 250 nm more military sailing than the class
with the shortest military leg in the same scenario.

## The scenarios

Each builds on the route scenario with the same number. A 600 nm WEZ centred on
the Taiwan Strait (23.5°N 121.0°E) is used in eight of them, so the same zone
can be compared across moves. It takes in Taiwan, Okinawa, and Luzon as far south as
Manila and Batangas.
C09 uses a 450 nm WEZ over the East China Sea (29.5°N 125.5°E), which takes in
Sasebo. Every scenario carries all nine classes, except C09, which carries
retrograde of major end items (VII) and repair parts (IX).

Results of the run on 30 October 2026:

| Scenario | Who carries what | Handover ports | Bottleneck classes |
|---|---|---|---|
| C01 San Diego to Subic Bay, no WEZ | Commercial the whole way, 6,733 nm | none | none |
| C02 San Diego to Subic Bay, San Bernardino Strait denied, WEZ | Commercial, then military | Cagayan de Oro for eight classes (military 644 nm); Bundaberg, Australia, for V (military 3,265 nm) | V |
| C03 Yokosuka to Singapore, Luzon Strait denied, WEZ | Commercial the whole way, 315 nm further to keep out of the WEZ | none | none |
| C04 Guam to Manila, Luzon and San Bernardino Straits denied, WEZ | Commercial, then military; V by military the whole way (1,864 nm) | Cagayan de Oro (military 650 nm) | V |
| C05 Honolulu to Kao-Hsiung, WEZ instead of S05's area of denial | Commercial, then military | Mokpo, South Korea, for eight classes (military 869 nm, 705 of it in the WEZ); Bundaberg for V (military 3,741 nm) | V |
| C06 Colombo to Yokosuka, Malacca denied, WEZ | Commercial the whole way, 249 nm further | none | none |
| C07 Diego Garcia to Yokosuka, Indonesian straits denied, WEZ | Commercial the whole way, 98 nm further | none | none |
| C08 Singapore to Manila, central South China Sea denied, WEZ | Commercial, then military; V by military the whole way (2,150 nm) | Cebu South Port for I, II and IV (military 605 nm); Cebu for III; Cagayan de Oro for VI to IX (650 nm) | V |
| C09 Retrograde, Sasebo to Guam, WEZ over the East China Sea | Military, then commercial | Kure (military 236 nm) | none |
| C10 Manila to Puerto Princesa, Mindoro Strait denied, WEZ | Military the whole way (672 nm); no handover would save more than 11 nm | none | I, II, III, VI, VII, VIII, IX |

What the runs show:

- **Ammunition (Class V) is the bottleneck wherever cargo must change hands.**
  In the handover countries, the World Port Index confirms only two ports for
  it, Geelong and Bundaberg in Australia, so the military carries it 1,864 to
  3,741 nm against 605 to 869 nm for the other classes. This is a gap in the
  data, not a finding about the ports: Class V is never inferred, and most
  ports are Unknown for it. Each Class V row names the nearest Unknown port
  that would shorten the military leg if a planner confirmed it.
- **Handover ports in the southern Philippines are thin.** Cagayan de Oro is
  only Inferred for every class, and in C02 and C04 no other port within
  250 nm can stand in for it.
- **Container-only classes hand over further away.** In C08, classes that can
  move as breakbulk hand over at Cebu South Port; VI to IX go 45 nm further,
  because the World Port Index says nothing about containers, Ro-Ro or heavy
  lift there.
- **Small destinations limit every carrier.** Puerto Princesa's cargo pier
  (7.9 m) is too shallow for the usual ship of seven classes (C10).
- **A WEZ costs commercial carriers distance even with no handover**: 98 to
  315 nm in C03, C06 and C07.

## A scenario file

```json
{
  "id": "C05",
  "base": "S05",
  "name": "Honolulu to Kao-Hsiung, Taiwan inside a WEZ instead of an area of denial",
  "purpose": "One or two sentences on what this scenario shows. It goes in the report.",
  "areas": [],
  "wez": [{"type": "circle", "center": [121.0, 23.5], "radius_nm": 600}],
  "expect": {
    "plan": {"*": "commercial_military"},
    "handover": {"*": "Mokpo", "V": "Bundaberg"},
    "military_nm": {"I": {"min": 800, "max": 950}},
    "bottleneck_classes": ["V"]
  }
}
```

| Field | Required | Meaning |
|---|---|---|
| `id` | yes | Short, unique label, such as `C05` |
| `base` | yes | The route scenario in `scenarios/` this builds on. Its `from`, `to` and `areas` are used unless this file gives its own. |
| `name` | yes | One line for the report |
| `purpose` | no | What the scenario is meant to show |
| `from`, `to` | no | Override the base's ports: an exact World Port Index name or a port id |
| `areas` | no | Areas of denial. Default: the base's. `[]` removes them. |
| `wez` | no | Weapons engagement zones, in the same form as areas. Points are `[longitude, latitude]`. |
| `cargo` | no | `[{"class": "VII", "short_tons": 1200}, ...]`. Default: all nine classes, in the quantities in `cargo/movement_rules.json` |
| `handover_countries` | no | The only countries whose ports may be handover ports. Default: the list in `cargo/movement_rules.json`. `[]` allows any. |
| `expect` | no | What must be true; see below |

### Expectations

Leave out any you don't need. An unknown key fails the scenario. In `plan`,
`handover` and `military_nm`, `"*"` stands for every class in the cargo that
is not named.

| Key | Example | Passes when |
|---|---|---|
| `plan` | `{"*": "commercial_military", "V": "military"}` | Each class is carried this way: `commercial`, `commercial_military`, `military_commercial`, `military` or `blocked` |
| `handover` | `{"*": "Mokpo", "V": null}` | Each class changes hands at this port (a name or a port id; `null` for none) |
| `military_nm` | `{"V": {"min": 3500, "max": 4000}}` | The military carries the class this far |
| `bottleneck_classes` | `["V"]` | Exactly these classes have a bottleneck |
| `commercial_extra_nm` | `{"min": 250, "max": 400}` | The WEZ adds this much to the commercial route (when commercial carriers make the whole trip) |
| `blocked_contains` | `"inside an area of denial"` | The reason no route is possible contains this text |

The expectations in these ten files record the results of the first run
(above), after Claude checked each one was plausible. They catch changes to
the data or the code; they are not independent proof that the plan is right.

### Checks every scenario gets

- Each class's legs join end to end, from the origin to the destination.
- No commercial leg enters a WEZ or an area of denial, re-checked every 2 nm.
- No military leg enters an area of denial, re-checked every 2 nm.
- Every handover port is outside every area, in a handover country, and rated
  Confirmed or Inferred for its class.
- Every leg starts and ends at its ports, matches its reported length within
  2%, and is no shorter than the great-circle distance.

## Assumptions and judgements

For traceability, these are the choices behind the model, and who made them.
"User" means the instruction for this phase; "Claude" means a choice Claude
made while building it, which a planner should review.

| # | Choice | Made by | Where to change it |
|---|---|---|---|
| M1 | Every leg is carried by a LOGCAP commercial contractor or by the military. Commercial carriers never enter a WEZ; the military may. Nobody enters an area of denial. | User | `cargo_movement.py` |
| M2 | "The nearest area not within a WEZ" is read as the port outside the WEZ with the **shortest military sail to the destination**, not the nearest point on the commercial route. Ties go to the shorter commercial leg. | Claude | `cargo_movement.py` |
| M3 | A handover port must be rated Confirmed or Inferred for the class. One rating, using the class's usual ship, covers both the commercial ship unloading and the military loading. | Claude | `movement_rules.json` (`statuses`), `cargo/cargo_classes.json` |
| M4 | The origin (or, for retrograde, the destination) is always a fallback, whatever its rating: the military can carry the whole way. Without this, ammunition from Guam to Manila went by commercial ship to Australia so that the military could sail 3,271 nm back, instead of 1,864 nm from Guam. | Claude | `cargo_movement.py` |
| M5 | A handover is used only if it saves the military at least 250 nm. Without this, Manila to Puerto Princesa handed over at Cebu to save 11 nm. The same 250 nm decides backups and "much further". | Claude | `movement_rules.json` (`window_nm`) |
| M6 | Handover countries: United States, Guam, Northern Mariana Islands, Japan, South Korea, Philippines, Australia, Singapore, Palau, Micronesia, Marshall Islands, Papua New Guinea. An illustrative list for the demo, not a statement of any agreement. Without a list, the first run handed Taiwan's cargo over in mainland China. | Claude | `movement_rules.json` (`countries`) or a scenario's `handover_countries` |
| M7 | WEZ shapes: a 600 nm circle centred on 23.5°N 121.0°E, and a 450 nm circle centred on 29.5°N 125.5°E. Chosen to make the scenarios show different cases, not taken from any weapon's range. | Claude | each scenario's `wez` |
| M8 | Cargo quantities (500 short tons of Class I, and so on) are illustrative, not planning factors. They only feed `military_ton_nm`. | Claude | `movement_rules.json` (`manifest`) |
| M9 | Both carriers sail at a nominal 18 knots, as the route map does. Time in port and at the handover is not counted. | Claude | `movement_rules.json` (`knots`) |
| M10 | A port is inside an area if its position is. A port outside whose link to the shipping lanes crosses an area cannot be reached, so it cannot be a handover port. | Claude | `cargo_movement.py` |
| M11 | Only Medium and Large ports are named as "nearer Unknown" ports to check, so the report does not suggest confirming small fishing harbours. | Claude | `cargo_movement.py` |
| M12 | Honolulu's cargo pier depth is overridden to 11.0 m (the World Port Index has 6.4 m), from a third-party port listing Claude fetched and quoted. It gives no source or date; confirm it with Hawaii DOT Harbors. Without it, every class in C05 was flagged because Honolulu could not load it. | Claude | `cargo/port_overrides.csv` |

### What was done in code, and what by Claude

| Step | Code or Claude |
|---|---|
| Rating every port for every class | Code: `cargo_capability.py` |
| Routes, handover ports, flags and distances | Code: `cargo_movement.py`, `sea_routing.py` |
| Checks and the report | Code: `run_cargo_scenarios.py`, `map_snapshot.py` |
| The rules M1 to M11 | Claude, from the instruction for this phase |
| Choosing each scenario's WEZ, cargo and purpose | Claude, trying candidate WEZs in throwaway scripts and keeping ones that each show a different case |
| Checking surprising results: Mokpo as the Taiwan handover port was checked against a list of the nearest ports outside the WEZ and their ratings; Cebu South Port's ratings were read to explain C08 | Claude, with throwaway scripts; the results are in the ratings CSV |
| Writing the expectations from the first run | Claude |
| The Honolulu override (M12) | Claude, web search and page fetch |
