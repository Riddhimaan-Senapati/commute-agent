"""Fetch the `counts` group: Cambridge bike counts, Somerville bike/ped counts, Boston Numina sensors."""
import csv
import json
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import RAW, USER_AGENT, download, manifest_entry, write_manifest

GROUP = "counts"
OUT = RAW / GROUP
NUMINA = OUT / "numina"
MANIFEST = OUT / "MANIFEST.json"

CAMBRIDGE_CSV = "https://data.cambridgema.gov/api/views/ijm6-q7em/rows.csv?accessType=DOWNLOAD"
CAMBRIDGE_META = "https://data.cambridgema.gov/api/views/ijm6-q7em.json"
SOMERVILLE_CSV = "https://data.somervillema.gov/api/views/qu9x-4xq5/rows.csv?accessType=DOWNLOAD"
SOMERVILLE_META = "https://data.somervillema.gov/api/views/qu9x-4xq5.json"
NUMINA_PACKAGE = "https://data.boston.gov/api/3/action/package_show?id=3b2ce71d-d7ca-4027-b971-7180c263d730"


class _PortStrippingRedirect(urllib.request.HTTPRedirectHandler):
    """data.boston.gov redirects to a presigned S3 URL whose host carries an explicit ':443';
    SigV4 signed only the bare host, so urllib's Host header with the port fails the signature check.
    Drop the redundant default port so the sent Host matches what was signed."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        newurl = newurl.replace("s3.amazonaws.com:443", "s3.amazonaws.com")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_BOSTON_OPENER = urllib.request.build_opener(_PortStrippingRedirect)


def boston_download(url: str, dest: Path) -> Path:
    """Like common.download but through the opener that repairs the ':443' S3 redirect host."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _BOSTON_OPENER.open(request, timeout=300) as response, part.open("wb") as out:
        shutil.copyfileobj(response, out)
    part.replace(dest)
    return dest


def fetch(fn, url: str, dest: Path) -> Path:
    """Download via fn only if dest is missing; retry once, then stop and report the URL and error."""
    if dest.exists():
        return dest
    for attempt in (1, 2):
        try:
            return fn(url, dest)
        except Exception as error:  # noqa: BLE001 - surface the failing URL, then stop
            if attempt == 2:
                raise SystemExit(f"Download failed twice for {url}: {error!r}")
            time.sleep(2)


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        return json.load(response)


def socrata_license(meta_url: str) -> str:
    return fetch_json(meta_url).get("license", {}).get("name", "")


def count_records(path: Path) -> int:
    """True CSV record count excluding the header; a multiline quoted field counts as one record."""
    with path.open(newline="", encoding="utf-8") as f:
        return max(sum(1 for _ in csv.reader(f)) - 1, 0)


def first_zone_id(path: Path) -> str:
    with path.open(newline="", encoding="utf-8") as f:
        return next(csv.DictReader(f))["zone_id"]


def sanitize(name: str) -> str:
    """Safe filename stem: keep alphanumerics, collapse every other run to a single dash."""
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-") or "document"


def known_zone_files() -> dict[str, str]:
    """source_url -> file basename, read from a prior manifest so a rerun skips the network.

    A Numina CSV's zone id lives in its first data row, not its download URL, so without this a rerun
    could not tell which existing zone_<id>.csv a URL already produced. The manifest already pairs
    them, so reuse it rather than keep a second index."""
    if not MANIFEST.exists():
        return {}
    files = json.loads(MANIFEST.read_text()).get("files", [])
    return {f["source_url"]: Path(f["file"]).name for f in files}


def socrata_entry(csv_url, meta_url, filename, notes) -> dict:
    dest = fetch(download, csv_url, OUT / filename)
    return manifest_entry(dest, csv_url, socrata_license(meta_url), count_records(dest), notes=notes)


def numina_csv(url: str, url_name: str, prior: dict[str, str]) -> Path:
    """Return zone_<id>.csv for a Numina CSV; skip the download when the manifest already maps this URL."""
    cached = prior.get(url)
    if cached and (NUMINA / cached).exists():
        return NUMINA / cached
    staged = fetch(boston_download, url, NUMINA / url_name)
    final = NUMINA / f"zone_{first_zone_id(staged)}.csv"
    if final != staged:
        staged.replace(final)
    return final


def numina_entries() -> list[dict]:
    package = fetch_json(NUMINA_PACKAGE)["result"]
    license_title = package.get("license_title", "")
    prior = known_zone_files()
    entries = []
    for resource in package["resources"]:
        url = resource["url"]
        fmt = (resource.get("format") or "").upper()
        url_name = url.rsplit("/", 1)[-1]
        if fmt == "CSV":
            final = numina_csv(url, url_name, prior)
            entries.append(manifest_entry(
                final, url, license_title, count_records(final),
                notes=f"Numina sensor {final.stem} (source resource: {url_name}).",
            ))
        elif fmt == "PDF":
            stem = sanitize(resource.get("name") or Path(url_name).stem or resource["id"])
            dest = fetch(boston_download, url, NUMINA / "docs" / f"{stem}.pdf")
            entries.append(manifest_entry(dest, url, license_title, None, notes="Numina supporting document."))
        else:
            raise SystemExit(f"Unexpected Numina resource format {fmt!r} at {url}")
    return entries


def main() -> None:
    entries = [
        socrata_entry(CAMBRIDGE_CSV, CAMBRIDGE_META, "cambridge_citywide_bike_counts.csv",
                      "Cambridge citywide bicycle counts (Socrata ijm6-q7em)."),
        socrata_entry(SOMERVILLE_CSV, SOMERVILLE_META, "somerville_bike_ped_counts.csv",
                      "Somerville bike and pedestrian counts (Socrata qu9x-4xq5)."),
    ]
    entries.extend(numina_entries())
    write_manifest(GROUP, entries)


if __name__ == "__main__":
    main()
