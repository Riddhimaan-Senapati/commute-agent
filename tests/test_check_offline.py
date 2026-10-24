"""Unit tests for commute_agent.check_offline and showcase-point containment."""

from pathlib import Path

import pytest

from commute_agent import check_offline
from commute_agent.check_offline import (
    BOUNDARY_FILES,
    GRAPHML_FILES,
    PROCESSED_FILES,
    VENDOR_WEB_FILES,
    required_offline_files,
)
from commute_agent.models import Point, ShowcaseTrip
from commute_agent.paths import PROCESSED
from commute_agent.transit.otp import otp_cache_key


def _trip(name: str, lat: float, lon: float) -> ShowcaseTrip:
    return ShowcaseTrip(
        name=name,
        origin_name="O",
        destination_name="D",
        origin=Point(lat=lat, lon=lon),
        destination=Point(lat=lat + 0.01, lon=lon + 0.01),
        weekday="tuesday",
        depart_local="08:15",
        description="test",
    )


def _populate(
    *,
    emission_factors: Path,
    processed: Path,
    alert_snapshots: Path,
    web: Path,
    otp_cache: Path,
    trips: list[ShowcaseTrip],
) -> None:
    """Create every file required_offline_files expects, so the check passes."""
    emission_factors.parent.mkdir(parents=True, exist_ok=True)
    emission_factors.write_text("walk: {}")

    processed.mkdir(parents=True, exist_ok=True)
    for name in (*GRAPHML_FILES, *BOUNDARY_FILES, *PROCESSED_FILES):
        (processed / name).write_text("x")

    alert_snapshots.mkdir(parents=True, exist_ok=True)
    (alert_snapshots / "synthetic.json").write_text("{}")

    for rel in VENDOR_WEB_FILES:
        target = web / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x")

    otp_cache.mkdir(parents=True, exist_ok=True)
    for trip in trips:
        key = otp_cache_key(trip.origin, trip.destination, trip.weekday, trip.depart_local)
        (otp_cache / f"{key}.json").write_text("{}")


def _dirs(tmp_path: Path) -> dict[str, Path]:
    return {
        "emission_factors": tmp_path / "data" / "emission_factors.yaml",
        "processed": tmp_path / "data" / "processed",
        "alert_snapshots": tmp_path / "data" / "alert_snapshots",
        "web": tmp_path / "web",
        "otp_cache": tmp_path / "data" / "cache" / "otp",
    }


def test_required_offline_files_lists_expected(tmp_path: Path) -> None:
    dirs = _dirs(tmp_path)
    trips = [_trip("a", 42.35, -71.06), _trip("b", 42.36, -71.07)]

    paths = required_offline_files(trips, **dirs)

    names = {p.name for p in paths}
    assert "walk.graphml" in names
    assert "boston_boundary.geojson" in names
    assert "bluebikes_stations.parquet" in names
    assert "basemap.geojson" in names
    assert "risk_meta.json" in names
    assert "chart.umd.min.js" in names
    # One OTP cache file per trip.
    cache_files = [p for p in paths if p.parent == dirs["otp_cache"]]
    assert len(cache_files) == len(trips)


def test_main_reports_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dirs = _dirs(tmp_path)
    showcase = tmp_path / "showcase_trips.yaml"
    showcase.write_text(
        "- name: a\n"
        "  origin_name: O\n"
        "  destination_name: D\n"
        "  origin: {lat: 42.35, lon: -71.06}\n"
        "  destination: {lat: 42.36, lon: -71.07}\n"
        "  weekday: tuesday\n"
        '  depart_local: "08:15"\n'
        "  description: test\n"
    )

    code = check_offline.main(showcase_trips=showcase, **dirs)

    assert code == 1
    out = capsys.readouterr().out
    assert "walk.graphml" in out


def test_main_offline_ready(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    dirs = _dirs(tmp_path)
    showcase = tmp_path / "showcase_trips.yaml"
    showcase.write_text(
        "- name: a\n"
        "  origin_name: O\n"
        "  destination_name: D\n"
        "  origin: {lat: 42.35, lon: -71.06}\n"
        "  destination: {lat: 42.36, lon: -71.07}\n"
        "  weekday: tuesday\n"
        '  depart_local: "08:15"\n'
        "  description: test\n"
    )
    trips = check_offline.load_showcase_trips(showcase)
    _populate(trips=trips, **dirs)

    code = check_offline.main(showcase_trips=showcase, **dirs)

    assert code == 0
    assert capsys.readouterr().out.strip() == "offline ready"


def test_showcase_points_inside_boston_boundary() -> None:
    boundary = PROCESSED / "boston_boundary.geojson"
    if not boundary.exists():
        pytest.skip("boston_boundary.geojson not built yet (fixtures only)")

    import geopandas as gpd
    from shapely.geometry import Point as ShapelyPoint

    from commute_agent.paths import SHOWCASE_TRIPS

    trips = check_offline.load_showcase_trips(SHOWCASE_TRIPS)
    gdf = gpd.read_file(boundary)
    land = gdf.union_all() if hasattr(gdf, "union_all") else gdf.unary_union

    for trip in trips:
        for name, point in (("origin", trip.origin), ("destination", trip.destination)):
            assert land.contains(ShapelyPoint(point.lon, point.lat)), (
                f"{trip.name} {name} is outside boston_boundary.geojson"
            )
