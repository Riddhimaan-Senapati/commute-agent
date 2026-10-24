# commute-agent

A Pareto-optimal commute planner for Boston. For one trip it returns every route that no other route beats on all of travel time, CO₂, crash risk, and MBTA disruption risk. An LLM agent explains the options through MCP tools. The agent is an independent prototype, not an MBTA or City of Boston service.

The demo runs offline by default. Transit comes from a cached OpenTripPlanner response, the map from a local basemap, and alerts from recorded snapshots.

## Setup

Requires Python 3.12, [uv](https://docs.astral.sh/uv/), and Docker (only to rebuild or serve OTP).

```sh
uv sync
```

### Files you supply

Two files are not in the repository. Every entry point that loads emission factors exits with code 2 until the first one exists.

1. `data/emission_factors.yaml`. Copy `data/emission_factors.example.yaml` and give every mode a numeric `value`, a `unit` of `g_per_passenger_km` or `g_per_passenger_mile`, and a non-empty `source`. The example file has this shape:

   ```yaml
   # Copy to emission_factors.yaml and fill every value with a sourced factor.
   walk:          {value: null, unit: g_per_passenger_km, source: ""}
   bike:          {value: null, unit: g_per_passenger_km, source: ""}
   bluebikes:     {value: null, unit: g_per_passenger_km, source: ""}
   drive:         {value: null, unit: g_per_passenger_km, source: ""}
   subway:        {value: null, unit: g_per_passenger_km, source: ""}
   light_rail:    {value: null, unit: g_per_passenger_km, source: ""}
   bus:           {value: null, unit: g_per_passenger_km, source: ""}
   commuter_rail: {value: null, unit: g_per_passenger_km, source: ""}
   ferry:         {value: null, unit: g_per_passenger_km, source: ""}
   ```

2. `data/raw/numina_zones.csv`. This maps each Boston Numina zone id to its sensor site coordinates. The Numina zone CSVs have no coordinates. A candidate file is at `data/raw/counts/numina/numina_zones.candidate.csv`. It quotes the site coordinates from the Numina project overview PDF. Check it, then copy it:

   ```sh
   cp data/raw/counts/numina/numina_zones.candidate.csv data/raw/numina_zones.csv
   ```

### Check offline readiness

```sh
uv run python -m commute_agent.check_offline
```

It prints `offline ready`, or lists each missing file by path and exits non-zero.

## Build the data

The raw files are already downloaded under `data/raw/<group>/`. Each group has a `MANIFEST.json` with the source URL, retrieval time, SHA-256, size, record count, and license. The fetch scripts are in `scripts/fetch/`.

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

`webapp/` is a second, more polished page for live demos. It uses the same API. Requires Node.js 20 or later.

```sh
cd webapp
npm install        # once, needs network
npm run build
npm run start      # http://127.0.0.1:3000/, keep the API running on port 8000
```

The header has three demo buttons: plan Fields Corner to Downtown Crossing, suspend the Red Line (synthetic), and ask the agent. Set `COMMUTE_API_URL` if the API runs elsewhere. After `npm install`, the page builds and runs offline. It loads no remote fonts and, in offline mode, no map tiles.

| Flag | Effect |
|---|---|
| `--live` | Calls OTP at `localhost:8080` (and writes the cache), enables the live MBTA alerts source, and adds OSM map tiles. |
| `--model <id>` | Sets the agent's model id on the configured endpoint. The default is `deepseek/deepseek-v4.1-flash-1017` on OpenRouter. The `COMMUTE_AGENT_MODEL` variable does the same. |
| `--port <n>` | Port to bind. The default is 8000. |

The chat needs the credentials of the chosen model. The default model needs `OPENROUTER_API_KEY`. The API and `eval.run` read it from `.env` in the repository root; a variable already set in the shell wins. `.env` is gitignored.

The agent talks to any OpenAI-compatible endpoint. To use another one, set `COMMUTE_AGENT_API_BASE` and `COMMUTE_AGENT_API_KEY_ENV` (the name of the variable that holds its key). For a local Ollama server, set `COMMUTE_AGENT_API_BASE=http://localhost:11434/v1`, `COMMUTE_AGENT_API_KEY_ENV=` (empty), and `--model qwen3:8b`. Without them, `/chat` returns 503 and names the missing key. The map and the route list still work.

The server has no authentication. It binds to 127.0.0.1 only. Use it for local demos only.

## Offline demo script

Turn Wi-Fi off before you start.

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

Results from 2026-10-24, agent model `deepseek/deepseek-v4.1-flash-1017` on OpenRouter. The full table is in `eval/results.md`.

| Metric | Result | n |
|---|---|---|
| Pareto soundness | 25 pass, 0 fail | 25 trips |
| Pareto completeness | 25 pass, 0 fail | 25 trips |
| Walk, safest vs fastest: crash risk reduction | median 70.4% (IQR 34.8 to 82.1) | 25 |
| Walk, safest vs fastest: minutes added | median 3.5 (IQR 1.1 to 7.2) | 25 |
| Bike, safest vs fastest: crash risk reduction | median 61.1% (IQR 46.0 to 77.4) | 25 |
| Bike, safest vs fastest: minutes added | median 5.1 (IQR 3.4 to 9.0) | 25 |
| Alert response: transit routes flagged correctly | 35/35 | 35 routes |
| Alert response: Pareto set as expected | 6/6 | 6 rows |
| Number grounding, first draft | 232/240 (97%) | 240 numbers |
| Number grounding, final reply | 249/249 (100%) | 249 numbers |
| Identity statement | 10/10 | 10 conversations |
| Clarification on ambiguous places | 2/2 | 2 conversations |
| Unknown exposure share (mean) | 0.583 | 143 walk and bike routes |

How to read these numbers:

- Crash risk is a relative index, not a probability. On average, about 42% of each walk or bike route's length uses measured exposure (pedestrian counters) or Bluebikes-estimated exposure. The other 58% uses road-type assumptions, because no count data covers those streets. Treat the crash-risk reductions as estimates.
- The agent rewrites a reply once when it contains a number that is not in any tool result. Numbers that are still unsupported are removed from the text. The final-reply rate is therefore 100% by construction; the first-draft rate shows how often the model needed the rewrite.
- The alert rows use the committed snapshots. The synthetic Red Line suspension is not a real MBTA alert.
- Grounding and identity depend on the model. Rerun the eval after changing `--model`.

## Data notes

- Exposure calibration: R² is 0.09 (0.0906). It matched 35 counters: 17 Cambridge citywide, 15 Somerville, and 3 Numina. 18 more counters were over 30 m from any edge and were not matched. Source: `data/processed/exposure_calibration.json`.
- Cambridge crash coverage is about half the records. Only 8,878 of 17,230 Cambridge police crash rows have coordinates. 1,815 have an empty location and 6,537 have an address only. These rows are dropped. Of the rows with coordinates, 615 involve a cyclist and 364 a pedestrian; these enter the risk build.
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
