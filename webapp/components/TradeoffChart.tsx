"use client";

import type { Filtered, Leg, Route } from "@/lib/api";
import { legColor, legTitle } from "@/lib/format";

type Props = {
  routes: Route[];
  filtered: Filtered[];
  selectedId: string | null;
  onSelect: (routeId: string) => void;
};

const WIDTH = 360;
const HEIGHT = 230;
const PAD = { left: 48, right: 12, top: 10, bottom: 40 };
const PLOT_W = WIDTH - PAD.left - PAD.right;
const PLOT_H = HEIGHT - PAD.top - PAD.bottom;
const FALLBACK_COLOR = "#5f6772";

/** Round ticks (1, 2 or 5 times a power of ten) from zero past max. A zero max gets 0 and 1, so the scale never divides by zero. */
function niceTicks(max: number): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = max / 4;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((f) => f * mag).find((s) => s >= raw) ?? 10 * mag;
  const count = Math.ceil(max / step - 1e-9);
  return Array.from({ length: count + 1 }, (_, i) => Number((i * step).toPrecision(12)));
}

/** The leg that takes the most time gives the route its colour and name. */
function mainLeg(route: Route): Leg | undefined {
  return route.legs.reduce<Leg | undefined>(
    (best, leg) => (!best || leg.duration_min > best.duration_min ? leg : best),
    undefined,
  );
}

export default function TradeoffChart({ routes, filtered, selectedId, onSelect }: Props) {
  if (routes.length + filtered.length === 0) return null;

  const all = [...routes, ...filtered.map((f) => f.route)];
  const xTicks = niceTicks(Math.max(0, ...all.map((r) => r.objectives.time_min)));
  const yTicks = niceTicks(Math.max(0, ...all.map((r) => r.objectives.co2_g)));
  const xMax = xTicks[xTicks.length - 1];
  const yMax = yTicks[yTicks.length - 1];
  const x = (minutes: number) => PAD.left + (minutes / xMax) * PLOT_W;
  const y = (grams: number) => PAD.top + PLOT_H - (grams / yMax) * PLOT_H;
  const baseline = PAD.top + PLOT_H;

  return (
    <details className="tradeoff-details" open>
      <summary>Trade-off chart</summary>
      <figure className="tradeoff" aria-label="Trade-off chart: time against CO2">
        <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} width="100%" role="group" aria-label="Time against CO2 for each route">
          {yTicks.map((v) => (
            <line key={`gy${v}`} x1={PAD.left} x2={PAD.left + PLOT_W} y1={y(v)} y2={y(v)} className="tradeoff-grid" />
          ))}
          <line x1={PAD.left} x2={PAD.left} y1={PAD.top} y2={baseline} className="tradeoff-axis" />
          <line x1={PAD.left} x2={PAD.left + PLOT_W} y1={baseline} y2={baseline} className="tradeoff-axis" />
          {yTicks.map((v) => (
            <text key={`ty${v}`} x={PAD.left - 6} y={y(v)} dy="0.32em" textAnchor="end" className="tradeoff-tick">
              {v}
            </text>
          ))}
          {xTicks.map((v) => (
            <text key={`tx${v}`} x={x(v)} y={baseline + 14} textAnchor="middle" className="tradeoff-tick">
              {v}
            </text>
          ))}
          <text x={PAD.left + PLOT_W / 2} y={HEIGHT - 4} textAnchor="middle" className="tradeoff-label">
            Time (min)
          </text>
          <text
            transform={`translate(12 ${PAD.top + PLOT_H / 2}) rotate(-90)`}
            textAnchor="middle"
            className="tradeoff-label"
          >
            CO2 (g)
          </text>

          {filtered.map((f) => {
            const o = f.route.objectives;
            return (
              <circle key={f.route.id} cx={x(o.time_min)} cy={y(o.co2_g)} r={3.5} className="tradeoff-filtered">
                <title>{`Not on the frontier: ${Math.round(o.time_min)} min, ${Math.round(o.co2_g)} g CO2`}</title>
              </circle>
            );
          })}

          {routes.map((route) => {
            const o = route.objectives;
            const leg = mainLeg(route);
            const label = `${leg ? legTitle(leg) : "Route"}: ${Math.round(o.time_min)} min, ${Math.round(o.co2_g)} g CO2`;
            const selected = route.id === selectedId;
            const cx = x(o.time_min);
            const cy = y(o.co2_g);
            return (
              <g
                key={route.id}
                role="button"
                tabIndex={0}
                aria-label={label}
                aria-current={selected ? "true" : undefined}
                className="tradeoff-dot"
                onClick={() => onSelect(route.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelect(route.id);
                  }
                }}
              >
                <title>{label}</title>
                {selected && <circle cx={cx} cy={cy} r={9} className="tradeoff-ring" />}
                <circle cx={cx} cy={cy} r={5.5} fill={leg ? legColor(leg) : FALLBACK_COLOR} />
              </g>
            );
          })}
        </svg>
        <figcaption>Each dot is a route. Lower and further left is better. Click a solid dot to select its route.</figcaption>
      </figure>
    </details>
  );
}
