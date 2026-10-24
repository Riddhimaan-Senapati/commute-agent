"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useState } from "react";
import ChatPanel from "@/components/ChatPanel";
import RouteList from "@/components/RouteList";
import TradeoffChart from "@/components/TradeoffChart";
import {
  type AlertSources,
  AlertSourcesSchema,
  AlertsViewSchema,
  ApiError,
  type Plan,
  PlanSchema,
  PreferenceSchema,
  type Status,
  StatusSchema,
  type Trip,
  TripsSchema,
  api,
} from "@/lib/api";
import { type ParetoChange, departureText, paretoChange } from "@/lib/format";

const RouteMap = dynamic(() => import("@/components/RouteMap"), {
  ssr: false,
  loading: () => <div className="map map-loading">Loading map…</div>,
});

const NO_ALERTS = "none";

export default function Home() {
  const [status, setStatus] = useState<Status | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [sources, setSources] = useState<AlertSources | null>(null);
  const [trip, setTrip] = useState<Trip | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [change, setChange] = useState<ParetoChange | null>(null);
  const [alertHeaders, setAlertHeaders] = useState<Map<string, string>>(new Map());
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onHighlight = useCallback((id: string | null) => setHighlightedId(id), []);

  useEffect(() => {
    Promise.all([
      api("/api/status", StatusSchema),
      api("/api/showcase-trips", TripsSchema),
      api("/api/alert-sources", AlertSourcesSchema),
    ])
      .then(([s, t, a]) => {
        setStatus(s);
        setTrips(t.trips);
        setSources(a);
      })
      .catch(() => setError("Cannot reach the API. Start it with: uv run python -m commute_agent.api"));
  }, []);

  async function refreshAlerts() {
    const view = await api("/api/alerts", AlertsViewSchema);
    setAlertHeaders(new Map(view.alerts.map((a) => [a.id, a.header])));
  }

  async function planTrip(next: Trip, previous: Plan | null): Promise<Plan | null> {
    setBusy(true);
    setError(null);
    try {
      const result = await api("/api/plan", PlanSchema, {
        method: "POST",
        body: { origin: next.origin, destination: next.destination, depart_at: null },
      });
      await refreshAlerts();
      setChange(previous ? paretoChange(previous, result) : null);
      setPlan(result);
      setTrip(next);
      setChosenId(null);
      return result;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Planning failed.");
      return null;
    } finally {
      setBusy(false);
    }
  }

  async function setAlertSource(name: string, current = { trip, plan }) {
    const updated = await api("/api/alert-source", AlertSourcesSchema, {
      method: "POST",
      body: { name },
    });
    setSources(updated);
    if (current.trip) await planTrip(current.trip, current.plan);
  }

  async function choose(routeId: string) {
    if (!plan) return;
    await api("/api/choice", PreferenceSchema, {
      method: "POST",
      body: { query_id: plan.query_id, route_id: routeId },
    });
    setChosenId(routeId);
  }

  const synthetic = sources?.sources.find((s) => s.startsWith("Synthetic")) ?? null;
  const live = status?.run_mode === "live";

  async function demoStep(step: 1 | 2) {
    const first = trips[0];
    if (!first) return;
    if (step === 1) {
      if (sources?.active !== NO_ALERTS) {
        setSources(await api("/api/alert-source", AlertSourcesSchema, { method: "POST", body: { name: NO_ALERTS } }));
      }
      await planTrip(first, null);
      return;
    }
    if (!synthetic) return;
    const base = trip?.name === first.name && plan ? plan : await planTrip(first, null);
    if (base) await setAlertSource(synthetic, { trip: first, plan: base });
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Pareto Commute</h1>
          <p className="tagline">Every Boston route no other route beats on time, CO₂, crash risk, and disruption.</p>
        </div>
        <p className="notice" role="note">
          {status?.notice ?? "Independent prototype. Not a City of Boston or MBTA service."}
        </p>
      </header>

      <nav className="controls" aria-label="Trip and alert controls">
        <ol className="demo" aria-label="Three-step demo">
          <li>
            <button type="button" onClick={() => void demoStep(1)} disabled={busy || !trips.length}>
              <span className="step">1</span> Plan Fields Corner → Downtown Crossing
            </button>
          </li>
          <li>
            <button type="button" onClick={() => void demoStep(2)} disabled={busy || !synthetic}>
              <span className="step">2</span> Suspend the Red Line (synthetic)
            </button>
          </li>
          <li>
            <span className="step-hint">
              <span className="step">3</span> Ask the agent, right panel
            </span>
          </li>
        </ol>
        <div className="selects">
          <label>
            Showcase trip
            <select
              value={trip?.name ?? ""}
              onChange={(e) => {
                const next = trips.find((t) => t.name === e.target.value);
                if (next) void planTrip(next, null);
              }}
              disabled={busy}
            >
              <option value="" disabled>
                Choose a trip
              </option>
              {trips.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.origin_name} → {t.destination_name}
                </option>
              ))}
            </select>
          </label>
          <label>
            Alert source
            <select
              value={sources?.active ?? NO_ALERTS}
              onChange={(e) => void setAlertSource(e.target.value)}
              disabled={busy || !sources}
            >
              {sources?.sources.map((s) => (
                <option key={s} value={s} disabled={s === "live" && !live}>
                  {s === "live" && !live ? "live (needs --live)" : s}
                </option>
              ))}
            </select>
          </label>
        </div>
      </nav>

      {error && (
        <p className="error-banner" role="alert">
          {error}
        </p>
      )}
      {change && (change.entered.length > 0 || change.left.length > 0) && (
        <p className="change-banner" role="status">
          Pareto set changed.
          {change.entered.length > 0 && <> Entered: <strong>{change.entered.join(", ")}</strong>.</>}
          {change.left.length > 0 && <> Left: <strong>{change.left.join(", ")}</strong>.</>}
        </p>
      )}

      <main className="grid">
        <section className="panel list-panel" aria-label="Routes">
          {plan ? (
            <>
              <p className="plan-meta">
                {plan.routes.length} routes from {plan.candidate_count} candidates · departs{" "}
                {departureText(plan.depart_at)} · alerts: {plan.alert_source}
              </p>
              <RouteList
                plan={plan}
                alertHeaders={alertHeaders}
                highlightedId={highlightedId}
                chosenId={chosenId}
                onHighlight={onHighlight}
                onChoose={(id) => void choose(id)}
              />
              <details className="notes">
                <summary>Caveats</summary>
                <ul>
                  {plan.notes.map((n) => (
                    <li key={n}>{n}</li>
                  ))}
                  {Object.entries(plan.omitted).map(([k, v]) => (
                    <li key={k}>
                      {k} omitted: {v}
                    </li>
                  ))}
                </ul>
              </details>
            </>
          ) : (
            <p className="empty">{busy ? "Planning…" : "Start with step 1, or pick a showcase trip."}</p>
          )}
        </section>

        <section className="panel map-panel" aria-label="Map">
          <RouteMap plan={plan} live={live} highlightedId={highlightedId} onHighlight={onHighlight} />
        </section>

        <aside className="panel side">
          {plan && <TradeoffChart plan={plan} highlightedId={highlightedId} onHighlight={onHighlight} />}
          {status && (
            <ChatPanel
              available={status.agent_available}
              model={status.agent_model}
              missingKeys={status.missing_model_keys}
            />
          )}
        </aside>
      </main>
    </div>
  );
}
