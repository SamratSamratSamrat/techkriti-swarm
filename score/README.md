# score/ — mission scoring harness (the learning loop)

Offline analysis only. Reads a run's telemetry JSON, scores it against a
`MetricProfile`, and appends the result to `swarm/logs/missions.db` so
improvement can be tracked across runs. **No pymavlink, no drones touched.**

## Files

- `profiles.py` — `MetricProfile` interface + `RingProfile` (Module 3/4 ring
  telemetry, schema matches the real `sprint2.json`) and `UavxProfile`
  (**PROVISIONAL** — the UAV-X telemetry contract is a separate, parallel
  task; field names here are a best-effort guess and may need reconciling).
- `db.py` — SQLite storage (`runs` + `metrics` tables) in
  `swarm/logs/missions.db`, plus the A12 guard (see below).
- `harness.py` — the CLI: `ingest` and `export`.

## Usage

```
python harness.py ingest ../sprint2.json --profile ring
python harness.py ingest <file>.json --profile ring \
    --gps-class M10 --wind "5 kt" --notes "week 2 re-baseline"
python harness.py export --profile ring --metric mean_ring_error_m --exclude-a12
```

`run_id` defaults to the SHA-1 of the source file's bytes, so re-ingesting the
same file is a stable no-op. Pass `--run-id` to name a run explicitly;
overwriting a run_id already bound to a *different* source file requires
`--force`.

## No fabricated numbers

Every metric a profile can't compute (missing field, unparsable value) is
still emitted, with `value=null`, `quality="unavailable"`, and a reason. A
metric is never silently dropped or replaced with a 0 / average-of-what's-left.

## Ruling A12 (see SWARM_OPERATIONS.md)

No sub-metre ring-error figure from a default/un-degraded SITL run may appear
in a competition improvement graph. On ingest, if `mean_ring_error_m < 0.10 m`
and `gps_class` is `"unknown"` or RTK-flavoured, the harness prints a loud
warning and tags the run's notes with `A12-EXCLUDE`. `export --exclude-a12`
filters those runs out.
