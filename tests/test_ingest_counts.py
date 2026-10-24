from pathlib import Path

import pandas as pd
import pytest

from commute_agent.ingest.counts import _cambridge, _somerville, counts
from commute_agent.ingest.meta import SchemaMismatch


def test_somerville_am_pm_average() -> None:
    # AM 120 + PM 80 over one location/year/mode -> (120+80)/2 = 100
    df = pd.DataFrame(
        {
            "Year": [2019, 2019],
            "Location Name": ["Beacon St", "Beacon St"],
            "Latitude": [42.38, 42.38],
            "Longitude": [-71.10, -71.10],
            "AM/PM": ["AM", "PM"],
            "Mode": ["Bike", "Bike"],
            "Count": [120, 80],
        }
    )
    out = _somerville(df)
    assert len(out) == 1
    assert out.iloc[0]["peak_hour_volume"] == pytest.approx(100.0)
    assert out.iloc[0]["mode"] == "bike"
    assert out.iloc[0]["source"] == "somerville"


def test_somerville_drops_pre_2018() -> None:
    df = pd.DataFrame(
        {
            "Year": [2017],
            "Location Name": ["Old St"],
            "Latitude": [42.38],
            "Longitude": [-71.10],
            "AM/PM": ["AM"],
            "Mode": ["Bike"],
            "Count": [100],
        }
    )
    assert len(_somerville(df)) == 0


def test_cambridge_peak_hour_from_bins() -> None:
    # two 15-min bins (0.5 h), counts 50+50 -> 100/0.5 = 200
    df = pd.DataFrame(
        {
            "Count Location": ["Harvard Sq", "Harvard Sq"],
            "Year": [2019, 2019],
            "Date": ["2019-07-01", "2019-07-01"],
            "Time": ["07:00", "07:15"],
            "Count": [50, 50],
            "Latitude": [42.37, 42.37],
            "Longitude": [-71.11, -71.11],
        }
    )
    out = _cambridge(df)
    assert len(out) == 1
    assert out.iloc[0]["peak_hour_volume"] == pytest.approx(200.0)
    assert out.iloc[0]["mode"] == "bike"


def test_missing_numina_zones_file_exits_with_path_and_columns(tmp_path: Path) -> None:
    with pytest.raises(SchemaMismatch) as exc:
        counts(raw_dir=tmp_path, out_dir=tmp_path, zones_path=tmp_path / "numina_zones.csv")
    msg = str(exc.value)
    assert "numina_zones.csv" in msg
    assert "zone_id" in msg and "lat" in msg and "lon" in msg
