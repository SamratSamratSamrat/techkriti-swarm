"""Reads uavx telemetry JSON and extracts exactly what ui/static/panels/
relay_map.js needs: the FULL recorded positions/chains time series (every
tick in the file, for replay/scrubbing), and pois[] for context markers.

Read-only, standard-library only. Never imports pymavlink and never
imports anything from swarm/ or uavx/ -- this module only ever opens the
telemetry JSON file named on the command line. See ui/DATA_CONTRACT.md for
the exact field contract and ui/README.md for why (ruling A16/A17: this UI
must be provably incapable of touching a vehicle, and immune to the
port-sharing bug that caused a GCS-failsafe RTL in an earlier sprint --
SWARM_OPERATIONS.md A13 documents that incident).
"""

from __future__ import annotations

import json
from typing import Any


def load_telemetry(path: str) -> dict:
    """Read + parse the telemetry JSON file. Raises on missing/malformed
    file -- the caller (ui/server.py) is responsible for the stale-state
    fallback; this function stays honest about failure rather than
    swallowing it."""
    with open(path, "r") as f:
        return json.load(f)


def empty_state() -> dict[str, Any]:
    """The all-unavailable state, used both as extract_state()'s base case
    and by ui/server.py as the very-first-read-ever fallback (before any
    good read has happened to fall back to)."""
    return {
        "t_s": None,
        "positions": None,
        "positions_available": False,
        "chains": None,
        "chains_available": False,
        "pois": None,
        "pois_available": False,
        "events": None,
        "events_available": False,
        # Additive (A26): only present for run_dynamic() telemetry. Static
        # runs leave all three unavailable and the panel draws exactly as
        # before.
        "visits": None,
        "visits_available": False,
        "arena": None,
        "arena_available": False,
    }


def extract_state(telemetry: dict) -> dict:
    """Extract exactly three things from a telemetry document, each
    independently optional -- a field missing (or the wrong shape) makes
    that piece "unavailable" in the output, it never crashes and never
    fabricates a value for it.

    - positions: EVERY valid entry in telemetry["positions"] (every UAV,
      every recorded tick) -- the full time series, for panel-side replay.
      Was "only the latest tick's rows" before; the panel now owns picking
      which tick to display (see ui/DATA_CONTRACT.md).
    - chains: EVERY valid entry in telemetry["chains"] (one row per tick:
      t_s/path/link_quality) -- likewise the full series, was "only the
      latest tick" before.
    - pois: id/x_m/y_m only, from telemetry["pois"] -- context markers
      (static, no time dimension -- unchanged).

    `t_s` in the returned state is still the single LATEST tick across
    whichever series are available -- that's a freshness indicator for the
    status bar ("how far has this file's recording gotten"), not the tick
    the panel happens to be displaying right now.
    """
    state = empty_state()
    if not isinstance(telemetry, dict):
        return state

    latest_ticks: list[float] = []

    positions = telemetry.get("positions")
    if isinstance(positions, list) and positions:
        valid = [p for p in positions if isinstance(p, dict) and "t_s" in p and "x_m" in p and "y_m" in p and "uav" in p]
        if valid:
            state["positions"] = valid
            state["positions_available"] = True
            latest_ticks.append(max(p["t_s"] for p in valid))

    chains = telemetry.get("chains")
    if isinstance(chains, list) and chains:
        valid = [
            {"t_s": c["t_s"], "path": c["path"], "link_quality": c["link_quality"]}
            for c in chains
            if isinstance(c, dict) and "t_s" in c and "path" in c and "link_quality" in c
        ]
        if valid:
            state["chains"] = valid
            state["chains_available"] = True
            latest_ticks.append(max(c["t_s"] for c in valid))

    if latest_ticks:
        state["t_s"] = max(latest_ticks)

    pois = telemetry.get("pois")
    if isinstance(pois, list):
        clean = []
        for p in pois:
            if not (isinstance(p, dict) and "id" in p and "x_m" in p and "y_m" in p):
                continue
            row = {"id": p["id"], "x_m": p["x_m"], "y_m": p["y_m"]}
            # Additive (A26): passed through only when present, so static
            # runs' PoIs are byte-for-byte what they were before.
            if isinstance(p.get("spawn_t_s"), (int, float)):
                row["spawn_t_s"] = p["spawn_t_s"]
                if isinstance(p.get("priority"), (int, float)):
                    row["priority"] = p["priority"]
            clean.append(row)
        if clean:
            state["pois"] = clean
            state["pois_available"] = True

    # visits (additive, A26): only the three timestamps the panel needs to
    # colour a PoI (visited / reported / missed) at any replay tick. Only
    # passed through when the run recorded report deadlines -- i.e. a
    # run_dynamic() run -- so static-run behaviour is unchanged.
    visits = telemetry.get("visits")
    if isinstance(visits, list):
        clean = [
            {
                "poi": v["poi"],
                "arrive_t_s": v["arrive_t_s"],
                "reported_t_s": v.get("reported_t_s"),
                "report_deadline_t_s": v["report_deadline_t_s"],
            }
            for v in visits
            if isinstance(v, dict) and "poi" in v and "arrive_t_s" in v and isinstance(v.get("report_deadline_t_s"), (int, float))
        ]
        if clean:
            state["visits"] = clean
            state["visits_available"] = True

    # arena (additive, A26): the operational-area geometry, read straight
    # from telemetry.conditions -- the panel never imports uavx/config.py.
    cond = telemetry.get("conditions")
    if isinstance(cond, dict) and isinstance(cond.get("arena_half_extent_m"), (int, float)):
        state["arena"] = {
            "half_extent_m": cond["arena_half_extent_m"],
            "base_offset_m": cond.get("base_offset_m"),
            "max_chain_reach_m": cond.get("max_chain_reach_m"),
            "mission_duration_s": cond.get("mission_duration_s"),
        }
        state["arena_available"] = True

    # events: t_s/type/uav only, from telemetry["events"] -- lets the panel
    # tell a relay that has gone OFFLINE (uav_fail / uav_dropout) apart
    # from a healthy idle spare. Missing/malformed -> events_available False
    # and the panel falls back to its old idle/standby styling.
    events = telemetry.get("events")
    if isinstance(events, list):
        clean = [
            {"t_s": e["t_s"], "type": e["type"], "uav": e["uav"]}
            for e in events
            if isinstance(e, dict) and "t_s" in e and "type" in e and "uav" in e
        ]
        if clean:
            state["events"] = clean
            state["events_available"] = True

    return state
