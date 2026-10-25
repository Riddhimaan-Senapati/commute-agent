"""The cities the planner covers, and the subset with crash data.

The service area is the union of SERVICE_CITIES. Crash risk is only measured where
CRASH_CITIES have crash records; edges in the other cities use the road-class prior.
"""

SERVICE_CITIES = ("Boston", "Cambridge", "Somerville", "Brookline")
CRASH_CITIES = ("Boston", "Cambridge", "Somerville")
SERVICE_AREA_FILE = "service_area.geojson"


def outside_message(names: list[str]) -> str:
    """The error for trip points outside the service area; names are 'origin' or 'destination'."""
    return f"Outside the service area ({', '.join(SERVICE_CITIES)}): {' and '.join(names)}"
