# Implementation Plan: Pareto Commute Agent

## Overview

Claude Haiku 5.5 subagents run these tasks. Each top-level task is one workstream from the design and touches only the files that workstream owns. Task 1 (WS0) runs alone. After its checkpoint, tasks 3 to 11 run in parallel against fixtures. A few subtasks wait on another workstream's module, as the dependency graph at the end lists. Tasks 13 to 15 bring in real data, integrate, and evaluate.

Rules for every implementer:

- Read the design sections named in your task before writing code. Use the exact names, signatures, and file paths given there.
- Edit only files your workstream owns in the design's file ownership table. If you need a change to `models.py`, `paths.py`, `routes.py`, or a fixture, stop and report it to the coordinator.
- If a dataset's columns or values differ from the design, stop and report what you saw. Do not adapt silently.
- Validate input only at ingest, config load, HTTP, and MCP tool boundaries. Do not add `try` blocks or `None` checks inside internal functions.
- Keep comments for a non-obvious reason only.
- A task is done when its named test command passes and its "done when" condition is observed. Paste the command output in your report.

## Tasks

- [ ] 1. Scaffold and shared contracts (WS0)
  - [ ] 1.1 Create the project skeleton
    - Run `uv init --package --name commute-agent` equivalents by hand: `pyproject.toml` with `requires-python = ">=3.12,<3.13"`, `.python-version` with `3.12`
    - Pin runtime dependencies exactly: `smolagents[mcp,openai]==1.26.0`, `mcp==1.30.0`, `openai==3.37.0`, `python-dotenv==1.2.4`, `osmnx==2.1.1`, `networkx==3.7`, `fastapi==0.162.0`, `uvicorn==0.54.0`, `pydantic==2.14.1`, `pyyaml==6.0.3`, `httpx==0.28.1`, `pandas==3.1.0`, `pyarrow==26.0.0`, `geopandas==1.2.0`, `shapely==2.2.0`, `scipy==1.18.1`, `polyline==2.0.4`
    - Pin dev dependencies exactly: `pytest==9.1.1`, `hypothesis==6.170.9`, `ruff==0.17.2`
    - Register the pytest marker `live` in `pyproject.toml` and add `-m "not live"` to default `addopts`
    - Add `data/raw/`, `data/processed/`, `data/otp/`, `data/cache/`, `data/preferences.db` to `.gitignore`
    - Run `uv lock` and `uv sync`
    - Done when `uv run python -c "import osmnx, smolagents, openai; from smolagents import MCPClient, OpenAIModel"` exits 0
    - _Requirements: 1.4, 17.1_
  - [ ] 1.2 Write `commute_agent/paths.py` and `commute_agent/models.py`
    - Copy both modules exactly from the design sections "WS0. Paths" and "Data Models"
    - Done when `uv run python -c "from commute_agent.models import PlanResult; PlanResult.model_json_schema()"` exits 0
    - _Requirements: 1.1, 1.2_
  - [ ] 1.3 Write `commute_agent/routes.py`
    - Implement `assemble_route`, `with_disruption`, and `DISRUPTION_SOURCE` as specified in "WS0. Route assembly"
    - _Requirements: 4.5, 5.8, 8.3, 8.4_
  - [ ] 1.4 Write property test for route assembly in `tests/test_routes.py`
    - **Property 15: Route assembly sums legs**
    - **Validates: Requirements 4.5, 5.8**
  - [ ] 1.5 Build shared fixtures in `tests/fixtures/`
    - Write `make_fixtures.py` that builds `graph_small.graphml` as the design's Testing Strategy describes, and commit its output
    - Hand-write `otp_fields_corner.json` in the exact shape of `PLAN_QUERY`, with one itinerary of walk, SUBWAY on `mbta:Red` with `directionId` `"1"` (toward Alewife) from stop `mbta:70090` (parent `mbta:place-fldcr`) to `mbta:70078` (parent `mbta:place-dwnxg`), with intermediate stop `mbta:70086` (parent `mbta:place-jfk`) among others, then walk. These are the real northbound platform ids from the MBTA V3 API; use a short real-looking encoded polyline for each leg
    - Write `mbta_alerts.json` as a V3 JSON:API document holding the three NOTICE alerts listed in the design plus the synthetic Red Line alert
    - Write `plan_result.json` as a valid `PlanResult` with four routes (one each of walk, bike, transit, drive) and validate it with `PlanResult.model_validate_json` in a test
    - _Requirements: 1.3_
  - [ ] 1.6 Write `tests/conftest.py`
    - Provide `make_route`, `objectives_strategy`, `fixture_graph`, and `fixture_paths` as named in the design's Testing Strategy
    - _Requirements: 1.3, 1.5_

- [ ] 2. Checkpoint, M0 gate
  - Run `uv run pytest` and `uv run ruff check`. Both must pass with no network access.
  - The coordinator diffs `commute_agent/models.py` against the design's Data Models block. Any difference blocks the start of tasks 3 to 11.

- [ ] 3. Ingest and emission factors (WS1)
  - [ ] 3.1 Write `commute_agent/emissions.py` and `data/emission_factors.example.yaml`
    - Implement `load_emission_factors`, `leg_co2_g`, `co2_source`, `EmissionFactorsError` as specified in "WS1. Emission factors"
    - Error messages name the file path, every missing mode, and every bad field in one message
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_
  - [ ] 3.2 Write property test for CO₂ linearity in `tests/test_emissions.py`
    - **Property 8: CO₂ is linear in distance**
    - **Validates: Requirements 3.4**
  - [ ] 3.3 Write unit tests for emission factor validation
    - Missing file, missing mode, null value, unsupported unit, and empty source each produce an error whose text names the offender
    - _Requirements: 3.2, 3.3_
  - [ ] 3.4 Write `commute_agent/ingest/meta.py`, `ingest/__main__.py`, and the `SchemaMismatch` helper
    - `require_columns`, `write_meta`, write-to-temp-then-rename, exit code 2 on `SchemaMismatch`
    - _Requirements: 2.1, 2.3, 2.4_
  - [ ] 3.5 Write `ingest/geo.py` for `boundary`, `graphs`, and `basemap`
    - Start `graphs` first in M1, because the Overpass download is the slowest step
    - Read boundaries from `data/raw/geo/` as listed in "WS1. Raw data"; do not download them
    - _Requirements: 2.1, 2.2, 4.1, 14.7_
  - [ ] 3.6 Write `ingest/crashes.py` (reads `data/raw/crashes/`, never downloads)
    - Follow "Crash rules in crashes.py" exactly, including one row per involved mode and the sidecar note on definitions
    - _Requirements: 2.4, 2.5, 2.6_
  - [ ] 3.7 Write `ingest/counts.py`
    - Follow "Peak-hour conversion rules in counts.py" exactly
    - Exit 2 with the expected path and columns when `data/raw/numina_zones.csv` is missing
    - _Requirements: 2.7, 5.2_
  - [ ] 3.8 Write `ingest/bluebikes.py` and `ingest/mbta.py`
    - Read only the zip member `202411-bluebikes-tripdata.csv`, drop rows without an end station, aggregate trips per station pair
    - `stations` reads `data/raw/transit/massgis_rapid_transit_stations.json`; `otp-inputs` copies the GTFS zip and `data/raw/geo/boston.osm.pbf` into `data/otp/`
    - _Requirements: 2.1, 2.2, 5.1_
  - [ ] 3.9 Write unit tests for ingest transforms in `tests/test_ingest_*.py`
    - Use small in-memory DataFrames: a Cambridge `Location` of `"8 SCOTT ST\nCambridge, MA\n(42.37886, -71.11025)"` parses to lat 42.37886 and lon -71.11025; a Cambridge crash with both flags yields two rows; a Vision Zero `mv` row yields none; a Somerville AM 120 and PM 80 bike count yields `peak_hour_volume` 100; a missing column raises `SchemaMismatch` naming it
    - _Requirements: 2.4, 2.6, 5.2_
  - [ ] 3.10 Write `data/showcase_trips.yaml` and `commute_agent/check_offline.py`
    - Copy the five trips from the design. `check_offline` lists missing paths and exits 1, or prints `offline ready`
    - Import `otp_cache_key` from `commute_agent.transit.otp` (task 6.1). Write the `check_offline` test after 6.1 lands
    - Add a test that each showcase point lies inside `boston_boundary.geojson`, skipped when that file is absent
    - _Requirements: 15.1, 15.4_

- [ ] 4. Street graph risk and exposure (WS2)
  - [ ] 4.1 Write `commute_agent/streets/risk.py`
    - `mean_crashes_per_km` and `edge_risk` as pure NumPy functions per "WS2. Crash risk"
    - _Requirements: 4.3, 4.4_
  - [ ] 4.2 Write property test for risk monotonicity in `tests/test_risk.py`
    - **Property 9: Risk is monotone**
    - **Validates: Requirements 4.3, 5.5**
  - [ ] 4.3 Write `commute_agent/streets/exposure.py`
    - `bluebikes_proxy`, `fit_calibration`, `road_class_prior`, `TooFewCounters` per "WS2. Exposure"
    - _Requirements: 5.1, 5.2, 5.4, 5.7_
  - [ ] 4.4 Write unit tests for exposure in `tests/test_exposure.py`
    - `fit_calibration([1, 2, 3], [2, 4, 6])` returns scale 2.0 and R² 1.0
    - On the fixture graph, two trips between the same corner stations add 2 to each edge of the shortest path and 0 elsewhere
    - A highway class with fewer than 20 measured edges gets the global median
    - _Requirements: 5.1, 5.2, 5.7_
  - [ ] 4.5 Write `commute_agent/streets/build.py`
    - `snap_points`, `build_risk`, `main` per "WS2. Crash risk"; reverse-edge crash sharing; discard and count snaps over 30 m; write `risk_meta.json` and `exposure_calibration.json`
    - Test `build_risk` on the fixture graph with three fixture crashes, one of them 40 m away, and assert `crashes_discarded_over_30m` is 1
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.3, 5.5, 5.6, 5.7_

- [ ] 5. Street router (WS3)
  - [ ] 5.1 Write `commute_agent/streets/router.py` and `streets/__init__.py`
    - `StreetGraphs`, `load_street_graphs`, `street_routes`, `score_walk_coords` per "WS3. Street router", with the λ sweep, edge-key selection, deduplication, bluebikes composition, and route ids and summaries as specified
    - Build each `RouteOption` with `routes.assemble_route`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 4.5, 4.7_
  - [ ] 5.2 Write unit tests in `tests/test_router.py` on the fixture graph
    - Raising one edge's risk makes the safer variant avoid that edge while `walk-1` still uses it
    - `score_walk_coords` on a polyline along one fixture edge returns that edge's risk within 1%
    - An origin on a disconnected fixture node reports `no path` in omitted kinds
    - _Requirements: 6.1, 6.5, 4.6_

- [ ] 6. Transit and disruptions (WS4)
  - [ ] 6.1 Write `commute_agent/transit/otp.py`
    - `PLAN_QUERY`, `otp_cache_key`, `fetch_itineraries`, `transit_routes` per "WS4. OTP client and cache"
    - Strip the `mbta:` prefix, decode polylines to `(lon, lat)`, convert `directionId` to int, score walk legs with `score_walk_coords`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5_
  - [ ] 6.2 Write unit tests in `tests/test_otp.py`
    - `transit_routes` on `otp_fields_corner.json` yields one route whose subway leg has `route_id == "Red"` and `"place-jfk"` in `stop_ids`
    - `fetch_itineraries(..., live=False)` with an empty cache dir returns `(None, "transit not cached for this trip")`
    - _Requirements: 7.2, 7.3, 7.5_
  - [ ] 6.3 Write `commute_agent/transit/alerts.py`
    - `EFFECT_SCORES`, `effect_score`, `parse_mbta_alerts`, `list_alert_sources`, `load_alerts`, `entity_matches`, `is_active`, `apply_disruptions`, `record_snapshot` and its CLI per "WS4. Alerts and disruption"
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 2.8_
  - [ ] 6.4 Write the synthetic snapshot `data/alert_snapshots/red-line-suspension-synthetic.json`
    - Exactly as the design describes, validated by `AlertSnapshot.model_validate_json` in a test
    - _Requirements: 8.7_
  - [ ] 6.5 Write property tests in `tests/test_alerts.py`
    - **Property 10: Unrelated alerts change nothing**
    - **Property 11: Route disruption is the leg maximum**
    - **Validates: Requirements 8.1, 8.3, 8.4**
  - [ ] 6.6 Write unit tests for alert matching
    - The synthetic alert gives the fixture Red Line route `disruption_risk` 1.0 and alert id `synthetic-red-1`; the same alert evaluated before its start time gives 0.0; an alert with effect `MADE_UP` lands in `unscored_alerts`
    - _Requirements: 8.2, 8.3, 8.5_
  - [ ] 6.7 Write `docker/otp/build-config.json`, `docker/otp/run.sh`, and `commute_agent/transit/cache_trips.py`
    - As specified in the design, including the exit-1 checks for empty itineraries and a missing Red Line leg
    - _Requirements: 7.6, 15.2_

- [ ] 7. Pareto engine and planner (WS5)
  - [ ] 7.1 Write `commute_agent/pareto.py`
    - Constants and functions exactly as in "WS5. Pareto engine"
    - _Requirements: 9.2, 9.3, 9.4, 9.5, 9.6_
  - [ ] 7.2 Write property tests in `tests/test_pareto.py`
    - **Property 1: Pareto soundness**
    - **Property 2: Pareto completeness**
    - **Property 3: Dominance is irreflexive and asymmetric**
    - **Property 4: Input order does not change the front**
    - **Property 6: Labels are unique and minimal**
    - **Property 7: Trade-off antisymmetry**
    - **Validates: Requirements 9.2, 9.3, 9.4, 9.5, 9.6**
  - [ ] 7.3 Write unit tests for Pareto edge cases
    - Two routes 0.3 min apart with otherwise equal objectives collapse to the one with fewer legs; a drive route with crash risk 0 does not get `lowest_crash_risk` when a bike route is present; `pct` is `None` when the reference CO₂ is 0
    - _Requirements: 9.4, 9.5, 9.6_
  - [ ] 7.4 Write `commute_agent/planner.py`
    - `PlannerContext`, `OutsideServiceArea`, `load_context`, `resolve_departure`, `gather_candidates`, `plan_trip`, and `FIXED_NOTES` per "WS5. Planner"
    - _Requirements: 9.1, 9.2, 9.7, 8.6_
  - [ ] 7.5 Write unit tests in `tests/test_planner.py`
    - With the fixture graph, fixture OTP cache, and a boundary polygon around the fixture, a plan returns a non-empty front with a `fastest` label; a point outside the polygon raises `OutsideServiceArea` naming `origin`
    - _Requirements: 9.1, 9.2_

- [ ] 8. Preferences (WS6)
  - [ ] 8.1 Write `commute_agent/preferences.py`
    - Functions and constants exactly as in "WS6. Preferences"
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_
  - [ ] 8.2 Write property tests in `tests/test_preferences.py`
    - **Property 5: Preference ordering keeps membership**
    - **Property 13: Preference fallback and normalization**
    - **Validates: Requirements 9.7, 10.2, 10.3**
  - [ ] 8.3 Write a unit test for learning direction
    - Five events where the user always picks the lowest-CO₂ route at a time cost give `weights["co2_g"]` larger than `weights["time_min"]`; after `clear_choices`, `current_weights` returns 0.25 each
    - _Requirements: 10.2, 10.5_

- [ ] 9. MCP server, gazetteer, agent, and grounding (WS7)
  - [ ] 9.1 Write `commute_agent/grounding.py` and its tests
    - `numbers_in`, `is_supported`, `unsupported_numbers` per "WS7. Grounding"
    - Unit test: reply "4 min slower, ~70% less CO2" against evidence `{"delta": 4.2, "pct": -69.6}` returns `[]`; reply "about 25 minutes" against the same evidence returns `["25"]`
    - _Requirements: 12.7_
  - [ ] 9.2 Write property test for grounding
    - **Property 12: Rounded evidence is supported**
    - **Validates: Requirements 12.7, 16.5**
  - [ ] 9.3 Write `commute_agent/gazetteer.py` and its tests
    - `normalize_name`, `build_gazetteer`, `geocode` per "WS7. Gazetteer"
    - Unit tests: "Park St" matches Park Street station with status `ok`; a two-candidate fixture over 150 m apart returns `ambiguous`; "Zzyzx" returns `not_found`
    - _Requirements: 11.2, 11.3_
  - [ ]* 9.4 Write property test for coordinate geocoding
    - **Property 14: Coordinate geocoding round trip**
    - **Validates: Requirements 11.2**
  - [ ] 9.5 Write `commute_agent/mcp_server.py` with `FastMCP` from `mcp.server.fastmcp`
    - `SessionState`, every tool in the design's tool table including `get_plan`, `plan_trip` emptying every leg's `coords` with the listed arguments and return types, the 60-second live alert cache, and `main` with `--live`
    - _Requirements: 11.1, 11.4, 11.5, 8.6_
  - [ ] 9.6 Write tests in `tests/test_mcp_server.py` calling `await mcp.call_tool(...)` in process
    - `plan_trip` on fixture state returns a result that validates as `PlanResult` with every `coords` empty, and `get_plan` returns the same plan with coords; `compare_routes` with an unknown id returns `is_error` true and keeps serving the next call; `set_alert_source("live")` offline returns an error
    - _Requirements: 11.1, 11.5_
  - [ ] 9.7 Write `commute_agent/agent.py`
    - `DEFAULT_MODEL`, `AGENT_TOOLS`, `SYSTEM_PROMPT` copied from the design, `ModelConfig`, `model_config`, `missing_model_keys`, `build_agent`, `Conversation`, `tool_calls_from_memory`, `run_turn`
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7, 12.8_
  - [ ] 9.8 Write tests in `tests/test_agent.py` with a stub `smolagents.Model` that scripts tool calls
    - A scripted `plan_trip` call followed by `final_answer` logs one `ToolCall`; `build_agent` exposes only `AGENT_TOOLS`; `missing_model_keys` returns `["OPENROUTER_API_KEY"]` when that key is unset, and `[]` for a keyless local endpoint (`COMMUTE_AGENT_API_BASE=http://localhost:11434/v1`, empty `COMMUTE_AGENT_API_KEY_ENV`); a final text containing a number absent from tool outputs lists it in `unsupported_numbers`
    - _Requirements: 12.1, 12.7_

- [ ] 10. API server (WS8)
  - [ ] 10.1 Write `commute_agent/api.py`
    - `create_app`, `main` with `--model`, `call_tool`, the `MCPClient` lifespan, every endpoint in the design's API table, and the startup log line
    - _Requirements: 13.1, 13.2, 13.3, 13.4_
  - [ ] 10.2 Write tests in `tests/test_api.py`
    - With the model's key unset, `POST /api/chat` returns 503 with a detail naming the model id and missing key; `POST /api/plan` with an outside point returns 422; `GET /` returns the HTML page
    - Run the real stdio MCP subprocess on fixture state through `MCPClient`
    - _Requirements: 13.1, 13.4_

- [ ] 11. Web UI (WS9)
  - [ ] 11.1 Vendor Leaflet 1.9.4 and Chart.js 4.6.1 into `web/vendor/` and commit them
    - _Requirements: 14.1_
  - [ ] 11.2 Write `web/index.html`, `web/style.css`, and `web/app.js`
    - Layout, state object, functions, and the WS9 rules from the design, developed against `tests/fixtures/plan_result.json`
    - _Requirements: 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 14.8, 14.9_
  - [ ] 11.3 Check the page by hand against the fixture
    - Serve with `uv run python -m http.server` and load the fixture plan. Save a screenshot showing four colored routes, four chart points, and a highlighted route in all three panels. Tab through every control and confirm each is reachable.
    - _Requirements: 14.2, 14.3, 14.9_

- [x] 11b. Next.js presentation app (WS9b)
  - Build `webapp/` per design "WS9b"; `npm run typecheck` and `npm run build` pass
  - Done when a headless browser run of the three demo steps against the real API shows the Pareto change banner, the alert header, an agent reply, zero console errors, and zero tile requests offline
  - _Requirements: 14.10_

- [ ] 12. Checkpoint, M1 gate
  - Run `uv run pytest` and `uv run ruff check` for the whole repo. Every test passes without network access.

- [ ] 13. Real data build (M2)
  - [ ] 13.1 WS1 runs `uv run python -m commute_agent.ingest all` and reports every sidecar row count
    - _Requirements: 2.1, 2.2, 2.3_
  - [ ] 13.2 WS2 runs `uv run python -m commute_agent.streets.build` and reports `exposure_calibration.json` and `risk_meta.json`
    - Stop and report when fewer than 10 counters match or the run passes 30 minutes
    - _Requirements: 4.1, 4.2, 5.3, 5.4_
  - [ ] 13.3 WS10 runs `uv run python eval/sample_pairs.py` and commits `eval/od_pairs.yaml`
    - _Requirements: 16.2_
  - [ ] 13.4 WS4 builds OTP with `docker/otp/run.sh build`, serves it, runs `cache_trips`, records two live snapshots, and replaces `otp_fields_corner.json` with the real cached response
    - Report OTP peak memory and the itinerary count per trip
    - _Requirements: 7.1, 7.6, 2.8, 15.2_
  - [ ] 13.5 Run `uv run python -m commute_agent.check_offline` and confirm it prints `offline ready`
    - _Requirements: 15.3, 15.4_

- [ ] 14. Integration (M3)
  - [ ] 14.1 Start `uv run python -m commute_agent.api` with Wi-Fi off and plan each Showcase_Trip from the UI
    - Each plan returns in under 5 seconds. Save one screenshot per trip.
    - _Requirements: 15.3, 13.1_
  - [ ] 14.2 Switch the alert source to the synthetic snapshot on the Fields Corner trip
    - The Red Line route shows `disruption_risk` 1.0 and the alert header, and the Pareto change banner lists at least one route entering or leaving. Save a screenshot.
    - _Requirements: 8.6, 8.7, 14.5_
  - [ ] 14.3 Ask the chat "How should I get from Fields Corner to Downtown Crossing?" with the default model, then restart with `--model` set to a second provider and ask again
    - Both replies name the agent an independent prototype, describe every route, and have no unsupported numbers. Save both reply JSONs.
    - _Requirements: 12.2, 12.5, 12.7, 12.8_

- [ ] 15. Evaluation and docs (WS10, M4 and M5)
  - [ ] 15.1 Write `eval/alert_expectations.yaml` and `eval/prompts.yaml` as the design describes
    - _Requirements: 16.4, 16.5_
  - [ ] 15.2 Write `eval/run.py` with every metric in the design's eval table, writing `eval/results.md` and `eval/results.json`
    - Agent metrics report `skipped: <missing keys>` when credentials are missing; `--model a,b` adds one column per model id
    - _Requirements: 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8_
  - [ ] 15.3 Run `uv run python -m eval.run` on real data and commit the results
    - Report every metric value. Any Pareto soundness or completeness failure, or any alert expectation failure, goes back to the owning workstream before M5.
    - _Requirements: 16.1_
  - [ ] 15.4 Write `README.md`
    - Setup commands, the user-supplied files, the offline demo script, every dataset with source URL and license or terms, "© OpenStreetMap contributors", the calibration R², and the eval table
    - _Requirements: 17.2_

- [ ] 16. Final checkpoint, M5 gate
  - Run `uv run pytest`, `uv run ruff check`, and `check_offline`. Run the full demo twice with Wi-Fi off. Report any step that failed.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP.
- Every property test references its design property by number in its docstring.
- Checkpoints 2, 12, and 16 are coordinator gates. No later task starts until the gate passes.

## Task Dependency Graph

```json
{
  "waves": [
    {"id": 0, "tasks": ["1"]},
    {"id": 1, "tasks": ["2"]},
    {"id": 2, "tasks": ["3", "4", "5", "6", "7", "8", "9", "10", "11"]},
    {"id": 3, "tasks": ["12"]},
    {"id": 4, "tasks": ["13"]},
    {"id": 5, "tasks": ["14", "15.1", "15.2"]},
    {"id": 6, "tasks": ["15.3", "15.4"]},
    {"id": 7, "tasks": ["16"]}
  ],
  "dependencies": {
    "3.10": ["6.1"], "6.1": ["5.1"], "7.5": ["5.1", "6.1", "6.3", "8.1"],
    "9.5": ["7.4", "8.1", "9.3"], "9.6": ["9.5"], "10.2": ["9.5", "9.7"],
    "13.2": ["13.1"], "13.4": ["13.1"], "13.3": ["13.1"], "13.5": ["13.2", "13.4"],
    "14": ["13.5"], "15.3": ["14", "15.2"]
  }
}
```
