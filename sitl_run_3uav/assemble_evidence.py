#!/usr/bin/env python3
"""Assemble PART B evidence pack: pull sprint2.* -> sprint3.* naming,
compute geometric worst-case minimum inter-drone separation bound from
sprint2.jsonl err_m data + known 120deg/5.0m slot geometry (approved
approach: geometric bound, not a direct SERIAL1 measurement)."""
import json, math, shutil, os

LOG_DIR = "/home/satis/techkriti-swarm/logs"
OUT_DIR = "/home/satis/swarm-defence/sitl_run_3uav/evidence_pack"
os.makedirs(OUT_DIR, exist_ok=True)

# 1. sprint3.json <- sprint2.json
shutil.copyfile(f"{LOG_DIR}/sprint2.json", f"{OUT_DIR}/sprint3.json")

# 2. statustext.log <- statustext.log (as-is)
shutil.copyfile(f"{LOG_DIR}/statustext.log", f"{OUT_DIR}/statustext.log")

# 3. commander_stdout.log <- the REAL commander stdout (not the stale techkriti one)
shutil.copyfile("/home/satis/swarm-defence/sitl_run_3uav/commander_stdout.log",
                 f"{OUT_DIR}/commander_stdout.log")

# 4. params.txt (ring.py's own broken SYSID_MYGCS attempt log) - include, annotate separately
shutil.copyfile(f"{LOG_DIR}/params.txt", f"{OUT_DIR}/params_ring_internal_SYSID_MYGCS_known_broken.txt")

# 5. preflight gate evidence
shutil.copyfile("/home/satis/swarm-defence/sitl_run_3uav/preflight_gate3.txt",
                 f"{OUT_DIR}/preflight_gate3.txt")

# 6. Compute geometric worst-case minimum inter-drone separation bound
#    Nominal neighbor distance at 3 drones / 120 deg / R=5.0m: 2*R*sin(60deg)
R = 5.0
nominal_neighbor_m = 2 * R * math.sin(math.radians(60))

per_tick = []
with open(f"{LOG_DIR}/sprint2.jsonl") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        per_tick.append(rec)

# real structure: each line is one tick, with 't_s' and a nested 'interceptors' list
# of {'sysid', 'status', 'err_m', 'converged'}
buckets = {}
for rec in per_tick:
    tval = rec.get('t_s')
    for ic in rec.get('interceptors', []):
        sysid = ic.get('sysid')
        err = ic.get('err_m')
        if sysid is None or err is None:
            continue
        buckets.setdefault(tval, {})[sysid] = err

worst_case_min_sep = None
worst_case_tick = None
all_mins = []
for tval, sysid_errs in buckets.items():
    sysids = sorted(sysid_errs.keys())
    if len(sysids) < 2:
        continue
    # worst-case bound per neighbor pair = nominal - err_i - err_j
    pair_mins = []
    for i in range(len(sysids)):
        for j in range(i+1, len(sysids)):
            bound = nominal_neighbor_m - sysid_errs[sysids[i]] - sysid_errs[sysids[j]]
            pair_mins.append(bound)
    tick_min = min(pair_mins)
    all_mins.append(tick_min)
    if worst_case_min_sep is None or tick_min < worst_case_min_sep:
        worst_case_min_sep = tick_min
        worst_case_tick = tval

result = {
    "method": "geometric_bound_not_direct_measurement",
    "nominal_neighbor_separation_m_at_120deg_R5.0": round(nominal_neighbor_m, 4),
    "num_ticks_used": len(all_mins),
    "worst_case_minimum_inter_drone_separation_bound_m": round(worst_case_min_sep, 4) if worst_case_min_sep is not None else None,
    "worst_case_tick": worst_case_tick,
    "note": "Lower bound only: nominal_neighbor_distance - err_i - err_j per tick per neighbor pair, minimized over all ticks and pairs. NOT a direct SERIAL1/lat-lon measurement -- sprint2.jsonl only logs per-drone radial error (err_m) to ring center, not raw position, so exact pairwise distance cannot be computed from this log (per user-approved approach)."
}

with open(f"{OUT_DIR}/separation_bound.json", "w") as f:
    json.dump(result, f, indent=2)

print(json.dumps(result, indent=2))
print("\nsample record keys:", list(per_tick[0].keys()) if per_tick else "NO RECORDS")
print("num sysid_errs first bucket:", len(list(buckets.values())[0]) if buckets else 0)

# 7. Write sprint3_summary.txt = sprint2_summary.txt + new min-separation field appended
with open(f"{LOG_DIR}/sprint2_summary.txt") as f:
    summary_txt = f.read()

summary_txt += "\n\n========== INTER-DRONE SEPARATION (NEW, geometric bound) ==========\n"
summary_txt += f"Nominal neighbor separation (120deg spacing, R=5.0m): {nominal_neighbor_m:.4f} m\n"
if worst_case_min_sep is not None:
    summary_txt += f"Worst-case minimum inter-drone separation bound observed: {worst_case_min_sep:.4f} m (at tick={worst_case_tick})\n"
summary_txt += "Method: geometric bound = nominal_neighbor_distance - err_i - err_j, minimized over all ticks/pairs.\n"
summary_txt += "This is a LOWER BOUND, not a direct measurement -- sprint2.jsonl logs only per-drone radial error to ring center, not raw lat/lon, so exact pairwise separation is not directly computable from this log.\n"

with open(f"{OUT_DIR}/sprint3_summary.txt", "w") as f:
    f.write(summary_txt)

print("\nWROTE:", OUT_DIR)
print(os.listdir(OUT_DIR))
