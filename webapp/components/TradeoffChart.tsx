"use client";

import type { Plan } from "@/lib/api";
import { grams, minutes, routeColor } from "@/lib/format";

const W = 360;
const H = 220;
const PAD = { left: 52, right: 14, top: 14, bottom: 36 };

type Props = {
  plan: Plan;
  highlightedId: string | null;
  onHighlight: (id: string | null) => void;
};

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

/** Time versus CO₂ scatter. Each point shares its route's colour on the map and list. */
export default function TradeoffChart({ plan, highlightedId, onHighlight }: Props) {
  const maxT = niceMax(Math.max(...plan.routes.map((r) => r.objectives.time_min)));
  const maxC = niceMax(Math.max(...plan.routes.map((r) => r.objectives.co2_g)));
  const x = (t: number) => PAD.left + (t / maxT) * (W - PAD.left - PAD.right);
  const y = (c: number) => H - PAD.bottom - (c / maxC) * (H - PAD.top - PAD.bottom);
  const ticks = [0, 0.5, 1];

  return (
    <figure className="chart">
      <figcaption>Time vs CO₂ (lower-left is better)</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Scatter of each route's time and CO2">
        {ticks.map((f) => (
          <g key={f} className="axis">
            <line x1={PAD.left} x2={W - PAD.right} y1={y(maxC * f)} y2={y(maxC * f)} />
            <text x={PAD.left - 6} y={y(maxC * f) + 4} textAnchor="end">
              {Math.round(maxC * f)}
            </text>
            <text x={x(maxT * f)} y={H - PAD.bottom + 16} textAnchor="middle">
              {Math.round(maxT * f)}
            </text>
          </g>
        ))}
        <text className="axis-title" x={(W + PAD.left) / 2} y={H - 4} textAnchor="middle">
          minutes
        </text>
        <text className="axis-title" x={12} y={H / 2} textAnchor="middle" transform={`rotate(-90 12 ${H / 2})`}>
          g CO₂
        </text>
        {plan.routes.map((route, index) => {
          const active = route.id === highlightedId;
          return (
            <circle
              key={route.id}
              cx={x(route.objectives.time_min)}
              cy={y(route.objectives.co2_g)}
              r={active ? 9 : 6}
              fill={routeColor(index)}
              stroke="#fff"
              strokeWidth={2}
              opacity={highlightedId === null || active ? 1 : 0.3}
              onMouseEnter={() => onHighlight(route.id)}
              onMouseLeave={() => onHighlight(null)}
            >
              <title>
                {`${route.summary}: ${minutes(route.objectives.time_min)}, ${grams(route.objectives.co2_g)}`}
              </title>
            </circle>
          );
        })}
      </svg>
    </figure>
  );
}
