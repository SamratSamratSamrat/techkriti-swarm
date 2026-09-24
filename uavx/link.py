"""Link-quality and connectivity-graph model for the UAV-X relay swarm.

The rulebook (page 5) says organizers "will provide ... communication
assumptions" but that model was not present in the document available when
this was written -- see uavx/RULES_NOTES.md. Until it arrives, link quality
is approximated as follows.

Physical reasoning: received RF power falls with the square of distance
(free-space path loss ~ 1/d^2). Converting that power to dB and comparing it
against a receiver's noise floor gives an effective maximum range where the
link stops working at all -- call it r_comm. Because the 1/d^2 curve is
steep, a real link is close to fully reliable for most of its range and then
degrades quickly near the edge, rather than decaying smoothly across the
whole range. We approximate that shape with a two-segment curve instead of
computing a full RF budget (transmit power, antenna gain, noise figure --
none of which the rulebook specifies):
  - d <= soft_band_start:            packet delivery ratio (PDR) = 1.0
  - soft_band_start < d <= r_comm:   PDR falls linearly 1.0 -> 0.0
  - d > r_comm:                      PDR = 0.0 (link is DOWN)
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from uavx import config

Point = tuple[float, float]


def distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def pdr(
    d: float,
    r_comm: float = config.R_COMM_M,
    soft_band_frac: float = config.LINK_SOFT_BAND_FRAC,
) -> float:
    """Packet delivery ratio for a single hop of length d metres."""
    if d < 0:
        raise ValueError("distance must be non-negative")
    soft_start = r_comm * soft_band_frac
    if d <= soft_start:
        return 1.0
    if d >= r_comm:
        return 0.0
    return 1.0 - (d - soft_start) / (r_comm - soft_start)


def link_quality(d: float, r_comm: float = config.R_COMM_M) -> float:
    """Alias for pdr() -- 'link quality' and 'PDR' are the same number here."""
    return pdr(d, r_comm)


def is_up(d: float, r_comm: float = config.R_COMM_M) -> bool:
    return d <= r_comm


@dataclass
class PathResult:
    connected: bool
    path: list[str]   # node ids, e.g. ["base", "relay_2", "poi_3"]
    path_pdr: float    # product of per-hop PDR along the path


def build_connectivity_graph(
    base_pos: Point,
    relay_positions: dict[int, Point],
    poi_positions: dict[int, Point],
    r_comm: float = config.R_COMM_M,
) -> dict[int, PathResult]:
    """Breadth-first search for the shortest (fewest-hop) all-UP path from
    base to every PoI, through any subset of relays.

    Returns one PathResult per PoI id. A PoI with no all-UP path gets
    connected=False and an empty path.
    """
    nodes: dict[str, Point] = {"base": base_pos}
    nodes.update({f"relay_{rid}": pos for rid, pos in relay_positions.items()})
    return {
        poi_id: _bfs_to_poi(nodes, poi_pos, r_comm)
        for poi_id, poi_pos in poi_positions.items()
    }


def _bfs_to_poi(nodes: dict[str, Point], poi_pos: Point, r_comm: float) -> PathResult:
    visited = {"base"}
    queue: deque[list[str]] = deque([["base"]])
    while queue:
        path = queue.popleft()
        last_pos = nodes[path[-1]]
        d_to_poi = distance(last_pos, poi_pos)
        if is_up(d_to_poi, r_comm):
            hop_pdrs = [pdr(distance(nodes[a], nodes[b]), r_comm) for a, b in zip(path, path[1:])]
            hop_pdrs.append(pdr(d_to_poi, r_comm))
            return PathResult(True, path + ["poi"], math.prod(hop_pdrs))
        for name, pos in nodes.items():
            if name in visited:
                continue
            if is_up(distance(last_pos, pos), r_comm):
                visited.add(name)
                queue.append(path + [name])
    return PathResult(False, [], 0.0)
