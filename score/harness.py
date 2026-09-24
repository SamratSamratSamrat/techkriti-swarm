#!/usr/bin/env python3
"""
score/harness.py — CLI for the mission scoring harness (the "learning loop").

This is OFFLINE analysis tooling. It reads a telemetry JSON file that some
other part of the project already wrote to disk, scores it with one of the
MetricProfiles in profiles.py, and stores the result in
swarm/logs/missions.db so improvement can be tracked across runs. It never
imports pymavlink and never talks to a drone.

Usage:
    python harness.py ingest sprint2.json --profile ring
    python harness.py ingest sprint2.json --profile ring --gps-class M10 --wind "5 kt" --notes "week 2 re-baseline"
    python harness.py export --profile ring --metric mean_ring_error_m --exclude-a12
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import db
from .profiles import PROFILES, Metric


def _load_telemetry(path: Path) -> dict:
    with open(path, "r") as f:
        return json.load(f)


def _pick(explicit: str | None, telemetry_conditions: dict, key: str) -> str:
    """Precedence for gps_class/wind: --flag > telemetry.conditions.<key> > "unknown"."""
    if explicit:
        return explicit
    value = telemetry_conditions.get(key)
    return str(value) if value not in (None, "") else "unknown"


def _print_metrics_table(metrics: list[Metric]) -> None:
    if not metrics:
        print("(no metrics computed)")
        return
    name_w = max(len(m.name) for m in metrics)
    print(f"{'metric'.ljust(name_w)}  {'value':>12}  {'unit':<8}  {'quality':<12}  reason")
    print("-" * (name_w + 60))
    for m in metrics:
        value_str = "null" if m.value is None else (
            f"{m.value:.4g}" if isinstance(m.value, float) else str(m.value)
        )
        reason = m.reason or ""
        print(f"{m.name.ljust(name_w)}  {value_str:>12}  {m.unit:<8}  {m.quality:<12}  {reason}")


def cmd_ingest(args: argparse.Namespace) -> int:
    # Normalize to an absolute, resolved path BEFORE it's used for anything
    # downstream (hashing, storage, dedup comparison) -- the same file
    # invoked as "uavx/logs/x.json" from the repo root vs "../uavx/logs/x.json"
    # from inside score/ used to stringify differently and store two
    # different `source_file` values for the same content-hash run_id,
    # which _same_content()/ingest_run()'s dedup check correctly flagged as
    # a conflict (it wasn't wrong to flag it -- the recorded path was
    # genuinely inconsistent). Resolving here, once, up front, means the
    # SAME real file always gets the SAME source_file string regardless of
    # cwd or relative-path spelling at the call site. See
    # uavx/RULES_NOTES.md for the full root-cause writeup.
    source_path = Path(args.telemetry_json).resolve()
    if not source_path.exists():
        print(f"error: {source_path} does not exist", file=sys.stderr)
        return 1

    telemetry = _load_telemetry(source_path)
    conditions = telemetry.get("conditions") or {}

    profile = PROFILES.get(args.profile)
    if profile is None:
        print(f"error: unknown profile {args.profile!r} (choices: {sorted(PROFILES)})", file=sys.stderr)
        return 1

    metrics = profile.compute(telemetry, params={})

    run_id = args.run_id or db.sha1_of_file(source_path)
    gps_class = _pick(args.gps_class, conditions, "gps_class")
    wind = _pick(args.wind, conditions, "wind")
    notes = db.apply_a12_guard(gps_class, wind, metrics, args.notes)

    conn = db.connect()
    try:
        outcome = db.ingest_run(
            conn,
            run_id=run_id,
            profile=args.profile,
            run_start_utc=telemetry.get("run_start_utc"),
            source_file=source_path,
            gps_class=gps_class,
            wind=wind,
            notes=notes,
            metrics=metrics,
            ingested_utc=datetime.now(timezone.utc).isoformat(),
            force=args.force,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"run_id: {run_id}  profile: {args.profile}  outcome: {outcome}")
    print(f"gps_class: {gps_class}  wind: {wind}")
    _print_metrics_table(metrics)
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        rows = db.export_metric(conn, args.profile, args.metric, exclude_a12=args.exclude_a12)
    finally:
        conn.close()

    writer = csv.writer(sys.stdout)
    writer.writerow(["run_start_utc", "value"])
    for run_start_utc, value in rows:
        writer.writerow([run_start_utc, value])
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mission scoring harness (learning loop, offline only).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="score a telemetry JSON file and store it in missions.db")
    p_ingest.add_argument("telemetry_json", help="path to the run's telemetry JSON")
    p_ingest.add_argument("--profile", required=True, choices=sorted(PROFILES), help="which MetricProfile to score with")
    p_ingest.add_argument("--run-id", default=None, help="override the default sha1(file)-derived run_id")
    p_ingest.add_argument("--gps-class", default=None, help="e.g. M10, RTK (overrides telemetry.conditions.gps_class)")
    p_ingest.add_argument("--wind", default=None, help="e.g. '5 kt' (overrides telemetry.conditions.wind)")
    p_ingest.add_argument("--notes", default=None, help="free-text notes to store with the run")
    p_ingest.add_argument("--force", action="store_true", help="allow replacing a run_id already used by a different source file")
    p_ingest.set_defaults(func=cmd_ingest)

    p_export = sub.add_parser("export", help="export one metric's history across all runs as CSV")
    p_export.add_argument("--profile", required=True, choices=sorted(PROFILES))
    p_export.add_argument("--metric", required=True, help="metric name, e.g. mean_ring_error_m")
    p_export.add_argument("--exclude-a12", action="store_true", help="drop runs tagged A12-EXCLUDE (see ruling A12)")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
