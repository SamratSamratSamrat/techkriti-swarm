# A26 evidence pack -- rulebook-accurate mission (seed 201)

`scenario_config.json`, `event_metrics.json`, `stage1_summary.txt` are the
evidence. The raw run they were built from, `stage1_run_a26.json` (~15 MB,
13,500 ticks), is **not committed** -- it is deterministic (see
`uavx/tests/test_a26_dynamic.py::Determinism`) and takes ~1 s to regenerate:

```bash
# from the repo root
python3 -m uavx.survey --dynamic-spawn --seed 201 --n-uav 4 --out a26/stage1_run_a26.json

# replay it (open http://127.0.0.1:8080, use 30x or 60x)
python3 -m ui.server --telemetry uavx/logs/a26/stage1_run_a26.json --port 8080
```

To check the committed pack reproduces on your machine (writes to a temp
dir, never over these files):

```bash
python3 -m uavx.evidence_pack --run uavx/logs/a26/stage1_run_a26.json --out-dir /tmp/a26check
diff /tmp/a26check/stage1_summary.txt uavx/logs/a26/stage1_summary.txt && echo REPRODUCED
```

**A28: seed 48 withdrawn, seed 201 adopted in its place.** Seed 48 was
originally picked as "the only seed of 1-100 where both A23 fault events
fire off different reports and the dropout recovers" -- but that check never
recorded *which relay node* each event landed on, and it turned out both of
seed 48's events hit `relay_2`, the same node, which fails A23's requirement
that Event 2 apply to a different relay than Event 1. That gap was only
visible once the seed-scan script was extended to record `deg_node`/
`drop_node` per seed and rerun across a larger sample (see below). Seed 201
was selected from that rescan as the lowest-numbered seed satisfying every
A23 condition at once: `link_degraded` on `relay_2` at t=1900.2s (off PoI
5's report), `uav_dropout` on `relay_3` at t=2316.2s (off PoI 0's report,
416.0s later -- a different node, a different report, well separated in
time), dropout recovery measured at 6.2s. 3/10 PoIs reachable in this run,
all 3 reported.

**Always cite it together with the 1000-seed numbers** (`uavx/logs/
seed_scan_1000.json` -- supersedes the earlier 100-seed scan,
`uavx/logs/a26_seed_scan_1-100.json`, which is kept for history but should
not be cited as current):

| measure | value (of 1000 seeds) |
|---|---|
| PoIs reachable | mean ~1.97/10 |
| PoIs reported | mean ~1.07/10 |
| both A23 events fire and the dropout recovers | 84/1000 |
| + also land on different relay nodes (full A23+A28 compliance) | 33/1000 |
| + also separated in tick and report (strict compliance) | 18/1000 |

These figures replace the earlier "6/100" figure, which was itself a count
of only "both fired off different reports" at n=100 and never checked node
identity at all -- it undercounted the true 1000-seed picture and, more
importantly, would not have caught the seed-48 node collision even if it
had been recomputed at n=100. Do not cite "6/100" going forward.
