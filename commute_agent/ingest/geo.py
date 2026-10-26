import os
from pathlib import Path

import geopandas as gpd
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from commute_agent.ingest.meta import (
    SchemaMismatch,
    atomic_write,
    manifest_entry,
    require_columns,
    write_meta,
)
from commute_agent.paths import PROCESSED, RAW
from commute_agent.service_area import CRASH_CITIES, SERVICE_AREA_FILE, SERVICE_CITIES

BOSTON_BOUNDARY = "geo/boston_boundary.geojson"
CAMBRIDGE_BOUNDARY = "geo/cambridge_boundary.geojson"
# MassGIS Census 2020 towns, one polygon per city or town. Somerville and Brookline come
# from here; the fetch step records the source and license in the manifest.
MUNICIPALITIES = "geo/massgis_towns.geojson"
MUNICIPALITY_NAME_FIELD = "TOWN20"

SERVICE_BUFFER_FILE = "service_area_buffered.geojson"
COVERAGE_FILE = "crash_coverage.geojson"

BUFFER_M = 2000
BASEMAP_CLASSES = {"primary", "secondary", "tertiary", "trunk"}
SIMPLIFY_M = 5.0
_METRIC_CRS = 26986


def _city_geometries(raw_dir: Path) -> dict[str, BaseGeometry]:
    """One WGS84 polygon per service city. Boston and Cambridge have their own files."""
    geoms: dict[str, BaseGeometry] = {
        "Boston": gpd.read_file(raw_dir / BOSTON_BOUNDARY).to_crs(4326).union_all(),
        "Cambridge": gpd.read_file(raw_dir / CAMBRIDGE_BOUNDARY).to_crs(4326).union_all(),
    }
    towns = gpd.read_file(raw_dir / MUNICIPALITIES).to_crs(4326)
    require_columns(towns, "geo/massgis_towns", {MUNICIPALITY_NAME_FIELD})
    names = towns[MUNICIPALITY_NAME_FIELD].astype(str).str.strip().str.upper()
    for city in SERVICE_CITIES:
        if city in geoms:
            continue
        selected = towns[names == city.upper()]
        if selected.empty:
            raise SchemaMismatch(f"geo: no {city} polygon in {MUNICIPALITY_NAME_FIELD} field")
        geoms[city] = selected.union_all()
    return geoms


def service_area(raw_dir: Path = RAW) -> BaseGeometry:
    """Union of every service city, WGS84."""
    geoms = _city_geometries(raw_dir)
    return unary_union([geoms[city] for city in SERVICE_CITIES])


def crash_coverage(raw_dir: Path = RAW) -> BaseGeometry:
    """Union of the cities that have crash records. Brookline is left out on purpose."""
    geoms = _city_geometries(raw_dir)
    return unary_union([geoms[city] for city in CRASH_CITIES])


def boundary(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    """Write the service-area union, its 2 km buffer, and the crash-coverage union."""
    geoms = _city_geometries(raw_dir)
    area = unary_union([geoms[city] for city in SERVICE_CITIES])
    coverage = unary_union([geoms[city] for city in CRASH_CITIES])
    buffered = gpd.GeoSeries([area], crs=4326).to_crs(_METRIC_CRS).buffer(BUFFER_M).to_crs(4326)

    area_path = out_dir / SERVICE_AREA_FILE
    atomic_write(
        area_path,
        lambda p: gpd.GeoDataFrame(geometry=[area], crs=4326).to_file(p, driver="GeoJSON"),
    )
    buffer_path = out_dir / SERVICE_BUFFER_FILE
    atomic_write(
        buffer_path,
        lambda p: gpd.GeoDataFrame(geometry=[buffered.union_all()], crs=4326).to_file(
            p, driver="GeoJSON"
        ),
    )
    coverage_path = out_dir / COVERAGE_FILE
    atomic_write(
        coverage_path,
        lambda p: gpd.GeoDataFrame(geometry=[coverage], crs=4326).to_file(p, driver="GeoJSON"),
    )

    sources = [
        manifest_entry(raw_dir, "geo", BOSTON_BOUNDARY),
        manifest_entry(raw_dir, "geo", CAMBRIDGE_BOUNDARY),
        manifest_entry(raw_dir, "geo", MUNICIPALITIES),
    ]
    cities = ", ".join(SERVICE_CITIES)
    write_meta(
        area_path,
        sources,
        row_count=1,
        notes=f"Service area: union of the municipal boundaries of {cities}.",
    )
    write_meta(
        buffer_path,
        sources,
        row_count=1,
        notes=f"Service area buffered by {BUFFER_M} m. Used for the Overpass graph polygon, "
        "Bluebikes station filter, and the OTP OSM extract.",
    )
    write_meta(
        coverage_path,
        sources,
        row_count=1,
        notes=(
            f"Crash coverage: {', '.join(CRASH_CITIES)}. Brookline is not covered: no Brookline "
            "crash source is built, so its edges use the road-class prior and are marked "
            "outside_crash_coverage."
        ),
    )
    return area_path


def graphs(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    """Download walk, bike, drive graphs for the buffered service area from Overpass.

    This is the one ingest step that downloads. The Overpass cache under
    data/raw/geo/overpass_cache makes a rerun work offline.
    """
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(raw_dir / "geo" / "overpass_cache")
    # COMMUTE_OVERPASS_URL points at a mirror when overpass-api.de is overloaded.
    ox.settings.overpass_url = os.environ.get("COMMUTE_OVERPASS_URL", ox.settings.overpass_url)

    polygon = gpd.read_file(out_dir / SERVICE_BUFFER_FILE).union_all()

    written: list[Path] = []
    for network_type in ("walk", "bike", "drive"):
        g = ox.graph_from_polygon(polygon, network_type=network_type)
        if network_type == "drive":
            g = ox.routing.add_edge_speeds(g)
            g = ox.routing.add_edge_travel_times(g)
        path = out_dir / f"{network_type}.graphml"
        atomic_write(path, lambda p, g=g: ox.save_graphml(g, p))
        written.append(path)

    return written[0]


def basemap(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    """Drive-graph edges of primary/secondary/tertiary/trunk class, simplified to 5 m."""
    import osmnx as ox

    g = ox.load_graphml(out_dir / "drive.graphml")
    edges = ox.graph_to_gdfs(g, nodes=False)

    def _first_class(value: object) -> str:
        return value[0] if isinstance(value, list) else value

    edges = edges.copy()
    edges["hwy"] = edges["highway"].map(_first_class)
    sel = edges[edges["hwy"].isin(BASEMAP_CLASSES)].copy()
    sel["geometry"] = sel.to_crs(_METRIC_CRS).simplify(SIMPLIFY_M).to_crs(4326)
    sel = gpd.GeoDataFrame(sel[["hwy", "geometry"]], crs=4326)

    path = out_dir / "basemap.geojson"
    atomic_write(path, lambda p: sel.to_file(p, driver="GeoJSON"))
    write_meta(
        path,
        manifest_entry(raw_dir, "geo", "geo/massachusetts-latest.osm.pbf"),
        row_count=len(sel),
        notes=(
            "Local street basemap: drive-graph primary/secondary/tertiary/trunk edges, "
            "simplified to 5 m. © OpenStreetMap contributors."
        ),
    )
    return path
