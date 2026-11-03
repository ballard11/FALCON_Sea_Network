# Tests

```bash
python -m unittest discover tests
```

Runs all 238 tests in about 40 seconds. It needs Python 3.8 or later and nothing
else. The tests also run under pytest (`python -m pytest tests`) if you
prefer it, but nothing here depends on it.

These tests check the **reference version**: the Python code in this
repository. To check a migrated server, use the acceptance test instead
(`acceptance/README.md`), which talks to any server over HTTP.

## What each file checks

| File | Checks |
|---|---|
| `test_sea_routing.py` | The routing core: distances, sampling a line, circles and polygons (including the date line and the poles), the port rule, A*, Yen's K shortest, distinct corridors, which links an area closes, and how a route is described. Uses the toy network for answers worked out by hand, plus checks on the real network (A* agrees with a full Dijkstra on 25 random pairs, and the area index finds what a full scan finds) |
| `test_server.py` | Every rule on a request, the shape of each answer, and the HTTP layer on a real socket: bad bodies, wrong lengths, a client that stops sending, an unexpected failure, simultaneous requests, and `python server.py` starting up, refusing a taken port and refusing bad data |
| `test_validate_data.py` | The data checks: each kind of broken row is caught and named, by file, row and field |
| `test_check_server.py` | The acceptance checker judges answers by the rules in `acceptance/README.md`, and reports a broken, garbled or silent server as a failure instead of crashing. Also `make_golden.py` |
| `test_acceptance.py` | The reference still gives the answers in `acceptance/golden.json` |
| `test_scenarios.py` | The scenario runner and its map pictures: each check, whole runs into a throwaway folder, broken scenario files, and escaping in the report |
| `test_brief.py` | The stakeholder brief: its wording, the sentence for each kind of answer, numbers that add up on the page, refusing failed runs, and a real run from scenarios to brief |
| `test_build.py` | The data rebuilds byte for byte from `data/`; the build's helpers; `tools/datamodel/check_routes.py` |
| `toy_network.py` | Not a test: a five-port network, small enough to check by hand, written as a real data model folder |

Areas of denial are also checked against brute force: 400 random circles and
short pieces of lane, judged against points every 0.05 nm.

## Coverage

```bash
python -m pip install coverage
python -m coverage run -m unittest discover tests
python -m coverage combine
python -m coverage report
```

`coverage` is a test tool only; the app doesn't need it. `.coveragerc` sets
it up, and includes the build, which the tests run as a separate process.
With coverage on, a run takes about 95 seconds. Results at v1.2:

| Module | Lines and branches covered |
|---|---|
| `sea_routing.py` | 100% |
| `server.py` | 100% |
| `validate_data.py` | 99% |
| `acceptance/check_server.py` | 99% |
| `acceptance/make_golden.py` | 95% |
| `run_scenarios.py` | 98% |
| `map_snapshot.py` | 99% |
| `make_brief.py` | 98% |
| `tools/datamodel/build.py` | 99% |
| `tools/datamodel/check_routes.py` | 98% |
| **All** | **99%** |

What's left is mostly the `if __name__ == "__main__":` line in each script,
and a few branches the real data never takes.

## Adding a test

Put it in the file for the module it tests. If it needs a network you can
reason about, use `toy_network.ToyFolder()`; if it needs the real one,
`import server` and use `server.GRAPH`, which is loaded once per run. A fix
for a bug should come with a test that fails without it.
