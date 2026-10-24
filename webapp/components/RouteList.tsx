"use client";

import type { Plan, Route, Tradeoff } from "@/lib/api";
import { LABEL_TEXT, grams, minutes, routeColor, signed } from "@/lib/format";

type Props = {
  plan: Plan;
  alertHeaders: Map<string, string>;
  highlightedId: string | null;
  chosenId: string | null;
  onHighlight: (id: string | null) => void;
  onChoose: (id: string) => void;
};

function tradeoffText(t: Tradeoff | undefined, route: Route): string | null {
  if (!t || t.route_id === t.reference_id || route.labels.includes("fastest")) return null;
  const parts = [`${signed(t.delta.time_min)} min`];
  const co2 = t.pct.co2_g;
  if (co2 !== null && co2 !== undefined) parts.push(`${signed(co2, 0)}% CO₂`);
  return `${parts.join(", ")} vs fastest`;
}

function shareText(share: number): string {
  return share < 0.01 ? "<1%" : `${Math.round(share * 100)}%`;
}

export default function RouteList({
  plan,
  alertHeaders,
  highlightedId,
  chosenId,
  onHighlight,
  onChoose,
}: Props) {
  const trades = new Map(plan.tradeoffs_vs_fastest.map((t) => [t.route_id, t]));
  return (
    <ol className="routes" aria-label="Pareto routes">
      {plan.routes.map((route, index) => {
        const o = route.objectives;
        const trade = tradeoffText(trades.get(route.id), route);
        const active = route.id === highlightedId;
        return (
          <li
            key={route.id}
            className={`route${active ? " active" : ""}${o.disruption_risk > 0 ? " disrupted" : ""}`}
            style={{ borderLeftColor: routeColor(index) }}
            onMouseEnter={() => onHighlight(route.id)}
            onMouseLeave={() => onHighlight(null)}
          >
            <button
              type="button"
              className="route-head"
              onFocus={() => onHighlight(route.id)}
              onBlur={() => onHighlight(null)}
              onClick={() => onHighlight(route.id)}
              aria-label={`Route: ${route.summary}`}
            >
              <span className="swatch" style={{ background: routeColor(index) }} aria-hidden />
              <span className="route-title">{route.summary}</span>
              <span className="route-time">{minutes(o.time_min)}</span>
            </button>
            {route.labels.length > 0 && (
              <ul className="pills" aria-label="Labels">
                {route.labels.map((l) => (
                  <li key={l} className="pill">
                    {LABEL_TEXT[l]}
                  </li>
                ))}
              </ul>
            )}
            <dl className="objectives">
              <div>
                <dt>CO₂</dt>
                <dd>{grams(o.co2_g)}</dd>
              </div>
              <div>
                <dt>Crash risk</dt>
                <dd>{o.crash_risk.toFixed(1)} index</dd>
              </div>
              <div>
                <dt>Disruption</dt>
                <dd>{o.disruption_risk.toFixed(2)} (0 to 1)</dd>
              </div>
            </dl>
            {trade && <p className="trade">{trade}</p>}
            {route.unknown_exposure_share > 0 && (
              <p className="badge">
                Exposure partly unknown ({shareText(route.unknown_exposure_share)} road-type estimate)
              </p>
            )}
            {route.alert_ids.map((id) => (
              <p key={id} className="alert-line" role="note">
                ⚠ {alertHeaders.get(id) ?? id}
              </p>
            ))}
            <details className="sources">
              <summary>Sources</summary>
              <ul>
                {Object.entries(route.sources).map(([k, v]) => (
                  <li key={k}>
                    <strong>{k}</strong>: {v}
                  </li>
                ))}
              </ul>
            </details>
            <button
              type="button"
              className="choose"
              onClick={() => onChoose(route.id)}
              aria-pressed={chosenId === route.id}
              aria-label={`Choose route ${route.summary}`}
            >
              {chosenId === route.id ? "Chosen ✓" : "Choose this route"}
            </button>
          </li>
        );
      })}
    </ol>
  );
}
