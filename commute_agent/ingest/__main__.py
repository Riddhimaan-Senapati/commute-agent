import sys

from commute_agent.ingest import bluebikes as bluebikes_mod
from commute_agent.ingest import counts as counts_mod
from commute_agent.ingest import crashes as crashes_mod
from commute_agent.ingest import geo as geo_mod
from commute_agent.ingest import mbta as mbta_mod
from commute_agent.ingest.meta import SchemaMismatch

# Order matters for `all`: boundary before graphs/basemap, graphs before basemap.
DATASETS = {
    "boundary": geo_mod.boundary,
    "graphs": geo_mod.graphs,
    "basemap": geo_mod.basemap,
    "crashes": crashes_mod.crashes,
    "counts": counts_mod.counts,
    "bluebikes": bluebikes_mod.bluebikes,
    "stations": mbta_mod.stations,
    "otp-inputs": mbta_mod.otp_inputs,
}


def run(name: str) -> int:
    if name == "all":
        names = list(DATASETS)
    elif name in DATASETS:
        names = [name]
    else:
        choices = ", ".join([*DATASETS, "all"])
        print(f"unknown dataset {name!r}; choose from {choices}", file=sys.stderr)
        return 2

    for ds in names:
        try:
            path = DATASETS[ds]()
        except SchemaMismatch as exc:
            print(exc, file=sys.stderr)
            return 2
        print(f"{ds}: wrote {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m commute_agent.ingest <dataset>", file=sys.stderr)
        return 2
    return run(args[0])


if __name__ == "__main__":
    raise SystemExit(main())
