# A26 evidence pack -- rulebook-accurate mission (seed 48)

`scenario_config.json`, `event_metrics.json`, `stage1_summary.txt` are the
evidence. The raw run they were built from, `stage1_run_a26.json` (~15 MB,
13,500 ticks), is **not committed** -- it is deterministic (see
`uavx/tests/test_a26_dynamic.py::Determinism`) and takes ~1 s to regenerate:

```bash
# from the repo root
python3 -m uavx.survey --dynamic-spawn --seed 48 --n-uav 4 --out a26/stage1_run_a26.json

# replay it (open http://127.0.0.1:8080, use 30x or 60x)
python3 -m ui.server --telemetry uavx/logs/a26/stage1_run_a26.json --port 8080
```

To check the committed pack reproduces on your machine (writes to a temp
dir, never over these files):

```bash
python3 -m uavx.evidence_pack --run uavx/logs/a26/stage1_run_a26.json --out-dir /tmp/a26check
diff /tmp/a26check/stage1_summary.txt uavx/logs/a26/stage1_summary.txt && echo REPRODUCED
```

**Why seed 48, and what it does NOT show on its own:** it is the only seed
of 1-100 where both A23 fault events fire off different reports and the
dropout recovers. Always cite it together with the 100-seed numbers in
`uavx/RULES_NOTES.md` section 14 (`uavx/logs/a26_seed_scan_1-100.json`):
~2/10 PoIs reachable, ~1.1/10 reported, both fault events fire separately
in only 6/100 runs.
