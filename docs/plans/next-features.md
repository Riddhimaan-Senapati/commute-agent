# Next features plan

Branch: `next-features`, cut from `live-transit` (what the live site runs). Each feature is built by a subagent in its own git worktree and branch, then merged here. Nothing goes to `main` until the user says so.

External docs checked with Context7: OpenTripPlanner 2 `planConnection` (`/opentripplanner/opentripplanner`). Bluebikes publishes a standard GBFS feed; there is no Context7 entry, so that work checks the live feed.

## Rules for every feature

- Match the surrounding code: comment density, naming, Pydantic models in `models.py`, MCP tools in `mcp_server.py`, zod schemas in `webapp/lib/api.ts`.
- Lint with `uv run ruff check commute_agent tests eval` and `uv run ruff format`, test with `uv run pytest -q`, and type-check with `cd webapp && npm ci && npm run typecheck`.
- Don't run `npm run test:demo` or start the full API. Several agents run at once, so ports and memory would clash. The integrator runs the end-to-end test after merging.
- No invented numbers. Every factor, speed, or score needs a source recorded next to it, the way `data/emission_factors.yaml` does. If no source can be found, use an obviously marked placeholder and say so in the report.
- The agent never sees data it shouldn't quote. Anything added to `PlanResult` for the UI only is stripped in `_agent_view`.
- Update `.kiro/specs/pareto-commute-agent/design.md` and `README.md` where behavior changes.

## Features

### 1. Departure time and live alerts (`f1-departure`)

- The page gets a "Leave now / Depart at" control. The chosen time is sent as `depart_at` on `/api/plan`.
- Offline cached trips keep working: a request within 300 m of a showcase trip with no time chosen still uses the fixed demo departure.
- With `COMMUTE_OTP_URL` set, any other time goes to OTP. Cache keys already include weekday and time.
- An alert source "MBTA live" uses the existing live loader (`load_alerts(..., live=True)`, 60 s TTL) without requiring `--live`. Offline snapshots stay selectable.
- Done when: tests cover a chosen departure reaching OTP and the showcase fallback still applying when no time is given.

### 2. Per-visitor preferences (`f2-preferences`)

- `record_choice`, `get_preferences` and `clear_preferences` take an optional `session_id`. The API passes the `X-Session-Id` it already uses for alert sources.
- Rows in `preferences.db` are keyed by session. Route ordering for one tab ignores other tabs' choices. The agent's copies of these tools are bound to the conversation's session the same way `AlertSourceTool` binds alert sources.
- Done when: an API test shows two sessions' choices don't affect each other.

### 3. Bluebikes live dock availability (`f3-gbfs`)

- New `commute_agent/bluebikes_live.py` reads Bluebikes GBFS (`gbfs.json`, then `station_status.json`) with a 60 s cache and a short timeout.
- The Bluebikes candidate picks start stations with `num_bikes_available > 0` and end stations with `num_docks_available > 0`, and notes the counts in its sources.
- Offline, or if the feed fails, behavior is unchanged and a note says availability was not checked.
- Done when: tests use a recorded fixture of the feed, and the fixed note "Bluebikes dock availability is not checked." appears only when it wasn't checked.

### 4. Chat protection and identity enforcement (`f4-chat-guard`)

- Per-session and global rate limits on `/api/chat`, with values in constants and a 429 carrying a clear message.
- A configurable `COMMUTE_ALLOWED_HOSTS` check on the API (DNS-rebinding guard). Unset means allow all, as today.
- `run_turn` makes sure the first reply of a conversation starts with `IDENTITY_SENTENCE`, prepending it if the model left it out.
- Done when: tests cover the 429, a rejected host, and the identity prepend.

### 5. Trade-off chart (`f5-chart`)

- A small time-against-CO₂ scatter in the side panel under "All trade-offs". Frontier routes are solid and clickable (selecting the card); `filtered` routes are grey.
- Inline SVG, no new chart library. Readable at 400 px wide.
- Done when: typecheck passes and the chart has an accessible name, plus a text fallback.

### 6. Drive times with traffic (`f6-traffic`)

- New `data/traffic_factors.yaml`: a multiplier on free-flow drive time by weekday or weekend and hour bucket. Every value has a `source`. Values come from a published Boston congestion source; if none can be confirmed, the agent ships placeholder 1.0 values marked "needs a source" and says so.
- The drive candidate multiplies free-flow time by the factor for the departure time, and the drive source text names the factor and its source.
- Done when: tests cover the multiplier, and the file is validated at load the way emission factors are.

### 7. Bike plus transit (`f7-bike-transit`)

- A second OTP query per trip with `modes: { transit: { access: [BICYCLE], egress: [WALK] } }` (bike on board where GTFS `bikesAllowed` permits), and, if the graph supports it, `BICYCLE_PARKING` access.
- Bike access legs become `bike` legs with crash risk scored on the bike graph, like walk legs on transit routes are scored on the walk graph.
- Cached separately from walk-access results. Offline, with no cache, it is simply absent.
- Done when: tests with a recorded OTP fixture produce a transit route with a bike first leg, and the existing transit tests still pass.

### 8. Step-free routes (`f8-step-free`)

- A "Step-free" toggle on the page. With it on, OTP is queried with wheelchair accessibility, and routes through a station with an active `ELEVATOR_CLOSURE` alert are flagged and given disruption 1.0 for that leg.
- Elevator alerts get an `EFFECT_SCORES` entry only when the toggle is on.
- Done when: tests cover the flag from a recorded alert fixture.

### 9. Service area beyond Boston (`f9-service-area`)

- The service area becomes Boston, Cambridge, Somerville and Brookline. The boundary is the union; street graphs, risk build and the OTP OSM extract cover it.
- Code and pipeline only in this pass: parametrize the boundary build and graph fetch, keep the planner's "outside" message naming the covered cities. Build the data into the worktree's own `data/`, never the shared checkout's.
- Brookline needs a crash source. If none is found, routes there use the road-class prior, and `crash_coverage.geojson` says so.
- Done when: tests cover a Cambridge-to-Boston trip on fixtures, and the report says what data was rebuilt and what is left (new release asset, OTP graph rebuild).

### 10. Better walking exposure (`f10-walk-exposure`)

- Investigate one extra exposure signal for walk edges, such as MBTA station entries near the edge or land use. Use only open data with a recorded source and license.
- Refit the calibration and report R² before and after. Adopt it only if R² improves, and record the result in `exposure_calibration.json` and the README data notes.
- Done when: the report gives both R² values and the decision.

## Order of merging

Merge 4, 2, 1, 3, 6, 7, 8, 5, then 9 and 10 last, because 9 and 10 touch data builds. After each merge, run lint, pytest and typecheck. After all merges, run `npm run test:demo` once.

## Not in this plan

- Pushing to `main` or deploying. The user merges.
- Uploading new release assets or rebuilding the hosted OTP graph for the larger service area. The feature 9 report lists those steps.
