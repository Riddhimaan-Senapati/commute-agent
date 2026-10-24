"use client";

import type { Route, Tradeoff } from "@/lib/api";
import { LABEL_TEXT, grams, km, legColor, legTitle, minutes, signed } from "@/lib/format";

type Props = {
  route: Route;
  tradeoff: Tradeoff | undefined;
  selected: boolean;
  chosen: boolean;
  alertHeaders: Map<string, string>;
  onSelect: () => void;
  onChoose: () => void;
};

export function RouteSegments({ route }: { route: Route }) {
  return (
    <span className="card-summary">
      {route.legs
        .filter((leg) => leg.transit || route.kind !== "transit")
        .slice(0, 4)
        .map((leg, i) => (
          <span key={i} className="seg" style={{ background: legColor(leg) }}>
            {legTitle(leg)}
          </span>
        ))}
    </span>
  );
}

function versusFastest(route: Route, t: Tradeoff | undefined): string | null {
  if (!t || route.labels.includes("fastest")) return null;
  const co2 = t.pct.co2_g;
  const parts = [`${signed(t.delta.time_min)} min`];
  if (co2 !== null && co2 !== undefined) parts.push(`${signed(co2)}% CO₂`);
  return `${parts.join(" · ")} vs fastest`;
}

export default function RouteCard({ route, tradeoff, selected, chosen, alertHeaders, onSelect, onChoose }: Props) {
  const o = route.objectives;
  const compare = versusFastest(route, tradeoff);
  const active = route.legs.some((leg) => leg.mode === "walk" || leg.mode === "bike" || leg.mode === "bluebikes");
  return (
    <li className={`card${selected ? " selected" : ""}${o.disruption_risk > 0 ? " disrupted" : ""}`}>
      <button type="button" className="card-head" onClick={onSelect} aria-expanded={selected} aria-label={`Route: ${route.summary}`}>
        <span className="card-time">{minutes(o.time_min)}</span>
        <RouteSegments route={route} />
        <span className="card-co2">{grams(o.co2_g)}</span>
      </button>
      {route.labels.length > 0 && (
        <p className="chips">
          {route.labels.map((l) => (
            <span key={l} className="chip">
              {LABEL_TEXT[l]}
            </span>
          ))}
        </p>
      )}
      {compare && <p className="compare">{compare}</p>}
      {route.alert_ids.map((id) => (
        <p key={id} className="alert" role="note">
          ⚠ {alertHeaders.get(id) ?? id}
        </p>
      ))}
      {selected && (
        <div className="details">
          <ol className="steps" aria-label="Directions">
            {route.legs.map((leg, i) => (
              <li key={i}>
                <span className="step-line" style={{ background: legColor(leg) }} aria-hidden />
                <span className="step-text">
                  <strong>{legTitle(leg)}</strong>
                  <span>
                    {km(leg.distance_m)} · {minutes(leg.duration_min)}
                  </span>
                </span>
              </li>
            ))}
          </ol>
          <dl className="metrics">
            <div>
              <dt>Crash risk</dt>
              <dd>{active ? `${o.crash_risk.toFixed(1)} index` : "not modeled"}</dd>
            </div>
            <div>
              <dt>Disruption</dt>
              <dd>{o.disruption_risk.toFixed(2)} of 1</dd>
            </div>
          </dl>
          {route.unknown_exposure_share > 0 && (
            <p className="note">
              {route.unknown_exposure_share < 0.01 ? "<1%" : `${Math.round(route.unknown_exposure_share * 100)}%`} of
              this route&apos;s crash risk uses a road-type estimate (no count data on those streets).
            </p>
          )}
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
          <button type="button" className="choose" onClick={onChoose} aria-pressed={chosen}>
            {chosen ? "Chosen ✓" : "I'll take this route"}
          </button>
        </div>
      )}
    </li>
  );
}
