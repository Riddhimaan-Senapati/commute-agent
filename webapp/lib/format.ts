import type { LegModeName, Plan, Route } from "./api";

// Okabe-Ito palette, as in the design's WS9 rules: colour by position in plan.routes.
const COLORS = ["#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7", "#56B4E9", "#000000", "#F0E442"];

export function routeColor(index: number): string {
  return COLORS[index % COLORS.length];
}

type LineStyle = { weight: number; dashArray?: string };

export const LEG_STYLE: Record<LegModeName, LineStyle> = {
  walk: { weight: 4, dashArray: "4 6" },
  bike: { weight: 4 },
  bluebikes: { weight: 4 },
  drive: { weight: 5, dashArray: "1 8" },
  subway: { weight: 7 },
  light_rail: { weight: 7 },
  bus: { weight: 6 },
  commuter_rail: { weight: 7 },
  ferry: { weight: 7 },
};

export const LABEL_TEXT: Record<Route["labels"][number], string> = {
  fastest: "Fastest",
  lowest_co2: "Lowest CO₂",
  lowest_crash_risk: "Lowest crash risk",
  least_disrupted: "Least disrupted",
};

export function minutes(value: number): string {
  return `${value.toFixed(1)} min`;
}

export function grams(value: number): string {
  return `${Math.round(value)} g CO₂`;
}

export function signed(value: number, digits = 1): string {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export function departureText(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    timeZone: "America/New_York",
    weekday: "long",
    hour: "numeric",
    minute: "2-digit",
  });
}

export type ParetoChange = { entered: string[]; left: string[] };

// Route ids are reassigned per plan, so routes are compared by their summary.
export function paretoChange(before: Plan, after: Plan): ParetoChange {
  const was = new Set(before.routes.map((r) => r.summary));
  const now = new Set(after.routes.map((r) => r.summary));
  return {
    entered: [...now].filter((s) => !was.has(s)),
    left: [...was].filter((s) => !now.has(s)),
  };
}
