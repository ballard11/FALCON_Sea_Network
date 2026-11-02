# Cargo capability

Rates every port for each class of supply as **Confirmed**, **Inferred**,
**Unknown** or **Ruled out**, from the NGA World Port Index fields in
`neo4j-datamodel/nodes/ports.csv`.

```bash
python cargo_capability.py
```

It writes `port_cargo_capability.csv` (3,806 ports × 9 classes) and prints a
summary. It takes about a second and needs only the Python standard library.

| File | What it is | Edit it? |
|---|---|---|
| `cargo_classes.json` | What each class needs: required fields, the typical ship, how gaps are inferred | Yes, to change the rules |
| `port_overrides.csv` | Sourced corrections for key ports. These win over the World Port Index | Yes, to add a port or fact |
| `port_cargo_capability.csv` | The ratings | No, it is regenerated |
| `movement_rules.json` | Carriers, the handover rule, handover countries and the default cargo, for `cargo_movement.py` | Yes, to change how cargo moves |

## How a port is rated

Each class lists **requirements**. A requirement is a set of fields where any
one being Yes is enough. For example, Class VII is met by `fac_roro` *or*
`lifts_100t_plus`.

| Status | When |
|---|---|
| **Ruled out** | A requirement is known No, or the port is more than 2 m shallower than the class's ship |
| **Confirmed** | Every requirement is Yes, and the depth is known and deep enough |
| **Inferred** | A requirement is Unknown, but the port is Medium or Large, has a cargo pier at least 9 m deep, and has related equipment (cranes, lifts, rail). Also used when every requirement is Yes but the depth is marginal or not recorded |
| **Unknown** | Not enough to say either way |

**Unknown is never treated as No.** In the World Port Index, "Unknown" means
no information. Most ports are Unknown for most cargo facilities.

**Depth** uses the port's maximum vessel draft where NGA records it, and
otherwise its deepest cargo berth. NGA's depths are mostly the lower edge of a
5 ft band, so a port up to 2 m shallower than the ship is **marginal**: it is
rated Inferred, not Ruled out. Channel depth is not used, because for large
ports it is often an older or secondary channel. The ship drafts are nominal
values for the demo, set in `cargo_classes.json`.

## Supply classes

| Class | Moves as | Requires (any one of) | Typical ship |
|---|---|---|---|
| I Subsistence | Containers, some refrigerated | Container, Breakbulk | Container, 11 m |
| II Clothing and equipment | Containers | Container, Breakbulk | Container, 11 m |
| III Petroleum | Liquid bulk | Liquid Bulk, Oil Terminal | Tanker, 11 m |
| IV Construction material | Breakbulk, dry bulk | Breakbulk, Solid Bulk | Breakbulk, 9 m |
| V Ammunition | Explosive cargo | Dangerous Cargo Anchorage, **and** Breakbulk or Container | Breakbulk, 9 m |
| VI Personal demand items | Containers | Container | Container, 11 m |
| VII Major end items | Ro-Ro, heavy lift | Ro-Ro, Lifts 100+ t | Ro-Ro, 10 m |
| VIII Medical supplies | Containers, some refrigerated | Container | Container, 11 m |
| IX Repair parts | Containers | Container | Container, 11 m |

**Class V is never inferred.** Explosives handling is only accepted from a
recorded Yes or an override.

**Class III ship fuel is not bulk fuel.** For Class III, inference uses
`supply_fuel_oil` and `supply_diesel`, which are bunkering supplies for
ships. That is a hint that fuel infrastructure exists, not that the port
receives bulk fuel.

Each rating also lists `supports_yes`: the class's supporting fields that are
Yes, such as electricity for refrigerated containers, or rail and airport for
onward movement.

## Results, Large and Medium ports (550)

| Class | Confirmed | Inferred | Unknown | Ruled out |
|---|---|---|---|---|
| I Subsistence | 113 | 233 | 55 | 149 |
| II Clothing and equipment | 113 | 233 | 55 | 149 |
| III Petroleum | 109 | 256 | 36 | 149 |
| IV Construction material | 138 | 230 | 101 | 81 |
| V Ammunition | 15 | 0 | 449 | 86 |
| VI Personal demand items | 104 | 232 | 57 | 157 |
| VII Major end items | 193 | 165 | 62 | 130 |
| VIII Medical supplies | 104 | 232 | 57 | 157 |
| IX Repair parts | 104 | 232 | 57 | 157 |

## Overrides

`port_overrides.csv` sets one field per row: `port_id`, `field` (a
`ports.csv` column), `value`, `source` (a link), `checked` (date),
`checked_by` (who checked it, and how) and `note`. The script stops if a port or field name is wrong. Each rating lists
any overridden fields it used, in its `overridden` column.

The 15 overrides so far cover Apra Harbor, Manila, Subic Bay, Naha, Busan,
Keppel (Singapore), Yokosuka, Honolulu and San Juan (Puerto Rico). They come from port authority,
operator, trade press and port-listing pages found by Claude on 30 October
2026. For 11 of them Claude read only the search engine's summary, not the page
itself, as `checked_by` records; a person should open each link before the
demo. The Yokosuka Ro-Ro entry rests on a secondary source and should be
confirmed. The Honolulu cargo pier depth (11.0 m, where the World Port Index
has 6.4 m) comes from a third-party port listing that gives no source or date;
confirm it with Hawaii DOT Harbors. San Juan's maximum draft and container
terminal come from the Puerto Nuevo terminal operator's page, fetched and
quoted. Diego Garcia, Sasebo,
Kao-Hsiung and Darwin were not researched and use the World Port Index alone.

To add one, copy a row, fill in a source link, and rerun the script.

## Moving cargo

`cargo_movement.py` uses these ratings to move cargo through a scenario:
which ports can be handover ports between a LOGCAP commercial contractor and
the military, for each class. See
[cargo_scenarios/README.md](../cargo_scenarios/README.md). Its rules are in
`movement_rules.json`.
