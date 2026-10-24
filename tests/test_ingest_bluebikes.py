import pandas as pd

from commute_agent.ingest.bluebikes import aggregate_trips, station_medians


def _trip_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "start_station_id": ["A", "A", "A", "B", ""],
            "end_station_id": ["B", "B", "", "A", "A"],
            "start_station_name": ["Alpha", "Alpha", "Alpha", "Beta", "Ghost"],
            "start_lat": [42.30, 42.32, 42.31, 42.40, 42.50],
            "start_lng": [-71.10, -71.12, -71.11, -71.20, -71.30],
            "end_lat": [42.40, 42.40, 42.40, 42.30, 42.30],
            "end_lng": [-71.20, -71.20, -71.20, -71.10, -71.10],
        }
    )


def test_aggregate_drops_blank_end_and_counts_pairs() -> None:
    trips = aggregate_trips(_trip_df())
    # A->B appears twice (third A row has blank end, dropped); B->A once; blank start dropped
    ab = trips[(trips["start_station_id"] == "A") & (trips["end_station_id"] == "B")]
    ba = trips[(trips["start_station_id"] == "B") & (trips["end_station_id"] == "A")]
    assert int(ab["trips"].iloc[0]) == 2
    assert int(ba["trips"].iloc[0]) == 1
    # no blank-start or blank-end rows survive
    assert (trips["start_station_id"] != "").all()
    assert (trips["end_station_id"] != "").all()


def test_station_medians() -> None:
    stations = station_medians(_trip_df())
    a = stations[stations["station_id"] == "A"].iloc[0]
    assert a["name"] == "Alpha"
    # median of 42.30, 42.32, 42.31 = 42.31
    assert abs(a["lat"] - 42.31) < 1e-9
    # blank start station id is excluded
    assert "" not in set(stations["station_id"])
