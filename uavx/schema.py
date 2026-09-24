"""Telemetry contract shared with the scoring harness's UAVX profile.

sim.py emits exactly this shape. Don't rename or drop a field here without
recording the change in uavx/RULES_NOTES.md -- the harness (score/harness.py,
`--profile uavx`) matches on these field names, not on position.
"""

from __future__ import annotations

from typing import Any, NotRequired, Optional, TypedDict


class Conditions(TypedDict):
    gps_class: str
    wind: str
    n_uav: int
    r_comm_m: float


class PoiSpec(TypedDict):
    id: int
    priority: float
    x_m: float
    y_m: float
    # Additive (A26, 24 Sep 2026, uavx/survey.py's run_dynamic() only): the
    # tick this PoI became known to the surveyor. Absent (NotRequired) for
    # every static-scenario run (run()) -- those PoIs all exist from t=0, as
    # before. See uavx/RULES_NOTES.md section 13.
    spawn_t_s: NotRequired[float]


class Packet(TypedDict):
    t_s: float
    poi: int
    delivered: bool
    latency_ms: Optional[float]


class LinkSample(TypedDict):
    t_s: float
    poi: int
    connected: bool


class Assignment(TypedDict):
    t_s: float
    relay_uav: int
    serves: list[int]
    # Additive (Task 3, see uavx/RULES_NOTES.md): why this row's serves-set
    # may differ from this relay's previous row -- "relay_failure" |
    # "relay_recharge" (reconfig.reconfigure() actually moved this relay in
    # response to a uav_fail/uav_recharge/return event this tick) |
    # "poi_report_dropout" (same, for ruling A23's report-gated uav_dropout
    # event -- see uavx/RULES_NOTES.md section 12) |
    # "surveyor_drift" (the routine per-tick continuous-tracker re-solve,
    # driven only by the surveyor moving -- the default). NotRequired so
    # older telemetry (uavx/legacy/sim.py's assignments, which never had
    # this field) keeps validating.
    cause: NotRequired[str]


class Event(TypedDict):
    t_s: float
    type: str  # "uav_fail" | "uav_recharge" | "uav_dropout" | "restored" | "link_degraded" | "link_restored"
    uav: int
    # Additive (ruling A23, see uavx/RULES_NOTES.md section 12): set only on
    # the report-gated fault events (link_degraded, uav_dropout) -- which
    # PoI's report success selected this relay, and when that report
    # succeeded. The target relay is always one of that visit's
    # carrying_relay_ids.
    poi: NotRequired[int]
    report_t_s: NotRequired[float]


class PoiCompletion(TypedDict):
    poi: int
    served_fraction: float


class UavPosition(TypedDict):
    """One UAV's position at one tick. Logged for every UAV (including the
    stationary base) every tick -- added for the Path C+ surveyor mission
    (uavx/survey.py) so a UI never needs to import uavx/config.py to plot
    the fleet."""

    t_s: float
    uav: Any  # "base", or the numeric id of a surveyor/relay UAV
    x_m: float
    y_m: float
    role: str  # "base" | "surveyor" | "relay"


class Chain(TypedDict):
    """The single active base->relay->...->surveyor chain at one tick.
    len(link_quality) == len(path) - 1 (one PDR value per hop)."""

    t_s: float
    path: list[Any]           # e.g. ["base", "relay_2", <surveyor_id>]
    link_quality: list[float]


class Visit(TypedDict):
    """One PoI visit by the surveyor -- analysis-only, not read by
    score/profiles.py."""

    poi: int
    arrive_t_s: float
    dwell_end_t_s: float
    reported_t_s: Optional[float]
    # Additive (ruling A23, see uavx/RULES_NOTES.md section 12): the relay
    # ids on the live chain at the exact tick reported_t_s was set -- the
    # relays that carried this report. None while unreported. NotRequired so
    # telemetry written before A23 keeps validating.
    carrying_relay_ids: NotRequired[Optional[list[int]]]
    # Additive (A26, run_dynamic() only): arrive_t_s + MAX_DETECT_TO_REPORT_S
    # -- the moment this PoI's report stops counting as on time.
    report_deadline_t_s: NotRequired[float]


class FaultNote(TypedDict):
    t_s: Optional[float]  # None when the note is about the whole run (event never fired)
    event: str            # "link_degraded" | "uav_dropout"
    note: str


class Telemetry(TypedDict):
    run_start_utc: str
    run_end_utc: str
    total_s: float
    conditions: Conditions
    pois: list[PoiSpec]
    packets: list[Packet]
    link_samples: list[LinkSample]
    assignments: list[Assignment]
    events: list[Event]
    poi_completion: list[PoiCompletion]
    # Added for the Path C+ surveyor mission (uavx/survey.py). The ring/PoI
    # mission (sim.py) does not emit these -- additive only, per
    # uavx/RULES_NOTES.md, so score/profiles.py's UavxProfile (which never
    # reads these three) keeps working unchanged either way.
    positions: NotRequired[list[UavPosition]]
    chains: NotRequired[list[Chain]]
    visits: NotRequired[list[Visit]]
    # Additive (ruling A23): plain-language log of report-gated fault events
    # that were retried or could not fire at all, e.g. the 75% dropout when
    # no report succeeded after 75% mission time. See uavx/RULES_NOTES.md
    # section 12.
    fault_notes: NotRequired[list[FaultNote]]
    # Additive (ruling A23): the earliest report-success time each
    # report-gated event may select from -- {"link_degraded_earliest_t_s",
    # "uav_dropout_earliest_t_s"} -- so the ~40%/~75% gating is checkable
    # from the telemetry alone.
    fault_schedule: NotRequired[dict[str, float]]
    # Additive (A26, 24 Sep 2026, run_dynamic() only): every PoI's spawn
    # time, in the order it became known -- {"poi": id, "t_s": ...} per row.
    # Kept separate from `events` (Event requires a "uav" field, which a PoI
    # spawn doesn't have). See uavx/RULES_NOTES.md section 13.
    poi_spawns: NotRequired[list[dict]]


REQUIRED_TOP_LEVEL_KEYS = (
    "run_start_utc", "run_end_utc", "total_s", "conditions", "pois",
    "packets", "link_samples", "assignments", "events", "poi_completion",
)
REQUIRED_CONDITIONS_KEYS = ("gps_class", "wind", "n_uav", "r_comm_m")


def validate(telemetry: dict) -> None:
    """Cheap shape check -- catches a renamed/missing field before it ships
    to the harness. Not a full schema validator."""
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in telemetry]
    if missing:
        raise ValueError(f"telemetry missing required keys: {missing}")
    missing_cond = [k for k in REQUIRED_CONDITIONS_KEYS if k not in telemetry["conditions"]]
    if missing_cond:
        raise ValueError(f"telemetry.conditions missing keys: {missing_cond}")
