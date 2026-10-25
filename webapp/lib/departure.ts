// Departure times for /api/plan. The page sends an explicit America/New_York offset, so the
// server gets the same instant whatever zone the browser is in.
export type DepartureMode = "default" | "now" | "at";

const BOSTON = "America/New_York";

type Wall = { year: number; month: number; day: number; hour: number; minute: number };

function bostonWall(instant: number): Wall {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: BOSTON,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    hourCycle: "h23",
  }).formatToParts(new Date(instant));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute") };
}

// Boston's UTC offset in minutes at an instant (negative for EDT and EST).
function bostonOffsetMinutes(instant: number): number {
  const wall = bostonWall(instant);
  const wallAsUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute);
  return Math.round((wallAsUtc - Math.floor(instant / 60000) * 60000) / 60000);
}

const pad = (n: number) => String(n).padStart(2, "0");

/** A Boston wall-clock time ("YYYY-MM-DDTHH:mm") as ISO 8601 with its America/New_York offset. */
export function bostonIso(wall: string): string {
  const clock = wall.slice(0, 16);
  const asUtc = Date.parse(`${clock}:00Z`);
  const first = bostonOffsetMinutes(asUtc);
  const offset = bostonOffsetMinutes(asUtc - first * 60000);
  const sign = offset < 0 ? "-" : "+";
  const abs = Math.abs(offset);
  return `${clock}:00${sign}${pad(Math.floor(abs / 60))}:${pad(abs % 60)}`;
}

/** The depart_at to send: null for no time chosen, the current time for "Leave now". */
export function departureParam(mode: DepartureMode, wall: string): string | null {
  if (mode === "default") return null;
  if (mode === "now") {
    const now = bostonWall(Date.now());
    const clock = `${now.year}-${pad(now.month)}-${pad(now.day)}T${pad(now.hour)}:${pad(now.minute)}`;
    return bostonIso(clock);
  }
  return wall ? bostonIso(wall) : null;
}
