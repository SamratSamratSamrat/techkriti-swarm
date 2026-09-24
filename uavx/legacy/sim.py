"""Discrete-time simulator for the UAV-X relay-swarm proof of concept.

Ticks at config.TICK_HZ. Each tick:
  1. Move every active relay toward its assigned position at a capped speed.
  2. Recompute base->relay->PoI connectivity from CURRENT (not target)
     positions -- a relay still under way isn't linked yet.
  3. Log a link sample per PoI.
  4. For every connected PoI, maybe generate a data packet and roll its
     delivery / latency from the seeded RNG.
  5. Apply any scripted uav_fail / uav_recharge event due this tick, re-run
     uavx.reconfig, and start watching for the network to recover.
  6. If a recovery watch is active and weighted coverage is back to (at
     least) its pre-event level, log a "restored" event and stop watching.

Deterministic off config.RNG_SEED so telemetry is reproducible (Stage 1
asks for reproducibility -- rulebook page 4).

Run with:  python3 -m uavx.sim   (from the repo root)
"""

from __future__ import annotations

import datetime
import json
import math
import os

from uavx import config, link, schema
from uavx.assign import get_assigner
from uavx.reconfig import reconfigure

Point = tuple[float, float]


def _poi_scenario(rng) -> dict[int, dict]:
    pois = {}
    for i in range(config.N_POI):
        angle = rng.uniform(0, 2 * math.pi)
        dist = rng.uniform(config.R_COMM_M, config.ARENA_HALF_EXTENT_M)
        pois[i] = {
            "pos": (dist * math.cos(angle), dist * math.sin(angle)),
            "priority": rng.choice(config.POI_PRIORITY_LEVELS),
        }
    return pois


def _scripted_events(relay_ids: list[int], rng) -> tuple[list[dict], dict[int, float]]:
    # One permanent failure and one recharge round-trip, spaced through the
    # run -- enough to exercise reconfig.py without a full disturbance suite
    # (Stage 2 adds "hidden disturbances" per the rulebook; this is the
    # Stage 1 proof of concept).
    fail_relay, recharge_relay = rng.sample(relay_ids, 2)
    events = [
        {"t_s": config.RUN_DURATION_S * 0.25, "type": "uav_fail", "uav": fail_relay},
        {"t_s": config.RUN_DURATION_S * 0.55, "type": "uav_recharge", "uav": recharge_relay},
    ]
    returns = {recharge_relay: config.RUN_DURATION_S * 0.55 + config.RELAY_RECHARGE_S}
    return events, returns


def _served_by(relay_id: int, served_pois: dict[int, list[str]]) -> list[int]:
    node = f"relay_{relay_id}"
    return [poi_id for poi_id, path in served_pois.items() if node in path]


def _step_toward(pos: Point, target: Point, max_step: float) -> Point:
    dx, dy = target[0] - pos[0], target[1] - pos[1]
    d = math.hypot(dx, dy)
    if d <= max_step or d == 0:
        return target
    return (pos[0] + dx / d * max_step, pos[1] + dy / d * max_step)


def run(seed: int = config.RNG_SEED) -> dict:
    rng = config.new_rng(seed)
    base_pos: Point = (0.0, 0.0)
    relay_ids = list(range(1, config.N_UAV + 1))
    pois = _poi_scenario(rng)
    poi_positions = {i: p["pos"] for i, p in pois.items()}
    poi_priority = {i: p["priority"] for i, p in pois.items()}

    assigner = get_assigner()
    initial = assigner.assign(base_pos, poi_positions, poi_priority, relay_ids, config.R_COMM_M)
    relay_pos = dict(initial.relay_positions)
    relay_target = dict(initial.relay_positions)
    relay_status = {rid: "active" for rid in relay_ids}

    scripted, recharge_returns = _scripted_events(relay_ids, rng)
    scripted_by_tick: dict[int, list[dict]] = {}
    for e in scripted:
        scripted_by_tick.setdefault(round(e["t_s"] / config.TICK_S), []).append(e)
    returns_by_tick = {round(t / config.TICK_S): rid for rid, t in recharge_returns.items()}

    events: list[dict] = []
    packets: list[dict] = []
    link_samples: list[dict] = []
    assignments: list[dict] = [
        {"t_s": 0.0, "relay_uav": rid, "serves": _served_by(rid, initial.served_pois)}
        for rid in relay_ids
    ]
    poi_connected_ticks = {i: 0 for i in pois}

    watch: dict | None = None  # {"uav": id, "baseline_weight": float}
    n_ticks = int(config.RUN_DURATION_S * config.TICK_HZ)
    packet_period_ticks = max(1, round(config.TICK_HZ / config.PACKET_RATE_HZ))

    for tick in range(n_ticks):
        t_s = tick * config.TICK_S

        for rid in relay_ids:
            if relay_status[rid] == "active":
                relay_pos[rid] = _step_toward(
                    relay_pos[rid], relay_target[rid], config.MAX_RELAY_SPEED_MPS * config.TICK_S
                )

        live_relay_pos = {rid: relay_pos[rid] for rid in relay_ids if relay_status[rid] == "active"}
        graph = link.build_connectivity_graph(base_pos, live_relay_pos, poi_positions, config.R_COMM_M)

        for poi_id, result in graph.items():
            link_samples.append({"t_s": t_s, "poi": poi_id, "connected": result.connected})
            if not result.connected:
                continue
            poi_connected_ticks[poi_id] += 1
            if tick % packet_period_ticks == 0:
                delivered = rng.random() <= result.path_pdr
                latency = None
                if delivered:
                    hops = max(1, len(result.path) - 1)
                    latency = (
                        config.BASE_LATENCY_MS
                        + hops * config.PER_HOP_LATENCY_MS
                        + rng.uniform(-config.LATENCY_JITTER_MS, config.LATENCY_JITTER_MS)
                    )
                packets.append({"t_s": t_s, "poi": poi_id, "delivered": delivered, "latency_ms": latency})

        for e in scripted_by_tick.get(tick, []):
            uav = e["uav"]
            weight_before = sum(poi_priority[p] for p, r in graph.items() if r.connected)
            relay_status[uav] = "failed" if e["type"] == "uav_fail" else "recharging"
            active_ids = [rid for rid in relay_ids if relay_status[rid] == "active"]
            recon = reconfigure(base_pos, poi_positions, poi_priority, relay_pos, active_ids, config.R_COMM_M)
            for rid, pos in recon.relay_positions.items():
                relay_target[rid] = pos
            events.append({"t_s": t_s, "type": e["type"], "uav": uav})
            watch = {"uav": uav, "baseline_weight": weight_before}
            for rid in active_ids:
                assignments.append({"t_s": t_s, "relay_uav": rid, "serves": _served_by(rid, recon.served_pois)})

        returning = returns_by_tick.get(tick)
        if returning is not None:
            relay_status[returning] = "active"
            active_ids = [rid for rid in relay_ids if relay_status[rid] == "active"]
            recon = reconfigure(base_pos, poi_positions, poi_priority, relay_pos, active_ids, config.R_COMM_M)
            for rid, pos in recon.relay_positions.items():
                relay_target[rid] = pos
            for rid in active_ids:
                assignments.append({"t_s": t_s, "relay_uav": rid, "serves": _served_by(rid, recon.served_pois)})

        if watch is not None:
            current_weight = sum(poi_priority[p] for p, r in graph.items() if r.connected)
            if current_weight >= watch["baseline_weight"]:
                events.append({"t_s": t_s, "type": "restored", "uav": watch["uav"]})
                watch = None

    run_start = datetime.datetime.now(datetime.timezone.utc)
    telemetry = {
        "run_start_utc": run_start.isoformat(),
        "run_end_utc": (run_start + datetime.timedelta(seconds=config.RUN_DURATION_S)).isoformat(),
        "total_s": config.RUN_DURATION_S,
        "conditions": {
            "gps_class": "ideal_sim",  # PROVISIONAL: no GPS-accuracy noise modeled yet
            "wind": "none",             # PROVISIONAL: no wind modeled yet
            "n_uav": config.N_UAV,
            "r_comm_m": config.R_COMM_M,
        },
        "pois": [{"id": i, "priority": p["priority"]} for i, p in pois.items()],
        "packets": packets,
        "link_samples": link_samples,
        "assignments": assignments,
        "events": events,
        "poi_completion": [
            {"poi": i, "served_fraction": poi_connected_ticks[i] / n_ticks} for i in pois
        ],
    }
    schema.validate(telemetry)
    return telemetry


def main() -> None:
    telemetry = run()
    out_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"telemetry_seed{config.RNG_SEED}.json")
    with open(out_path, "w") as f:
        json.dump(telemetry, f, indent=2)

    delivered = sum(1 for p in telemetry["packets"] if p["delivered"])
    print(f"wrote {out_path}")
    print(f"  packets sent: {len(telemetry['packets'])}, delivered: {delivered}")
    print(f"  events: {telemetry['events']}")
    print(f"  poi_completion: {telemetry['poi_completion']}")


if __name__ == "__main__":
    main()
