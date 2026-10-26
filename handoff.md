# Handoff

State of the Pareto Commute Agent as of 2026-10-25, for whoever picks it up next.

## What it is

A commute planner for trips inside Boston, Cambridge, Somerville, and Brookline. For one trip it returns every route that no other route beats on all four objectives: travel time, CO₂, crash risk on walk and bike segments, and MBTA disruption risk. The page also lists the routes it dropped and which route beats each one. An LLM agent explains the trade-offs using only numbers the tools returned. It is an independent prototype, not a City of Boston or MBTA service.

- Hosting: none right now. The Render services and workspace were deleted on 2026-10-26; the old address `commute-agent-5hla.onrender.com` no longer works. Run it locally (see "Running it").
- Repository: https://github.com/Riddhimaan-Senapati/commute-agent, branch `main`
- Spec: `.kiro/specs/pareto-commute-agent/` (`requirements.md`, `design.md`, `tasks.md`). `docs/plans/next-features.md` is the plan for the second round of features.
- Setup, data build, deploy, eval, and data notes: `README.md`

## How it fits together

| Part | Where | Notes |
|---|---|---|
| Data ingest and risk build | `scripts/fetch/`, `commute_agent/ingest/`, `commute_agent/streets/build.py` | Fetch writes `data/raw/` with manifests; ingest and the risk build write `data/processed/`. The service area is the union of four city boundaries (`commute_agent/service_area.py`). |
| Street routing | `commute_agent/streets/router.py` | OSMnx graphs, crash-risk-weighted sweep for safer variants. Drive time is free flow times an hourly traffic factor (`data/traffic_factors.yaml`, TomTom 2025). Bluebikes can check live dock counts (`bluebikes_live.py`, `COMMUTE_BLUEBIKES_LIVE=1`). |
| Transit | `commute_agent/transit/otp.py`, `alerts.py` | OpenTripPlanner 2.10.0. Cache first (`data/cache/otp/`), then the server at `COMMUTE_OTP_URL` when set. Walk-access, bike-access ("Bike, then X, then Bike") and step-free (wheelchair) queries have separate cache keys. A suspending alert triggers a re-query that excludes the suspended route. Alert matching honours MBTA `activities`. |
| Pareto and planner | `commute_agent/pareto.py`, `planner.py` | Tolerance dominance, with a strict-dominance fallback if tolerance forms a cycle. `filtered_out` explains every dropped candidate. |
| MCP tool server | `commute_agent/mcp_server.py` | Owns plans. Takes `alert_source`, `session_id`, `step_free` per call. |
| Agent | `commute_agent/agent.py`, `grounding.py` | smolagents `ToolCallingAgent` on an OpenAI-compatible endpoint (OpenRouter by default). `ConversationTool` fills the tab's alert source and session into tool calls, hidden from the model. Unsupported numbers are rewritten once, then redacted. The identity sentence is prepended on a first reply that lacks it (`identity_added`). |
| API | `commute_agent/api.py` | FastAPI. Per-tab state keyed by `X-Session-Id` (alert source; preferences live in `preferences.db` by session). Chat rate limits: 5 a minute per tab, 60 a minute overall. Optional `COMMUTE_ALLOWED_HOSTS`. |
| Web page | `webapp/` (Next.js), `web/` (vanilla fallback) | Map, From/To, objective picker, departure picker, alert source menu, Red Line suspension and Step-free switches, trade-off chart, frontier and dropped-route lists, agent drawer with New chat. |
| Eval | `eval/run.py` | Writes `eval/results.md` and `eval/results.json`. |

## Decisions

- **Emission factors** (`data/emission_factors.yaml`): EPA GHG Emission Factors Hub 2025, Table 10, and UK DESNZ 2026 for ferry. CO₂ only. Walk, bike, and Bluebikes are 0.
- **Traffic factors** (`data/traffic_factors.yaml`): TomTom Traffic Index 2025, City of Boston hourly congestion grid, read from the page with "Show values" on. Value = 1 + level / 100 (TomTom's definition); weekday is the Monday to Friday mean, weekend the Saturday and Sunday mean. Weekday 8 AM is 1.60 and 5 PM is 1.84.
- **Agent model:** `deepseek/deepseek-v4.1-flash-1017` on OpenRouter, key `OPENROUTER_API_KEY` in `.env`. LiteLLM was removed because of an `mcp` version conflict.
- **Hosting:** Render cost $110 a month (web `2c-4g` $85, OTP `1c-2g` $25) and was shut down. Render's free plan (512 MB) can't hold the app, which needs about 3.4 GB plus about 1 GB for OTP. Hugging Face Docker Spaces need a PRO subscription ($9 a month) as of 2026-10-26. A free option still to try is an Oracle Cloud Always Free ARM VM.
- **Walking exposure:** MBTA station entries were tried as an extra signal and not adopted (adjusted R² fell on the Boston-only data). The code is behind `USE_STATION_ENTRIES = False`.
- **Brookline** has no open crash data; its streets use the road-class prior and are outside `crash_coverage.geojson`.

## Deployment

- `.github/workflows/deploy.yml` (named `test`): every push to `main` runs ruff, pytest and the webapp typecheck. It no longer deploys.
- Branch `hf-spaces` has the next hosting setup, ready for any host that runs a Docker image with about 6 GB of memory: a single `Dockerfile` with OTP, the API and Next.js on port 7860 (about 4.4 GiB in use, tested locally), a Space deploy in `deploy.yml`, and `.github/workflows/gtfs-refresh.yml`, which rebuilds the OTP graph and re-caches trips weekly when the MBTA publishes a new GTFS feed. The current feed ends 2026-12-12.
- Runtime data is not in git. It lives on GitHub releases: `data-latest` (current data, OTP graph, OSM extract, GTFS version; the refresh job replaces it), `data-v2` (four-city build), and `data-v1` (Boston-only build).
- Rebuilding the data: see "Build the data" in the README. The graph download uses Overpass; set `COMMUTE_OVERPASS_URL` to a mirror if overpass-api.de returns 502.

## Known limits

- The Pareto soundness and completeness checks use the planner's own dominance rule, so they show the filter is applied consistently, not that the rule is right.
- Walk crash risk is effectively crash density: almost no pedestrian counters exist, so walk exposure is a road-type prior.
- Offline, transit exists only for cached trips. The hosted site has live OTP, so any trip in the four cities gets transit.
- A suspending alert excludes the whole route from the re-query, even when the alert covers only part of the line.
- The traffic factor scales the whole drive by a citywide hourly average; it is not per-road.
- The live link has no login. The chat spends the OpenRouter key; a credit limit is set on it, and the API rate-limits chat.
- Rate limits and per-tab alert sources live in memory and reset when the service restarts.
- `scripts/fetch/common.py` and `scripts/fetch/transit.py` were reformatted by an early `ruff --fix`; the originals were not in git.

## Running it

```sh
uv sync
uv run python -m commute_agent.check_offline   # prints "offline ready" once data/ is built
docker/otp/run.sh serve                         # optional: live transit on :8080
COMMUTE_OTP_URL=http://localhost:8080/otp/gtfs/v1 uv run python -m commute_agent.api   # http://127.0.0.1:8000/
cd webapp && npm install && npm run build && npm run start   # http://127.0.0.1:3000/
npm run test:demo                               # Playwright end-to-end, starts both servers
```
