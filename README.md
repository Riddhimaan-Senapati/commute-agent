# commute-agent

commute-agent plans trips inside its service area (Boston, Cambridge, Somerville, and Brookline) and shows the trade-offs instead of picking one answer for you. For a trip, it compares walk, bike, Bluebikes, drive, and MBTA routes on four numbers: travel time, CO₂, crash risk on the walking and biking parts, and MBTA disruption risk. It keeps every route that no other route beats on all four (the Pareto set) and drops the rest. An LLM agent then explains the options through MCP tools, and it may quote only numbers the planner returned.

This is an independent prototype. It is not an MBTA or City of Boston service.

The hosted demo is at <https://commute-agent-5hla.onrender.com>.

Run locally, the app works offline by default. Transit comes from cached OpenTripPlanner responses, the map from a local basemap, and alerts from recorded snapshots.

## Setup

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). Docker is only needed to rebuild or serve OTP.

```sh
uv sync
```

### Files you supply

Two inputs need your attention before the first run.

1. `data/emission_factors.yaml` is committed with sourced factors (EPA GHG Emission Factors Hub 2025, Table 10, and UK DESNZ 2026 for ferry; each entry names its source). To use other factors, keep the shape of `data/emission_factors.example.yaml`: every mode needs a numeric `value`, a `unit` of `g_per_passenger_km` or `g_per_passenger_mile`, and a non-empty `source`. Every entry point exits with code 2 if the file is missing or invalid.

   `data/traffic_factors.yaml` holds drive-time multipliers (each at least 1.0) for each hour of a weekday and of a weekend day. They come from the TomTom Traffic Index 2025 hourly congestion grid for the City of Boston (value = 1 + congestion level / 100; weekday is the Monday to Friday mean, weekend the Saturday and Sunday mean), and each bucket names that source. A weekday 8 AM departure gets 1.60 and 5 PM gets 1.84. The factor scales the whole free-flow drive time; it is a citywide average, not a per-road speed. A bucket marked `needs a source` must stay at 1.0. A missing file means 1.0 everywhere, and an invalid one exits with code 2. `data/traffic_factors.example.yaml` shows the shape.

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

`data/raw/`, `data/processed/`, `data/otp/`, and `data/cache/` are gitignored, so a fresh clone has none of them. Download the raw files first; each script writes `data/raw/<group>/` and a `MANIFEST.json` with the source URL, retrieval time, SHA-256, size, record count, and license.

`geo.py` needs `osmium` on `PATH`. Somerville and Brookline boundaries come from MassGIS's Census 2020 towns layer, queried from its ArcGIS service as GeoJSON.

```sh
for group in geo crashes counts transit ridership; do uv run --no-project --python 3.12 python scripts/fetch/$group.py; done
```

1. Ingest every dataset into `data/processed/`. The `graphs` step downloads the walk, bike, and drive graphs for the service area from the Overpass API once.

   ```sh
   uv run python -m commute_agent.ingest all
   ```

   To run one step, name it: `boundary`, `graphs`, `basemap`, `crashes`, `counts`, `bluebikes`, `ridership`, `stations`, or `otp-inputs`. `boundary` writes `service_area.geojson` (the union of Boston, Cambridge, Somerville, and Brookline), `service_area_buffered.geojson` (the same, 2 km out), and `crash_coverage.geojson` (Boston, Cambridge, and Somerville only). `otp-inputs` clips the Massachusetts extract to the buffered service area with `osmium extract -p` and writes `data/otp/service_area.osm.pbf`.

2. Add crash risk and exposure to the street graphs. This writes `risk_meta.json` and `exposure_calibration.json`.

   ```sh
   uv run python -m commute_agent.streets.build
   ```

3. Sample the 20 evaluation trips. The output, `eval/od_pairs.yaml`, is committed. Run this only to regenerate it.

   ```sh
   uv run python eval/sample_pairs.py
   ```

## OpenTripPlanner

OTP reads `data/otp/` (the MBTA GTFS zip and the OSM extract clipped to the service area, `service_area.osm.pbf`). The script copies `docker/otp/build-config.json` into `data/otp/` before each run. It runs the `opentripplanner/opentripplanner:2.10.0` image with a 6 GB heap. The measured peak during the build was 3.75 GiB.

```sh
docker/otp/run.sh build    # writes data/otp/graph.obj (about 112 MB, under a minute)
docker/otp/run.sh serve    # serves http://localhost:8080/otp/gtfs/v1
```

With OTP serving, fill the transit cache for the showcase trips and the eval trips:

```sh
uv run python -m commute_agent.transit.cache_trips
```

It writes one file per trip to `data/cache/otp/`. The cache is keyed by weekday and departure time, so a cached Tuesday 08:15 trip serves any later Tuesday. For each showcase trip, it also caches the itineraries that avoid any route a committed alert snapshot suspends, so those alternatives work offline. Offline, a request whose ends are within 300 m of a showcase trip is planned from that trip's points at its fixed demo departure, and the plan notes say so. It also caches a bike-access request for each trip, in a separate file keyed with `;access=bike`, so walk-access cache files are unchanged. A bike-access itinerary rides a bike to the first transit stop and rides it from the last one, because OTP 2.10 accepts `BICYCLE` for access only when it is also used for egress and transfer. Its bike legs are scored for crash risk on the bike graph. `BICYCLE_PARKING` returns no transit on the current graph, since no bike park-and-ride stations are built. Stop OTP afterwards with `docker stop <container>`.

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

It plans Fields Corner to Downtown Crossing on load and draws the selected route on a street map, with the other options muted. Under All trade-offs, a heading counts the Pareto routes out of all candidates, and a greyed "Not on the frontier" list shows each dropped candidate with the route that beats it. A time-against-CO₂ chart above the route list plots every candidate: solid frontier dots select their route, and grey hollow dots are the dropped ones. Use the From and To boxes (MBTA stations, Bluebikes docks, showcase places, or `lat,lon`) or the demo trip chips. Pick what matters with Fastest, Lowest CO₂, Safest, Least disrupted, or All trade-offs; a single objective shows only that route. The switch "Simulate Red Line suspension" applies the synthetic alert. The "Step-free" switch asks OTP for wheelchair-accessible transit and flags MBTA elevator closures at stations a transit leg uses. "Ask the agent" opens the chat.

The page loads OpenStreetMap tiles when it can reach them and falls back to the local basemap offline. Offline, transit is available only for cached trips; other trips show walk, bike, Bluebikes, and drive routes. Set `COMMUTE_API_URL` if the API runs elsewhere.

| Flag | Effect |
|---|---|
| `--live` | Calls OTP at `localhost:8080` (and writes the cache), enables the `live` alert source, and adds OSM map tiles. |
| `--model <id>` | Sets the agent's model id on the configured endpoint. The default is `deepseek/deepseek-v4.1-flash-1017` on OpenRouter. The `COMMUTE_AGENT_MODEL` variable does the same. |
| `--port <n>` | Port to bind. The default is 8000. |

The chat needs the credentials of the chosen model. The default model needs `OPENROUTER_API_KEY`. The API and `eval.run` read it from `.env` in the repository root; a variable already set in the shell wins. `.env` is gitignored.

The agent talks to any OpenAI-compatible endpoint. To use another one, set `COMMUTE_AGENT_API_BASE` and `COMMUTE_AGENT_API_KEY_ENV` (the name of the variable that holds its key). For a local Ollama server, set `COMMUTE_AGENT_API_BASE=http://localhost:11434/v1`, `COMMUTE_AGENT_API_KEY_ENV=` (empty), and `--model qwen3:8b`. Without them, `/chat` returns 503 and names the missing key. The map and the route list still work.

The server has no authentication. It binds to 127.0.0.1 only. Use it for local demos only.

Chat has two in-memory rate limits in `commute_agent/api.py`: `CHAT_LIMIT_PER_SESSION = (5, 60)` (5 messages per `X-Session-Id` per 60 s) and `CHAT_LIMIT_GLOBAL = (60, 60)` (60 messages per 60 s for the whole server). Over a limit, `/api/chat` returns 429 with a `Retry-After` header. The counts reset when the server restarts.

`COMMUTE_ALLOWED_HOSTS` is a comma-separated list of host names, for example `localhost,commute-agent.example`. When it is set, a request whose `Host` header (without port) is not listed gets 400. Unset allows every host, as before. The hosted deploy proxies `/api` through Next.js, so list the host name the API process actually sees in the `Host` header, which may be the upstream name and not the public domain. Check it on the deployed service before relying on the guard.

## Deploy (Render)

`Dockerfile` builds one container: FastAPI and the MCP server on the container's loopback, and the Next.js page on `$PORT`. Only Next.js is public. The processed graphs, basemap, and OTP cache (about 41 MB compressed) come from the `data-v2` GitHub release, because they are not in git. `data-v1` holds the older Boston-only build. `render.yaml` defines the service on the `2c-4g` plan: the Boston-only data peaked at about 3.2 GB of memory on Render, and the four-city graphs take about 10% more to load (2.56 GiB against 2.32 GiB measured locally), so smaller plans run out of memory. The first start takes about 80 s while the street graphs load.

`.github/workflows/deploy.yml` runs the tests on every push to `main`, then deploys that commit with the Render CLI and waits for it to go live. It needs two repository secrets: `RENDER_API_KEY` and `RENDER_SERVICE_ID`. Set `OPENROUTER_API_KEY` on the Render service for the chat.

Transit for trips outside the cache needs OpenTripPlanner. Set `COMMUTE_OTP_URL` (for example `http://localhost:8080/otp/gtfs/v1`) and offline mode asks that server whenever the cache has no entry, then caches the answer. The demo trips still come from the cache. On Render, `render.yaml` adds a private service, `commute-otp`, built from `docker/otp-render/Dockerfile`. It downloads the saved graph from the `otp-graph.obj` asset on the `data-v2` release and runs on the `standard` plan (2 GB; OTP used about 1 GiB serving the Boston-only graph, and the four-city graph is smaller on disk). The web service gets its address as `COMMUTE_OTP_HOSTPORT`. Without that variable, the app behaves as before.

Set `COMMUTE_BLUEBIKES_LIVE=1` to read live Bluebikes dock counts from the GBFS `station_status` feed (`gbfs.bluebikes.com/gbfs/gbfs.json`, then the feed it lists). The Bluebikes route then starts only at a station with bikes out and renting, and ends only at one with a dock free and returning, still nearest first. Its time source names the counts. The feed is cached for 60 seconds, and a failed or slow fetch (5 s timeout) falls back to the unchanged route with the note "Bluebikes dock availability is not checked." The flag is off by default, so offline tests and the demo stay deterministic.

The hosted demo has no login. Anyone with the link can use the chat, which spends the OpenRouter key; keep a credit limit on it. Each browser tab sends an `X-Session-Id` header and gets its own alert source, so one visitor's "Simulate Red Line suspension" does not change anyone else's plans. Route choices ("I'll take this route") are kept per session: each tab's choices go into the preference store under its `X-Session-Id` and order only that tab's routes. Requests without the header share one session.

## How the Pareto set is computed

Each candidate route gets four numbers, and lower is better for all of them:

- `time_min`: the sum of leg durations. Drive time is OSMnx free-flow time multiplied by a time-of-day traffic factor from `data/traffic_factors.yaml` for the departure's hour bucket (America/New_York local time, weekday or weekend). Routing itself still uses free-flow edge times. Until a factor has a confirmed source, it is 1.0 and the notes say drive time is free-flow.
- `co2_g`: the sum over legs of distance times the mode's factor in `data/emission_factors.yaml`.
- `crash_risk`: the sum of edge risk along walk and bike legs (bike legs on transit routes are scored on the bike graph), where edge risk is `(crashes + p * length_km) / exposure * 1000` and `p` is the network mean crashes per km. Drive legs and transit vehicle legs count as 0 because they are not modeled.
- `disruption_risk`: the highest alert score on any transit leg (suspension 1.0, shuttle 0.8, detour 0.5, and so on, in `EFFECT_SCORES` in `commute_agent/transit/alerts.py`). With Step-free on, an `ELEVATOR_CLOSURE` alert that matches a leg scores 1.0 instead of its table score 0.2; with it off, the table score applies. `ESCALATOR_CLOSURE` is not step-free-scored: a wheelchair trip does not use an escalator, and the stairs beside it are not step-free either.

Route A dominates route B when A is no worse than B on every objective, allowing a tolerance, and better than B by more than the tolerance on at least one. The tolerance for each pair is `max(abs_tol, rel_tol * max(|a|, |b|))`: 0.5 min for time, 1 g or 1% for CO₂, 0.01 or 2% for crash risk, and 0.01 for disruption. `pareto_front` in `commute_agent/pareto.py` keeps every candidate that no other candidate dominates, then drops any route within tolerance of one already kept on all four objectives. If the tolerance creates a cycle that removes every route, it falls back to plain dominance with no tolerance. `filtered_out` then names, for each dropped candidate, a route that dominates it (a frontier route when one does) or the frontier route it duplicates. The plan returns these as `filtered`; the agent's copy of the plan leaves them out.

## Offline demo

These steps run the local page with Wi-Fi off.

1. Run `uv run python -m commute_agent.check_offline`. It must print `offline ready`.
2. Run `uv run python -m commute_agent.api` and open <http://127.0.0.1:8000/>.
3. Plan each of the five showcase trips from the trip menu. They are defined in `data/showcase_trips.yaml`.
4. Pick "Fields Corner, Dorchester" to "Downtown Crossing". Set the alert source to "Synthetic: Red Line suspended JFK/UMass to Park Street". The Red Line route shows `disruption_risk` 1.0 and the alert header, and ranks lower. The planner re-plans transit without the Red Line, and the Pareto change banner lists two routes that entered the set: "19, then Orange Line" (43.0 min) and "15, then SL4" (51.8 min).
5. In the chat, ask "How should I get from Fields Corner to Downtown Crossing?". The reply starts with "I am an independent prototype, not a City of Boston or MBTA service.", states the departure the plan used, and describes every route.
6. Stop the server, restart it with `--model <second model id>`, and ask again.

Known limits shown in this demo:

- When an alert suspends a route, the planner re-plans transit without that whole route. The synthetic alert covers only JFK/UMass to Park Street, so a trip that rides only an unaffected part of the Red Line would lose a valid Red Line alternative. The original Red Line itineraries stay as candidates, flagged.
- Offline, a request within 300 m of a showcase trip uses that trip's points. With no departure chosen it also uses the trip's fixed departure (Tuesday 08:15). With a chosen departure it uses that time, which must be in the cache or reachable through `COMMUTE_OTP_URL`.
- Step-free trips ask OTP for wheelchair-accessible itineraries (`preferences.accessibility.wheelchair.enabled`). They have their own cache entries, marked `;step-free` in the key. Offline, a step-free request is served only from those entries, so a trip that was never cached with the Step-free switch on reports "transit not cached for this trip" and has no transit routes. Default cache entries are unchanged.

The step-free elevator scoring uses recorded `ELEVATOR_CLOSURE` alerts in `tests/fixtures/mbta_elevator_alerts.json`, trimmed from the MBTA V3 API (`filter[activity]=USING_WHEELCHAIR`, because the API does not accept an effect filter).

The synthetic snapshot is labeled synthetic in the data and in the UI. The MBTA API serves only current alerts, so a real Red Line disruption may not exist on demo day.

## Evaluation

```sh
uv run python -m eval.run
uv run python -m eval.run --model deepseek/deepseek-v4.1-flash-1017,<second model id>
```

It writes `eval/results.md` and `eval/results.json`. It checks Pareto soundness and completeness on the 5 showcase trips and the 20 trips in `eval/od_pairs.yaml`. It also reports crash-risk reduction versus minutes added for walk and bike, the alert expectations in `eval/alert_expectations.yaml`, and the mean unknown-exposure share. Agent metrics (number grounding, identity statement, clarification) run on `eval/prompts.yaml`. When the model's credentials are missing, they show `skipped: <missing keys>`. `--model a,b` adds one column per model.

Results from 2026-10-26 on the four-city data (`data-v2`), agent model `deepseek/deepseek-v4.1-flash-1017` on OpenRouter. The full table is in `eval/results.md`.

| Metric | Result | n |
|---|---|---|
| Pareto soundness (self-consistency) | 25 pass, 0 fail | 25 trips |
| Pareto completeness (self-consistency) | 25 pass, 0 fail | 25 trips |
| Walk, safest vs fastest: crash risk reduction | median 76.2% (IQR 38.4 to 84.3) | 25 |
| Walk, safest vs fastest: minutes added | median 3.5 (IQR 1.2 to 7.2) | 25 |
| Bike, safest vs fastest: crash risk reduction | median 62.1% (IQR 49.0 to 79.0) | 25 |
| Bike, safest vs fastest: minutes added | median 5.4 (IQR 3.4 to 9.4) | 25 |
| Alert response: transit routes flagged correctly | 45/45 | 45 routes |
| Alert response: Pareto set as expected | 6/6 | 6 rows |
| Number grounding, first draft | 281/288 (98%) | 288 numbers |
| Number grounding, final reply | 298/298 (100%) | 298 numbers |
| Identity statement (model's own reply) | 9/10 | 10 conversations |
| Clarification on ambiguous places | 2/2 | 2 conversations |
| Unknown exposure share, walk and bike | 0.578 | 143 routes |
| Unknown exposure share, walk | 1.000 | 62 routes |
| Unknown exposure share, bike | 0.254 | 81 routes |

How to read these numbers:

- Crash risk is a relative index, not a probability. Across walk and bike routes, about 42% of route length uses measured or Bluebikes-estimated exposure and 58% uses road-type assumptions, but the split is uneven. Bike routes use Bluebikes-estimated exposure for about 73% of their length. Walk routes use road-type assumptions for all of it. Only 27 walk edges match a pedestrian counter (the walk calibration below), and only 2 of them keep the `ped_counter` label because the rest lie outside crash coverage and are labelled `outside_crash_coverage`. So walk crash risk is effectively crash density on the route, not risk per pedestrian. Treat all crash-risk reductions as estimates.
- The Pareto soundness and completeness checks use the same dominance rule as the planner, so they confirm the filter is applied consistently, not that the rule is the right one.
- Identity is 9/10: in one of ten conversations the model's first reply left out the required opening sentence. `run_turn` now prepends `IDENTITY_SENTENCE` to the first reply when the model leaves it out and sets `identity_added` on the reply. The eval counts a reply only when the model wrote the sentence itself, so the column still measures the model.
- The agent rewrites a reply once when it contains a number that is not in any tool result. Numbers that are still unsupported are removed from the text. The final-reply rate is therefore 100% by construction; the first-draft rate shows how often the model needed the rewrite.
- The alert rows use the committed snapshots. The synthetic Red Line suspension is not a real MBTA alert.
- Grounding and identity depend on the model. Rerun the eval after changing `--model`.

## Data notes

- Exposure calibration: bike R² is 0.22 (0.2205). It matched 57 counters: 21 Cambridge citywide, 34 Somerville, and 2 Numina; none was more than 30 m from an edge. The Boston-only build matched 35 and had R² 0.09, because most Somerville counters lay outside its graph. Source: `data/processed/exposure_calibration.json`.
- Walk station-entries term (not adopted, flag `USE_STATION_ENTRIES` is False). On the Boston-only build, walk calibration matched 27 walk edges to pedestrian counters (per-edge mean). R² of the road-class prior alone is -0.2300; R² with the prior plus distance-decayed MBTA station entries (2025, half-mile linear taper) is -0.2098. The raw R² rises only because the two-term fit nests the prior-only fit. Adjusted R² falls from -0.2300 to -0.2582, so the term is not adopted. Both coefficients are kept in `exposure_calibration.json` under `walk`. Source: `data/processed/exposure_calibration.json`.
- The half-mile catchment is a planning convention (FTA Circular 4702.1B, cited in the `station_entry_term` comment), not a fitted value.
- Cambridge crash coverage is about half the records. Only 8,878 of 17,230 Cambridge police crash rows have coordinates. 1,815 have an empty location and 6,537 have an address only. The risk build drops these rows. Of the rows with coordinates, 615 involve a cyclist and 364 a pedestrian; these enter the risk build.
- The risk build snapped 4,953 bike and 7,534 pedestrian crashes to street edges. It discarded 74 bike and 61 pedestrian crashes more than 30 m from any edge. By source, the crash rows are Boston Vision Zero 4,072 bike and 6,999 pedestrian, Cambridge police 615 and 364, and Somerville police 340 and 232. Source: `data/processed/risk_meta.json` and `crashes.parquet`.
- Somerville police crashes (May 2018 to September 2026, `data.somervillema.gov` dataset `mtik-28va`, 3,548 rows, all with coordinates) enter the risk build with the same one-row-per-mode rule as Cambridge. Records without coordinates are dropped and counted in the notes. The dataset excludes state roads and crashes still under investigation.
- Brookline has no crash source in this project. Its edges are not in `crash_coverage.geojson`, so they use the road-class prior and are marked `outside_crash_coverage`. Routes there are therefore not crash-measured.
- Boston Vision Zero, Cambridge police, and Somerville police records use different crash definitions. None is the MassDOT state crash file.

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
| crashes | `crashes/somerville_police_crashes.csv` | not yet fetched | <https://data.somervillema.gov/api/views/mtik-28va/rows.csv?accessType=DOWNLOAD> | Open Data Commons Open Database License (ODbL) |
| geo | `geo/boston_boundary.geojson` | 1 | <https://gisportal.boston.gov/arcgis/rest/services/Planning/OpenData/MapServer/9/query?where=1%3D1&outFields=*&outSR=4326&f=geojson> | Open Data Commons Public Domain Dedication and License (PDDL) |
| geo | `geo/cambridge_boundary.geojson` | 1 | <https://raw.githubusercontent.com/cambridgegis/cambridgegis_data/main/Boundary/City_Boundary/BOUNDARY_CityBoundary.geojson> | Open Data Commons Public Domain Dedication and License (PDDL) |
| geo | `geo/massachusetts-latest.osm.pbf` |  | <https://download.geofabrik.de/north-america/us/massachusetts-latest.osm.pbf> | ODbL 1.0, © OpenStreetMap contributors |
| geo | `geo/massgis_towns.geojson` | 2 | <https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/Census2020_Towns/MapServer/2> (Somerville and Brookline) | U.S. Census Bureau 2020 municipal boundaries (public domain), processed by MassGIS |
| transit | `transit/MBTA_GTFS.zip` |  | <https://cdn.mbta.com/MBTA_GTFS.zip> | MBTA/MassDOT Developer License Agreement, https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf |
| ridership | `ridership/GSE_by_year.zip` |  | <https://www.arcgis.com/sharing/rest/content/items/7859894afb5641ce91a2bb03599fdf5b/data> | CC0 1.0 (ArcGIS item licenseInfo), https://mbta-massdot.opendata.arcgis.com/datasets/7859894afb5641ce91a2bb03599fdf5b |
| transit | `transit/mbta_routes.json` | 8 | <https://api-v3.mbta.com/routes?filter[type]=0,1> | MBTA/MassDOT Developer License Agreement, https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf |
| transit | `transit/mbta_alerts_20261024T164830Z.json` | 61 | <https://api-v3.mbta.com/alerts?filter[route_type]=0,1,3&page[limit]=500> | MBTA/MassDOT Developer License Agreement, https://cdn.mbta.com/sites/default/files/2023-08/mbta-massdot-develop-license-agreement.pdf |
| transit | `transit/massgis_rapid_transit_stations.json` | 170 | <https://arcgisserver.digital.mass.gov/arcgisserver/rest/services/AGOL/MBTA_Rapid_Transit/MapServer/0/query?where=1%3D1&outFields=STATION,LINE,ROUTE&returnGeometry=true&outSR=4326&f=json> | MassGIS open data (Commonwealth of Massachusetts) |
| transit | `transit/202411-bluebikes-tripdata.zip` |  | <https://s3.amazonaws.com/hubway-data/202411-bluebikes-tripdata.zip> | Bluebikes Data License Agreement, https://www.bluebikes.com/data-license-agreement |

The walk, bike, and drive street graphs come from OpenStreetMap through the Overpass API. `data/otp/` holds a copy of the MBTA GTFS zip and `service_area.osm.pbf`, the Massachusetts extract clipped to the buffered service area.

© OpenStreetMap contributors. OpenStreetMap data is available under the Open Database License (ODbL) 1.0, <https://opendatacommons.org/licenses/odbl/1-0/>. See <https://www.openstreetmap.org/copyright>.

The web page bundles Leaflet and Chart.js in `web/vendor/`.
