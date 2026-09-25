# UAV-X — Resilient BVLOS Swarm Challenge (Stage 1)

A proof-of-concept simulation for the Techfest 2026-27 / PUSHPAK Grand
Challenge 1 rulebook (`UAV-X__Resilient_BVLOS_Swarm_Challenge_compressed.pdf`,
repo root): one surveyor UAV visits priority-ordered Points of Interest while
a pool of relay UAVs dynamically reposition to keep it connected back to a
ground control station, surviving a relay failure and a comms-degradation
event along the way. Pure Python, no physics engine, no hardware — see
`uavx/RULES_NOTES.md` for the full rulebook-vs-implementation reconciliation
(what's REAL from the rulebook vs. PROVISIONAL/guessed, and why).

**Scope note:** this repository also contains `swarm/` and `tools/`, which
are a separate, unrelated project (an anti-drone interceptor ground
commander using `pymavlink`/ArduPilot SITL — see `CLAUDE.md`). Nothing
below touches `swarm/`, `pymavlink`, or any drone hardware/SITL instance.
The UAV-X pipeline below (`uavx/` → `score/` → `ui/`) is pure Python,
offline, and read-only throughout.

## 0. Get the code

```
git clone https://github.com/SamratSamratSamrat/techkriti-swarm.git
cd techkriti-swarm
```

## Requirements

- Python 3.12 or newer. Standard library only — **no `pip install` is
  needed** for any of the three steps below (`score/requirements.txt`
  documents this explicitly for the scoring harness; `uavx/` and `ui/` are
  the same).

Check your interpreter:

```
python3 --version   # should print 3.12.x or newer
```

## 1. Run the simulation

From the repo root:

```
python3 -m uavx.survey
```

This is the Stage-1 entry point — `uavx/survey.py`'s Path C+ surveyor
mission (one surveyor + relay pool, dynamic reconfiguration on relay
failure, a comms-degradation event, priority-ordered PoI visits). It writes
a telemetry JSON file to `uavx/logs/` (default: `survey_telemetry_seed42.json`,
deterministic — re-running with no flags reproduces the same scenario) and
prints a run summary: PoIs visited in order, packets sent/delivered, and
what happened with the relay-failure and comms-degradation events.

Useful overrides (all optional). **Always pass `--out` with any of these** —
without it, the output filename only depends on `--seed`, so an override run
left at the default seed silently overwrites `survey_telemetry_seed42.json`,
the standard-config baseline file used above and by `score`/`ui` in this
README. `--out` takes a bare filename, written inside `uavx/logs/` (not a
path):

```
python3 -m uavx.survey --seed 101 --out my_run.json
python3 -m uavx.survey --n-uav 4 --r-comm-m 120 --out tightened_run.json   # tighten redundancy for this run only
```

**`uavx/legacy/sim.py` is NOT part of this run path.** It was the original
static-PoI relay simulator, superseded by `survey.py` as of 18 Sep 2026 and
kept only for reproducibility/history — see `uavx/RULES_NOTES.md` §8. Don't
run `python3 -m uavx.legacy.sim` expecting a Stage-1 result; `survey.py` is
the simulation this challenge submission is built around.

## 2. Score the run

```
python3 -m score.harness ingest uavx/logs/survey_telemetry_seed42.json --profile uavx
```

Reads the telemetry file, scores it against the UAV-X metric profile
(packet delivery ratio, latency, connectivity downtime, relay
reallocations, recovery time, PoI completion, priority-weighted mission
score), prints the metrics table, and appends the result to
`swarm/logs/missions.db` so improvement can be tracked run over run. A
metric the harness can't compute is reported as `unavailable` with a
reason — never silently dropped or faked (see `score/README.md`).

## 3. View it

```
python3 -m ui.server --telemetry uavx/logs/survey_telemetry_seed42.json
```

Then open `http://127.0.0.1:8080/` in a browser. The panel shows the fleet
map and the active base→relay→…→surveyor chain, with playback controls
(play/pause, a scrub slider, a 0.5x/1x/2x/4x speed selector) to replay the
whole recorded run tick by tick, not just the latest snapshot. Idle relays
(healthy spares not currently on the chain) render as neutral grey
"idle/standby" dots, not failure styling — only an actually-broken chain
gets the alarm banner.

This server is **read-only**: it never sends a command, never arms
anything, never touches vehicle state, and only ever opens the telemetry
file named by `--telemetry` (see `ui/README.md`, ruling A16). Stop it with
`Ctrl+C`.

## Repository layout

| Path | What it is |
|---|---|
| `uavx/` | The Stage-1 simulation (`survey.py` — run this; `assign.py`/`reconfig.py`/`link.py` — relay placement/reconfiguration/comms model; `config.py` — every tunable, REAL vs. PROVISIONAL; `schema.py` — the telemetry contract). See `uavx/RULES_NOTES.md`. |
| `uavx/legacy/sim.py` | Superseded prototype (static-PoI relay sim). Kept for history, not the Stage-1 entry point. |
| `uavx/logs/` | Telemetry JSON produced by `survey.py`. |
| `score/` | The offline scoring harness (`ingest`/`export`). See `score/README.md`. |
| `ui/` | The read-only telemetry viewer used in step 3. See `ui/README.md` and `ui/DATA_CONTRACT.md`. |
| `swarm/`, `tools/` | A separate, unrelated project (see Scope note above) — not part of this submission. |

## Demonstration video / Stage-1 evidence

`uavx/logs/stress_test_seed101.json` is a separate run, deliberately
distinct from the standard scoring baseline above, built specifically to
exercise a genuinely hard relay failure (no idle relay in instant-swap
range) plus a comms-degradation event — see `uavx/RULES_NOTES.md` §7 for
how it was produced and its actual (unforced) recovery-time numbers. Score
or view it the same way, substituting its filename for
`survey_telemetry_seed42.json` above.
