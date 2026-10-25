"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import PlaceInput from "@/components/PlaceInput";
import FilteredRoutes from "@/components/FilteredRoutes";
import RouteCard from "@/components/RouteCard";
import TradeoffChart from "@/components/TradeoffChart";
import {
  type AlertSources,
  AlertSourcesSchema,
  AlertsViewSchema,
  ApiError,
  type Place,
  type Plan,
  PlanSchema,
  PreferenceSchema,
  type Status,
  StatusSchema,
  type Trip,
  TripsSchema,
  api,
} from "@/lib/api";
import { OBJECTIVES, type Objective, departureText, enteredRoutes, visibleRoutes } from "@/lib/format";

const RouteMap = dynamic(() => import("@/components/RouteMap"), {
  ssr: false,
  loading: () => <div className="map map-loading">Loading map…</div>,
});

const NO_ALERTS = "none";
const PANEL_WIDTH = 400;

function placeOf(trip: Trip, end: "origin" | "destination"): Place {
  return end === "origin"
    ? { name: trip.origin_name, point: trip.origin, source: "showcase" }
    : { name: trip.destination_name, point: trip.destination, source: "showcase" };
}

export default function Home() {
  const [status, setStatus] = useState<Status | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [sources, setSources] = useState<AlertSources | null>(null);
  const [from, setFrom] = useState<Place | null>(null);
  const [to, setTo] = useState<Place | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [objective, setObjective] = useState<Objective>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [alertHeaders, setAlertHeaders] = useState<Map<string, string>>(new Map());
  const [news, setNews] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chatOpen, setChatOpen] = useState(false);

  const synthetic = sources?.sources.find((s) => s.startsWith("Synthetic")) ?? null;
  const suspended = synthetic !== null && sources?.active === synthetic;
  const shown = useMemo(() => (plan ? visibleRoutes(plan, objective) : []), [plan, objective]);
  const trades = useMemo(() => new Map(plan?.tradeoffs_vs_fastest.map((t) => [t.route_id, t])), [plan]);

  const planRoute = useCallback(async (origin: Place, destination: Place) => {
    setBusy(true);
    setError(null);
    try {
      const result = await api("/api/plan", PlanSchema, {
        method: "POST",
        body: { origin: origin.point, destination: destination.point, depart_at: null },
      });
      const view = await api("/api/alerts", AlertsViewSchema);
      setAlertHeaders(new Map(view.alerts.map((a) => [a.id, a.header])));
      setPlan(result);
      setChosenId(null);
      return result;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Planning failed.");
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    Promise.all([
      api("/api/status", StatusSchema),
      api("/api/showcase-trips", TripsSchema),
      api("/api/alert-sources", AlertSourcesSchema),
    ])
      .then(async ([s, t, a]) => {
        setStatus(s);
        setTrips(t.trips);
        setSources(a);
        const first = t.trips[0];
        if (first) {
          setFrom(placeOf(first, "origin"));
          setTo(placeOf(first, "destination"));
          await planRoute(placeOf(first, "origin"), placeOf(first, "destination"));
        }
      })
      .catch(() => setError("Cannot reach the API. Start it with: uv run python -m commute_agent.api"));
  }, [planRoute]);

  // Keep a visible route selected whenever the plan or objective changes.
  useEffect(() => {
    if (!shown.some((r) => r.id === selectedId)) setSelectedId(shown[0]?.id ?? null);
  }, [shown, selectedId]);

  async function go(origin: Place | null, destination: Place | null) {
    if (!origin || !destination) return;
    setNews(null);
    await planRoute(origin, destination);
  }

  function pickTrip(trip: Trip) {
    const a = placeOf(trip, "origin");
    const b = placeOf(trip, "destination");
    setFrom(a);
    setTo(b);
    void go(a, b);
  }

  async function toggleSuspension() {
    if (!synthetic) return;
    const name = suspended ? NO_ALERTS : synthetic;
    setSources((current) => (current ? { ...current, active: name } : current));
    const updated = await api("/api/alert-source", AlertSourcesSchema, { method: "POST", body: { name } });
    setSources(updated);
    if (!from || !to) return;
    const before = plan;
    const after = await planRoute(from, to);
    if (!after || !before) return;
    if (name === NO_ALERTS) {
      setNews("Red Line service restored.");
      return;
    }
    const entered = enteredRoutes(before, after);
    setNews(
      entered.length
        ? `Red Line suspended (synthetic). New options: ${entered.join("; ")}.`
        : "Red Line suspended (synthetic). Affected routes are flagged.",
    );
    setObjective("all");
  }

  async function choose(routeId: string) {
    if (!plan) return;
    await api("/api/choice", PreferenceSchema, { method: "POST", body: { query_id: plan.query_id, route_id: routeId } });
    setChosenId(routeId);
  }

  return (
    <div className="app">
      <RouteMap
        routes={shown}
        selectedId={selectedId}
        origin={plan?.origin ?? from?.point ?? null}
        destination={plan?.destination ?? to?.point ?? null}
        onSelect={setSelectedId}
        panelWidth={PANEL_WIDTH}
      />

      <aside className="panel" style={{ width: PANEL_WIDTH }} aria-label="Trip planner">
        <header className="brand">
          <h1>Pareto Commute</h1>
          <p className="notice" role="note">
            {status?.notice ?? "Independent prototype. Not a City of Boston or MBTA service."}
          </p>
        </header>

        <div className="places">
          <PlaceInput label="From" marker="A" value={from} onChange={(p) => { setFrom(p); void go(p, to); }} />
          <PlaceInput label="To" marker="B" value={to} onChange={(p) => { setTo(p); void go(from, p); }} />
          <button
            type="button"
            className="swap"
            aria-label="Swap start and destination"
            onClick={() => { setFrom(to); setTo(from); void go(to, from); }}
          >
            ⇅
          </button>
        </div>

        <div className="demo-trips" aria-label="Demo trips">
          {trips.map((t) => (
            <button key={t.name} type="button" onClick={() => pickTrip(t)} disabled={busy} title={t.description}>
              {t.origin_name.split(",")[0]} → {t.destination_name.split(",")[0]}
            </button>
          ))}
        </div>

        <div className="objectives" role="radiogroup" aria-label="What matters most">
          {OBJECTIVES.map((o) => (
            <button
              key={o.id}
              type="button"
              role="radio"
              aria-checked={objective === o.id}
              title={o.hint}
              onClick={() => setObjective(o.id)}
            >
              {o.label}
            </button>
          ))}
        </div>

        {synthetic && (
          <label className="switch">
            <input type="checkbox" checked={suspended} onChange={() => void toggleSuspension()} disabled={busy} />
            <span>Simulate Red Line suspension</span>
            <small>synthetic demo alert</small>
          </label>
        )}

        {news && (
          <p className="news" role="status">
            {news}
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        {plan && (
          <p className="meta">
            {busy ? "Updating…" : `Leaving ${departureText(plan.depart_at)}`}
            {plan.notes[0]?.startsWith("Planned as showcase") && " (fixed demo departure)"}
            {plan.omitted.transit && ` · transit: ${plan.omitted.transit}`}
          </p>
        )}
        {!plan && <p className="meta">{busy ? "Finding routes…" : "Enter a start and a destination."}</p>}

        {plan && shown.length === 0 && (
          <p className="meta">No route holds this label for this trip. Pick “All trade-offs”.</p>
        )}
        {plan && objective === "all" && (
          <h2 className="list-head">
            On the Pareto frontier: {plan.routes.length} of {plan.candidate_count} routes
          </h2>
        )}
        {plan && objective === "all" && (
          <TradeoffChart
            routes={plan.routes}
            filtered={plan.filtered}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        )}
        <ol className="cards" aria-label="Routes">
          {shown.map((route) => (
            <RouteCard
              key={route.id}
              route={route}
              tradeoff={trades.get(route.id)}
              selected={route.id === selectedId}
              chosen={route.id === chosenId}
              alertHeaders={alertHeaders}
              onSelect={() => setSelectedId(route.id)}
              onChoose={() => void choose(route.id)}
            />
          ))}
        </ol>
        {plan && objective === "all" && <FilteredRoutes filtered={plan.filtered} />}
        {plan && objective !== "all" && plan.routes.length > shown.length && (
          <button type="button" className="link" onClick={() => setObjective("all")}>
            Show all {plan.routes.length} trade-off routes
          </button>
        )}

        {plan && (
          <details className="caveats">
            <summary>How to read these numbers</summary>
            <ul>
              {plan.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </details>
        )}
      </aside>

      <button
        type="button"
        className="agent-fab"
        aria-expanded={chatOpen}
        aria-controls="agent-drawer"
        onClick={() => setChatOpen((v) => !v)}
      >
        {chatOpen ? "Close agent" : "Ask the agent"}
      </button>
      <section id="agent-drawer" className={`drawer${chatOpen ? " open" : ""}`} inert={!chatOpen}>
        {status && (
          // A new trip starts a new conversation, so the agent never carries an old trip over.
          <ChatPanel
            key={`${from?.name ?? ""}|${to?.name ?? ""}`}
            available={status.agent_available} model={status.agent_model} missingKeys={status.missing_model_keys} />
        )}
      </section>
    </div>
  );
}
