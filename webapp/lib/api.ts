// API boundary: every response from the FastAPI server is parsed here with zod.
// The schemas mirror commute_agent/models.py; fields the page does not use are omitted.
import { z } from "zod";

const Objective = z.enum(["time_min", "co2_g", "crash_risk", "disruption_risk"]);
const LegMode = z.enum([
  "walk",
  "bike",
  "bluebikes",
  "drive",
  "subway",
  "light_rail",
  "bus",
  "commuter_rail",
  "ferry",
]);

const PointSchema = z.object({ lat: z.number(), lon: z.number() });

const ObjectivesSchema = z.object({
  time_min: z.number(),
  co2_g: z.number(),
  crash_risk: z.number(),
  disruption_risk: z.number(),
});

const LegSchema = z.object({
  mode: LegMode,
  coords: z.array(z.tuple([z.number(), z.number()])),
  distance_m: z.number(),
  duration_min: z.number(),
  transit: z.object({ route_id: z.string(), route_name: z.string() }).nullable(),
});

const RouteSchema = z.object({
  id: z.string(),
  kind: z.enum(["walk", "bike", "bluebikes", "drive", "transit"]),
  summary: z.string(),
  legs: z.array(LegSchema),
  objectives: ObjectivesSchema,
  sources: z.record(Objective, z.string()),
  labels: z.array(z.enum(["fastest", "lowest_co2", "lowest_crash_risk", "least_disrupted"])),
  alert_ids: z.array(z.string()),
  unknown_exposure_share: z.number(),
});

const TradeoffSchema = z.object({
  route_id: z.string(),
  reference_id: z.string(),
  delta: ObjectivesSchema,
  pct: z.record(Objective, z.number().nullable()),
});

const FilteredSchema = z.object({
  route: RouteSchema,
  reason: z.enum(["dominated", "near_duplicate"]),
  by_route_id: z.string(),
  by_summary: z.string(),
  by_objectives: ObjectivesSchema,
});

export const PlanSchema = z.object({
  query_id: z.string(),
  origin: PointSchema,
  destination: PointSchema,
  depart_at: z.string(),
  run_mode: z.enum(["offline", "live"]),
  alert_source: z.string(),
  routes: z.array(RouteSchema),
  tradeoffs_vs_fastest: z.array(TradeoffSchema),
  omitted: z.record(z.string(), z.string()),
  candidate_count: z.number(),
  notes: z.array(z.string()),
  filtered: z.array(FilteredSchema).default([]),
});

export const StatusSchema = z.object({
  run_mode: z.enum(["offline", "live"]),
  agent_model: z.string(),
  agent_available: z.boolean(),
  missing_model_keys: z.array(z.string()),
  notice: z.string(),
});

const TripSchema = z.object({
  name: z.string(),
  origin_name: z.string(),
  destination_name: z.string(),
  origin: PointSchema,
  destination: PointSchema,
  description: z.string(),
});
export const TripsSchema = z.object({ trips: z.array(TripSchema) });

export const AlertSourcesSchema = z.object({
  sources: z.array(z.string()),
  active: z.string(),
  live_enabled: z.boolean(),
});

export const AlertsViewSchema = z.object({
  source: z.string(),
  synthetic: z.boolean(),
  alerts: z.array(z.object({ id: z.string(), header: z.string(), effect: z.string() })),
});

export const PreferenceSchema = z.object({
  weights: z.record(Objective, z.number()),
  n_choices: z.number(),
  insufficient_data: z.boolean(),
});

export const ReplySchema = z.object({
  conversation_id: z.string(),
  text: z.string(),
  tool_calls: z.array(z.object({ name: z.string(), is_error: z.boolean() })),
  unsupported_numbers: z.array(z.string()),
  first_draft_unsupported: z.array(z.string()),
});

const PlaceSchema = z.object({ name: z.string(), point: PointSchema, source: z.string() });
export const GeocodeSchema = z.object({
  status: z.enum(["ok", "ambiguous", "not_found"]),
  candidates: z.array(PlaceSchema),
});

export type Plan = z.infer<typeof PlanSchema>;
export type Place = z.infer<typeof PlaceSchema>;
export type Point = z.infer<typeof PointSchema>;
export type Route = z.infer<typeof RouteSchema>;
export type Filtered = z.infer<typeof FilteredSchema>;
export type Leg = z.infer<typeof LegSchema>;
export type LegModeName = z.infer<typeof LegMode>;
export type Tradeoff = z.infer<typeof TradeoffSchema>;
export type Status = z.infer<typeof StatusSchema>;
export type Trip = z.infer<typeof TripSchema>;
export type AlertSources = z.infer<typeof AlertSourcesSchema>;
export type AlertsView = z.infer<typeof AlertsViewSchema>;
export type Preference = z.infer<typeof PreferenceSchema>;
export type Reply = z.infer<typeof ReplySchema>;

const ErrorSchema = z.object({ detail: z.string() });

export class ApiError extends Error {}

// The server keeps one alert source per session id, so each tab can simulate a
// disruption without changing what other visitors see.
const SESSION_KEY = "commute-session-id";
let sessionId: string | null = null;

function getSessionId(): string {
  if (sessionId) return sessionId;
  try {
    sessionId = sessionStorage.getItem(SESSION_KEY);
  } catch {
    // Storage blocked: keep the id in memory for this page load.
  }
  if (!sessionId) {
    sessionId = crypto.randomUUID();
    try {
      sessionStorage.setItem(SESSION_KEY, sessionId);
    } catch {
      // Same as above.
    }
  }
  return sessionId;
}

export async function api<T>(
  path: string,
  schema: z.ZodType<T>,
  init?: { method: "POST" | "DELETE"; body?: unknown },
): Promise<T> {
  const headers: Record<string, string> = { "x-session-id": getSessionId() };
  if (init?.body !== undefined) headers["content-type"] = "application/json";
  const response = await fetch(path, {
    method: init?.method ?? "GET",
    headers,
    body: init?.body === undefined ? undefined : JSON.stringify(init.body),
  });
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const parsed = ErrorSchema.safeParse(payload);
    throw new ApiError(parsed.success ? parsed.data.detail : `HTTP ${response.status}`);
  }
  return schema.parse(payload);
}
