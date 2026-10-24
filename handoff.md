# Handoff

State of the Pareto Commute Agent as of 2026-10-24, for whoever picks it up next.

## What it is

A commute planner for trips inside Boston. For one trip it returns every route that no other route beats on all four objectives: travel time, CO₂, crash risk on walk and bike segments, and MBTA disruption risk. An LLM agent explains the trade-offs using only numbers the tools returned. It is an independent hackathon prototype, not a City of Boston or MBTA service.

- Live demo: https://commute-agent-5hla.onrender.com (no login)
- Repository: https://github.com/Riddhimaan-Senapati/commute-agent, branch `main`
- Spec: `.kiro/specs/pareto-commute-agent/` (`requirements.md`, `design.md`, `tasks.md`). It is kept in sync with the code.
- Setup, data build, and eval details: `README.md`

## How it fits together

| Part | Where | Notes |
|---|---|---|
| Data ingest and risk build | `commute_agent/ingest/`, `commute_agent/streets/build.py` | Reads `data/raw/` (fetched by `scripts/fetch/`). Writes `data/processed/`. |
| Street routing | `commute_agent/streets/router.py` | OSMnx graphs, crash-risk-weighted sweep for safer variants. Graphs are projected once at load. |
| Transit | `commute_agent/transit/otp.py`, `alerts.py` | OpenTripPlanner 2.10.0 in Docker, cached in `data/cache/otp/`. A suspending alert triggers a second OTP query that excludes the suspended route. |
| Pareto and planner | `commute_agent/pareto.py`, `planner.py` | Tolerance dominance, with a strict-dominance fallback if tolerance forms a cycle. |
| MCP tool server | `commute_agent/mcp_server.py` | Owns plans and choices. The API keeps one alert source per browser tab (`X-Session-Id`) and passes it to `plan_trip` and `get_active_alerts`. Offline, a request within 300 m of a showcase trip uses that trip's points and fixed Tuesday 08:15 departure. |
| Agent | `commute_agent/agent.py`, `grounding.py` | smolagents `ToolCallingAgent` on an OpenAI-compatible endpoint (OpenRouter by default). A reply with numbers not found in tool output is rewritten once; numbers still unsupported are replaced with a removal marker. |
| API | `commute_agent/api.py` | FastAPI, binds 127.0.0.1 by default, no auth. Starts the MCP server as a subprocess. |
| Web pages | `webapp/` (Next.js, main demo), `web/` (vanilla fallback) | `webapp/` is one map-first page: street map, From/To search, objective picker, directions, Red Line suspension switch, agent drawer. |
| Eval | `eval/run.py` | Writes `eval/results.md` and `eval/results.json`. |

## Decisions made in this session

- **Emission factors** (`data/emission_factors.yaml`): user-verified. EPA GHG Emission Factors Hub 2025, Table 10 (drive alone, transit rail, commuter rail, bus), UK DESNZ 2026 for ferry. Values are CO₂ only, not CO₂e. Walk, bike, and Bluebikes are 0 (rebalancing not counted).
- **Agent model:** `deepseek/deepseek-v4.1-flash-1017` on OpenRouter. The key is `OPENROUTER_API_KEY`, read from `.env` (gitignored).
- **LiteLLM was removed.** `litellm==1.106.3` needs a newer `mcp` than the `mcp==1.30.0` smolagents requires, and every agent step failed. The agent now uses smolagents `OpenAIModel`. Switch endpoints with `COMMUTE_AGENT_API_BASE` and `COMMUTE_AGENT_API_KEY_ENV`.
- **Identity sentence:** the system prompt requires the first reply to begin with "I am an independent prototype, not a City of Boston or MBTA service." Nothing enforces it in code.
- **Hosting:** Render, not Vercel. The backend needs about 3.2 GB of memory and about 840 MB of packages and data, which exceeds Vercel's Python function limits.

## Deployment

- One Docker container (`Dockerfile`, `docker/render/start.sh`). FastAPI and the MCP server run on the container's loopback; Next.js serves `$PORT` and proxies `/api/*`. Only Next.js is public.
- Runtime data (processed graphs, basemap, OTP cache, about 35 MB compressed) is not in git. The build downloads it from the GitHub release `data-v1`.
- Render service `srv-dbeitagae00c73da2ht0`, plan `2c-4g` (2 CPU, 4 GB), region Virginia. `render.yaml` describes it. Render's own auto-deploy is off.
- `.github/workflows/deploy.yml`: on every push to `main`, runs ruff, pytest, and the webapp type check, then deploys that commit with `render deploys create --commit <sha> --wait`. Repository secrets `RENDER_API_KEY` and `RENDER_SERVICE_ID` are set. `OPENROUTER_API_KEY` is set on the Render service.
- A deploy takes about 2 minutes to build plus about 80 seconds while the street graphs load.

## Verified state

- Python: 168 tests pass; ruff is clean. Webapp: `npm run typecheck` passes.
- GitHub Actions run 40140003759: `test` and `deploy` both succeeded. Render's live deploy is commit `43992a1`.
- Playwright (`webapp/e2e/demo.spec.ts`) passes against the live URL: the full demo flow, and the offline basemap fallback.
- Eval at commit `2dde2f9`: Pareto checks 25/25, alert checks 35/35 and 6/6, grounding 98% first draft and 100% final, identity 9/10, clarification 2/2.

## Known limits

- The Pareto soundness and completeness checks use the planner's own dominance rule, so they show the filter is applied consistently, not that the rule is right.
- Walk routes use road-type exposure for all of their length (2 of about 297,000 walk edges match a pedestrian counter), so walk crash risk is effectively crash density. Bike routes use Bluebikes-estimated exposure for about 73% of their length.
- Exposure calibration R² is 0.09.
- Offline, transit exists only for cached trips (the five showcase trips and the 20 eval pairs).
- A suspending alert excludes the whole route from the re-query, even when the alert covers only part of the line.
- Drive time is free-flow and ignores traffic.
- The live link has no login. Anyone with it can use the chat, which spends the OpenRouter key; a credit limit is set on that key.
- The Red Line suspension in the demo is a labeled synthetic alert, not a real MBTA alert.

## Open items

- Review nits from the code review that were not fixed: a Host-header check against DNS rebinding, pagination for live MBTA alerts, `activities` matching for alert entities, duplicated weekday helpers, ignoring OTP GraphQL errors when caching.
- Consider enforcing the identity sentence in code.
- `scripts/fetch/common.py` and `scripts/fetch/transit.py` were reformatted by an early `ruff --fix`; the originals were not in git and could not be restored.

## Running it

Locally:

```sh
uv sync
uv run python -m commute_agent.check_offline   # prints "offline ready" once data/ is built
uv run python -m commute_agent.api             # http://127.0.0.1:8000/
cd webapp && npm install && npm run build && npm run start   # http://127.0.0.1:3000/
npm run test:demo                               # Playwright end-to-end, starts both servers
```

Two-minute demo, using the five trip chips:

1. The page opens on Fields Corner to Downtown Crossing. Tap Fastest, then Lowest CO₂, then All trade-offs, and open the Red Line card.
2. Tap Maverick → Seaport: the harbor makes the bike slow, and Blue Line then bus wins.
3. Back on Fields Corner, flip "Simulate Red Line suspension". "19, then Orange Line" and "15, then SL4" enter the set.
4. Open "Ask the agent" and tap the suggested question. The reply takes about 25 seconds.
