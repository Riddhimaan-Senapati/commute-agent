# Requirements Document

## Introduction

The Pareto Commute Agent is a 12-hour hackathon prototype for Hackachusetts 2026 (Civic action track V3). For a trip whose origin and destination are inside the City of Boston, the System returns every Pareto-optimal route across walk, bike, Bluebikes, drive, and MBTA transit. It minimizes four objectives. These are travel time, CO₂, crash risk on walk and bike segments, and MBTA disruption risk. An LLM agent explains each trade-off in plain language using only numbers that tools returned, and it learns which trade-offs the user picks.

Decisions fixed before this document:

- OpenTripPlanner 2.10.0 in Docker, loaded with MBTA GTFS and a clipped OSM extract, plans transit itineraries.
- Walk, bike, Bluebikes, and drive routes come from OSMnx graphs searched with crash-risk weights.
- One FastAPI process serves REST, runs a smolagents agent, owns the MCP tool server subprocess, and serves one static page.
- The agent model is one model id on one OpenAI-compatible endpoint, set by environment variables or a flag, so OpenRouter, a local server, or any other compatible provider can run it. The default is `deepseek/deepseek-v4.1-flash-1017` on OpenRouter.
- Raw data is already curated in `data/raw/<group>/` with a `MANIFEST.json` per group.
- Monetary cost is out of scope.
- The user supplies `data/emission_factors.yaml` and `data/raw/numina_zones.csv`.
- Claude Haiku 5.5 subagents implement the work in parallel, so every requirement names its workstream (WS0 to WS10).

## Glossary

- **System**: The Pareto Commute Agent prototype.
- **Service_Area**: The City of Boston municipal boundary. Origin and destination must be inside it. Route geometry may leave it.
- **Crash_Coverage_Area**: The union of the Boston and Cambridge municipal boundaries, where the System has crash records.
- **Route_Kind**: One of `walk`, `bike`, `bluebikes`, `drive`, `transit`.
- **Leg_Mode**: One of `walk`, `bike`, `bluebikes`, `drive`, `subway`, `light_rail`, `bus`, `commuter_rail`, `ferry`.
- **Route_Option**: One candidate route with an ordered list of Legs, Objectives, per-objective Source_Labels, labels, and alert ids.
- **Leg**: A contiguous part of a Route_Option in one Leg_Mode.
- **Objectives**: The values `time_min`, `co2_g`, `crash_risk`, and `disruption_risk`. Lower is better for all four.
- **Dominates**: Route_Option A dominates Route_Option B when, for every objective, A is no worse than B beyond that objective's tolerance, and for at least one objective A is better than B by more than that objective's tolerance.
- **Pareto_Set**: The candidates that no other candidate Dominates, after near-duplicates are merged.
- **Near_Duplicate**: Two candidates whose four objectives are each equal within tolerance.
- **Source_Label**: A short string that names where a number came from, for example `emission_factors.yaml: bike (https://...)`.
- **Data_Store**: The local `data/` directory.
- **Emission_Factors_File**: `data/emission_factors.yaml`, supplied by the user.
- **Numina_Zones_File**: `data/raw/numina_zones.csv` with columns `zone_id,lat,lon,street`, supplied by the user.
- **Street_Graphs**: The OSMnx walk, bike, and drive graphs for the Service_Area plus a 2 km buffer.
- **Peak_Hour_Volume**: People per weekday peak hour (07:00 to 09:00 and 16:00 to 19:00 windows), the exposure unit.
- **Exposure_Proxy**: Per-edge count of Bluebikes November 2024 trips routed on the shortest bike path.
- **Risk_Basis**: Per-edge label for how exposure was estimated. One of `bluebikes_proxy`, `ped_counter`, `road_class_prior`, `outside_crash_coverage`.
- **Unknown_Exposure_Share**: For a Route_Option, the fraction of walk and bike length whose Risk_Basis is `road_class_prior` or `outside_crash_coverage`.
- **Crash_Risk_Index**: The crash-risk objective. It is a relative index, comparable between routes and not an absolute probability.
- **Alert_Source**: `none`, `live`, or the label of one Alert_Snapshot.
- **Alert_Snapshot**: A JSON file in `data/alert_snapshots/` holding recorded or synthetic MBTA alerts with `label`, `recorded_at`, and `synthetic`.
- **Alert_Clock**: The time at which alert active periods are evaluated. It is the trip departure time for `live` and the snapshot `recorded_at` for a snapshot.
- **Effect_Score_Table**: A fixed table in code that maps an MBTA alert `effect` to a score from 0 to 1.
- **Plan_Result**: The output of one trip query, including the ordered Pareto_Set, trade-offs versus the fastest route, omitted kinds with reasons, and the active Alert_Source.
- **Choice_Event**: One user selection, storing the chosen Objectives and the Objectives of every rejected Pareto_Set member.
- **Preference_Weights**: One non-negative weight per objective, summing to 1.
- **MCP_Server**: The MCP tool server subprocess that owns all planner state.
- **Agent**: The smolagents ToolCallingAgent that calls MCP_Server tools.
- **Model_Id**: A model identifier on the configured OpenAI-compatible endpoint, such as `deepseek/deepseek-v4.1-flash-1017` on OpenRouter or `qwen3:8b` on a local Ollama server.
- **Tool_Log**: Every tool call input and output in one conversation.
- **Offline_Mode**: The default mode. The System reads only the Data_Store and calls no network host except the configured model provider, and none at all with a local model.
- **Live_Mode**: The mode selected by `--live`. The System also calls the MBTA V3 API and a local OTP server.
- **Showcase_Trip**: One of five fixed demo trips in `data/showcase_trips.yaml`.
- **Eval_Suite**: The command that writes `eval/results.md` and `eval/results.json`.

## Requirements

### Requirement 1: Shared contracts and scaffold (WS0)

**User Story:** As a parallel implementer, I want fixed types, paths, and fixtures before any feature work, so that my module integrates without coordinating with other implementers.

#### Acceptance Criteria

1. THE System SHALL define every cross-module type in `commute_agent/models.py` as a Pydantic model or Literal alias, exactly as listed in the design document.
2. THE System SHALL define every Data_Store path in `commute_agent/paths.py` and SHALL reference Data_Store paths only through that module.
3. THE System SHALL provide test fixtures in `tests/fixtures/` for a small street graph with all edge attributes, one raw OTP response, one MBTA alerts payload, one Alert_Snapshot, and one Plan_Result JSON.
4. THE System SHALL pin every Python dependency to an exact version in `pyproject.toml` and SHALL commit `uv.lock`.
5. WHEN `uv run pytest` runs on a fresh clone with only fixtures, THE System SHALL pass every unit test without network access and without files under `data/raw/` or `data/processed/`.

### Requirement 2: Data ingestion (WS1)

**User Story:** As a developer, I want each source dataset downloaded once and normalized into the Data_Store, so that every other module reads local files and the demo runs offline.

#### Acceptance Criteria

1. THE System SHALL provide `uv run python -m commute_agent.ingest <dataset>` for each dataset name listed in the design document, plus `all`.
2. THE System SHALL read source files from `data/raw/<group>/` as listed in each group's `MANIFEST.json`, and SHALL download only the street graphs.
3. WHEN the System writes a processed file, THE System SHALL write a sidecar `<file>.meta.json` with the source URL and license copied from the raw manifest, the row count, and notes.
4. IF a source file lacks an expected column, THEN THE System SHALL exit with a non-zero status, name the dataset and each missing column, and leave the previous processed file unchanged.
5. THE System SHALL write crash records with a `source_dataset` value of `boston_vision_zero` or `cambridge_police`, and SHALL record in the sidecar that the two sources use different crash definitions and that neither is the MassDOT state crash file.
6. THE System SHALL write one crash row per involved vulnerable mode, so that a Cambridge record flagged for both cyclist and pedestrian produces one `bike` row and one `ped` row.
7. IF the Numina_Zones_File is missing, THEN THE System SHALL exit the `counts` ingest with a non-zero status and a message naming the expected path and columns.
8. WHEN `record-alert-snapshot <label>` runs, THE System SHALL fetch the MBTA V3 alerts for subway, light rail, and bus and write an Alert_Snapshot with `synthetic` set to false.

### Requirement 3: Emission factors (WS1)

**User Story:** As the user, I want every CO₂ number traced to a factor I supplied, so that the System never invents emission values.

#### Acceptance Criteria

1. WHEN any entry point starts, THE System SHALL load the Emission_Factors_File and validate that every Leg_Mode has a numeric `value`, a `unit`, and a non-empty `source`.
2. IF the Emission_Factors_File is missing, THEN THE System SHALL exit with a non-zero status and a message naming the path and the required fields.
3. IF an entry is missing, or a `unit` is not `g_per_passenger_km` or `g_per_passenger_mile`, THEN THE System SHALL exit with a non-zero status and name each offending Leg_Mode and field.
4. THE System SHALL compute Leg `co2_g` as the Leg distance converted to the entry's distance unit multiplied by the entry `value`.
5. THE System SHALL attach the Source_Label `emission_factors.yaml: <leg_mode> (<source>)` for each Leg_Mode that contributes to a Route_Option `co2_g`.

### Requirement 4: Street graphs and crash risk (WS2)

**User Story:** As a cyclist or pedestrian, I want crash risk measured per person using the street, so that a busy street with few crashes per rider is not penalized for its raw crash count.

#### Acceptance Criteria

1. THE System SHALL build walk, bike, and drive Street_Graphs for the Service_Area buffered by 2 km and save each as GraphML under `data/processed/`.
2. THE System SHALL snap each `bike` crash row to the nearest bike-graph edge and each `ped` crash row to the nearest walk-graph edge within 30 m, and SHALL log the count of rows discarded for exceeding 30 m.
3. THE System SHALL compute each walk and bike edge's `risk` as `(crashes + k * p * length_km) / exposure`, where `p` is the network mean crashes per km for that mode, `k` is the shrinkage constant in code, and `exposure` is the edge Peak_Hour_Volume.
4. THE System SHALL set an edge's Risk_Basis to `outside_crash_coverage` when the edge midpoint is outside the Crash_Coverage_Area, and SHALL compute that edge's risk from the `k * p * length_km` term alone.
5. WHEN a walk, bike, or bluebikes Leg is scored, THE System SHALL return the sum of edge risk over the Leg's edges as the Leg `crash_risk` and SHALL return the Leg length in metres per Risk_Basis.
6. WHEN an OTP itinerary contains a walk Leg, THE System SHALL score the walk Leg against the walk graph by matching each geometry segment midpoint to its nearest walk edge.
7. THE System SHALL set `crash_risk` to 0 for drive and transit-vehicle Legs and SHALL label the Route_Option crash Source_Label as covering walk and bike segments only.

### Requirement 5: Exposure estimation (WS2)

**User Story:** As the user, I want the System to say when exposure is unknown, so that a street with no counter is not treated as empty.

#### Acceptance Criteria

1. THE System SHALL compute the Exposure_Proxy by routing every Bluebikes November 2024 trip from its start station to its end station on the length-shortest bike-graph path and counting traversals per edge.
2. THE System SHALL convert Cambridge citywide, Somerville, and Numina bike counts to Peak_Hour_Volume, match each count location to the nearest bike edge within 30 m, and fit one least-squares scale factor through the origin from Exposure_Proxy to Peak_Hour_Volume.
3. THE System SHALL write `data/processed/exposure_calibration.json` with the scale factor, the matched location count per source, and the R² of the fit.
4. IF fewer than 10 count locations match, THEN THE System SHALL exit the risk build with a non-zero status and report the matched count per source.
5. THE System SHALL assign bike edges with a non-zero Exposure_Proxy the calibrated value and Risk_Basis `bluebikes_proxy`.
6. THE System SHALL assign walk edges matched to a Somerville or Numina pedestrian count that count's Peak_Hour_Volume and Risk_Basis `ped_counter`.
7. THE System SHALL assign every other walk or bike edge the median Peak_Hour_Volume of measured edges with the same OSM highway class, falling back to the median of all measured edges when the class has fewer than 20 measured edges, and SHALL set Risk_Basis `road_class_prior`.
8. THE System SHALL report each Route_Option's Unknown_Exposure_Share.

### Requirement 6: Street routing (WS3)

**User Story:** As a user, I want walk, bike, Bluebikes, and drive routes whose search accounts for crash risk, so that safer alternatives exist among the options.

#### Acceptance Criteria

1. WHEN the System routes a walk, bike, or bluebikes trip, THE System SHALL return the time-shortest path and the paths minimizing `time_s + λ * risk` for each multiplier in the design's fixed list, with λ scaled by the time-shortest path's time divided by its risk.
2. WHEN the System routes a drive trip, THE System SHALL return the free-flow time-shortest path using OSMnx imputed speeds.
3. THE System SHALL compute walk, bike, and bluebikes time from fixed per-mode speeds in code and SHALL attach a Source_Label naming the speed.
4. WHEN the System builds a bluebikes Route_Option, THE System SHALL compose a walk Leg to the nearest Bluebikes station, a bluebikes Leg to the station nearest the destination, and a walk Leg to the destination.
5. IF no path exists for a Route_Kind, THEN THE System SHALL omit that Route_Kind and report the reason `no path`.
6. THE System SHALL return at most one Route_Option per Route_Kind for each distinct edge sequence.

### Requirement 7: Transit routing (WS4)

**User Story:** As a transit rider, I want MBTA itineraries from the published schedule, so that transit options match real service.

#### Acceptance Criteria

1. WHILE the System is in Live_Mode, THE System SHALL request up to 5 transit-only itineraries from OTP for the origin, destination, and departure time, and SHALL write the raw response to `data/cache/otp/<key>.json`.
2. WHILE the System is in Offline_Mode, THE System SHALL read the raw OTP response from `data/cache/otp/<key>.json`.
3. IF no cached response exists in Offline_Mode, THEN THE System SHALL omit transit and report the reason `transit not cached for this trip`.
4. IF OTP is unreachable in Live_Mode, THEN THE System SHALL omit transit, report the reason `OTP unreachable`, and continue with street routes.
5. THE System SHALL record for every transit Leg the MBTA route id, route type, direction id, and the stop ids and parent station ids of the boarding, intermediate, and alighting stops, with the OTP feed prefix removed.
6. THE System SHALL provide `uv run python -m commute_agent.transit.cache_trips` that requests and caches OTP responses for every Showcase_Trip and every eval origin-destination pair.

### Requirement 8: Disruption risk (WS4)

**User Story:** As a transit rider, I want routes to reflect MBTA disruptions, so that the answer changes when the Red Line is disrupted.

#### Acceptance Criteria

1. THE System SHALL treat an alert as affecting a transit Leg when one informed entity matches the Leg, and an entity matches when every field it sets (route, stop, route type, direction id) equals the Leg's value or is contained in the Leg's stop ids.
2. THE System SHALL treat an alert as active when the Alert_Clock falls inside one of its active periods, where a missing end means open-ended.
3. THE System SHALL set a Leg's `disruption_risk` to the maximum Effect_Score_Table score among affecting alerts, and a Route_Option's to the maximum over its Legs.
4. THE System SHALL list every affecting alert id on the Leg and on the Route_Option.
5. IF an affecting alert's effect is absent from the Effect_Score_Table, THEN THE System SHALL leave that alert out of the score and list it in the Plan_Result `unscored_alerts`.
6. WHEN the Alert_Source changes, THE System SHALL recompute disruption risk and the Pareto_Set for the next query.
7. THE System SHALL ship one Alert_Snapshot with `synthetic` set to true that suspends Red Line service between JFK/UMass and Park Street, and THE System SHALL show the word `synthetic` wherever that snapshot is named.
8. WHEN an active alert with an Effect_Score_Table score of 1.0 names a route that a transit candidate rides, THE System SHALL also request transit itineraries that exclude those routes (cached under their own key for offline use) and add them as candidates.

### Requirement 9: Pareto engine and planner (WS5)

**User Story:** As a user, I want only non-dominated routes with stated trade-offs, so that I compare real choices.

#### Acceptance Criteria

1. IF the origin or destination is outside the Service_Area, THEN THE System SHALL return an error naming each point outside it and compute no routes.
2. THE System SHALL gather candidates from street routing and transit routing and return the Pareto_Set.
3. THE System SHALL use the absolute and relative tolerances fixed in the design for each objective when testing dominance and Near_Duplicates.
4. WHEN two candidates are Near_Duplicates, THE System SHALL keep the one with fewer Legs, then the one with lower `time_min`.
5. THE System SHALL give the labels `fastest`, `lowest_co2`, `lowest_crash_risk`, and `least_disrupted` each to exactly one Pareto_Set member, the one with the minimum value, breaking ties by lower `time_min`.
6. THE System SHALL return, for every Pareto_Set member, a trade-off versus the `fastest` member with signed absolute deltas, percentage deltas (null when the reference value is 0), and the alerts that affect the reference but not the member.
7. THE System SHALL order the Pareto_Set by Preference_Weights and SHALL return every member regardless of weights.

### Requirement 10: Preference learning (WS6)

**User Story:** As a repeat user, I want the System to learn which trade-offs I pick, so that routes like the ones I choose appear first.

#### Acceptance Criteria

1. WHEN the user chooses a Route_Option, THE System SHALL store a Choice_Event in `data/preferences.db`.
2. THE System SHALL fit Preference_Weights with a non-negative logistic model on per-query normalized objective differences between each rejected and the chosen Route_Option.
3. WHILE fewer than 3 Choice_Events exist, THE System SHALL return weights of 0.25 per objective and `insufficient_data` set to true.
4. WHEN a Choice_Event is stored, THE System SHALL use refit weights for the next query.
5. WHEN preferences are cleared, THE System SHALL delete all Choice_Events and return equal weights.

### Requirement 11: MCP tool server (WS7)

**User Story:** As the Agent, I want tools that return structured numbers with sources, so that every number I state comes from a tool output.

#### Acceptance Criteria

1. THE MCP_Server SHALL expose the tools listed in the design document, with Pydantic return types so that each result carries `structured_content`.
2. THE MCP_Server SHALL resolve `geocode_place` from a local gazetteer of MBTA rapid transit stations, Bluebikes stations, and Showcase_Trip places, and SHALL accept a literal `lat,lon` string.
3. WHEN `geocode_place` finds no match or several equally scored matches, THE MCP_Server SHALL return status `not_found` or `ambiguous` with all candidates.
4. THE MCP_Server SHALL hold the active Alert_Source and every Plan_Result of the session in memory, and SHALL be the only process that holds them.
5. IF a tool raises an error, THEN THE MCP_Server SHALL return an error result naming the tool and message and keep serving.

### Requirement 12: Agent behavior and grounding (WS7)

**User Story:** As a user, I want plain-language trade-offs with honest numbers, so that I can trust the recommendation.

#### Acceptance Criteria

1. THE Agent SHALL be a smolagents ToolCallingAgent whose model is a smolagents OpenAIModel built from one Model_Id, read from `COMMUTE_AGENT_MODEL` or a `--model` flag, on the endpoint in `COMMUTE_AGENT_API_BASE` with the key named by `COMMUTE_AGENT_API_KEY_ENV`, and THE System SHALL record every tool call in the Tool_Log.
2. WHEN the Agent answers a trip question, THE Agent SHALL describe each Pareto_Set member by its trade-off versus the fastest route, in the style "4 min slower, ~70% less CO₂, avoids the Red Line disruption".
3. WHEN the Agent states an estimate, THE Agent SHALL name its Source_Label in the same reply.
4. WHEN a route has an Unknown_Exposure_Share above 0, THE Agent SHALL say that part of that route's crash risk uses a road-class prior.
5. WHEN a conversation starts, THE Agent SHALL begin its first reply with the fixed sentence "I am an independent prototype, not a City of Boston or MBTA service."
6. WHEN `geocode_place` returns `ambiguous` or `not_found`, THE Agent SHALL ask the user to clarify instead of picking a place.
7. WHEN the Agent finishes a reply that contains a number matching no number in the Tool_Log or the user's messages after rounding, THE System SHALL ask the Agent to rewrite the reply once, SHALL replace any number still unsupported with a removal marker, and SHALL return those numbers as `unsupported_numbers`. Digits that are part of a name (CO2, SL4) are not numbers.
8. WHEN the Model_Id changes, THE System SHALL run the Agent with no code change.

### Requirement 13: API server (WS8)

**User Story:** As a presenter, I want one command that starts the API, agent, and page, so that the demo starts reliably.

#### Acceptance Criteria

1. WHEN `uv run python -m commute_agent.api` runs, THE System SHALL start the MCP_Server subprocess, serve the REST endpoints listed in the design, and serve the web page at `/`.
2. THE System SHALL bind to 127.0.0.1 and SHALL log at startup that the server has no authentication and is for local demo use.
3. THE System SHALL run in Offline_Mode unless started with `--live`.
4. IF the configured model's credentials are missing, THEN THE System SHALL serve every endpoint except `/api/chat`, which SHALL return HTTP 503 naming the Model_Id and the missing keys.
5. THE System SHALL read provider keys from `.env` in the repository root, and a variable already set in the environment SHALL take precedence.

### Requirement 14: Web UI (WS9)

**User Story:** As a demo viewer, I want a map, a linked time-versus-CO₂ chart, a chat panel, and an alert replay control, so that I can see trade-offs change live.

#### Acceptance Criteria

1. THE System SHALL serve one HTML page with vanilla JavaScript, vendored Leaflet 1.9.4, and vendored Chart.js 4.6.1, with no build step.
2. WHEN a Plan_Result arrives, THE System SHALL draw each Pareto_Set member on the map in its own color, with a line style per Leg_Mode, and plot each member on a time-versus-CO₂ scatter chart in the same color.
3. WHEN the user hovers or focuses a route on the map, the chart, or the route list, THE System SHALL highlight that route in all three.
4. THE System SHALL list each route with its labels, four objectives with units, Source_Labels, an `exposure partly unknown` badge when its Unknown_Exposure_Share is above 0, and affecting alert headers.
5. THE System SHALL provide an Alert_Source selector that lists `none`, each snapshot label, and `live` (enabled only in Live_Mode), and WHEN the selection changes, THE System SHALL re-plan the current trip and name the routes that entered or left the Pareto_Set.
6. THE System SHALL provide a Showcase_Trip selector, a `Choose this route` button per route, and a chat panel that marks each unsupported number.
7. WHILE in Offline_Mode, THE System SHALL draw a local street basemap from `data/processed/basemap.geojson` and request no map tiles.
8. THE System SHALL show a persistent notice that it is an independent prototype and not a City of Boston or MBTA service, and SHALL show "© OpenStreetMap contributors" on the map.
9. THE System SHALL give every control a visible label or `aria-label` and SHALL make every control reachable and operable by keyboard.
10. THE System SHALL also provide a Next.js presentation app in `webapp/` that meets criteria 2 to 9 against the same REST API through a same-origin proxy, binds to 127.0.0.1, loads no remote fonts or map tiles in Offline_Mode, and offers a three-step demo control (plan the Fields Corner trip, apply the synthetic Red Line suspension, ask the agent) that fits a three-minute demo. Criterion 1 applies to `web/` only.

### Requirement 15: Offline demo and showcase trips (WS1 and WS4)

**User Story:** As a presenter, I want the demo to work without network access, so that venue Wi-Fi cannot break it.

#### Acceptance Criteria

1. THE System SHALL define five Showcase_Trips in `data/showcase_trips.yaml`, each with a name, origin, destination, weekday, local departure time, and description.
2. THE System SHALL include a Showcase_Trip whose cached transit itinerary rides the Red Line between JFK/UMass and Park Street.
3. WHILE in Offline_Mode, THE System SHALL plan every Showcase_Trip from Data_Store files only.
4. THE System SHALL provide `uv run python -m commute_agent.check_offline`, which lists every missing Offline_Mode file by path and exits non-zero when any is missing.

### Requirement 16: Evaluation suite (WS10)

**User Story:** As a judge, I want measured numbers on correctness, safety, and grounding, so that the pitch rests on evidence.

#### Acceptance Criteria

1. WHEN `uv run python -m eval.run` runs, THE System SHALL write the summary table to `eval/results.md` and `eval/results.json`.
2. THE System SHALL check Pareto soundness and completeness for every Showcase_Trip and the 20 pairs in `eval/od_pairs.yaml` and report pass and fail counts.
3. THE System SHALL report, for walk and for bike separately, the median and interquartile range of percentage `crash_risk` reduction and of minutes added, for the lowest-risk member of that kind versus the fastest member of that kind.
4. THE System SHALL run every Showcase_Trip under each Alert_Source in `eval/alert_expectations.yaml` and report how many routes match the expected affected lines and how many Pareto_Sets changed as expected.
5. THE System SHALL run the Agent on the prompts in `eval/prompts.yaml` and report supported numbers over total numbers for first drafts and for final replies, the identity-statement pass count, and the clarification pass count (the final reply asks a question and no plan_trip call was made).
6. IF the configured model's credentials are missing, THEN THE System SHALL report the agent metrics as `skipped: <missing keys>` and complete the other metrics.
7. THE System SHALL report the mean Unknown_Exposure_Share across evaluated walk and bike routes.
8. WHEN `eval.run` receives several Model_Ids, THE System SHALL report the agent metrics once per Model_Id in the same table.

### Requirement 17: Code and attribution constraints (all workstreams)

**User Story:** As the maintainer who reads every line, I want lean, clearly named code with correct attribution, so that review is fast and the prototype is legally clean.

#### Acceptance Criteria

1. THE System SHALL call no Google API and no paid routing service.
2. THE System SHALL list every dataset with its source URL and license or terms in `README.md`, including OpenStreetMap attribution.
3. THE System SHALL keep each module within the file ownership table in the design, so that two workstreams never edit the same file.
4. THE System SHALL validate external data only at ingest, config load, HTTP request, and MCP tool boundaries.
