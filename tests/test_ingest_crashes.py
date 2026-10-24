import pandas as pd
import pytest

from commute_agent.ingest.crashes import _boston_rows, _cambridge_rows
from commute_agent.ingest.meta import SchemaMismatch, require_columns


def test_cambridge_location_parses_lat_lon() -> None:
    df = pd.DataFrame(
        {
            "Date Time": ["01/02/2020 08:15:00 AM"],
            "May involve cyclist": ["1"],
            "May Involve Pedestrian": [""],
            "Location": ["8 SCOTT ST\nCambridge, MA\n(42.37886, -71.11025)"],
        }
    )
    rows, _ = _cambridge_rows(df)
    assert len(rows) == 1
    assert rows.iloc[0]["lat"] == pytest.approx(42.37886)
    assert rows.iloc[0]["lon"] == pytest.approx(-71.11025)
    assert rows.iloc[0]["mode"] == "bike"


def test_cambridge_both_flags_yields_two_rows() -> None:
    df = pd.DataFrame(
        {
            "Date Time": ["03/04/2021 05:30:00 PM"],
            "May involve cyclist": ["1"],
            "May Involve Pedestrian": ["1"],
            "Location": ["1 MAIN ST\nCambridge, MA\n(42.3700, -71.1000)"],
        }
    )
    rows, _ = _cambridge_rows(df)
    assert sorted(rows["mode"]) == ["bike", "ped"]


def test_cambridge_drops_rows_without_coordinates() -> None:
    df = pd.DataFrame(
        {
            "Date Time": ["03/04/2021 05:30:00 PM", "03/04/2021 05:30:00 PM"],
            "May involve cyclist": ["1", "1"],
            "May Involve Pedestrian": ["", ""],
            "Location": ["", "5 ELM ST\nCambridge, MA"],
        }
    )
    rows, counts = _cambridge_rows(df)
    assert len(rows) == 0
    assert counts["cambridge_empty_location"] == 1
    assert counts["cambridge_address_only"] == 1


def test_cambridge_flags_read_by_pandas_as_floats_are_kept(tmp_path) -> None:
    # The real CSV holds "1" or blank, so read_csv yields 1.0 and NaN, not the string "1".
    (tmp_path / "c.csv").write_text(
        "Date Time,May involve cyclist,May Involve Pedestrian,Location\n"
        '01/02/2020 08:15:00 AM,1,,"8 SCOTT ST\nCambridge, MA\n(42.37886, -71.11025)"\n'
        '01/02/2020 09:15:00 AM,,1,"6 CHANNING PL\nCambridge, MA\n(42.37785, -71.13623)"\n'
        '01/02/2020 10:15:00 AM,,,"1 MAIN ST\nCambridge, MA\n(42.3700, -71.1000)"\n'
    )
    df = pd.read_csv(tmp_path / "c.csv")
    assert df["May involve cyclist"].dtype == float
    rows, _ = _cambridge_rows(df)
    assert list(rows["mode"]) == ["bike", "ped"]
    assert list(rows["lat"]) == pytest.approx([42.37886, 42.37785])


def test_vision_zero_mv_row_yields_none() -> None:
    df = pd.DataFrame(
        {
            "dispatch_ts": ["2015-12-23 02:25:08+00", "2016-01-01 10:00:00+00"],
            "mode_type": ["mv", "bike"],
            "lat": [42.35, 42.36],
            "long": [-71.06, -71.07],
        }
    )
    rows = _boston_rows(df)
    assert list(rows["mode"]) == ["bike"]
    assert rows.iloc[0]["source_dataset"] == "boston_vision_zero"


def test_require_columns_names_missing() -> None:
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(SchemaMismatch) as exc:
        require_columns(df, "crashes/boston_vision_zero", {"a", "lat", "long"})
    assert "lat" in str(exc.value) and "long" in str(exc.value)
