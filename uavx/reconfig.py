"""Re-run relay assignment after a uav_fail / uav_recharge event.

Rulebook requirement (page 4): "Reconfigure the network when communication
degrades or UAVs fail or return to home for recharging." The Autonomy metric
(page 5) specifically counts "relay reallocations" and "recovery time", so
the reconfig strategy here explicitly prefers keeping relays that are still
doing useful work in place over a full re-solve from scratch.
"""

from __future__ import annotations

from dataclasses import dataclass

from uavx import config, link
from uavx.assign import AssignmentResult, RelayAssigner, get_assigner

Point = tuple[float, float]


@dataclass
class ReconfigResult:
    relay_positions: dict[int, Point]
    served_pois: dict[int, list[str]]
    unserved_weight: float
    moved_relay_ids: list[int]   # relays whose position changed -- the "reallocation count"


def reconfigure(
    base_pos: Point,
    poi_positions: dict[int, Point],
    poi_priority: dict[int, float],
    current_positions: dict[int, Point],
    available_relay_ids: list[int],
    r_comm: float = config.R_COMM_M,
    assigner: RelayAssigner | None = None,
) -> ReconfigResult:
    assigner = assigner or get_assigner()

    # 1. Which currently-available relays are still plugged into the
    #    backbone (within one hop of base, or of another kept relay)?
    #    Those keep their exact position -- moving them would be a wasted
    #    reallocation for coverage they already provide. Order-dependent by
    #    construction (a heuristic, not a global optimum -- fine for
    #    Stage 1).
    kept: dict[int, Point] = {}
    for rid in available_relay_ids:
        pos = current_positions.get(rid)
        if pos is not None and _on_backbone(base_pos, pos, kept, r_comm):
            kept[rid] = pos

    # 2. Whatever PoI weight `kept` alone doesn't cover, hand to the
    #    assigner using only the NOT-kept relays -- so it only moves what it
    #    has to.
    free_relay_ids = [rid for rid in available_relay_ids if rid not in kept]
    filled: AssignmentResult = assigner.assign(base_pos, poi_positions, poi_priority, free_relay_ids, r_comm)

    final_positions = {**kept, **filled.relay_positions}
    graph = link.build_connectivity_graph(base_pos, final_positions, poi_positions, r_comm)
    served = {pid: r.path for pid, r in graph.items() if r.connected}
    unserved_weight = sum(w for pid, w in poi_priority.items() if pid not in served)

    moved = [
        rid for rid in free_relay_ids
        if current_positions.get(rid) != final_positions.get(rid)
    ]

    return ReconfigResult(final_positions, served, unserved_weight, moved)


def _on_backbone(base_pos: Point, pos: Point, other_kept: dict[int, Point], r_comm: float) -> bool:
    if link.is_up(link.distance(base_pos, pos), r_comm):
        return True
    return any(link.is_up(link.distance(pos, p), r_comm) for p in other_kept.values())
