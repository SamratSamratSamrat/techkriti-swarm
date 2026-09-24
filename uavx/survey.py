"""Surveyor mission simulation (Path C+).

ONE surveyor UAV flies a priority-ordered Point-of-Interest (PoI) route:
travel to the next PoI, dwell, report, move on. The remaining (N_UAV - 1)
UAVs are relays -- exactly the relays already built in uavx/assign.py and
uavx/reconfig.py -- except their connectivity objective is no longer a set
of static PoIs, it is the SURVEYOR'S CURRENT (moving) position.

This module is a thin adapter, not a rewrite: it feeds the surveyor's live
position into uavx.assign / uavx.reconfig as if it were a single, moving
"PoI" those modules already know how to reach. Two different calls into
that existing code do two different jobs, matching how sim.py already
separates them:
  - every tick, uavx.assign.get_assigner().assign() recomputes the ideal
    relay chain toward the surveyor's CURRENT position -- this is the
    "track a moving target" objective.
  - only when a uav_fail/uav_recharge event fires, uavx.reconfig.reconfigure()
    is called instead, because it specifically minimises how many relays
    move (see reconfig.py's docstring) -- reconfiguration still triggers on
    those events exactly as before, not on every tick's target drift.

At any moment there is exactly one active chain: base -> relay -> ... ->
surveyor. sim.py's ring/PoI mission is untouched -- this is a separate
mission built on the same link/assign/reconfig layer.

Run with:  python3 -m uavx.survey   (from the repo root)
"""

from __future__ import annotations

import datetime
import json
import math
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from uavx import config, link, schema
from uavx.assign import RelayAssigner, get_assigner
from uavx.reconfig import reconfigure

Point = tuple[float, float]

# Scripted-event timing, as fractions of the estimated mission duration --
# same style sim.py uses relative to its fixed RUN_DURATION_S (see
# uavx/sim.py's _scripted_events). Not a rulebook value; a Stage-1 demo knob.
_FAIL_FRACTION = 0.25
_RECHARGE_FRACTION = 0.55

# Which scripted fault events a run includes (run(events=...), CLI --events):
#   "all"      -- the default, unchanged: 25% uav_fail, 40% link_degraded,
#                 55% uav_recharge, 75% uav_dropout.
#   "a23-only" -- only ruling A23's two events (40% link_degraded, 75%
#                 uav_dropout); no uav_fail, no uav_recharge. Exists because
#                 stacking all four on a 3-relay (N_UAV=4) fleet left no relay
#                 alive after the dropout -- see uavx/RULES_NOTES.md section 12.
EVENT_SETS = ("all", "a23-only")


# ---------------------------------------------------------------------------
# Route planning
# ---------------------------------------------------------------------------


class RouteScheduler(ABC):
    """Swappable route-planning strategy. Computed ONCE at mission start --
    PoI positions/priorities are static for this scope, so no online
    re-planning is needed (see uavx/RULES_NOTES.md)."""

    @abstractmethod
    def plan(
        self,
        start_pos: Point,
        poi_positions: dict[int, Point],
        poi_priority: dict[int, float],
    ) -> list[int]:
        """Return the full visit order: a permutation of poi_positions'
        keys."""


class GreedyPriorityDistance(RouteScheduler):
    """At each step, visit the unvisited PoI maximizing
    priority / distance_from_current_position.

    PROVISIONAL formula -- the rulebook says the swarm must "prioritize
    newly emerging high-priority regions" (p.4) but gives no routing
    formula (checked against RULES_NOTES.md and the rulebook PDF directly;
    neither states one). This is the simplest reading of that requirement
    for a fixed, known PoI set.
    """

    _EPS = 1e-6  # avoids divide-by-zero for a PoI at the current position

    def plan(self, start_pos, poi_positions, poi_priority):
        remaining = set(poi_positions)
        route: list[int] = []
        current = start_pos
        while remaining:
            best = max(
                remaining,
                key=lambda pid: poi_priority[pid] / (link.distance(current, poi_positions[pid]) + self._EPS),
            )
            route.append(best)
            current = poi_positions[best]
            remaining.discard(best)
        return route


def get_scheduler(name: str = "greedy") -> RouteScheduler:
    if name == "greedy":
        return GreedyPriorityDistance()
    raise ValueError(f"unknown scheduler: {name}")


# ---------------------------------------------------------------------------
# Surveyor-as-a-moving-PoI adapter around link.py
# ---------------------------------------------------------------------------


@dataclass
class ChainResult:
    connected: bool
    path: list       # e.g. ["base", "relay_2", <surveyor_id>]
    link_quality: list[float]  # per-hop PDR; len == len(path) - 1


def _chain_to_surveyor(
    base_pos: Point,
    relay_positions: dict[int, Point],
    surveyor_pos: Point,
    surveyor_id: int,
    r_comm: float,
    degraded_relay: int | None = None,
    degrade_extra_distance_m: float = 0.0,
) -> ChainResult:
    """Treat the surveyor's current position as the sole "PoI" to reach,
    reusing link.build_connectivity_graph's BFS unchanged, then swap its
    generic trailing "poi" placeholder for the surveyor's real id and
    recover the per-hop PDRs (build_connectivity_graph only returns their
    product) from the same node positions it already used.

    degraded_relay / degrade_extra_distance_m (Task 2, additive -- default
    is "no degradation" so every existing caller is unaffected): when set,
    the ONE hop terminating at that relay (its incoming edge, from
    whichever node precedes it on the path) gets degrade_extra_distance_m
    added to its effective distance before the PDR lookup -- a worse link
    on that single hop, not a topology change. BFS connectivity itself is
    computed on the UNDEGRADED distances, so this only dips the reported
    link_quality for that hop; it never flips `connected` or reroutes the
    chain. See uavx/RULES_NOTES.md Task 2."""
    graph = link.build_connectivity_graph(base_pos, relay_positions, {surveyor_id: surveyor_pos}, r_comm)
    result = graph[surveyor_id]
    if not result.connected:
        return ChainResult(False, [], [])

    nodes: dict[str, Point] = {"base": base_pos}
    nodes.update({f"relay_{rid}": pos for rid, pos in relay_positions.items()})
    hop_nodes = result.path[:-1]  # drop BFS's generic trailing "poi" placeholder
    full_path = hop_nodes + [surveyor_id]
    degraded_name = f"relay_{degraded_relay}" if degraded_relay is not None else None

    def hop_pdr(a: str, b_pos: Point, b_name: str | None) -> float:
        d = link.distance(nodes[a], b_pos)
        if degraded_name is not None and b_name == degraded_name:
            d += degrade_extra_distance_m
        return link.pdr(d, r_comm)

    quality = [hop_pdr(a, nodes[b], b) for a, b in zip(hop_nodes, hop_nodes[1:])]
    quality.append(hop_pdr(hop_nodes[-1], surveyor_pos, None))
    return ChainResult(True, full_path, quality)


def _relay_ids_on_chain(path: list) -> list[int]:
    return [int(node.split("_", 1)[1]) for node in path if isinstance(node, str) and node.startswith("relay_")]


def _path_pdr(link_quality: list[float]) -> float:
    return math.prod(link_quality) if link_quality else 0.0


def _record_report_success(
    visits: list[dict], vi: int, t_s: float, tick: int, chain_path: list, report_log: list[tuple[int, int]],
) -> None:
    """Ruling A23 (see uavx/RULES_NOTES.md section 12): mark visit vi
    reported at this tick AND record which relays were on the live chain at
    this exact tick -- the "verifiably carrying live traffic" evidence the
    report-gated fault events select their target from."""
    visits[vi]["reported_t_s"] = t_s
    visits[vi]["carrying_relay_ids"] = _relay_ids_on_chain(chain_path)
    report_log.append((vi, tick))


def _next_report_target(
    report_log: list[tuple[int, int]],
    cursor: int,
    earliest_tick: int,
    visits: list[dict],
    relay_status: dict[int, str],
    event: str,
    fault_notes: list[dict],
    blocked_reason: str | None = None,
) -> tuple[tuple[int, list[int]] | None, int]:
    """Ruling A23 (see uavx/RULES_NOTES.md section 12): walk report_log
    forward from `cursor`, report to report, and return the first report
    that succeeded on or after earliest_tick AND still has an active relay
    among its carrying_relay_ids, as ((visit index, candidate relay ids),
    new cursor). Reports before earliest_tick are passed over silently;
    eligible-by-time reports that can't be used (no carrying relays, none
    still active, or blocked_reason set) are passed over WITH a
    fault_notes entry saying why. Returns (None, new cursor) if no usable
    report has succeeded yet."""
    while cursor < len(report_log):
        vi, report_tick = report_log[cursor]
        cursor += 1
        if report_tick < earliest_tick:
            continue
        visit = visits[vi]
        where = f"PoI {visit['poi']}'s report at t={visit['reported_t_s']:.1f}s"
        if blocked_reason is not None:
            fault_notes.append({"t_s": visit["reported_t_s"], "event": event, "note": f"{where} passed over: {blocked_reason}"})
            continue
        carrying = visit["carrying_relay_ids"]
        candidates = [rid for rid in carrying if relay_status[rid] == "active"]
        if not carrying:
            fault_notes.append({"t_s": visit["reported_t_s"], "event": event, "note": f"{where} passed over: no relay on its chain (surveyor linked straight to base)"})
            continue
        if not candidates:
            fault_notes.append({"t_s": visit["reported_t_s"], "event": event, "note": f"{where} passed over: none of its carrying relays {carrying} still active"})
            continue
        return (vi, candidates), cursor
    return None, cursor


# ---------------------------------------------------------------------------
# Scenario + movement helpers
# ---------------------------------------------------------------------------


def _poi_scenario(rng) -> dict[int, dict]:
    """Same distribution style as sim.py's _poi_scenario -- PoIs drawn
    between R_COMM_M and ARENA_HALF_EXTENT_M from the base so the demo run
    has real travel distance and real reconfig work to do."""
    pois = {}
    for i in range(config.N_POI):
        angle = rng.uniform(0, 2 * math.pi)
        dist = rng.uniform(config.R_COMM_M, config.ARENA_HALF_EXTENT_M)
        pois[i] = {
            "pos": (dist * math.cos(angle), dist * math.sin(angle)),
            "priority": rng.choice(config.POI_PRIORITY_LEVELS),
        }
    return pois


def load_scenario(path: str, base_pos: Point = (0.0, 0.0), r_comm: float = config.R_COMM_M) -> dict[int, dict]:
    """Read a fixed PoI layout from a scenario JSON file (e.g.
    claude/scenario_config.json) instead of drawing one with _poi_scenario().
    Shape read:

        {"base_pos": [x, y], "r_comm_m": 100.0,
         "pois": [{"id": "poi_1", "priority": 3, "pos": [x, y], ...}, ...]}

    Only id / priority / pos are used; any other per-PoI or top-level keys
    (hop_class, distance_from_base_m, _note, reachability_spread_check, ...)
    are metadata and ignored. "poi_<n>" ids become integer PoI id n (plain
    ints are accepted as-is), matching schema.PoiSpec.id. If the file states
    base_pos / r_comm_m, they must match this run's -- the layout was drawn
    against them. Anything malformed raises ValueError naming the problem; a
    bad file must never fall back to the internal random layout silently."""
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("pois"), list) or not data["pois"]:
        raise ValueError(f"{path}: expected a top-level non-empty 'pois' list")
    if "base_pos" in data and tuple(float(v) for v in data["base_pos"]) != tuple(base_pos):
        raise ValueError(f"{path}: base_pos {data['base_pos']} != this run's base {list(base_pos)}")
    if "r_comm_m" in data and float(data["r_comm_m"]) != float(r_comm):
        raise ValueError(f"{path}: r_comm_m {data['r_comm_m']} != this run's r_comm_m {r_comm}")
    pois: dict[int, dict] = {}
    for i, p in enumerate(data["pois"]):
        missing = [k for k in ("id", "priority", "pos") if k not in p]
        if missing:
            raise ValueError(f"{path}: pois[{i}] missing {missing}")
        raw_id = p["id"]
        if isinstance(raw_id, int):
            pid = raw_id
        elif isinstance(raw_id, str) and raw_id.startswith("poi_") and raw_id[4:].isdigit():
            pid = int(raw_id[4:])
        else:
            raise ValueError(f"{path}: pois[{i}] id {raw_id!r} is neither an int nor 'poi_<n>'")
        if pid in pois:
            raise ValueError(f"{path}: duplicate PoI id {raw_id!r}")
        pos = p["pos"]
        if not (isinstance(pos, list) and len(pos) == 2):
            raise ValueError(f"{path}: pois[{i}] pos must be [x, y], got {pos!r}")
        pois[pid] = {"pos": (float(pos[0]), float(pos[1])), "priority": float(p["priority"])}
    return pois


def _step_toward(pos: Point, target: Point, max_step: float) -> Point:
    dx, dy = target[0] - pos[0], target[1] - pos[1]
    d = math.hypot(dx, dy)
    if d <= max_step or d == 0:
        return target
    return (pos[0] + dx / d * max_step, pos[1] + dy / d * max_step)


def _estimate_duration_s(base_pos: Point, route: list[int], poi_positions: dict[int, Point]) -> float:
    """Rough total-mission-time estimate: travel time along the route plus
    DWELL_S per PoI plus one REPORT_TIMEOUT_S margin at the end (report
    timeouts for earlier PoIs run concurrently with later travel, so they
    are not summed per PoI). Used only to size the tick loop and to
    schedule the two demo failure/recharge events -- not written to
    telemetry."""
    pos = base_pos
    travel_s = 0.0
    for pid in route:
        travel_s += link.distance(pos, poi_positions[pid]) / config.SURVEYOR_SPEED_MPS
        pos = poi_positions[pid]
    return travel_s + len(route) * config.DWELL_S + config.REPORT_TIMEOUT_S


def _hold_isolated_relays(base_pos: Point, active_relay_ids: list[int], relay_pos: dict[int, Point], r_comm: float) -> set[int]:
    """Task 3 / ruling A19 (paper-only cosmetic consistency, NOT a failsafe
    implementation -- swarm/'s ArduPilot GCS-loss failsafe is a separate,
    Module-4/5-gated concern that cannot exist in this pure-Python sim; see
    uavx/RULES_NOTES.md): return the ids of relays that currently have NO
    path back to base at all (through any subset of the OTHER active
    relays). Reuses link.build_connectivity_graph unchanged, treating each
    relay in turn as the sole target to reach."""
    isolated: set[int] = set()
    for rid in active_relay_ids:
        others = {o: relay_pos[o] for o in active_relay_ids if o != rid}
        result = link.build_connectivity_graph(base_pos, others, {rid: relay_pos[rid]}, r_comm)[rid]
        if not result.connected:
            isolated.add(rid)
    return isolated


def _scan_hard_failure_candidates(
    base_pos: Point,
    surveyor_start: Point,
    surveyor_id: int,
    route: list[int],
    poi_positions: dict[int, Point],
    relay_ids: list[int],
    r_comm: float,
    assigner: RelayAssigner,
    max_ticks: int,
) -> list[tuple[int, int]]:
    """Task 1 (see uavx/RULES_NOTES.md): dry-run the surveyor/relay chase --
    no events fired -- to find (tick, relay_id) pairs where, if that
    on-chain relay failed right then, NO other currently-idle relay would
    already be within r_comm of it to instantly replace it. Recovery from
    one of these would require real repositioning, not a lucky instant
    swap.

    This duplicates run()'s movement/chain-computation steps (not a call
    into run() itself) because nothing has diverged before any event fires:
    same seed, same route, same r_comm, same assigner -> this dry run's
    positions exactly match what the real tick loop will compute up to
    whichever candidate tick ends up chosen, so it is safe to pick the
    result of a chosen (tick, relay) pair as the real run's fail event."""
    surveyor_pos = surveyor_start
    relay_pos = {rid: base_pos for rid in relay_ids}
    relay_target = dict(relay_pos)
    route_idx = 0
    dwell_started_at: float | None = None
    finished = False
    candidates: list[tuple[int, int]] = []

    for tick in range(max_ticks):
        t_s = tick * config.TICK_S
        target_poi = route[route_idx] if not finished else None
        target_pos = poi_positions[target_poi] if target_poi is not None else None

        if target_pos is not None and dwell_started_at is None:
            surveyor_pos = _step_toward(surveyor_pos, target_pos, config.SURVEYOR_SPEED_MPS * config.TICK_S)

        tracked = assigner.assign(base_pos, {surveyor_id: surveyor_pos}, {surveyor_id: 1.0}, relay_ids, r_comm)
        relay_target.update(tracked.relay_positions)
        isolated = _hold_isolated_relays(base_pos, relay_ids, relay_pos, r_comm)
        for rid in relay_ids:
            if rid in isolated:
                continue
            relay_pos[rid] = _step_toward(relay_pos[rid], relay_target[rid], config.MAX_RELAY_SPEED_MPS * config.TICK_S)

        chain = _chain_to_surveyor(base_pos, relay_pos, surveyor_pos, surveyor_id, r_comm)
        on_chain = set(_relay_ids_on_chain(chain.path))
        idle_pos = [relay_pos[rid] for rid in relay_ids if rid not in on_chain]
        for rid in on_chain:
            gap_pos = relay_pos[rid]
            if not any(link.distance(p, gap_pos) <= r_comm for p in idle_pos):
                candidates.append((tick, rid))

        if target_poi is not None:
            if dwell_started_at is None:
                if link.distance(surveyor_pos, target_pos) <= config.ARRIVAL_TOLERANCE_M:
                    dwell_started_at = t_s
            elif t_s - dwell_started_at >= config.DWELL_S:
                dwell_started_at = None
                route_idx += 1
                finished = route_idx >= len(route)

    return candidates


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


def run(
    seed: int = config.RNG_SEED,
    scheduler: RouteScheduler | None = None,
    n_uav: int = config.N_UAV,
    r_comm_m: float = config.R_COMM_M,
    events: str = "all",
    scenario_path: str | None = None,
) -> dict:
    """scenario_path (additive, default None = the internal seeded random
    layout, unchanged): read the PoI layout from this JSON file via
    load_scenario() instead. The path used is recorded in
    telemetry.conditions.scenario.

    events (additive, default "all" so existing callers are unaffected):
    which scripted fault events run -- see EVENT_SETS and
    uavx/RULES_NOTES.md section 12 ("--events a23-only").

    n_uav / r_comm_m (additive, default = config's standard values so
    existing callers are unaffected): override the fleet size / comms
    radius for THIS run only, without touching config.py's global values.
    Task 1's hard-failure search (see uavx/RULES_NOTES.md) may require
    tightening these below the standard demo baseline to force a genuine
    gap instead of an instant swap -- that tightening, if any was needed,
    is recorded in the returned telemetry's conditions.n_uav/r_comm_m so it
    is never silently mistaken for the standard config."""
    rng = config.new_rng(seed)
    scheduler = scheduler or get_scheduler()
    assigner: RelayAssigner = get_assigner()
    r_comm = r_comm_m

    base_pos: Point = (0.0, 0.0)
    uav_ids = list(range(1, n_uav + 1))
    surveyor_id = uav_ids[0]
    relay_ids = uav_ids[1:]

    pois = load_scenario(scenario_path, base_pos, r_comm) if scenario_path is not None else _poi_scenario(rng)
    poi_positions = {i: p["pos"] for i, p in pois.items()}
    poi_priority = {i: p["priority"] for i, p in pois.items()}

    route = scheduler.plan(base_pos, poi_positions, poi_priority)

    run_duration_s = _estimate_duration_s(base_pos, route, poi_positions) * (1 + config.SURVEY_DURATION_MARGIN_FRAC)
    n_ticks = int(run_duration_s * config.TICK_HZ)
    packet_period_ticks = max(1, round(config.TICK_HZ / config.PACKET_RATE_HZ))

    # Task 1 (see uavx/RULES_NOTES.md): don't just pick a fixed fraction of
    # the run and grab whatever relay happens to be on-chain then (the old
    # behaviour) -- that's "on the chain" but not necessarily a real stress
    # test if an idle relay is already sitting in range to instantly plug
    # the gap. Dry-run the chase first to find (tick, relay) pairs where
    # that ISN'T true, then fire the real event at the candidate closest to
    # the originally-intended timing.
    if events not in EVENT_SETS:
        raise ValueError(f"unknown events set {events!r} (choices: {EVENT_SETS})")
    event_set = events  # kept under its own name: `events` is reused below for the events[] log
    fail_tick: int | None = None
    fail_relay: int | None = None
    recharge_tick: int | None = None
    if event_set == "all":
        target_fail_tick = round(run_duration_s * _FAIL_FRACTION / config.TICK_S)
        hard_candidates = _scan_hard_failure_candidates(
            base_pos, base_pos, surveyor_id, route, poi_positions, relay_ids, r_comm, assigner, n_ticks,
        )
        if not hard_candidates:
            raise RuntimeError(
                f"no hard-failure candidate found for n_uav={n_uav}, r_comm_m={r_comm} -- every on-chain relay "
                "is trivially replaceable by an idle relay at every tick under this config. Per Task 1 "
                "(uavx/RULES_NOTES.md), tighten redundancy for THIS run specifically -- pass a smaller n_uav "
                "and/or r_comm_m -- rather than accepting an instant-swap 'failure'."
            )
        fail_tick, fail_relay = min(hard_candidates, key=lambda tr: abs(tr[0] - target_fail_tick))
        recharge_tick = max(round(run_duration_s * _RECHARGE_FRACTION / config.TICK_S), fail_tick + 1)
    # "a23-only": no uav_fail / uav_recharge at all (fail_tick/recharge_tick
    # stay None, so their blocks below never fire), and the A23 events are
    # scheduled at their plain fractions -- nothing earlier to wait behind.
    after_fail_tick = fail_tick + 1 if fail_tick is not None else 0
    # Scheduled relative to fail_tick (not recharge_tick): DEGRADE_FRACTION
    # (0.40) sits between _FAIL_FRACTION (0.25) and _RECHARGE_FRACTION
    # (0.55) by design, so the degrade window lands in the network's
    # recovered-and-stable stretch after the hard failure but before the
    # recharge event further thins redundancy for the rest of the run --
    # scheduling it after recharge risked the chain being permanently down
    # for the remainder (RELAY_RECHARGE_S=300s outlives most runs), leaving
    # no active on-chain relay to ever degrade. See uavx/RULES_NOTES.md.
    degrade_earliest_tick = max(round(run_duration_s * config.DEGRADE_FRACTION / config.TICK_S), after_fail_tick)
    degrade_duration_ticks = max(1, round(config.DEGRADE_DURATION_S / config.TICK_S))
    # Ruling A23 (see uavx/RULES_NOTES.md section 12): the 75% full-dropout
    # event. Also kept after fail_tick -- a dropout changes relay_status, and
    # _scan_hard_failure_candidates' dry run is only valid if nothing has
    # diverged before fail_tick.
    dropout_earliest_tick = max(round(run_duration_s * config.DROPOUT_FRACTION / config.TICK_S), after_fail_tick)

    surveyor_pos = base_pos
    relay_pos: dict[int, Point] = {rid: base_pos for rid in relay_ids}
    relay_target: dict[int, Point] = dict(relay_pos)
    relay_status: dict[int, str] = {rid: "active" for rid in relay_ids}

    route_idx = 0
    dwell_started_at: float | None = None
    finished = False
    visit_index_by_poi: dict[int, int] = {}
    pending_reports: list[tuple[int, float]] = []  # (visit index, report deadline t_s)

    watch: dict | None = None  # {"uav": relay_id, "baseline_connected": bool}
    fault_window_cause: str | None = None  # Task 3 -- mirrors `watch`'s lifetime (see uavx/RULES_NOTES.md)
    scheduled_returns: dict[int, int] = {}  # tick -> relay_id becoming active again
    fail_done = False
    recharge_done = False
    degrade_active = False
    degrade_fire_done = False
    degraded_relay: int | None = None
    degrade_end_tick: int | None = None
    dropout_fire_done = False
    # Ruling A23 (see uavx/RULES_NOTES.md section 12): every report success,
    # in order, as (visit index, tick it succeeded on). The link_degraded and
    # uav_dropout events each walk this list with their own cursor, report
    # to report, so a report whose carrying relays can't be used just moves
    # that event on to the next report instead of skipping it.
    report_log: list[tuple[int, int]] = []
    degrade_report_cursor = 0
    dropout_report_cursor = 0
    fault_notes: list[dict] = []

    positions: list[dict] = []
    chains: list[dict] = []
    visits: list[dict] = []
    packets: list[dict] = []
    link_samples: list[dict] = []
    assignments: list[dict] = []
    events: list[dict] = []

    for tick in range(n_ticks):
        t_s = tick * config.TICK_S
        target_poi = route[route_idx] if not finished else None
        target_pos = poi_positions[target_poi] if target_poi is not None else None

        # 1. Move the surveyor toward its current target PoI (not while dwelling).
        if target_pos is not None and dwell_started_at is None:
            surveyor_pos = _step_toward(surveyor_pos, target_pos, config.SURVEYOR_SPEED_MPS * config.TICK_S)

        # 2. Every tick, chase the surveyor's CURRENT position: recompute the
        #    ideal relay chain toward it and move relays incrementally
        #    toward that target at capped speed. This is the "connectivity
        #    target is the surveyor's live position" adapter.
        active_relay_ids = [rid for rid in relay_ids if relay_status[rid] == "active"]
        tracked = assigner.assign(base_pos, {surveyor_id: surveyor_pos}, {surveyor_id: 1.0}, active_relay_ids, r_comm)
        relay_target.update(tracked.relay_positions)
        # Task 3 / ruling A19 (paper-only cosmetic consistency, NOT a
        # failsafe implementation -- see uavx/RULES_NOTES.md): a relay with
        # no path back to base at all holds its current position instead of
        # continuing to reposition, until a path is restored.
        isolated = _hold_isolated_relays(base_pos, active_relay_ids, relay_pos, r_comm)
        for rid in active_relay_ids:
            if rid in isolated:
                continue
            relay_pos[rid] = _step_toward(relay_pos[rid], relay_target[rid], config.MAX_RELAY_SPEED_MPS * config.TICK_S)

        live_relay_pos = {rid: relay_pos[rid] for rid in active_relay_ids}
        chain = _chain_to_surveyor(
            base_pos, live_relay_pos, surveyor_pos, surveyor_id, r_comm,
            degraded_relay=degraded_relay if degrade_active else None,
            degrade_extra_distance_m=config.DEGRADE_EXTRA_DISTANCE_M,
        )
        on_chain = set(_relay_ids_on_chain(chain.path))  # this tick's PRE-event baseline -- see Task 3 below

        # 3. Log every UAV's position this tick, including failed/recharging
        #    relays (parked wherever they were when they went down).
        positions.append({"t_s": t_s, "uav": "base", "x_m": base_pos[0], "y_m": base_pos[1], "role": "base"})
        positions.append({"t_s": t_s, "uav": surveyor_id, "x_m": surveyor_pos[0], "y_m": surveyor_pos[1], "role": "surveyor"})
        for rid in relay_ids:
            positions.append({"t_s": t_s, "uav": rid, "x_m": relay_pos[rid][0], "y_m": relay_pos[rid][1], "role": "relay"})

        # 4. Log the single active chain this tick.
        chains.append({"t_s": t_s, "path": chain.path, "link_quality": chain.link_quality})

        # 5. link_samples / packets, tagged to whichever PoI is current
        #    (travel target, or the one being dwelled/report-checked).
        if target_poi is not None:
            link_samples.append({"t_s": t_s, "poi": target_poi, "connected": chain.connected})
            if chain.connected and tick % packet_period_ticks == 0:
                delivered = rng.random() <= _path_pdr(chain.link_quality)
                latency = None
                if delivered:
                    hops = max(1, len(chain.path) - 1)
                    latency = (
                        config.BASE_LATENCY_MS
                        + hops * config.PER_HOP_LATENCY_MS
                        + rng.uniform(-config.LATENCY_JITTER_MS, config.LATENCY_JITTER_MS)
                    )
                packets.append({"t_s": t_s, "poi": target_poi, "delivered": delivered, "latency_ms": latency})

        # 6. Scripted uav_fail / uav_recharge / link_degraded events.
        status_changed = False
        change_reason: str | None = None  # Task 3 (see uavx/RULES_NOTES.md): which
        # kind of status change is driving any reconfigure() call below --
        # feeds the reallocation `cause` tag on assignments logged after it.
        if tick == fail_tick and not fail_done:
            # Task 1 (see uavx/RULES_NOTES.md): fail_relay was pre-selected
            # by _scan_hard_failure_candidates as a genuine gap -- nothing
            # diverges before this tick, so it is still on-chain now.
            assert fail_relay in _relay_ids_on_chain(chain.path), "hard-failure candidate drifted off-chain before firing"
            relay_status[fail_relay] = "failed"
            events.append({"t_s": t_s, "type": "uav_fail", "uav": fail_relay})
            watch = {"uav": fail_relay, "baseline_connected": chain.connected, "event_tick": tick}
            status_changed = True
            change_reason = "relay_failure"
            fail_done = True

        if tick == recharge_tick and not recharge_done:
            still_active = [rid for rid in relay_ids if relay_status[rid] == "active"]
            candidates = [rid for rid in _relay_ids_on_chain(chain.path) if rid in still_active] or still_active
            if candidates:
                chosen = rng.choice(candidates)
                relay_status[chosen] = "recharging"
                events.append({"t_s": t_s, "type": "uav_recharge", "uav": chosen})
                watch = {"uav": chosen, "baseline_connected": chain.connected, "event_tick": tick}
                scheduled_returns[tick + round(config.RELAY_RECHARGE_S / config.TICK_S)] = chosen
                status_changed = True
                change_reason = "relay_recharge"
            recharge_done = True

        # Task 2 (see uavx/RULES_NOTES.md): comms-degradation event --
        # distinct from uav_fail/uav_recharge, the relay stays "active" and
        # on the chain the whole time; only its hop's link_quality dips for
        # the window. Purely an annotation marker in events[] -- see
        # RULES_NOTES.md on why score/'s current _recovery_time reader
        # won't pick these up.
        # Ruling A23 (see uavx/RULES_NOTES.md section 12): the target is no
        # longer "any on-chain relay at the first tick >= 40%" -- it is a
        # relay that carried the first PoI report to succeed at or after
        # degrade_earliest_tick (that visit's carrying_relay_ids). Report
        # successes are logged in step 9/10, after this step, so a report
        # that succeeds on tick T fires the event here on tick T+1: at or
        # just after report completion, not strictly during it.
        if not degrade_fire_done:
            picked, degrade_report_cursor = _next_report_target(
                report_log, degrade_report_cursor, degrade_earliest_tick, visits, relay_status,
                "link_degraded", fault_notes,
            )
            if picked is not None:
                vi, candidates = picked
                degraded_relay = rng.choice(candidates)
                degrade_active = True
                degrade_fire_done = True
                degrade_end_tick = tick + degrade_duration_ticks
                events.append({
                    "t_s": t_s, "type": "link_degraded", "uav": degraded_relay,
                    "poi": visits[vi]["poi"], "report_t_s": visits[vi]["reported_t_s"],
                })
        elif degrade_active and tick >= degrade_end_tick:
            events.append({"t_s": t_s, "type": "link_restored", "uav": degraded_relay})
            degrade_active = False

        # Ruling A23 (see uavx/RULES_NOTES.md section 12): full dropout of a
        # relay that carried the first report to succeed at or after
        # dropout_earliest_tick. Same removal path as uav_fail above
        # (relay_status -> "failed", then reconfigure() below), with its own
        # cause tag. Never fires while another fault's recovery watch is
        # still open (it would overwrite that fault's pending "restored") --
        # such a report is logged to fault_notes and passed over for the
        # next one, rather than firing late against a stale report.
        if not dropout_fire_done:
            picked, dropout_report_cursor = _next_report_target(
                report_log, dropout_report_cursor, dropout_earliest_tick, visits, relay_status,
                "uav_dropout", fault_notes,
                blocked_reason=f"relay {watch['uav']}'s recovery watch still open" if watch is not None else None,
            )
            if picked is not None:
                vi, candidates = picked
                dropped = rng.choice(candidates)
                relay_status[dropped] = "failed"
                events.append({
                    "t_s": t_s, "type": "uav_dropout", "uav": dropped,
                    "poi": visits[vi]["poi"], "report_t_s": visits[vi]["reported_t_s"],
                })
                watch = {"uav": dropped, "baseline_connected": chain.connected, "event_tick": tick}
                status_changed = True
                change_reason = change_reason or "poi_report_dropout"
                dropout_fire_done = True

        returning = scheduled_returns.get(tick)
        if returning is not None:
            relay_status[returning] = "active"
            status_changed = True
            change_reason = change_reason or "relay_recharge"  # a relay coming back FROM recharge is the same cause category

        if status_changed:
            # Reconfiguration triggers on uav_fail/uav_recharge exactly as
            # before -- reconfig.reconfigure() (not the continuous tracker
            # above) so relays still doing useful work keep their position.
            active_relay_ids_after_change = [rid for rid in relay_ids if relay_status[rid] == "active"]
            recon = reconfigure(base_pos, {surveyor_id: surveyor_pos}, {surveyor_id: 1.0}, relay_pos, active_relay_ids_after_change, r_comm)
            relay_target.update(recon.relay_positions)
            # Task 3 (see uavx/RULES_NOTES.md): a fault event just triggered
            # reconfig.reconfigure() -- start (or extend) a "fault window":
            # every assignments row logged from here until this event's
            # matching "restored" fires (the SAME ground-truth recovery
            # state `watch` below already tracks, not a re-guess from
            # timing) is tagged with the cause that opened it, since any
            # serves-set change during that window is a consequence of this
            # reconfigure() call -- rather than searching for the single
            # exact tick reconfig.py's chain-membership prediction flips
            # (chain.path this tick reflects the PRE-event baseline, so the
            # observable serves change actually lands 1+ ticks later, once
            # relay_pos has physically moved enough to change who's on the
            # live chain).
            fault_window_cause = change_reason or "relay_recharge"

        # 7. assignments -- one row per active relay per tick (the ORIGINAL
        #    active_relay_ids from step 2, before any status change this
        #    tick -- same relay set the pre-Task-3 code logged, so
        #    relay_reallocations' row count/meaning is unchanged). `cause`
        #    is "surveyor_drift" (the routine per-tick continuous-tracker
        #    re-solve, driven only by the surveyor moving) outside any fault
        #    window, else whichever fault opened the current window.
        for rid in active_relay_ids:
            serves = [target_poi] if rid in on_chain and target_poi is not None else []
            cause = fault_window_cause if fault_window_cause is not None else "surveyor_drift"
            assignments.append({"t_s": t_s, "relay_uav": rid, "serves": serves, "cause": cause})

        # 8. Watch for recovery back to (at least) the pre-event connectivity.
        #    Skip the event's own tick -- `chain` there was computed BEFORE
        #    the failure/recharge was applied (it's this tick's baseline),
        #    so comparing against it would trivially "restore" immediately
        #    on every event. Only later ticks' freshly-recomputed chains
        #    reflect the real post-event state.
        if watch is not None and tick > watch["event_tick"]:
            cur = 1.0 if chain.connected else 0.0
            baseline = 1.0 if watch["baseline_connected"] else 0.0
            if cur >= baseline:
                events.append({"t_s": t_s, "type": "restored", "uav": watch["uav"]})
                watch = None
                fault_window_cause = None  # Task 3: fault window closes exactly when recovery is confirmed

        # 9. Arrival / dwell / report state machine. Movement never blocks
        #    on connectivity -- the surveyor moves on once DWELL_S elapses
        #    regardless of report status.
        if target_poi is not None:
            if dwell_started_at is None:
                if link.distance(surveyor_pos, target_pos) <= config.ARRIVAL_TOLERANCE_M:
                    dwell_started_at = t_s
                    visits.append({
                        "poi": target_poi, "arrive_t_s": t_s, "dwell_end_t_s": None,
                        "reported_t_s": None, "carrying_relay_ids": None,
                    })
                    visit_index_by_poi[target_poi] = len(visits) - 1
            elif t_s - dwell_started_at >= config.DWELL_S:
                vi = visit_index_by_poi[target_poi]
                visits[vi]["dwell_end_t_s"] = t_s
                if chain.connected:
                    _record_report_success(visits, vi, t_s, tick, chain.path, report_log)
                else:
                    pending_reports.append((vi, t_s + config.REPORT_TIMEOUT_S))
                dwell_started_at = None
                route_idx += 1
                finished = route_idx >= len(route)

        # 10. Keep re-checking connectivity for any PoI that dwelled but
        #     didn't report yet, until its REPORT_TIMEOUT_S elapses.
        still_pending = []
        for vi, deadline in pending_reports:
            if chain.connected:
                _record_report_success(visits, vi, t_s, tick, chain.path, report_log)
            elif t_s < deadline:
                still_pending.append((vi, deadline))
            # else: timed out -- stays unreported (served_fraction 0.5) permanently.
        pending_reports = still_pending

    # Ruling A23 (see uavx/RULES_NOTES.md section 12): a report-gated fault
    # that never fired is logged plainly, never silently skipped and never
    # forced onto an arbitrary tick. (The dropout in particular can
    # legitimately fail to fire: after the 55% recharge event the chain may
    # stay down for the rest of the run -- RELAY_RECHARGE_S=300 outlives most
    # runs -- so no report may succeed after 75% at all.)
    for event, fired, earliest_tick, cursor, pct in (
        ("link_degraded", degrade_fire_done, degrade_earliest_tick, degrade_report_cursor, config.DEGRADE_FRACTION),
        ("uav_dropout", dropout_fire_done, dropout_earliest_tick, dropout_report_cursor, config.DROPOUT_FRACTION),
    ):
        if fired:
            continue
        label = "dropout" if event == "uav_dropout" else "degradation"
        after = f"after {pct:.0%} mission time (t>={earliest_tick * config.TICK_S:.1f}s)"
        if not any(rtick >= earliest_tick for _, rtick in report_log):
            note = f"{label} event could not fire -- no report succeeded {after}"
        elif any(rtick >= earliest_tick for _, rtick in report_log[cursor:]):
            note = f"{label} event could not fire -- a report succeeded {after} but on the run's final tick, so the run ended before the event could fire"
        else:
            note = f"{label} event could not fire -- every report that succeeded {after} was passed over (see this event's earlier fault_notes)"
        fault_notes.append({"t_s": None, "event": event, "note": note})

    poi_completion = []
    for pid in pois:
        if pid in visit_index_by_poi:
            reported = visits[visit_index_by_poi[pid]]["reported_t_s"] is not None
            frac = 1.0 if reported else 0.5
        else:
            frac = 0.0  # route never reached it before the run ended
        poi_completion.append({"poi": pid, "served_fraction": frac})

    run_start = datetime.datetime.now(datetime.timezone.utc)
    total_s = n_ticks * config.TICK_S
    telemetry = {
        "run_start_utc": run_start.isoformat(),
        "run_end_utc": (run_start + datetime.timedelta(seconds=total_s)).isoformat(),
        "total_s": total_s,
        "conditions": {
            "gps_class": "ideal_sim",  # PROVISIONAL: no GPS-accuracy noise modeled yet
            "wind": "none",             # PROVISIONAL: no wind modeled yet
            "n_uav": n_uav,
            "r_comm_m": r_comm,
            "event_set": event_set,  # additive: "all" | "a23-only" -- see EVENT_SETS
            "scenario": scenario_path or "internal_random",  # additive: PoI layout source
        },
        "pois": [
            {"id": i, "priority": p["priority"], "x_m": p["pos"][0], "y_m": p["pos"][1]} for i, p in pois.items()
        ],
        "packets": packets,
        "link_samples": link_samples,
        "assignments": assignments,
        "events": events,
        "poi_completion": poi_completion,
        "positions": positions,
        "chains": chains,
        "visits": visits,
        "fault_notes": fault_notes,
        "fault_schedule": {
            "link_degraded_earliest_t_s": degrade_earliest_tick * config.TICK_S,
            "uav_dropout_earliest_t_s": dropout_earliest_tick * config.TICK_S,
        },
    }
    schema.validate(telemetry)
    return telemetry


# ---------------------------------------------------------------------------
# A26 (24 Sep 2026): rulebook-accurate dynamic-spawn mission model.
# See uavx/RULES_NOTES.md section 13 for the full ruling this implements:
# base 75m outside a 1000x1000m arena (config.BASE_OFFSET_M/ARENA_HALF_EXTENT_M),
# 10 PoIs spawning at random positions AND times over a fixed 45-minute
# mission (config.N_POI_A26/MISSION_DURATION_S), a 10s hard detect-to-report
# deadline (config.MAX_DETECT_TO_REPORT_S). Deliberately a SEPARATE function
# from run() rather than a rewrite of it -- run()'s precomputed-route path is
# what CP1-CP6's evidence pack was just verified against (R_COMM_M 150->100
# migration); this shares run()'s helpers but keeps its own tick loop so
# that already-verified path stays untouched.
# ---------------------------------------------------------------------------


def _dynamic_poi_scenario(rng) -> dict[int, dict]:
    """A26: PoIs spawn randomly in BOTH position and time, per the
    rulebook's mission-constraints slide ("Number of POIs - 10. Spawned
    randomly (position and the time of spawning)"). Position: uniform
    anywhere in the full ARENA_HALF_EXTENT_M box -- the rulebook's diagram
    spreads PoIs across the whole 1000x1000m operational area, and most of
    that box is outside relay range of the settled 4-aircraft fleet by
    construction (see config.py's BASE_OFFSET_M warning) -- a deliberate
    ruling (spread across the whole arena, most fail), not an oversight; see
    uavx/RULES_NOTES.md section 13. Time: uniform across
    [0, MISSION_DURATION_S) -- the rulebook gives no distribution, this is
    our call, documented rather than silently assumed."""
    pois = {}
    for i in range(config.N_POI_A26):
        x = rng.uniform(-config.ARENA_HALF_EXTENT_M, config.ARENA_HALF_EXTENT_M)
        y = rng.uniform(-config.ARENA_HALF_EXTENT_M, config.ARENA_HALF_EXTENT_M)
        pois[i] = {
            "pos": (x, y),
            "priority": rng.choice(config.POI_PRIORITY_LEVELS),
            "spawn_t_s": rng.uniform(0.0, config.MISSION_DURATION_S),
        }
    return pois


def _pick_next_target(current_pos: Point, candidates: dict[int, Point], priority: dict[int, float]) -> int | None:
    """One-shot greedy priority/distance pick -- same formula as
    GreedyPriorityDistance.plan() (PROVISIONAL, simplest reading of
    "prioritize ... high-priority regions", no rulebook formula given), but
    choosing ONE next target from whatever is currently known instead of a
    full up-front permutation -- A26's mission can't precompute a route once
    PoIs are allowed to appear mid-mission."""
    if not candidates:
        return None
    eps = 1e-6
    return max(candidates, key=lambda pid: priority[pid] / (link.distance(current_pos, candidates[pid]) + eps))


def run_dynamic(
    seed: int = config.RNG_SEED,
    n_uav: int = config.N_UAV,
    r_comm_m: float = config.R_COMM_M,
    base_pos: Point | None = None,
) -> dict:
    """A26: the rulebook-accurate mission -- see this section's module
    comment above. Only the link_degraded/uav_dropout report-gated fault
    events (ruling A23's methodology, reused unchanged); does NOT support
    the "all" event set (uav_fail/uav_recharge) -- _scan_hard_failure_candidates
    needs a precomputed route, which doesn't exist once PoIs can appear
    mid-mission. Flagged as a known gap, not built tonight."""
    rng = config.new_rng(seed)
    assigner: RelayAssigner = get_assigner()
    r_comm = r_comm_m

    base_pos = base_pos or (-(config.ARENA_HALF_EXTENT_M + config.BASE_OFFSET_M), 0.0)
    uav_ids = list(range(1, n_uav + 1))
    surveyor_id = uav_ids[0]
    relay_ids = uav_ids[1:]

    pois = _dynamic_poi_scenario(rng)
    poi_positions = {i: p["pos"] for i, p in pois.items()}
    poi_priority = {i: p["priority"] for i, p in pois.items()}
    poi_spawn_t = {i: p["spawn_t_s"] for i, p in pois.items()}

    n_ticks = int(config.MISSION_DURATION_S * config.TICK_HZ)
    packet_period_ticks = max(1, round(config.TICK_HZ / config.PACKET_RATE_HZ))

    degrade_earliest_tick = round(config.MISSION_DURATION_S * config.DEGRADE_FRACTION / config.TICK_S)
    degrade_duration_ticks = max(1, round(config.DEGRADE_DURATION_S / config.TICK_S))
    dropout_earliest_tick = round(config.MISSION_DURATION_S * config.DROPOUT_FRACTION / config.TICK_S)

    surveyor_pos = base_pos
    relay_pos: dict[int, Point] = {rid: base_pos for rid in relay_ids}
    relay_target: dict[int, Point] = dict(relay_pos)
    relay_status: dict[int, str] = {rid: "active" for rid in relay_ids}

    known_pois: set[int] = set()
    visited: set[int] = set()
    pending_pois: set[int] = set()  # bugfix (A26 first draft): a PoI whose
    # report is deferred (visited/dwelled, not yet connected, deadline not
    # yet blown) must NOT be immediately re-pickable as the next target --
    # without this, the surveyor re-arrived at the same still-pending PoI
    # every tick (distance already 0) and re-dwelled it over and over
    # instead of moving on, inflating `visits` (34 rows for 10 PoIs in the
    # first test run) and double-tracking the same report in report_log.
    current_target: int | None = None
    dwell_started_at: float | None = None
    visit_index_by_poi: dict[int, int] = {}
    pending_reports: list[tuple[int, float]] = []  # (visit index, hard deadline t_s)

    watch: dict | None = None
    fault_window_cause: str | None = None
    degrade_active = False
    degrade_fire_done = False
    degraded_relay: int | None = None
    degrade_end_tick: int | None = None
    dropout_fire_done = False
    report_log: list[tuple[int, int]] = []
    degrade_report_cursor = 0
    dropout_report_cursor = 0
    fault_notes: list[dict] = []
    poi_spawns: list[dict] = []

    positions: list[dict] = []
    chains: list[dict] = []
    visits: list[dict] = []
    packets: list[dict] = []
    link_samples: list[dict] = []
    assignments: list[dict] = []
    events: list[dict] = []

    for tick in range(n_ticks):
        t_s = tick * config.TICK_S

        # 0. A26: any PoI whose spawn time has arrived becomes visible.
        for pid, spawn_t in poi_spawn_t.items():
            if pid not in known_pois and t_s >= spawn_t:
                known_pois.add(pid)
                poi_spawns.append({"poi": pid, "t_s": t_s})

        # 0b. A26: pick a target if idle -- greedy priority/distance among
        # known-and-unvisited PoIs. Never interrupts an in-progress travel
        # leg or dwell (PROVISIONAL simplification -- avoids target-switch
        # thrashing every time a higher-priority PoI spawns mid-flight).
        if current_target is None:
            available = {pid: poi_positions[pid] for pid in known_pois if pid not in visited and pid not in pending_pois}
            current_target = _pick_next_target(surveyor_pos, available, poi_priority)
        target_pos = poi_positions[current_target] if current_target is not None else None

        # 1. Move the surveyor toward its current target (not while dwelling
        #    or idle with no target).
        if target_pos is not None and dwell_started_at is None:
            surveyor_pos = _step_toward(surveyor_pos, target_pos, config.SURVEYOR_SPEED_MPS_A26 * config.TICK_S)

        # 2. Chase the surveyor's live position with the relay chain -- same as run().
        active_relay_ids = [rid for rid in relay_ids if relay_status[rid] == "active"]
        tracked = assigner.assign(base_pos, {surveyor_id: surveyor_pos}, {surveyor_id: 1.0}, active_relay_ids, r_comm)
        relay_target.update(tracked.relay_positions)
        isolated = _hold_isolated_relays(base_pos, active_relay_ids, relay_pos, r_comm)
        for rid in active_relay_ids:
            if rid in isolated:
                continue
            relay_pos[rid] = _step_toward(relay_pos[rid], relay_target[rid], config.MAX_RELAY_SPEED_MPS_A26 * config.TICK_S)

        live_relay_pos = {rid: relay_pos[rid] for rid in active_relay_ids}
        chain = _chain_to_surveyor(
            base_pos, live_relay_pos, surveyor_pos, surveyor_id, r_comm,
            degraded_relay=degraded_relay if degrade_active else None,
            degrade_extra_distance_m=config.DEGRADE_EXTRA_DISTANCE_M,
        )
        on_chain = set(_relay_ids_on_chain(chain.path))

        # 3. Log positions.
        positions.append({"t_s": t_s, "uav": "base", "x_m": base_pos[0], "y_m": base_pos[1], "role": "base"})
        positions.append({"t_s": t_s, "uav": surveyor_id, "x_m": surveyor_pos[0], "y_m": surveyor_pos[1], "role": "surveyor"})
        for rid in relay_ids:
            positions.append({"t_s": t_s, "uav": rid, "x_m": relay_pos[rid][0], "y_m": relay_pos[rid][1], "role": "relay"})

        # 4. Log the chain.
        chains.append({"t_s": t_s, "path": chain.path, "link_quality": chain.link_quality})

        # 5. link_samples / packets, tagged to the current target.
        if current_target is not None:
            link_samples.append({"t_s": t_s, "poi": current_target, "connected": chain.connected})
            if chain.connected and tick % packet_period_ticks == 0:
                delivered = rng.random() <= _path_pdr(chain.link_quality)
                latency = None
                if delivered:
                    hops = max(1, len(chain.path) - 1)
                    latency = (
                        config.BASE_LATENCY_MS
                        + hops * config.PER_HOP_LATENCY_MS
                        + rng.uniform(-config.LATENCY_JITTER_MS, config.LATENCY_JITTER_MS)
                    )
                packets.append({"t_s": t_s, "poi": current_target, "delivered": delivered, "latency_ms": latency})

        # 6. Ruling A23's report-gated fault events, reused unchanged --
        #    fractions now apply to the fixed MISSION_DURATION_S rather than
        #    an estimated duration (see this function's docstring).
        if not degrade_fire_done:
            picked, degrade_report_cursor = _next_report_target(
                report_log, degrade_report_cursor, degrade_earliest_tick, visits, relay_status,
                "link_degraded", fault_notes,
            )
            if picked is not None:
                vi, candidates = picked
                degraded_relay = rng.choice(candidates)
                degrade_active = True
                degrade_fire_done = True
                degrade_end_tick = tick + degrade_duration_ticks
                events.append({
                    "t_s": t_s, "type": "link_degraded", "uav": degraded_relay,
                    "poi": visits[vi]["poi"], "report_t_s": visits[vi]["reported_t_s"],
                })
        elif degrade_active and tick >= degrade_end_tick:
            events.append({"t_s": t_s, "type": "link_restored", "uav": degraded_relay})
            degrade_active = False

        status_changed = False
        change_reason: str | None = None
        if not dropout_fire_done:
            picked, dropout_report_cursor = _next_report_target(
                report_log, dropout_report_cursor, dropout_earliest_tick, visits, relay_status,
                "uav_dropout", fault_notes,
                blocked_reason=f"relay {watch['uav']}'s recovery watch still open" if watch is not None else None,
            )
            if picked is not None:
                vi, candidates = picked
                dropped = rng.choice(candidates)
                relay_status[dropped] = "failed"
                events.append({
                    "t_s": t_s, "type": "uav_dropout", "uav": dropped,
                    "poi": visits[vi]["poi"], "report_t_s": visits[vi]["reported_t_s"],
                })
                watch = {"uav": dropped, "baseline_connected": chain.connected, "event_tick": tick}
                status_changed = True
                change_reason = "poi_report_dropout"
                dropout_fire_done = True

        if status_changed:
            active_relay_ids_after_change = [rid for rid in relay_ids if relay_status[rid] == "active"]
            recon = reconfigure(base_pos, {surveyor_id: surveyor_pos}, {surveyor_id: 1.0}, relay_pos, active_relay_ids_after_change, r_comm)
            relay_target.update(recon.relay_positions)
            fault_window_cause = change_reason

        # 7. assignments.
        for rid in active_relay_ids:
            serves = [current_target] if rid in on_chain and current_target is not None else []
            cause = fault_window_cause if fault_window_cause is not None else "surveyor_drift"
            assignments.append({"t_s": t_s, "relay_uav": rid, "serves": serves, "cause": cause})

        # 8. Recovery watch, same pattern as run().
        if watch is not None and tick > watch["event_tick"]:
            cur = 1.0 if chain.connected else 0.0
            baseline = 1.0 if watch["baseline_connected"] else 0.0
            if cur >= baseline:
                events.append({"t_s": t_s, "type": "restored", "uav": watch["uav"]})
                watch = None
                fault_window_cause = None

        # 9. A26 arrival / dwell / report state machine -- DWELL_S_A26 (3s)
        #    and a hard deadline anchored to ARRIVAL time (arrive_t_s +
        #    MAX_DETECT_TO_REPORT_S = 10s total), not run()'s DWELL_S=15/
        #    REPORT_TIMEOUT_S=20 (impossible under the rulebook's 10s
        #    deadline -- see config.py's DWELL_S_A26 comment).
        if current_target is not None:
            if dwell_started_at is None:
                if link.distance(surveyor_pos, target_pos) <= config.ARRIVAL_TOLERANCE_M:
                    dwell_started_at = t_s
                    visits.append({
                        "poi": current_target, "arrive_t_s": t_s, "dwell_end_t_s": None,
                        "reported_t_s": None, "carrying_relay_ids": None,
                        # Additive (A26): when this PoI's report stops being
                        # on time -- lets the UI mark it "missed" without
                        # knowing MAX_DETECT_TO_REPORT_S itself.
                        "report_deadline_t_s": t_s + config.MAX_DETECT_TO_REPORT_S,
                    })
                    visit_index_by_poi[current_target] = len(visits) - 1
            elif t_s - dwell_started_at >= config.DWELL_S_A26:
                vi = visit_index_by_poi[current_target]
                visits[vi]["dwell_end_t_s"] = t_s
                deadline = visits[vi]["arrive_t_s"] + config.MAX_DETECT_TO_REPORT_S
                if chain.connected:
                    _record_report_success(visits, vi, t_s, tick, chain.path, report_log)
                    visited.add(current_target)
                elif t_s < deadline:
                    pending_reports.append((vi, deadline))
                    pending_pois.add(current_target)
                else:
                    visited.add(current_target)  # deadline already blown -- unreported, permanently
                dwell_started_at = None
                current_target = None  # A26: triggers re-plan next tick (step 0b)

        # 10. Keep re-checking connectivity for any PoI dwelled but not yet
        #     reported, until its hard deadline (from ARRIVAL, not dwell-end) elapses.
        still_pending = []
        for vi, deadline in pending_reports:
            if chain.connected:
                _record_report_success(visits, vi, t_s, tick, chain.path, report_log)
                visited.add(visits[vi]["poi"])
                pending_pois.discard(visits[vi]["poi"])
            elif t_s < deadline:
                still_pending.append((vi, deadline))
            else:
                visited.add(visits[vi]["poi"])  # timed out -- unreported, permanently
                pending_pois.discard(visits[vi]["poi"])
        pending_reports = still_pending

    for event, fired, earliest_tick, cursor, pct in (
        ("link_degraded", degrade_fire_done, degrade_earliest_tick, degrade_report_cursor, config.DEGRADE_FRACTION),
        ("uav_dropout", dropout_fire_done, dropout_earliest_tick, dropout_report_cursor, config.DROPOUT_FRACTION),
    ):
        if fired:
            continue
        label = "dropout" if event == "uav_dropout" else "degradation"
        after = f"after {pct:.0%} mission time (t>={earliest_tick * config.TICK_S:.1f}s)"
        if not any(rtick >= earliest_tick for _, rtick in report_log):
            note = f"{label} event could not fire -- no report succeeded {after}"
        elif any(rtick >= earliest_tick for _, rtick in report_log[cursor:]):
            note = f"{label} event could not fire -- a report succeeded {after} but on the run's final tick, so the run ended before the event could fire"
        else:
            note = f"{label} event could not fire -- every report that succeeded {after} was passed over (see this event's earlier fault_notes)"
        fault_notes.append({"t_s": None, "event": event, "note": note})

    poi_completion = []
    for pid in pois:
        if pid in visit_index_by_poi:
            reported = visits[visit_index_by_poi[pid]]["reported_t_s"] is not None
            frac = 1.0 if reported else 0.5
        elif pid in known_pois:
            frac = 0.0  # spawned, but never reached before the mission ended
        else:
            frac = None  # A26: never spawned at all during the 45-min mission -- excluded, not a failure
        if frac is not None:
            poi_completion.append({"poi": pid, "served_fraction": frac})

    run_start = datetime.datetime.now(datetime.timezone.utc)
    total_s = n_ticks * config.TICK_S
    telemetry = {
        "run_start_utc": run_start.isoformat(),
        "run_end_utc": (run_start + datetime.timedelta(seconds=total_s)).isoformat(),
        "total_s": total_s,
        "conditions": {
            "gps_class": "ideal_sim",
            "wind": "none",
            "n_uav": n_uav,
            "r_comm_m": r_comm,
            "event_set": "a23-only",
            "scenario": "dynamic_a26",
            "seed": seed,  # A26: the scenario is generated from this, not read from a file -- needed to reproduce it
            "dynamic_spawn": True,
            "base_pos": list(base_pos),
            "arena_half_extent_m": config.ARENA_HALF_EXTENT_M,
            "base_offset_m": config.BASE_OFFSET_M,
            # A26: the farthest a full-fleet relay chain can reach from base
            # (n_uav-1 relays + the surveyor = n_uav hops, each <= r_comm) --
            # written here so the UI draws it from the telemetry instead of
            # re-deriving link physics itself.
            "max_chain_reach_m": n_uav * r_comm,
            "mission_duration_s": config.MISSION_DURATION_S,
        },
        "pois": [
            {"id": i, "priority": p["priority"], "x_m": p["pos"][0], "y_m": p["pos"][1], "spawn_t_s": p["spawn_t_s"]}
            for i, p in pois.items()
        ],
        "packets": packets,
        "link_samples": link_samples,
        "assignments": assignments,
        "events": events,
        "poi_completion": poi_completion,
        "positions": positions,
        "chains": chains,
        "visits": visits,
        "fault_notes": fault_notes,
        "fault_schedule": {
            "link_degraded_earliest_t_s": degrade_earliest_tick * config.TICK_S,
            "uav_dropout_earliest_t_s": dropout_earliest_tick * config.TICK_S,
        },
        "poi_spawns": poi_spawns,
    }
    schema.validate(telemetry)
    return telemetry


def _print_resilience_report(telemetry: dict) -> None:
    """Task 4 (see uavx/RULES_NOTES.md): report the hard-failure and
    comms-degradation events honestly, straight from the telemetry that was
    just written -- no numbers are adjusted after the fact."""
    r_comm = telemetry["conditions"]["r_comm_m"]
    relay_ids_all = sorted({p["uav"] for p in telemetry["positions"] if p["role"] == "relay"})
    pos_by_tick_uav: dict[float, dict] = {}
    for p in telemetry["positions"]:
        pos_by_tick_uav.setdefault(p["t_s"], {})[p["uav"]] = (p["x_m"], p["y_m"])
    chain_by_tick = {c["t_s"]: c for c in telemetry["chains"]}
    events = telemetry["events"]
    restored_t_by_uav = {e["uav"]: e["t_s"] for e in events if e["type"] == "restored"}

    for fe in (e for e in events if e["type"] == "uav_fail"):
        t, failed_uav = fe["t_s"], fe["uav"]
        chain = chain_by_tick.get(t)
        on_chain = set(_relay_ids_on_chain(chain["path"])) if chain else set()
        tick_pos = pos_by_tick_uav.get(t, {})
        fail_pos = tick_pos.get(failed_uav)
        idle_ids = [rid for rid in relay_ids_all if rid != failed_uav and rid not in on_chain]
        idle_nearby = [rid for rid in idle_ids if link.distance(tick_pos[rid], fail_pos) <= r_comm]
        print(f"  relay {failed_uav} FAILED at t={t:.1f}s")
        print(
            f"    idle relay already in comms range at fire time: "
            f"{'YES ' + str(idle_nearby) + ' -- NOT a real stress test' if idle_nearby else 'NO -- genuine gap'}"
        )
        restored_t = restored_t_by_uav.get(failed_uav)
        if restored_t is not None:
            dt = restored_t - t
            print(f"    chain restored at t={restored_t:.1f}s ({dt:.1f}s / {dt * config.TICK_HZ:.0f} ticks later)")
        else:
            print(f"    chain NOT restored by end of run (total_s={telemetry['total_s']:.1f})")

    degraded = [e for e in events if e["type"] == "link_degraded"]
    restored = [e for e in events if e["type"] == "link_restored"]
    for de in degraded:
        matching_restore = next((e for e in restored if e["uav"] == de["uav"] and e["t_s"] > de["t_s"]), None)
        end_str = f"{matching_restore['t_s']:.1f}s" if matching_restore else "NOT restored by end of run"
        print(f"  comms DEGRADED on relay {de['uav']}'s incoming hop: t={de['t_s']:.1f}s -> {end_str}")
        if "report_t_s" in de:
            print(f"    selected from PoI {de['poi']}'s report at t={de['report_t_s']:.1f}s (ruling A23)")

    # Ruling A23 (see uavx/RULES_NOTES.md section 12): the report-gated
    # full dropout, with its own recovery time -- never pooled with the
    # uav_fail recovery above.
    for de in (e for e in events if e["type"] == "uav_dropout"):
        t = de["t_s"]
        print(f"  relay {de['uav']} DROPPED OUT at t={t:.1f}s, selected from PoI {de['poi']}'s report at t={de['report_t_s']:.1f}s")
        restored_e = next((e for e in events if e["type"] == "restored" and e["uav"] == de["uav"] and e["t_s"] > t), None)
        if restored_e is not None:
            dt = restored_e["t_s"] - t
            print(f"    chain restored at t={restored_e['t_s']:.1f}s ({dt:.1f}s / {dt * config.TICK_HZ:.0f} ticks later)")
        else:
            print(f"    chain NOT restored by end of run (total_s={telemetry['total_s']:.1f})")

    for fn in telemetry.get("fault_notes", []):
        when = f"t={fn['t_s']:.1f}s: " if fn["t_s"] is not None else ""
        print(f"  NOTE [{fn['event']}] {when}{fn['note']}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Run the Path C+ surveyor mission (see module docstring).")
    parser.add_argument("--seed", type=int, default=config.RNG_SEED)
    parser.add_argument(
        "--n-uav", type=int, default=config.N_UAV,
        help="override fleet size for THIS run only (Task 1 redundancy tightening -- see uavx/RULES_NOTES.md)",
    )
    parser.add_argument(
        "--r-comm-m", type=float, default=config.R_COMM_M,
        help="override comms radius for THIS run only (Task 1 redundancy tightening -- see uavx/RULES_NOTES.md)",
    )
    parser.add_argument(
        "--events", choices=EVENT_SETS, default="all",
        help='which scripted fault events run: "all" (default) or "a23-only" (40%% degrade + 75%% dropout only; see uavx/RULES_NOTES.md section 12)',
    )
    parser.add_argument(
        "--scenario", type=str, default=None,
        help="read the PoI layout from this JSON file (see load_scenario) instead of the internal seeded random layout",
    )
    parser.add_argument(
        "--out", type=str, default=None,
        help="output filename inside uavx/logs/ (default: survey_telemetry_seed<seed>.json)",
    )
    parser.add_argument(
        "--dynamic-spawn", action="store_true",
        help="A26: run the rulebook-accurate mission (base 75m outside a 1000x1000m arena, "
        "10 PoIs spawning at random positions/times, fixed 45-min duration, 10s report deadline) "
        "instead of the static-scenario mission. Ignores --events/--scenario (a23-only fault "
        "events only, no precomputed scenario file -- see run_dynamic()'s docstring).",
    )
    args = parser.parse_args()

    if args.dynamic_spawn:
        telemetry = run_dynamic(seed=args.seed, n_uav=args.n_uav, r_comm_m=args.r_comm_m)
    else:
        telemetry = run(seed=args.seed, n_uav=args.n_uav, r_comm_m=args.r_comm_m, events=args.events, scenario_path=args.scenario)
    out_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(out_dir, exist_ok=True)
    out_name = args.out or f"survey_telemetry_seed{args.seed}.json"
    out_path = os.path.join(out_dir, out_name)
    with open(out_path, "w") as f:
        json.dump(telemetry, f, indent=2)

    priority_by_poi = {p["id"]: p["priority"] for p in telemetry["pois"]}
    mode = "dynamic-spawn (A26), events=a23-only" if args.dynamic_spawn else f"events={args.events}"
    print(f"wrote {out_path}  (n_uav={args.n_uav}, r_comm_m={args.r_comm_m}, {mode})")
    print(f"  {len(telemetry['visits'])} PoIs visited, in order:")
    for v in telemetry["visits"]:
        if v["reported_t_s"] is not None:
            outcome = f"reported t={v['reported_t_s']:.1f}s"
        elif v["dwell_end_t_s"] is None:
            # A26: the mission's fixed 45-min clock can end mid-dwell -- the
            # last PoI was reached but never finished. Not a timeout.
            outcome = "arrived, mission ended mid-dwell"
        else:
            outcome = "visited, UNREPORTED (timed out)"
        dwell_end = f"{v['dwell_end_t_s']:.1f}s" if v["dwell_end_t_s"] is not None else "--"
        print(
            f"    PoI {v['poi']} (priority {priority_by_poi[v['poi']]}): "
            f"arrive t={v['arrive_t_s']:.1f}s, dwell_end t={dwell_end} -> {outcome}"
        )
    delivered = sum(1 for p in telemetry["packets"] if p["delivered"])
    print(f"  packets sent: {len(telemetry['packets'])}, delivered: {delivered}")
    print(f"  events: {telemetry['events']}")
    _print_resilience_report(telemetry)


if __name__ == "__main__":
    main()
