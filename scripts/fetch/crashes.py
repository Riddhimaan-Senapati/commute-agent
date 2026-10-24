"""Fetch full crash datasets for the Boston commute-routing project: group `crashes`."""
import csv
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import RAW, USER_AGENT, download, manifest_entry, write_manifest

CRASHES = RAW / "crashes"
CKAN = "https://data.boston.gov/api/3/action"
VISION_ZERO_RESOURCE = "e4bfe397-6bfc-49c5-9367-c879fac7401d"
CAMBRIDGE_ID = "gb5w-yva3"
CAMBRIDGE_CSV = f"https://data.cambridgema.gov/api/views/{CAMBRIDGE_ID}/rows.csv?accessType=DOWNLOAD"
CAMBRIDGE_META = f"https://data.cambridgema.gov/api/views/{CAMBRIDGE_ID}.json"


def ensure_system_cas() -> None:
    # The uv-managed Python trusts only its bundled openssl CA file, which lacks the
    # GlobalSign Root R46 that data.cambridgema.gov chains to; export the macOS system
    # roots (which do include it) and point the default SSL context at them.
    if os.environ.get("SSL_CERT_FILE") or sys.platform != "darwin":
        return
    try:
        pem = subprocess.run(
            ["security", "find-certificate", "-a", "-p",
             "/System/Library/Keychains/SystemRootCertificates.keychain"],
            check=True, capture_output=True, text=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return
    bundle = Path(tempfile.gettempdir()) / "commute_agent_macos_roots.pem"
    bundle.write_text(pem)
    os.environ["SSL_CERT_FILE"] = str(bundle)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def resolve_download_url(url: str) -> str:
    # Boston CKAN 302s to an S3 presigned URL whose signed host is "s3.amazonaws.com:443";
    # urllib sends the port-less Host for a default HTTPS port, so the signature mismatches
    # (403). Strip the spurious :443 so the Host we send matches what was signed.
    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        opener.open(request, timeout=120).close()
        return url
    except urllib.error.HTTPError as error:
        if error.status in (301, 302, 303, 307, 308) and "location" in error.headers:
            return error.headers["location"].replace("s3.amazonaws.com:443", "s3.amazonaws.com")
        raise


def get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.load(response)


def ensure_download(url: str, dest: Path) -> Path:
    """Download url to dest, but skip it when dest already exists.

    Reruns are meant to rebuild the manifest from files already on disk, not to
    re-pull large CSVs. If a file is corrupt, delete it and rerun to force a
    fresh download.
    """
    if dest.exists():
        print(f"skip (already present): {dest.relative_to(RAW)}")
        return dest
    return download(url, dest)


def record_count(path: Path) -> int:
    """True number of data rows parsed with the csv module.

    Counting physical newlines (the old approach) over-counts any file whose
    quoted cells contain embedded newlines -- e.g. the Cambridge export stores a
    multiline address in its `Location` column, which inflated 17,230 records to
    41,523 lines. Parsing with csv.reader honors RFC 4180 quoting, so a row with
    embedded newlines counts once.
    """
    csv.field_size_limit(1 << 30)
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        try:
            next(reader)  # header
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def fetch_vision_zero() -> dict:
    resource = get_json(f"{CKAN}/resource_show?id={VISION_ZERO_RESOURCE}")["result"]
    package = get_json(f"{CKAN}/package_show?id={resource['package_id']}")["result"]
    dest = CRASHES / "boston_vision_zero.csv"
    ensure_download(resolve_download_url(resource["url"]), dest)
    return manifest_entry(
        dest, resource["url"], package.get("license_title") or "unknown",
        record_count(dest), "Boston Vision Zero crash records; CKAN resource "
        f"{VISION_ZERO_RESOURCE}.",
    )


def fetch_cambridge() -> dict:
    meta = get_json(CAMBRIDGE_META)
    license = (meta.get("license") or {}).get("name") or meta.get("licenseId") or "unknown"
    dest = CRASHES / "cambridge_police_crashes.csv"
    ensure_download(CAMBRIDGE_CSV, dest)
    return manifest_entry(
        dest, CAMBRIDGE_CSV, license, record_count(dest),
        "Cambridge police crash records; Socrata dataset gb5w-yva3.",
    )


def main() -> None:
    ensure_system_cas()
    entries = [fetch_vision_zero(), fetch_cambridge()]
    path = write_manifest("crashes", entries)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
