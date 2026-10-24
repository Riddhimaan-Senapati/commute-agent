import type { Leg, Plan, Route } from "./api";

export type Objective = "all" | Route["labels"][number];

export const OBJECTIVES: { id: Objective; label: string; hint: string }[] = [
  { id: "fastest", label: "Fastest", hint: "Least travel time" },
  { id: "lowest_co2", label: "Lowest CO₂", hint: "Least estimated CO₂" },
  { id: "lowest_crash_risk", label: "Safest", hint: "Lowest crash risk among walk and bike routes" },
  { id: "least_disrupted", label: "Least disrupted", hint: "Least affected by MBTA alerts" },
  { id: "all", label: "All trade-offs", hint: "Every route no other route beats on all four" },
];

export const LABEL_TEXT: Record<Route["labels"][number], string> = {
  fastest: "Fastest",
  lowest_co2: "Lowest CO₂",
  lowest_crash_risk: "Safest",
  least_disrupted: "Least disrupted",
};

/** Routes to show for an objective: the one route holding that label, or all by time. */
export function visibleRoutes(plan: Plan, objective: Objective): Route[] {
  if (objective === "all") {
    return [...plan.routes].sort((a, b) => a.objectives.time_min - b.objectives.time_min);
  }
  return plan.routes.filter((r) => r.labels.includes(objective));
}

// Official MBTA line colours; other modes use neutral map colours.
const LINE_COLOR: [RegExp, string][] = [
  [/^(Red|Mattapan)$/, "#DA291C"],
  [/^Orange$/, "#ED8B00"],
  [/^Blue$/, "#003DA5"],
  [/^Green/, "#00843D"],
  [/^SL/, "#7C878E"],
  [/^CR-/, "#80276C"],
  [/^Boat-/, "#008EAA"],
];

export function legColor(leg: Leg): string {
  if (leg.transit) {
    const hit = LINE_COLOR.find(([pattern]) => pattern.test(leg.transit?.route_id ?? ""));
    if (hit) return hit[1];
    return leg.mode === "bus" ? "#E0A800" : "#4b5563";
  }
  switch (leg.mode) {
    case "walk":
      return "#5f6b7a";
    case "bike":
    case "bluebikes":
      return "#1e8e3e";
    default:
      return "#1a73e8";
  }
}

export function legDash(leg: Leg): string | undefined {
  return leg.mode === "walk" ? "2 8" : undefined;
}

const MODE_TEXT: Record<Leg["mode"], string> = {
  walk: "Walk",
  bike: "Bike",
  bluebikes: "Bluebikes",
  drive: "Drive",
  subway: "Subway",
  light_rail: "Green Line",
  bus: "Bus",
  commuter_rail: "Commuter Rail",
  ferry: "Ferry",
};

export function legTitle(leg: Leg): string {
  if (!leg.transit) return MODE_TEXT[leg.mode];
  return leg.mode === "bus" ? `Bus ${leg.transit.route_name}` : leg.transit.route_name;
}

export function km(metres: number): string {
  return metres < 1000 ? `${Math.round(metres)} m` : `${(metres / 1000).toFixed(1)} km`;
}

export function minutes(value: number): string {
  if (value < 60) return `${Math.round(value)} min`;
  return `${Math.floor(value / 60)} h ${Math.round(value % 60)} min`;
}

export function grams(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} kg CO₂` : `${Math.round(value)} g CO₂`;
}

export function signed(value: number, digits = 0): string {
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

/** Route summaries present after a change but not before. Ids are reassigned per plan. */
export function enteredRoutes(before: Plan, after: Plan): string[] {
  const was = new Set(before.routes.map((r) => r.summary));
  return after.routes.map((r) => r.summary).filter((s) => !was.has(s));
}
