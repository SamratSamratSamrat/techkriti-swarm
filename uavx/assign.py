"""Relay placement: given PoI positions + priorities and a pool of relay
UAVs, decide where each relay should sit so the base station reaches the
highest-weighted set of PoIs.

RelayAssigner is an interface on purpose -- the heuristic below is
deliberately simple (Stage 1 asks for "feasibility ... and reproducibility",
not an optimal solver; see uavx/RULES_NOTES.md). Swapping in something
smarter later (an ILP, a potential-field placer, etc.) means writing a new
class here, not touching link.py, reconfig.py, or sim.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from uavx import config, link

Point = tuple[float, float]


@dataclass
class AssignmentResult:
    relay_positions: dict[int, Point]     # every relay_id handed in, placed or parked at base
    served_pois: dict[int, list[str]]     # poi_id -> connectivity path, for PoIs that ARE connected
    unserved_weight: float                # total priority weight NOT connected


class RelayAssigner(ABC):
    @abstractmethod
    def assign(
        self,
        base_pos: Point,
        poi_positions: dict[int, Point],
        poi_priority: dict[int, float],
        relay_ids: list[int],
        r_comm: float = config.R_COMM_M,
    ) -> AssignmentResult:
        ...


class GreedyChainAssigner(RelayAssigner):
    """Stage-1 heuristic:

    1. Visit PoIs highest-priority first ("serve highest-priority-first").
    2. Track "reachable nodes" -- base plus every relay placed so far.
    3. For a PoI that isn't reachable yet, take the next unused relay and
       place it one hop (r_comm * soft_band_frac, for margin) out from
       whichever reachable node is closest to that PoI, moving toward it.
       This extends the base -> ... -> PoI chain by exactly one hop.
    4. Keep extending that same chain with more relays until the PoI is
       reached or relays run out, then move to the next unserved PoI.
    5. Before starting a fresh chain, PoIs already brought into range by the
       relay(s) just placed are skipped ("cover the most unserved weighted
       PoIs" a single relay placement often reaches several nearby PoIs at
       once, for free).

    This is greedy, not optimal -- it can spend a hop reaching a PoI that a
    global solver would route differently. That trade-off is intentional for
    a proof-of-concept; see RelayAssigner for how to replace it.
    """

    def assign(self, base_pos, poi_positions, poi_priority, relay_ids, r_comm=config.R_COMM_M):
        step = r_comm * config.LINK_SOFT_BAND_FRAC
        pool = list(relay_ids)
        relay_positions: dict[int, Point] = {}
        order = sorted(poi_positions, key=lambda pid: -poi_priority.get(pid, 0.0))

        def reachable_nodes() -> dict[str, Point]:
            nodes = {"base": base_pos}
            nodes.update({f"relay_{rid}": pos for rid, pos in relay_positions.items()})
            return nodes

        def is_served(poi_id: int) -> bool:
            pos = poi_positions[poi_id]
            return any(link.is_up(link.distance(p, pos), r_comm) for p in reachable_nodes().values())

        for poi_id in order:
            while not is_served(poi_id) and pool:
                nodes = reachable_nodes()
                poi_pos = poi_positions[poi_id]
                _src_name, src_pos = min(nodes.items(), key=lambda kv: link.distance(kv[1], poi_pos))
                d = link.distance(src_pos, poi_pos)
                dx, dy = _unit(src_pos, poi_pos)
                hop = min(step, d)
                relay_positions[pool.pop(0)] = (src_pos[0] + dx * hop, src_pos[1] + dy * hop)

        for rid in relay_ids:
            relay_positions.setdefault(rid, base_pos)

        graph = link.build_connectivity_graph(base_pos, relay_positions, poi_positions, r_comm)
        served_pois = {pid: r.path for pid, r in graph.items() if r.connected}
        unserved_weight = sum(w for pid, w in poi_priority.items() if pid not in served_pois)
        return AssignmentResult(relay_positions, served_pois, unserved_weight)


def _unit(a: Point, b: Point) -> Point:
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = (dx * dx + dy * dy) ** 0.5
    if n == 0:
        return (0.0, 0.0)
    return (dx / n, dy / n)


def get_assigner(name: str = "greedy") -> RelayAssigner:
    if name == "greedy":
        return GreedyChainAssigner()
    raise ValueError(f"unknown assigner: {name}")
