import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd


class SchemaMismatch(Exception):
    """A source file is missing expected columns."""


def require_columns(df: pd.DataFrame, dataset: str, expected: set[str]) -> None:
    """Raise SchemaMismatch naming the dataset and every missing column."""
    missing = expected - set(df.columns)
    if missing:
        raise SchemaMismatch(
            f"{dataset}: missing columns {sorted(missing)}; found {sorted(df.columns)}"
        )


def manifest_entry(raw_dir: Path, group: str, file_rel: str) -> dict[str, Any]:
    """Read the raw MANIFEST.json for a group and return the entry for file_rel.

    file_rel is the path relative to data/raw/, e.g. 'crashes/boston_vision_zero.csv'.
    Raises SchemaMismatch when the manifest or the entry is absent.
    """
    manifest_path = raw_dir / group / "MANIFEST.json"
    if not manifest_path.exists():
        raise SchemaMismatch(f"{group}: manifest not found at {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest.get("files", []):
        if entry.get("file") == file_rel:
            return entry
    raise SchemaMismatch(f"{group}: no manifest entry for {file_rel}")


def write_meta(
    path: Path,
    source_manifest_entry: dict[str, Any] | list[dict[str, Any]],
    row_count: int,
    notes: str,
) -> Path:
    """Write a sidecar <path>.meta.json with source URL and license from the raw manifest,
    the row count, and notes. Accepts a single entry or a list of entries (multi-source)."""
    entries = (
        source_manifest_entry
        if isinstance(source_manifest_entry, list)
        else [source_manifest_entry]
    )
    sources = [
        {"file": e.get("file"), "source_url": e.get("source_url"), "license": e.get("license")}
        for e in entries
    ]
    meta = {
        "generated_at": datetime.now(UTC).isoformat(),
        "output": path.name,
        "sources": sources,
        "row_count": row_count,
        "notes": notes,
    }
    meta_path = path.with_name(path.name + ".meta.json")
    _atomic_write_text(meta_path, json.dumps(meta, indent=2))
    return meta_path


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def atomic_write(path: Path, write: "callable[[Path], None]") -> None:
    """Write via a temp file in the same directory, then rename, so a failed run leaves
    the old file. `write` receives the temp path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    os.replace(tmp, path)
