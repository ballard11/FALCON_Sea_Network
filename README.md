# FALCON Sea Network

Sea routes between any two of 3,806 ports, along real shipping lanes, with K
alternative routes. It contains a worldwide port and shipping-lane data model
for Neo4j, plus a small map that shows origin, destination and the K routes.

![Busan to Subic Bay, four distinct corridors](docs/distinct-corridors.jpg)

## Run it

Needs Python 3.8 or later and nothing else (standard library only).

```bash
python server.py
```

Then open http://localhost:8100. Type or pick an origin port and a destination
port, choose K (1 to 5) and a method, and select **Find routes**. Routes take
milliseconds.

## How routing works

The data does not store port-to-port lanes. It stores the **shipping-lane
network itself**: 4,525 sea junctions joined by 9,686 lane segments, built from
Eurostat's MARNET maritime network. Each port is linked to the lane beside it.
A route is the shortest path through that network, so any port can reach any
other, and every route follows real water around land and through real straits.

Ports are only a route's start and end, never a stop in between.

### Two ways to get K routes

| Method | What you get | When to use it |
|---|---|---|
| **Yen's K shortest** | The exact 1st, 2nd, ... Kth shortest routes | When you need the true ranking |
| **Distinct corridors** | The shortest route through each separate stretch of water | When you need alternatives a planner would recognise |

On a mesh of shipping lanes, Yen's algorithm finds thousands of nearly identical
routes, the same track nudged a few miles around one junction. Busan to Subic Bay
with K=3 gives 1,449.6, 1,450.0 and 1,450.9 nm, all through the Taiwan Strait,
drawn on top of each other.

Distinct corridors uses the via-point method. It works out the shortest distance
from the origin to every point in the network, and from every point to the
destination. With those two, the best route through any point X is known at once.
Points are taken cheapest first, and a route is kept only if X lies at least 10%
of the route's length (50 nm minimum) from every route already kept. Routes that
detour out and back are rejected. Each kept route is the shortest route through
its own waters.

| Busan to Subic Bay | Yen's K shortest | Distinct corridors |
|---|---|---|
| #1 | 1,450 nm, Taiwan Strait | 1,450 nm, Taiwan Strait |
| #2 | 1,450 nm, Taiwan Strait | 1,453 nm, Luzon Strait, east of Taiwan |
| #3 | 1,451 nm, Taiwan Strait | 1,587 nm, Luzon Strait, across the East China Sea |
| #4 | 1,451 nm, Taiwan Strait | 1,643 nm, Luzon Strait, east of the Ryukyu Islands |

## What's here

| Path | What it is |
|---|---|
| `neo4j-datamodel/` | The data, ready to load into Neo4j. Start with `START_HERE.md`. |
| `sea_routing.py` | Shortest path (A*), Yen's K shortest, and distinct corridors |
| `server.py` | A small web server for the map |
| `web/` | The map page, with d3-geo and the Natural Earth coastline |

To use the data in Neo4j instead, follow `neo4j-datamodel/START_HERE.md`.
`neo4j-datamodel/ASSUMPTIONS.md` lists every filter, default and known gap.

## Data sources and licences

| Data | Source | Licence |
|---|---|---|
| Ports | [NGA World Port Index (Pub 150)](https://msi.nga.mil/Publications/WPI) | U.S. government public data |
| Airfields and runways | [OurAirports](https://ourairports.com/data/) | Public domain |
| Shipping-lane network | [Eurostat searoute, MARNET network](https://github.com/eurostat/searoute), via [searoute-ts](https://github.com/mayurrawte/searoute-ts) | [EUPL-1.2](https://spdx.org/licenses/EUPL-1.2.html) |
| Coastline (map only) | [Natural Earth](https://www.naturalearthdata.com/) 1:50m land | Public domain |
| Map libraries | [d3-geo, d3-array](https://d3js.org/), [topojson-client](https://github.com/topojson/topojson-client) | ISC |

The sea network files in `neo4j-datamodel/` (`sea_nodes.csv`,
`sea_segments.csv` and the lane shapes in the port link files) are derived from
the Eurostat MARNET network and remain under EUPL-1.2.
