from datetime import datetime
from typing import Literal

from pydantic import BaseModel

RouteKind = Literal["walk", "bike", "bluebikes", "drive", "transit"]
LegMode = Literal[
    "walk",
    "bike",
    "bluebikes",
    "drive",
    "subway",
    "light_rail",
    "bus",
    "commuter_rail",
    "ferry",
]
ACTIVE_LEG_MODES: frozenset[LegMode] = frozenset({"walk", "bike", "bluebikes"})
Objective = Literal["time_min", "co2_g", "crash_risk", "disruption_risk"]
OBJECTIVES: tuple[Objective, ...] = ("time_min", "co2_g", "crash_risk", "disruption_risk")
RiskBasis = Literal["bluebikes_proxy", "ped_counter", "road_class_prior", "outside_crash_coverage"]
UNKNOWN_EXPOSURE_BASES: frozenset[RiskBasis] = frozenset(
    {"road_class_prior", "outside_crash_coverage"}
)
Label = Literal["fastest", "lowest_co2", "lowest_crash_risk", "least_disrupted"]
RunMode = Literal["offline", "live"]
Weekday = Literal["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class Point(BaseModel):
    lat: float
    lon: float


class Objectives(BaseModel):
    time_min: float
    co2_g: float
    crash_risk: float
    disruption_risk: float


class TransitRef(BaseModel):
    route_id: str  # MBTA id with feed prefix removed, e.g. "Red", "Green-E", "SL1"
    route_name: str  # shortName or longName from OTP
    route_type: int  # GTFS route_type
    direction_id: int
    stop_ids: list[
        str
    ]  # board, intermediate, alight stops plus their parent stations, deduplicated


class Leg(BaseModel):
    mode: LegMode
    coords: list[tuple[float, float]] = []  # (lon, lat), WGS84; emptied in agent-facing results
    distance_m: float
    duration_min: float
    co2_g: float
    crash_risk: float = 0.0
    risk_basis_m: dict[RiskBasis, float] = {}
    transit: TransitRef | None = None
    alert_ids: list[str] = []
    disruption_risk: float = 0.0


class RouteOption(BaseModel):
    id: str  # f"{kind}-{n}", unique within one PlanResult
    kind: RouteKind
    summary: str  # "Red Line from Fields Corner", "Bike, safer variant 2"
    legs: list[Leg]
    objectives: Objectives
    sources: dict[Objective, str]
    labels: list[Label] = []
    alert_ids: list[str] = []
    unknown_exposure_share: float = 0.0


class AlertEntity(BaseModel):
    route: str | None = None
    stop: str | None = None
    route_type: int | None = None
    direction_id: int | None = None


class ActivePeriod(BaseModel):
    start: datetime
    end: datetime | None = None


class Alert(BaseModel):
    id: str
    header: str
    effect: str
    severity: int
    active_periods: list[ActivePeriod]
    entities: list[AlertEntity]


class AlertSnapshot(BaseModel):
    label: str
    recorded_at: datetime
    synthetic: bool
    alerts: list[Alert]


class AlertRef(BaseModel):
    id: str
    header: str
    effect: str
    score: float | None  # None when the effect is not in EFFECT_SCORES


class Tradeoff(BaseModel):
    route_id: str
    reference_id: str
    delta: Objectives  # route minus reference
    pct: dict[Objective, float | None]  # 100 * delta / reference, None when reference is 0
    alerts_avoided: list[AlertRef]  # affect the reference, not the route


class PreferenceWeights(BaseModel):
    weights: dict[Objective, float]
    n_choices: int
    insufficient_data: bool


class ChoiceEvent(BaseModel):
    query_id: str
    chosen_at: datetime
    chosen: Objectives
    rejected: list[Objectives]


class PlanResult(BaseModel):
    query_id: str
    origin: Point
    destination: Point
    depart_at: datetime
    run_mode: RunMode
    alert_source: str
    routes: list[RouteOption]  # Pareto set, ordered by preference
    tradeoffs_vs_fastest: list[Tradeoff]
    omitted: dict[RouteKind, str]  # kind -> reason
    candidate_count: int
    unscored_alerts: list[AlertRef]
    preference: PreferenceWeights
    notes: list[str]  # fixed caveats, e.g. "Drive time is free-flow and ignores traffic."


class AlertsView(BaseModel):
    source: str
    synthetic: bool
    alert_clock: datetime | None
    alerts: list[AlertRef]


class AlertSources(BaseModel):
    sources: list[str]
    active: str
    live_enabled: bool


class EmissionFactor(BaseModel):
    value: float
    unit: Literal["g_per_passenger_km", "g_per_passenger_mile"]
    source: str


class ShowcaseTrip(BaseModel):
    name: str
    origin_name: str
    destination_name: str
    origin: Point
    destination: Point
    weekday: Weekday
    depart_local: str  # "HH:MM", America/New_York
    description: str


class ShowcaseTrips(BaseModel):
    trips: list[ShowcaseTrip]


class Place(BaseModel):
    name: str
    point: Point
    source: Literal["mbta_station", "bluebikes_station", "showcase", "coordinates"]


class GeocodeResult(BaseModel):
    status: Literal["ok", "ambiguous", "not_found"]
    candidates: list[Place]


class ToolCall(BaseModel):
    name: str
    input: dict
    output: dict | str
    is_error: bool


class AgentReply(BaseModel):
    conversation_id: str
    text: str
    tool_calls: list[ToolCall]
    unsupported_numbers: list[str]  # still unsupported after one rewrite; redacted in text
    first_draft_unsupported: list[str] = []  # flagged in the first draft, before the rewrite
    first_draft_numbers: int = 0  # distinct numbers in the first draft
