from pathlib import Path

import geopandas as gpd

from commute_agent.ingest.meta import atomic_write, manifest_entry, write_meta
from commute_agent.paths import PROCESSED, RAW

BOSTON_BOUNDARY = "geo/boston_boundary.geojson"
CAMBRIDGE_BOUNDARY = "geo/cambridge_boundary.geojson"

BUFFER_M = 2000
BASEMAP_CLASSES = {"primary", "secondary", "tertiary", "trunk"}
SIMPLIFY_M = 5.0


def boundary(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    """Dissolve the Boston boundary and write it plus the Boston-union-Cambridge coverage."""
    boston = gpd.read_file(raw_dir / BOSTON_BOUNDARY).to_crs(4326)
    cambridge = gpd.read_file(raw_dir / CAMBRIDGE_BOUNDARY).to_crs(4326)

    boston_dissolved = gpd.GeoDataFrame(geometry=[boston.union_all()], crs=4326)
    coverage = gpd.GeoDataFrame(
        geometry=[boston.union_all().union(cambridge.union_all())], crs=4326
    )

    boston_path = out_dir / "boston_boundary.geojson"
    atomic_write(boston_path, lambda p: boston_dissolved.to_file(p, driver="GeoJSON"))
    coverage_path = out_dir / "crash_coverage.geojson"
    atomic_write(coverage_path, lambda p: coverage.to_file(p, driver="GeoJSON"))

    write_meta(
        boston_path,
        manifest_entry(raw_dir, "geo", BOSTON_BOUNDARY),
        row_count=1,
        notes="Boston municipal boundary, dissolved to one polygon.",
    )
    write_meta(
        coverage_path,
        [
            manifest_entry(raw_dir, "geo", BOSTON_BOUNDARY),
            manifest_entry(raw_dir, "geo", CAMBRIDGE_BOUNDARY),
        ],
        row_count=1,
        notes="Crash coverage area: union of the Boston and Cambridge boundaries.",
    )
    return boston_path


def _buffered_boston_polygon(raw_dir: Path):
    boston = gpd.read_file(raw_dir / BOSTON_BOUNDARY).to_crs(26986)
    buffered = boston.buffer(BUFFER_M).to_crs(4326).union_all()
    return buffered


def graphs(raw_dir: Path = RAW, out_dir: Path = PROCESSED) -> Path:
    """Download walk, bike, drive graphs for the 2 km-buffered Boston boundary from Overpass.

    This is the one ingest step that downloads. The Overpass cache under
    data/raw/geo/overpass_cache makes a rerun work offline.
    """
    import osmnx as ox

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(raw_dir / "geo" / "overpass_cache")

    polygon = _buffered_boston_polygon(raw_dir)

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
    sel["geometry"] = sel.to_crs(26986).simplify(SIMPLIFY_M).to_crs(4326)
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
