"""Shared download and manifest helpers for scripts/fetch/<group>.py."""

import hashlib
import json
import shutil
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

RAW = Path(__file__).resolve().parents[2] / "data" / "raw"
USER_AGENT = "pareto-commute-agent/0.1 (hackathon prototype; data curation)"


def download(url: str, dest: Path) -> Path:
    """Stream url to dest through a .part file so an interrupted run leaves no partial file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, part.open("wb") as out:
        shutil.copyfileobj(response, out)
    part.replace(dest)
    return dest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_entry(path: Path, source_url: str, license: str, rows: int | None, notes: str = "") -> dict:
    return {
        "file": str(path.relative_to(RAW)),
        "source_url": source_url,
        "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
        "rows": rows,
        "license": license,
        "notes": notes,
    }


def write_manifest(group: str, entries: list[dict]) -> Path:
    path = RAW / group / "MANIFEST.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"group": group, "files": entries}, indent=2) + "\n")
    return path
