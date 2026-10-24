"use client";

import type { Filtered } from "@/lib/api";
import { RouteSegments } from "@/components/RouteCard";
import { grams, minutes } from "@/lib/format";

// Thresholds match the planner's absolute tolerances, so every dominated route has a reason.
function why(f: Filtered): string {
  if (f.reason === "near_duplicate") return `Almost the same as ${f.by_summary} on all four`;
  const a = f.route.objectives;
  const b = f.by_objectives;
  const better: string[] = [];
  if (a.time_min - b.time_min > 0.5) better.push(`${minutes(a.time_min - b.time_min)} faster`);
  if (a.co2_g - b.co2_g > 1) better.push(`${grams(a.co2_g - b.co2_g)} less`);
  if (a.crash_risk - b.crash_risk > 0.01) better.push("lower crash risk");
  if (a.disruption_risk - b.disruption_risk > 0.01) better.push("less disrupted");
  const rest = better.length < 4 ? ", and no worse on the rest" : "";
  return `${f.by_summary}: ${better.join(", ")}${rest}`;
}

export default function FilteredRoutes({ filtered }: { filtered: Filtered[] }) {
  if (filtered.length === 0) return null;
  return (
    <section className="filtered">
      <h2 className="list-head">Not on the frontier: {filtered.length}</h2>
      <p className="meta">Another route beats each of these, so the planner drops it.</p>
      <ol className="cards" aria-label="Not on the frontier">
        {filtered.map((f) => (
          <li key={f.route.id} className="card dominated">
            <div className="card-head">
              <span className="card-time">{minutes(f.route.objectives.time_min)}</span>
              <RouteSegments route={f.route} />
              <span className="card-co2">{grams(f.route.objectives.co2_g)}</span>
            </div>
            <p className="compare">Beaten by {why(f)}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}
