# commute-agent

commute-agent plans trips inside Boston and shows the trade-offs instead of picking one answer for you. For a trip, it compares walk, bike, Bluebikes, drive, and MBTA routes on four numbers: travel time, CO₂, crash risk on the walking and biking parts, and MBTA disruption risk. It keeps every route that no other route beats on all four (the Pareto set) and drops the rest. An LLM agent then explains the options through MCP tools, and it may quote only numbers the planner returned.

This is an independent prototype. It is not an MBTA or City of Boston service.

The hosted demo is at <https://commute-agent-5hla.onrender.com>. [DEMO.md](DEMO.md) is a script for presenting it.

Run locally, the app works offline by default. Transit comes from cached OpenTripPlanner responses, the map from a local basemap, and alerts from recorded snapshots.

## Setup

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is only needed to rebuild or serve OTP.

```sh
uv sync
```

### Files you supply

Two inputs need your attention before the first run.

1. `data/emission_factors.yaml` is committed with sourced factors (EPA GHG Emission Factors Hub 2025, Table 10, and UK DESNZ 2026 for ferry; each entry names its source). To use other factors, keep the shape of `data/emission_factors.example.yaml`: every mode needs a numeric `value`, a `unit` of `g_per_passenger_km` or `g_per_passenger_mile`, and a non-empty `source`. Every entry point exits with code 2 if the file is missing or invalid.

2. `data/raw/numina_zones.csv`. This maps each Boston Numina zone id to its sensor site coordinates, because the Numina zone CSVs have none. A candidate is committed at `data/numina_zones.candidate.csv`. It quotes the site coordinates from the Numina project overview PDF. Check it, then copy it after fetching the raw data:

   ```sh
   cp data/numina_zones.candidate.csv data/raw/numina_zones.csv
   ```

### Check offline readiness

```sh
uv run python -m commute_agent.check_offline
```

It prints `offline ready`, or lists each missing file by path and exits non-zero.

## Build the data

`data/raw/`, `data/processed/`, `data/otp/`, and `data/cache/` are gitignored, so a fresh clone has none of them. Download the raw files first; each script writes `data/raw/<group>/` and a `MANIFEST.json` with the source URL, retrieval time, SHA-256, size, record count, and license. `geo.py` also needs `osmium` on `PATH`.

```sh
for group in geo crashes counts transit; do uv run --no-project --python 3.12 python scripts/fetch/$group.py; done
```

1. Ingest every dataset into `data/processed/`. The `graphs` step downloads the walk, bike, and drive graphs from the Overpass API once.

   ```sh
   uv run python -m commute_agent.ingest all
   ```

   To run one step, name it: `boundary`, `graphs`, `basemap`, `crashes`, `counts`, `bluebikes`, `stations`, or `otp-inputs`.

2. Add crash risk and exposure to the street graphs. This writes `risk_meta.json` and `exposure_calibration.json`.

   ```sh
   uv run python -m commute_agent.streets.build
   ```

3. Sample the 20 evaluation trips. The output, `eval/od_pairs.yaml`, is committed. Run this only to regenerate it.

   ```sh
   uv run python eval/sample_pairs.py
   ```

## OpenTripPlanner

OTP reads `data/otp/` (the MBTA GTFS zip and the clipped Boston OSM extract). The script copies `docker/otp/build-config.json` into `data/otp/` before each run. It runs the `opentripplanner/opentripplanner:2.10.0` image with a 6 GB heap. The measured peak during the build was 3.75 GiB.

```sh
docker/otp/run.sh build    # writes data/otp/graph.obj (about 112 MB, under a minute)
docker/otp/run.sh serve    # serves http://localhost:8080/otp/gtfs/v1
```

With OTP serving, fill the transit cache for the showcase trips and the eval trips:

```sh
uv run python -m commute_agent.transit.cache_trips
```

It writes one file per trip to `data/cache/otp/`. The cache is keyed by weekday and departure time, so a cached Tuesday 08:15 trip serves any later Tuesday. For each showcase trip, it also caches the itineraries that avoid any route a committed alert snapshot suspends, so those alternatives work offline. Offline, a request whose ends are within 300 m of a showcase trip is planned from that trip's points at its fixed demo departure, and the plan notes say so. Stop OTP afterwards with `docker stop <container>`.

Record a live MBTA alert snapshot (the MBTA V3 API needs no key):

```sh
uv run python -m commute_agent.transit.alerts record "Live capture 2026-10-24"
```

## Run

```sh
uv run python -m commute_agent.api
```

Open <http://127.0.0.1:8000/>.

### Next.js demo page

`webapp/` is a single map-first page for live demos. It talks to the same API and needs Node.js 20 or later.

```sh
cd webapp
npm install        # once, needs network
npm run build
npm run start      # http://127.0.0.1:3000/, keep the API running on port 8000
npm run test:demo  # optional: Playwright drives the demo end to end (starts both servers)
```

It plans Fields Corner to Downtown Crossing on load and draws the selected route on a street map, with the other options muted. Under All trade-offs, a heading counts the Pareto routes out of all candidates, and a greyed "Not on the frontier" list shows each dropped candidate with the route that beats it. Use the From and To boxes (MBTA stations, Bluebikes docks, showcase places, or `lat,lon`) or the demo trip chips. Pick what matters with Fastest, Lowest CO₂, Safest, Least disrupted, or All trade-offs; a single objective shows only that route. The switch "Simulate Red Line suspension" applies the synthetic alert. "Ask the agent" opens the chat.

The page loads OpenStreetMap tiles when it can reach them and falls back to the local basemap offline. Offline, transit is available only for cached trips; other trips show walk, bike, Bluebikes, and drive routes. Set `COMMUTE_API_URL` if the API runs elsewhere.

| Flag | Effect |
|---|---|
| `--live` | Calls OTP at `localhost:8080` (and writes the cache), enables the live MBTA alerts source, and adds OSM map tiles. |
| `--model <id>` | Sets the agent's model id on the configured endpoint. The default is `deepseek/deepseek-v4.1-flash-1017` on OpenRouter. The `COMMUTE_AGENT_MODEL` variable does the same. |
| `--port <n>` | Port to bind. The default is 8000. |

The chat needs the credentials of the chosen model. The default model needs `OPENROUTER_API_KEY`. The API and `eval.run` read it from `.env` in the repository root; a variable already set in the shell wins. `.env` is gitignored.

The agent talks to any OpenAI-compatible endpoint. To use another one, set `COMMUTE_AGENT_API_BASE` and `COMMUTE_AGENT_API_KEY_ENV` (the name of the variable that holds its key). For a local Ollama server, set `COMMUTE_AGENT_API_BASE=http://localhost:11434/v1`, `COMMUTE_AGENT_API_KEY_ENV=` (empty), and `--model qwen3:8b`. Without them, `/chat` returns 503 and names the missing key. The map and the route list still work.

The server has no authentication. It binds to 127.0.0.1 only. Use it for local demos only.

Chat has two in-memory rate limits in `commute_agent/api.py`: `CHAT_LIMIT_PER_SESSION = (5, 60)` (5 messages per `X-Session-Id` per 60 s) and `CHAT_LIMIT_GLOBAL = (60, 60)` (60 messages per 60 s for the whole server). Over a limit, `/api/chat` returns 429 with a `Retry-After` header. The counts reset when the server restarts.

`COMMUTE_ALLOWED_HOSTS` is a comma-separated list of host names, for example `localhost,commute-agent.example`. When it is set, a request whose `Host` header (without port) is not listed gets 400. Unset allows every host, as before. The hosted deploy proxies `/api` through Next.js, so list the host name the API process actually sees in the `Host` header, which may be the upstream name and not the public domain. Check it on the deployed service before relying on the guard.

## Deploy (Render)

`Dockerfile` builds one container: FastAPI and the MCP server on the container's loopback, and the Next.js page on `$PORT`. Only Next.js is public. The processed graphs, basemap, and OTP cache (about 35 MB compressed) come from the `data-v1` GitHub release, because they are not in git. `render.yaml` defines the service on the `2c-4g` plan: the app peaks at about 3.2 GB of memory, so smaller plans run out of memory. The first start takes about 80 s while the street graphs load.

`.github/workflows/deploy.yml` runs the tests on every push to `main`, then deploys that commit with the Render CLI and waits for it to go live. It needs two repository secrets: `RENDER_API_KEY` and `RENDER_SERVICE_ID`. Set `OPENROUTER_API_KEY` on the Render service for the chat.

Transit for trips outside the cache needs OpenTripPlanner. Set `COMMUTE_OTP_URL` (for example `http://localhost:8080/otp/gtfs/v1`) and offline mode asks that server whenever the cache has no entry, then caches the answer. The demo trips still come from the cache. On Render, `render.yaml` adds a private service, `commute-otp`, built from `docker/otp-render/Dockerfile`. It downloads the saved graph from the `otp-graph.obj` asset on the `data-v1` release and runs on the `standard` plan (2 GB; OTP uses about 1.4 GB). The web service gets its address as `COMMUTE_OTP_HOSTPORT`. Without that variable, the app behaves as before.

The hosted demo has no login. Anyone with the link can use the chat, which spends the OpenRouter key; keep a credit limit on it. Each browser tab sends an `X-Session-Id` header and gets its own alert source, so one visitor's "Simulate Red Line suspension" does not change anyone else's plans. Route choices ("I'll take this route") are still shared: they all go into one preference store, which orders routes for every visitor.

## How the Pareto set is computed

Each candidate route gets four numbers, and lower is better for all of them:

- `time_min`: the sum of leg durations. Drive time is free flow.
- `co2_g`: the sum over legs of distance times the mode's factor in `data/emission_factors.yaml`.
- `crash_risk`: the sum of edge risk along walk and bike legs, where edge risk is `(crashes + p * length_km) / exposure * 1000` and `p` is the network mean crashes per km. Drive legs and transit vehicle legs count as 0 because they are not modeled.
- `disruption_risk`: the highest alert score on any transit leg (suspension 1.0, shuttle 0.8, detour 0.5, and so on, in `EFFECT_SCORES` in `commute_agent/transit/alerts.py`).

Route A dominates route B when A is no worse than B on every objective, allowing a tolerance, and better than B by more than the tolerance on at least one. The tolerance for each pair is `max(abs_tol, rel_tol * max(|a|, |b|))`: 0.5 min for time, 1 g or 1% for CO₂, 0.01 or 2% for crash risk, and 0.01 for disruption. `pareto_front` in `commute_agent/pareto.py` keeps every candidate that no other candidate dominates, then drops any route within tolerance of one already kept on all four objectives. If the tolerance creates a cycle that removes every route, it falls back to plain dominance with no tolerance. `filtered_out` then names, for each dropped candidate, a route that dominates it (a frontier route when one does) or the frontier route it duplicates. The plan returns these as `filtered`; the agent's copy of the plan leaves them out.

## Offline demo

[DEMO.md](DEMO.md) covers the hosted page. These steps run the local page with Wi-Fi off.

1. Run `uv run python -m commute_agent.check_offline`. It must print `offline ready`.
2. Run `uv run python -m commute_agent.api` and open <http://127.0.0.1:8000/>.
3. Plan each of the five showcase trips from the trip menu. They are defined in `data/showcase_trips.yaml`.
4. Pick "Fields Corner, Dorchester" to "Downtown Crossing". Set the alert source to "Synthetic: Red Line suspended JFK/UMass to Park Street". The Red Line route shows `disruption_risk` 1.0 and the alert header, and ranks lower. The planner re-plans transit without the Red Line, and the Pareto change banner lists two routes that entered the set: "19, then Orange Line" (43.0 min) and "15, then SL4" (51.8 min).
5. In the chat, ask "How should I get from Fields Corner to Downtown Crossing?". The reply starts with "I am an independent prototype, not a City of Boston or MBTA service.", states the departure the plan used, and describes every route.
6. Stop the server, restart it with `--model <second model id>`, and ask again.

Known limits shown in this demo:

- When an alert suspends a route, the planner re-plans transit without that whole route. The synthetic alert covers only JFK/UMass to Park Street, so a trip that rides only an unaffected part of the Red Line would lose a valid Red Line alternative. The original Red Line itineraries stay as candidates, flagged.
- Offline, a request within 300 m of a showcase trip uses that trip's points and fixed departure (Tuesday 08:15), not the requested time.

The synthetic snapshot is labeled synthetic in the data and in the UI. The MBTA API serves only current alerts, so a real Red Line disruption may not exist on demo day.

## Evaluation

```sh
uv run python -m eval.run
uv run python -m eval.run --model deepseek/deepseek-v4.1-flash-1017,<second model id>
```

It writes `eval/results.md` and `eval/results.json`. It checks Pareto soundness and completeness on the 5 showcase trips and the 20 trips in `eval/od_pairs.yaml`. It also reports crash-risk reduction versus minutes added for walk and bike, the alert expectations in `eval/alert_expectations.yaml`, and the mean unknown-exposure share. Agent metrics (number grounding, identity statement, clarification) run on `eval/prompts.yaml`. When the model's credentials are missing, they show `skipped: <missing keys>`. `--model a,b` adds one column per model.

Results from 2026-10-24 at commit `2dde2f9`, agent model `deepseek/deepseek-v4.1-flash-1017` on OpenRouter. The full table is in `eval/results.md`.

| Metric | Result | n |
|---|---|---|
| Pareto soundness (self-consistency) | 25 pass, 0 fail | 25 trips |
| Pareto completeness (self-consistency) | 25 pass, 0 fail | 25 trips |
| Walk, safest vs fastest: crash risk reduction | median 70.4% (IQR 34.8 to 82.1) | 25 |
| Walk, safest vs fastest: minutes added | median 3.5 (IQR 1.1 to 7.2) | 25 |
| Bike, safest vs fastest: crash risk reduction | median 61.1% (IQR 46.0 to 77.4) | 25 |
| Bike, safest vs fastest: minutes added | median 5.1 (IQR 3.4 to 9.0) | 25 |
| Alert response: transit routes flagged correctly | 35/35 | 35 routes |
| Alert response: Pareto set as expected | 6/6 | 6 rows |
| Number grounding, first draft | 209/214 (98%) | 214 numbers |
| Number grounding, final reply | 231/231 (100%) | 231 numbers |
| Identity statement | 9/10 | 10 conversations |
| Clarification on ambiguous places | 2/2 | 2 conversations |
| Unknown exposure share, walk and bike | 0.583 | 143 routes |
| Unknown exposure share, walk | 1.000 | 61 routes |
| Unknown exposure share, bike | 0.272 | 82 routes |

How to read these numbers:

- Crash risk is a relative index, not a probability. Across walk and bike routes, about 42% of route length uses measured or Bluebikes-estimated exposure and 58% uses road-type assumptions, but the split is uneven. Bike routes use Bluebikes-estimated exposure for about 73% of their length. Walk routes use road-type assumptions for all of it: only 2 of about 297,000 walk edges match a pedestrian counter. So walk crash risk is effectively crash density on the route, not risk per pedestrian. Treat all crash-risk reductions as estimates.
- The Pareto soundness and completeness checks use the same dominance rule as the planner, so they confirm the filter is applied consistently, not that the rule is the right one.
- Identity was 9/10 in an earlier run, before `run_turn` existed to enforce it: on one ambiguous-place prompt the model asked its clarifying question without the required opening sentence. `run_turn` now prepends `IDENTITY_SENTENCE` to the first reply of a conversation when the model leaves it out, and the eval reads that final reply. The identity column therefore passes by construction on first replies. It no longer measures the model. The 9/10 figure is the model's own rate from before the prepend.
- The agent rewrites a reply once when it contains a number that is not in any tool result. Numbers that are still unsupported are removed from the text. The final-reply rate is therefore 100% by construction; the first-draft rate shows how often the model needed the rewrite.
- The alert rows use the committed snapshots. The synthetic Red Line suspension is not a real MBTA alert.
- Grounding and identity depend on the model. Rerun the eval after changing `--model`.

## Data notes

- Exposure calibration: R² is 0.09 (0.0906). It matched 35 counters: 17 Cambridge citywide, 15 Somerville, and 3 Numina. 18 more counters were over 30 m from any edge and were not matched. Source: `data/processed/exposure_calibration.json`.
- Cambridge crash coverage is about half the records. Only 8,878 of 17,230 Cambridge police crash rows have coordinates. 1,815 have an empty location and 6,537 have an address only. The risk build drops these rows. Of the rows with coordinates, 615 involve a cyclist and 364 a pedestrian; these enter the risk build.
- The risk build snapped 4,564 bike and 7,267 pedestrian crashes to street edges. It discarded 123 bike and 96 pedestrian crashes more than 30 m from any edge. Source: `data/processed/risk_meta.json`.
- Boston Vision Zero and Cambridge police records use different crash definitions. Neither is the MassDOT state crash file.

## Datasets and licenses

This table is copied from `data/raw/*/MANIFEST.json`.

| Group | File under `data/raw/` | Records | Source URL | License or terms |
|---|---|---|---|---|
| counts | `counts/cambridge_citywide_bike_counts.csv` | 238,799 | <https://data.cambridgema.gov/api/views/ijm6-q7em/rows.csv?accessType=DOWNLOAD> | Open Data Commons Public Domain Dedication and License |
| counts | `counts/somerville_bike_ped_counts.csv` | 2,532 | <https://data.somervillema.gov/api/views/qu9x-4xq5/rows.csv?accessType=DOWNLOAD> | Open Data Commons Open Database License |
| counts | `counts/numina/docs/projectoverview-numinasensormeasurementofmultimodalactivityandsharedstreets.pdf` |  | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/05d1634c-8642-419e-baf3-988d1513597f/download/projectoverview_numinasensormeasurementofmultimodalactivityandsharedstreets.pdf> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_52115.csv` | 24,965 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/546c819e-97fd-4d27-9757-5bba41f9fcb1/download/jackson-square-eastbound-lanes.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_52113.csv` | 24,965 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/c8e8bce9-382c-4798-bade-9233df8dde60/download/jackson-square-eastside-crosswalk.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_51671.csv` | 24,965 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/784da106-da55-45a6-a0dd-554f81d1f602/download/jackson-square-northside-sidewalk.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_52114.csv` | 24,965 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/b0b51080-55ef-46bd-84a2-f64e1ae9701e/download/jackson-square-westbound-lanes.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/docs/sensor-1-data-detail-jackson-square.pdf` |  | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/77de3ef0-cad2-44c3-b3fc-c9130c8829e4/download/sensor-1-data-detail-jackson-square.pdf> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_52110.csv` | 35,400 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/d47f85e5-07f9-4c77-8471-5283d61c4645/download/northern-ave-northside-sidewalk.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_52112.csv` | 35,400 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/d6c06150-418d-4705-b0c9-e92afd23ede7/download/northern-ave-roadway.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/docs/sensor-2-data-detail-northern-ave.pdf` |  | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/ef05b61e-8e83-4e04-a20e-9f941ea66e3f/download/sensor-2-data-detail-northern-ave.pdf> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_51600.csv` | 24,965 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/371fe13a-258a-44fd-8455-6766be95cdbc/download/tremont-diagonal-crossing.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_52117.csv` | 24,965 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/77914b24-91bd-437f-9efa-8c8463c21a0b/download/tremont-northside-crosswalk.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/zone_52116.csv` | 24,965 | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/f58dac33-8255-4274-862d-71f5a22b6f14/download/tremont-northwest-side-bicycles.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/docs/sensor-3-data-detail-tremont.pdf` |  | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/ff1f17e3-cb21-48f1-be7a-52996a020b42/download/sensor-3-data-detail-tremont.pdf> | Open Data Commons Public Domain Dedication and License (PDDL) |
| counts | `counts/numina/docs/numina-data-dictionary.pdf` |  | <https://data.boston.gov/dataset/3b2ce71d-d7ca-4027-b971-7180c263d730/resource/383263c2-068b-4685-b59d-4302c3169f2c/download/numina-data-dictionary-.pdf> | Open Data Commons Public Domain Dedication and License (PDDL) |
| crashes | `crashes/boston_vision_zero.csv` | 44,986 | <https://data.boston.gov/dataset/7b29c1b2-7ec2-4023-8292-c24f5d8f0905/resource/e4bfe397-6bfc-49c5-9367-c879fac7401d/download/tmpgxjopxm7.csv> | Open Data Commons Public Domain Dedication and License (PDDL) |
| crashes | `crashes/cambridge_police_crashes.csv` | 17,230 | <https://data.cambridgema.gov/api/views/gb5w-yva3/rows.csv?accessType=DOWNLOAD> | Open Data Commons Public Domain Dedication and License |
| geo | `geo/boston_boundary.geojson` | 1 | <https://gisportal.boston.gov/arcgis/rest/services/Planning/OpenData/MapServer/9/query?where=1%3D1&outFields=*&outSR=4326&f=geojson> | Open Data Commons Public Domain Dedication and License (PDDL) |
| geo | `geo/cambridge_boundary.geojson` | 1 | <https://raw.githubusercontent.com/cambridgegis/cambridgegis_data/main/Boundary/City_Boundary/BOUNDARY_CityBoundary.geojson> | Open Data Commons Public Domain Dedication and License (PDDL) |
| geo | `geo/massachusetts-latest.osm.pbf` |  | <https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf> | ODbL 1.0, © OpenStreetMap contributors |
| geo | `geo/boston.osm.pbf` |  | <https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf> | ODbL 1.0, © OpenStreetMap contributors |
| transit | `transit/MBTA_GTFS.zip` |  | <https://cdn.mbta.com/MBTA_GTFS.zip> | MBTA/MassDOT Developer License Agreement, https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf |
| transit | `transit/mbta_routes.json` | 8 | <https://api-v3.mbta.com/routes?filter[type]=0,1> | MBTA/MassDOT Developer License Agreement, https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf |
| transit | `transit/mbta_alerts_20261024T164830Z.json` | 61 | <https://api-v3.mbta.com/alerts?filter[route_type]=0,1,3&page[limit]=500> | MBTA/MassDOT Developer License Agreement, https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf |
| transit | `transit/massgis_rapid_transit_stations.json` | 170 | <https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/MBTA_Rapid_Transit/MapServer/0/query?where=1%3D1&outFields=STATION,LINE,ROUTE&returnGeometry=true&outSR=4326&f=json> | MassGIS open data (Commonwealth of Massachusetts) |
| transit | `transit/202411-bluebikes-tripdata.zip` |  | <https://s3.amazonaws.com/hubway-data/202411-bluebikes-tripdata.zip> | Bluebikes Data License Agreement, https://www.bluebikes.com/data-license-agreement |

The walk, bike, and drive street graphs come from OpenStreetMap through the Overpass API. `data/otp/` holds copies of the MBTA GTFS zip and the clipped Boston OSM extract listed above.

© OpenStreetMap contributors. OpenStreetMap data is available under the Open Database License (ODbL) 1.0, <https://opendatacommons.org/licenses/odbl/1-0/>. See <https://www.openstreetmap.org/copyright>.

The web page bundles Leaflet and Chart.js in `web/vendor/`.
