"""Build the A23 Stage-1 evidence pack (CP1-CP5) from one survey run.

Given the telemetry written by

    python3 -m uavx.survey --seed 1 --n-uav 4 --events a23-only \
        --scenario claude/scenario_config.json --out stage1_run.json

this writes, next to it in uavx/logs/:
  scenario_config.json -- byte-for-byte copy of the scenario file the run used
  event_metrics.json   -- recovery_time_s / relay_reallocations per A23 event, unpooled
  stage1_summary.txt   -- human-readable timeline + metrics

Every number comes from the telemetry via score/profiles.py's UavxProfile
(the same code `score.harness ingest --profile uavx` uses) or is counted
directly from telemetry rows -- nothing is typed in by hand.

Run with:  python3 -m uavx.evidence_pack [--run uavx/logs/stage1_run.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil

from score.profiles import PROFILES
from uavx import config
from uavx.survey import _relay_ids_on_chain

LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
A23_EVENTS = ("link_degraded", "uav_dropout")


def _sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _close(a: float, b: float) -> bool:
    return abs(a - b) < config.TICK_S / 2


def _serves_changes(assignments: list[dict], t_from: float, t_to: float | None) -> list[dict]:
    """Serves-set changes (the same per-relay consecutive-row comparison
    UavxProfile._relay_reallocations uses) whose row falls in [t_from, t_to]."""
    by_relay: dict = {}
    for a in assignments:
        by_relay.setdefault(a["relay_uav"], []).append(a)
    out = []
    for rows in by_relay.values():
        rows.sort(key=lambda a: a["t_s"])
        prev = None
        for a in rows:
            cur = set(a["serves"])
            if prev is not None and cur != prev and a["t_s"] >= t_from - 1e-9 and (t_to is None or a["t_s"] <= t_to + 1e-9):
                out.append(a)
            prev = cur
    return out


def build_event_metrics(tel: dict) -> dict:
    metrics = {m.name: m for m in PROFILES["uavx"].compute(tel, {})}
    events = tel["events"]
    chain_at = {round(c["t_s"], 1): c for c in tel["chains"]}
    per_event = []
    for ev in (e for e in events if e["type"] in A23_EVENTS):
        visit = next(v for v in tel["visits"] if v["poi"] == ev["poi"] and v["reported_t_s"] is not None
                     and _close(v["reported_t_s"], ev["report_t_s"]))
        report_chain = chain_at[round(visit["reported_t_s"], 1)]
        fire_chain = chain_at[round(ev["t_s"], 1)]
        rec = {
            "event": ev["type"],
            "fraction_target": config.DEGRADE_FRACTION if ev["type"] == "link_degraded" else config.DROPOUT_FRACTION,
            "node": f"relay_{ev['uav']}",
            "t_s": ev["t_s"],
            "selected_from_report": {
                "poi": ev["poi"],
                "report_t_s": visit["reported_t_s"],
                "carrying_relay_ids": visit["carrying_relay_ids"],
                "chain_path_at_report_tick": report_chain["path"],
            },
            "target_in_carrying_relay_ids": ev["uav"] in visit["carrying_relay_ids"],
            "target_on_live_chain_at_fire_tick": ev["uav"] in _relay_ids_on_chain(fire_chain["path"]),
        }
        if ev["type"] == "uav_dropout":
            m = metrics["recovery_time_s_uav_dropout"]
            restored = next((e for e in events if e["type"] == "restored" and e["uav"] == ev["uav"] and e["t_s"] > ev["t_s"]), None)
            r = metrics["relay_reallocations_poi_report_dropout"]
            rec.update({
                "recovery_time_s": m.value, "recovery_time_quality": m.quality, "recovery_time_reason": m.reason,
                "restored_t_s": restored["t_s"] if restored else None,
                "relay_reallocations": r.value, "relay_reallocations_quality": r.quality,
                "relay_reallocations_basis": "assignment rows tagged cause=poi_report_dropout (score/profiles.py)",
            })
        else:
            end = next((e for e in events if e["type"] == "link_restored" and e["uav"] == ev["uav"] and e["t_s"] > ev["t_s"]), None)
            in_window = _serves_changes(tel["assignments"], ev["t_s"], end["t_s"] if end else None)
            rec.update({
                "window_end_t_s": end["t_s"] if end else None,
                "recovery_time_s": None, "recovery_time_quality": "not_applicable",
                "recovery_time_reason": (
                    "degradation never breaks connectivity (BFS uses undegraded distances) and never triggers "
                    "reconfigure(); link_restored is a DEGRADE_DURATION_S timer, not a measured recovery -- "
                    "uavx/RULES_NOTES.md section 12"),
                "relay_reallocations": 0, "relay_reallocations_quality": "by_construction",
                "relay_reallocations_basis": (
                    "no reconfigure() call is caused by a degradation, so no fault-driven reallocation exists; "
                    "serves-set changes that happened to fall inside the window are counted separately below "
                    "and are all routine surveyor_drift"),
                "serves_changes_during_window": len(in_window),
                "serves_changes_during_window_causes": sorted({a["cause"] for a in in_window}),
            })
        per_event.append(rec)

    def val(name):
        m = metrics[name]
        return {"value": m.value, "unit": m.unit, "quality": m.quality, **({"reason": m.reason} if m.reason else {})}

    return {
        "source_run": "stage1_run.json",
        "conditions": tel["conditions"],
        "per_event": per_event,
        "fault_notes": tel.get("fault_notes", []),
        "run_level": {n: val(n) for n in (
            "packet_delivery_ratio", "latency_ms_mean", "latency_ms_p95", "connectivity_downtime_s",
            "relay_reallocations", "relay_reallocations_relay_failure", "relay_reallocations_relay_recharge",
            "relay_reallocations_poi_report_dropout", "relay_reallocations_surveyor_drift",
            "poi_completion", "priority_weighted_score")},
    }


def build_summary(tel: dict, em: dict, scenario_src: str, scenario_sha: str) -> str:
    c = tel["conditions"]
    lines = [
        "UAV-X Stage 1 -- A23 evidence run",
        "=" * 60,
        f"seed/config : n_uav={c['n_uav']}, r_comm_m={c['r_comm_m']}, event_set={c['event_set']}",
        f"scenario    : {scenario_src} (sha256 {scenario_sha[:16]}...) -> copied to scenario_config.json",
        f"duration    : {tel['total_s']:.1f} s simulated, tick {config.TICK_S} s",
        "",
        "PoIs (from the scenario file)",
    ]
    for p in tel["pois"]:
        lines.append(f"  PoI {p['id']}: priority {p['priority']:.0f}, ({p['x_m']:.0f}, {p['y_m']:.0f}) m")
    lines += ["", "Timeline"]
    rows: list[tuple[float, str]] = []
    for v in tel["visits"]:
        rows.append((v["arrive_t_s"], f"surveyor arrives at PoI {v['poi']}"))
        if v["reported_t_s"] is not None:
            rows.append((v["reported_t_s"], f"PoI {v['poi']} report SUCCEEDS, carried by relays {v['carrying_relay_ids']}"))
        else:
            rows.append((v["dwell_end_t_s"], f"PoI {v['poi']} report NOT delivered (timed out)"))
    for e in tel["events"]:
        extra = f" (selected from PoI {e['poi']}'s report at t={e['report_t_s']:.1f}s)" if "report_t_s" in e else ""
        rows.append((e["t_s"], f"EVENT {e['type']} on relay_{e['uav']}{extra}"))
    prev_path = None
    for ch in tel["chains"]:
        if ch["path"] != prev_path:
            path = " -> ".join(str(n) for n in ch["path"]) or "(no chain: surveyor disconnected)"
            rows.append((ch["t_s"], f"topology: {path}"))
            prev_path = ch["path"]
    for t, text in sorted(rows, key=lambda r: r[0]):
        lines.append(f"  t={t:7.1f}s  {text}")
    lines += ["", "Per-event metrics (A23: reported separately, never pooled)"]
    for rec in em["per_event"]:
        lines.append(f"  {rec['event']} on {rec['node']} at t={rec['t_s']:.1f}s")
        lines.append(f"    target in the report's carrying_relay_ids: {rec['target_in_carrying_relay_ids']}; "
                     f"on the live chain at fire tick: {rec['target_on_live_chain_at_fire_tick']}")
        rt = rec["recovery_time_s"]
        lines.append(f"    recovery_time_s    : {f'{rt:.1f}' if rt is not None else 'n/a'} ({rec['recovery_time_quality']})")
        lines.append(f"    relay_reallocations: {rec['relay_reallocations']} ({rec['relay_reallocations_quality']})")
    for n in em["fault_notes"]:
        lines.append(f"  NOTE [{n['event']}] {n['note']}")
    lines += ["", "Run-level metrics"]
    for name, m in em["run_level"].items():
        v = m["value"]
        lines.append(f"  {name:40} {v if not isinstance(v, float) else round(v, 4)} {m['unit']} ({m['quality']})")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the A23 evidence pack from one survey run (see module docstring).")
    parser.add_argument("--run", default=os.path.join(LOG_DIR, "stage1_run.json"))
    args = parser.parse_args()

    with open(args.run) as f:
        tel = json.load(f)
    scenario_src = tel["conditions"].get("scenario")
    if not scenario_src or scenario_src == "internal_random":
        raise SystemExit(f"{args.run} was not run from a scenario file (conditions.scenario={scenario_src!r})")
    out_dir = os.path.dirname(os.path.abspath(args.run))
    targets = {n: os.path.join(out_dir, n) for n in ("scenario_config.json", "event_metrics.json", "stage1_summary.txt")}
    existing = [p for p in targets.values() if os.path.exists(p)]
    if existing:
        raise SystemExit(f"refusing to overwrite existing evidence files: {existing}")

    shutil.copyfile(scenario_src, targets["scenario_config.json"])
    sha = _sha256(scenario_src)
    assert _sha256(targets["scenario_config.json"]) == sha

    em = build_event_metrics(tel)
    em["scenario_sha256"] = sha
    with open(targets["event_metrics.json"], "w") as f:
        json.dump(em, f, indent=2)
    with open(targets["stage1_summary.txt"], "w") as f:
        f.write(build_summary(tel, em, scenario_src, sha))
    for p in targets.values():
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
