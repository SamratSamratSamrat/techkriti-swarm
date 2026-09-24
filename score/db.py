"""
score/db.py — the missions database for the learning loop.

Stores one SQLite file: swarm/logs/missions.db. This is OFFLINE analysis
storage only — nothing here talks to a drone or to pymavlink.

Two tables:
  runs    — one row per ingested telemetry file.
  metrics — one row per (run_id, metric name), so `export` can pull a single
            metric's history across every run for a plot.

run_id defaults to the SHA-1 of the source file's raw bytes. That makes
ingesting the exact same file twice a stable no-op (same bytes -> same
run_id -> same content -> nothing to do) without the caller having to
remember what they called it last time.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .profiles import Metric

DB_PATH = Path(__file__).resolve().parent.parent / "swarm" / "logs" / "missions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    profile TEXT NOT NULL,
    run_start_utc TEXT,
    source_file TEXT NOT NULL,
    ingested_utc TEXT NOT NULL,
    gps_class TEXT NOT NULL,
    wind TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS metrics (
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    value REAL,
    unit TEXT,
    quality TEXT NOT NULL,
    PRIMARY KEY (run_id, name),
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the missions database with tables ready."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def sha1_of_file(path: Path) -> str:
    """Stable run_id source: hash the file's raw bytes, not its parsed content."""
    h = hashlib.sha1()
    h.update(Path(path).read_bytes())
    return h.hexdigest()


@dataclass
class ExistingRun:
    run_id: str
    profile: str
    run_start_utc: Optional[str]
    source_file: str
    gps_class: str
    wind: str
    notes: Optional[str]
    metrics: dict[str, tuple]  # name -> (value, unit, quality)


def _fetch_existing(conn: sqlite3.Connection, run_id: str) -> Optional[ExistingRun]:
    row = conn.execute(
        "SELECT run_id, profile, run_start_utc, source_file, gps_class, wind, notes "
        "FROM runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        return None
    metric_rows = conn.execute(
        "SELECT name, value, unit, quality FROM metrics WHERE run_id = ?", (run_id,)
    ).fetchall()
    metrics = {name: (value, unit, quality) for name, value, unit, quality in metric_rows}
    return ExistingRun(
        run_id=row[0],
        profile=row[1],
        run_start_utc=row[2],
        source_file=row[3],
        gps_class=row[4],
        wind=row[5],
        notes=row[6],
        metrics=metrics,
    )


def _is_a12_suspect(gps_class: str) -> bool:
    """A12: 'unknown' or any RTK-flavoured class (e.g. "RTK", "RTK Fixed", "FIX 6")."""
    gps_class = (gps_class or "unknown").strip().lower()
    return gps_class == "unknown" or "rtk" in gps_class or gps_class.startswith("fix")


def apply_a12_guard(gps_class: str, wind: str, metrics: list[Metric], notes: Optional[str]) -> str:
    """
    Ruling A12 (SWARM_OPERATIONS.md): no sub-metre accuracy figure from a
    default/unrealistic-GPS SITL run may appear in a competition improvement
    graph. If this run's mean_ring_error_m is suspiciously good (< 10 cm) AND
    we can't confirm degraded-GPS/wind conditions, flag it loudly and tag the
    notes so `export --exclude-a12` can filter it out later.

    Returns the (possibly amended) notes string. Never changes gps_class,
    wind, or the metric values themselves — this only tags, it never edits
    measured data.
    """
    mean_error = next((m.value for m in metrics if m.name == "mean_ring_error_m"), None)
    if mean_error is None or mean_error >= 0.10:
        return notes
    if not _is_a12_suspect(gps_class):
        return notes

    print(
        "\n*** WARNING (ruling A12): mean_ring_error_m = "
        f"{mean_error:.3f} m with gps_class={gps_class!r} — this looks like an "
        "un-degraded SITL run (RTK-class GPS / unknown conditions). Per A12 this "
        "run must NOT appear in any competition improvement graph. ***\n"
    )
    tag = "A12-EXCLUDE"
    if notes and tag not in notes:
        return f"{notes}; {tag}"
    return notes or tag


def _same_content(existing: ExistingRun, profile: str, run_start_utc, source_file, gps_class, wind, notes, metrics) -> bool:
    if (existing.profile, existing.run_start_utc, existing.source_file, existing.gps_class, existing.wind, existing.notes) != (
        profile,
        run_start_utc,
        str(source_file),
        gps_class,
        wind,
        notes,
    ):
        return False
    new_metrics = {m.name: (m.value, m.unit, m.quality) for m in metrics}
    return existing.metrics == new_metrics


def ingest_run(
    conn: sqlite3.Connection,
    run_id: str,
    profile: str,
    run_start_utc: Optional[str],
    source_file: Path,
    gps_class: str,
    wind: str,
    notes: Optional[str],
    metrics: list[Metric],
    ingested_utc: str,
    force: bool = False,
) -> str:
    """
    Insert or replace one run's rows. Returns "inserted" / "replaced" / "unchanged".

    Idempotent by design: re-ingesting the same run_id with identical content
    is a no-op. If the run_id already exists but points at a DIFFERENT source
    file, that's a collision (most likely an explicit --run-id typo/reuse) —
    it's refused unless --force is passed, so a mistake can't silently
    overwrite a different mission's data.
    """
    existing = _fetch_existing(conn, run_id)

    if existing is not None:
        if _same_content(existing, profile, run_start_utc, source_file, gps_class, wind, notes, metrics):
            return "unchanged"
        if existing.source_file != str(source_file) and not force:
            raise ValueError(
                f"run_id {run_id!r} already stored for a different source file "
                f"({existing.source_file!r} != {str(source_file)!r}). Pass --force to overwrite."
            )
        conn.execute("DELETE FROM metrics WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
        outcome = "replaced"
    else:
        outcome = "inserted"

    conn.execute(
        "INSERT INTO runs (run_id, profile, run_start_utc, source_file, ingested_utc, gps_class, wind, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, profile, run_start_utc, str(source_file), ingested_utc, gps_class, wind, notes),
    )
    conn.executemany(
        "INSERT INTO metrics (run_id, name, value, unit, quality) VALUES (?, ?, ?, ?, ?)",
        [(run_id, m.name, m.value, m.unit, m.quality) for m in metrics],
    )
    conn.commit()
    return outcome


def export_metric(
    conn: sqlite3.Connection, profile: str, metric_name: str, exclude_a12: bool = False
) -> list[tuple[str, float]]:
    """Return (run_start_utc, value) pairs for one metric across all runs, sorted by time."""
    query = (
        "SELECT r.run_start_utc, m.value FROM runs r "
        "JOIN metrics m ON m.run_id = r.run_id "
        "WHERE r.profile = ? AND m.name = ? AND m.value IS NOT NULL"
    )
    args: list = [profile, metric_name]
    if exclude_a12:
        query += " AND (r.notes IS NULL OR r.notes NOT LIKE '%A12-EXCLUDE%')"
    query += " ORDER BY r.run_start_utc"
    return conn.execute(query, args).fetchall()
