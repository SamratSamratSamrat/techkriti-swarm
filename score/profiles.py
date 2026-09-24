"""
score/profiles.py — pluggable metric profiles for the mission scoring harness.

This is OFFLINE analysis code (the "learning loop"). It never touches a drone,
never imports pymavlink, and never runs while a mission is live. It only reads
telemetry JSON that some other part of the project already wrote to disk.

A MetricProfile turns one run's telemetry dict into a list of Metric objects.
Every formula below is commented in plain language, because the person who has
to read and fix this code is a Grade 8 student, not a data scientist.

Golden rule: NEVER invent a number. If an input a metric needs is missing or
unparsable, that metric is still emitted, but with value=None,
quality="unavailable", and a plain-English reason. A missing field must never
silently turn into a 0 or an average that quietly excludes it.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Optional

# Metres per degree of latitude is ~constant on Earth. Metres per degree of
# longitude shrinks by cos(latitude) as you move away from the equator. This
# is the same flat-earth approximation used everywhere in this project for
# small distances (a few tens of metres) — good to well under 1 cm error at
# ring scale, and far simpler than a full geodesic formula.
METRES_PER_DEG_LAT = 111_320.0


@dataclass
class Metric:
    """One scored number. quality is one of measured / approx / unavailable."""

    name: str
    value: Any
    unit: str
    quality: str
    reason: Optional[str] = None  # why it's approx/unavailable; None for a clean "measured"


def unavailable(name: str, unit: str, reason: str) -> Metric:
    """Shorthand for 'this metric's inputs are missing — say so, don't guess.'"""
    return Metric(name=name, value=None, unit=unit, quality="unavailable", reason=reason)


class MetricProfile:
    """Interface every profile implements: telemetry + params -> list[Metric]."""

    def compute(self, telemetry: dict, params: dict) -> list[Metric]:
        raise NotImplementedError


# --------------------------------------------------------------------------
# RING profile — Module 3/4 telemetry (5-drone ring holding a target point).
# Ground-truth schema: the real sprint2.json at the project root.
# --------------------------------------------------------------------------


def _ll_to_local_metres(lat: float, lon: float, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    """Convert a lat/lon pair to flat (x_east_m, y_north_m) relative to ref_lat/ref_lon."""
    dlat = lat - ref_lat
    dlon = lon - ref_lon
    y = dlat * METRES_PER_DEG_LAT
    x = dlon * METRES_PER_DEG_LAT * math.cos(math.radians(ref_lat))
    return x, y


def _steady_ticks(hold_series: list[dict]) -> list[dict]:
    """STEADY ticks = every interceptor reports converged == true.

    Tick 0 in a fresh ring is the pre-settle transient: drones are still
    flying into their slots, so err_m is briefly large. Mixing that into a
    "how well do we hold the ring" average makes the number lie. STEADY
    excludes any tick where even one interceptor hasn't converged yet.
    """
    steady = []
    for tick in hold_series:
        interceptors = tick.get("interceptors")
        if not interceptors:
            continue
        if all(i.get("converged") is True for i in interceptors):
            steady.append(tick)
    return steady


def _mean_ring_error(ticks: list[dict]) -> Optional[float]:
    """Mean of ring_err_mean_m (the run's own per-tick ring error average)."""
    values = [t["ring_err_mean_m"] for t in ticks if "ring_err_mean_m" in t]
    if not values:
        return None
    return statistics.mean(values)


def _max_ring_error(ticks: list[dict]) -> Optional[float]:
    """Worst single interceptor error seen across the given ticks."""
    values = [
        i["err_m"]
        for t in ticks
        for i in t.get("interceptors", [])
        if "err_m" in i
    ]
    if not values:
        return None
    return max(values)


def _slot_convergence_s(per_drone: dict) -> tuple[Optional[float], Optional[str]]:
    """Max of per_drone[*].conv_s, a string like "2.21s" -> strip "s", parse float.

    Returns (value, reason). reason is only set when value is None.
    """
    if not per_drone:
        return None, "per_drone missing or empty"
    parsed = []
    for sysid, stats in per_drone.items():
        raw = stats.get("conv_s")
        if not isinstance(raw, str) or not raw.endswith("s"):
            continue
        try:
            parsed.append(float(raw[:-1]))
        except ValueError:
            continue
    if not parsed:
        return None, "no per_drone conv_s value could be parsed as '<float>s'"
    return max(parsed), None


def _rtl_events(hold_series: list[dict]) -> Optional[int]:
    """Count of distinct interceptor sysids whose status was ever != 'active'."""
    seen_any_tick = False
    non_active_sysids: set[Any] = set()
    for tick in hold_series:
        for interceptor in tick.get("interceptors", []):
            if "status" not in interceptor:
                continue
            seen_any_tick = True
            if interceptor["status"] != "active":
                non_active_sysids.add(interceptor.get("sysid"))
    if not seen_any_tick:
        return None
    return len(non_active_sysids)


def _min_active_of_5(hold_series: list[dict]) -> Optional[int]:
    values = [t["active_of_5"] for t in hold_series if "active_of_5" in t]
    if not values:
        return None
    return min(values)


def _min_separation_and_collisions(
    hold_series: list[dict], ring_radius_m: float, n_drones: int, collision_threshold_m: float
) -> tuple[Optional[float], Optional[int], str, Optional[str]]:
    """Minimum pairwise separation between interceptors, plus a collision count.

    Preferred path (quality="measured"): if every interceptor entry carries a
    real lat/lon, compute the TRUE minimum pairwise distance, over every pair,
    across every tick.

    Fallback path (quality="approx", the one sprint2.json actually hits,
    because interceptors only carry sysid/status/err_m/converged — no
    position): assume the n interceptors are evenly spaced around the ring, so
    each ADJACENT pair is nominally `chord = 2*R*sin(pi/n)` apart. Two drones
    that are each off their slot by their own err_m could, in the worst case,
    have drifted straight at each other, so the true gap could be as low as
    `chord - err_i - err_j`. That's a LOWER BOUND, not a measurement: it can
    under-estimate (report drones as closer than they really are) because it
    assumes the worst-case drift direction for both drones at once.

    This function itself is scope-agnostic — it scans whatever `hold_series`
    slice the caller hands it. RingProfile.compute() calls it twice: once on
    STEADY ticks only (the real "did the formation nearly touch" number, used
    for both min_separation_m and collision_count) and once on the full run
    (min_separation_all_ticks_m, informational — includes the approach phase).
    """
    have_positions = any(
        "lat" in i and "lon" in i for t in hold_series for i in t.get("interceptors", [])
    )

    if have_positions:
        per_tick_min = []
        for tick in hold_series:
            pts = [
                (i["sysid"], i["lat"], i["lon"])
                for i in tick.get("interceptors", [])
                if "lat" in i and "lon" in i
            ]
            if len(pts) < 2:
                continue
            ref_lat = pts[0][1]
            ref_lon = pts[0][2]
            xy = [(_ll_to_local_metres(lat, lon, ref_lat, ref_lon)) for _, lat, lon in pts]
            pair_mins = []
            for a in range(len(xy)):
                for b in range(a + 1, len(xy)):
                    dx = xy[a][0] - xy[b][0]
                    dy = xy[a][1] - xy[b][1]
                    pair_mins.append(math.hypot(dx, dy))
            per_tick_min.append(min(pair_mins))
        if not per_tick_min:
            return None, None, "measured", "no tick had 2+ interceptor positions"
        min_sep = min(per_tick_min)
        collisions = sum(1 for v in per_tick_min if v <= collision_threshold_m)
        return min_sep, collisions, "measured", None

    # Fallback: geometric lower bound from ring geometry + reported err_m.
    if n_drones < 2:
        return None, None, "approx", "n_drones < 2, no adjacent pairs exist"
    chord = 2 * ring_radius_m * math.sin(math.pi / n_drones)
    per_tick_min = []
    for tick in hold_series:
        interceptors = sorted(
            (i for i in tick.get("interceptors", []) if "err_m" in i),
            key=lambda i: i.get("sysid", 0),
        )
        m = len(interceptors)
        if m < 2:
            continue
        bounds = []
        for a in range(m):
            b = (a + 1) % m  # wrap around: last slot is adjacent to the first
            bounds.append(chord - interceptors[a]["err_m"] - interceptors[b]["err_m"])
        per_tick_min.append(min(bounds))
    if not per_tick_min:
        return None, None, "approx", "no tick had 2+ interceptor err_m values"
    min_sep = min(per_tick_min)
    collisions = sum(1 for v in per_tick_min if v <= collision_threshold_m)
    note = "lower bound from ring geometry, not measured position — can under-estimate true separation and over-count collisions"
    return min_sep, collisions, "approx", note


class RingProfile(MetricProfile):
    """Metrics for a 5-drone ring-hold mission (Module 3/4 telemetry)."""

    DEFAULTS = {"ring_radius_m": 5.0, "n_drones": 5, "collision_threshold_m": 1.0}

    def compute(self, telemetry: dict, params: dict) -> list[Metric]:
        p = {**self.DEFAULTS, **(params or {})}
        metrics: list[Metric] = []

        hold_series = telemetry.get("hold_series") or []
        if not hold_series:
            reason = "hold_series missing or empty"
            return [
                unavailable("mean_ring_error_m", "m", reason),
                unavailable("mean_ring_error_m_full_run", "m", reason),
                unavailable("max_ring_error_m", "m", reason),
                unavailable("max_ring_error_m_full_run", "m", reason),
                unavailable("convergence_time_s", "s", "takeoff_to_ring_formed_s missing"),
                unavailable("slot_convergence_s", "s", "per_drone missing"),
                unavailable("min_separation_m", "m", reason),
                unavailable("min_separation_all_ticks_m", "m", reason),
                unavailable("collision_count", "count", reason),
                unavailable("rtl_events", "count", reason),
                unavailable("min_active_of_5", "count", reason),
            ]

        steady = _steady_ticks(hold_series)

        # --- headline error metrics: STEADY first, full-run alongside them ---
        mean_steady = _mean_ring_error(steady)
        metrics.append(
            Metric("mean_ring_error_m", mean_steady, "m", "measured")
            if mean_steady is not None
            else unavailable("mean_ring_error_m", "m", "no STEADY ticks (no tick had every interceptor converged)")
        )
        mean_full = _mean_ring_error(hold_series)
        metrics.append(
            Metric("mean_ring_error_m_full_run", mean_full, "m", "measured")
            if mean_full is not None
            else unavailable("mean_ring_error_m_full_run", "m", "no ticks had ring_err_mean_m")
        )

        max_steady = _max_ring_error(steady)
        metrics.append(
            Metric("max_ring_error_m", max_steady, "m", "measured")
            if max_steady is not None
            else unavailable("max_ring_error_m", "m", "no STEADY ticks (no tick had every interceptor converged)")
        )
        max_full = _max_ring_error(hold_series)
        metrics.append(
            Metric("max_ring_error_m_full_run", max_full, "m", "measured")
            if max_full is not None
            else unavailable("max_ring_error_m_full_run", "m", "no ticks had interceptor err_m")
        )

        # --- convergence ---
        conv_time = telemetry.get("takeoff_to_ring_formed_s")
        metrics.append(
            Metric("convergence_time_s", conv_time, "s", "measured")
            if conv_time is not None
            else unavailable("convergence_time_s", "s", "takeoff_to_ring_formed_s missing")
        )

        slot_conv, slot_reason = _slot_convergence_s(telemetry.get("per_drone") or {})
        metrics.append(
            Metric("slot_convergence_s", slot_conv, "s", "measured")
            if slot_conv is not None
            else unavailable("slot_convergence_s", "s", slot_reason)
        )

        # --- separation / collision ---
        # STEADY ticks only: during approach the drones are still flying toward
        # their slots, so a large slot error there is expected and does not mean
        # drones were close to each other — mixing it in would make collision_count
        # a measure of "how far away did drones start," not "did the formation
        # nearly touch." That's the number we actually care about for safety.
        min_sep, collisions, sep_quality, sep_note = _min_separation_and_collisions(
            steady, p["ring_radius_m"], p["n_drones"], p["collision_threshold_m"]
        )
        if min_sep is None:
            metrics.append(unavailable("min_separation_m", "m", sep_note or "could not compute"))
            metrics.append(unavailable("collision_count", "count", sep_note or "could not compute"))
        else:
            metrics.append(Metric("min_separation_m", min_sep, "m", sep_quality, sep_note))
            metrics.append(Metric("collision_count", collisions, "count", sep_quality, sep_note))

        # All-ticks version, informational only — includes the approach/transit
        # phase, so it is NOT a safety indicator on its own (see comment above).
        min_sep_all, _collisions_all, sep_quality_all, sep_note_all = _min_separation_and_collisions(
            hold_series, p["ring_radius_m"], p["n_drones"], p["collision_threshold_m"]
        )
        if min_sep_all is None:
            metrics.append(unavailable("min_separation_all_ticks_m", "m", sep_note_all or "could not compute"))
        else:
            metrics.append(Metric("min_separation_all_ticks_m", min_sep_all, "m", sep_quality_all, sep_note_all))

        # --- fleet health ---
        rtl = _rtl_events(hold_series)
        metrics.append(
            Metric("rtl_events", rtl, "count", "measured")
            if rtl is not None
            else unavailable("rtl_events", "count", "no interceptor status field found in hold_series")
        )

        min_active = _min_active_of_5(hold_series)
        metrics.append(
            Metric("min_active_of_5", min_active, "count", "measured")
            if min_active is not None
            else unavailable("min_active_of_5", "count", "no active_of_5 field found in hold_series")
        )

        return metrics


# --------------------------------------------------------------------------
# UAVX profile — matches uavx/schema.py's telemetry contract exactly.
#
# uavx/schema.py is authoritative (sim.py emits exactly that shape and
# validate() checks it before writing). This profile reads that contract:
#   packets:        [{"t_s", "poi", "delivered", "latency_ms"}, ...]
#   link_samples:   [{"t_s", "poi", "connected"}, ...]
#   assignments:    [{"t_s", "relay_uav", "serves": [poi_id, ...]}, ...]  (one row per relay per tick)
#   events:         [{"t_s", "type": "uav_fail"|"uav_recharge"|"uav_dropout"|"restored", "uav"}, ...]
#                   (plus link_degraded/link_restored annotation markers, not scored)
#   pois:           [{"id", "priority"}, ...]
#   poi_completion: [{"poi", "served_fraction"}, ...]  (separate top-level list, NOT merged into pois)
#   params["priorities"]: {poi_id: priority_number, ...} — OPTIONAL override; when
#   absent (params={}, what the CLI always passes), priority comes from
#   telemetry["pois"][i]["priority"] instead.
#
# Every lookup stays defensive (.get with a fallback to "unavailable") because
# metric DEFINITIONS may still change pending rulebook reconciliation — see
# uavx/RULES_NOTES.md. A field renamed there again must not crash this
# profile, it should just make the affected metric unavailable.
# --------------------------------------------------------------------------


class UavxProfile(MetricProfile):
    """Metrics for the UAV-X relay/coverage mission. See module docstring above."""

    def compute(self, telemetry: dict, params: dict) -> list[Metric]:
        params = params or {}
        metrics: list[Metric] = []

        metrics.append(self._packet_delivery_ratio(telemetry))
        metrics += self._latency(telemetry)
        metrics.append(self._connectivity_downtime(telemetry))
        metrics.append(self._relay_reallocations(telemetry))
        metrics += self._relay_reallocations_by_cause(telemetry)
        metrics += self._recovery_time(telemetry)
        metrics.append(self._poi_completion(telemetry))
        metrics.append(self._priority_weighted_score(telemetry, params))
        return metrics

    def _packet_delivery_ratio(self, telemetry: dict) -> Metric:
        packets = telemetry.get("packets")
        if not packets:
            return unavailable("packet_delivery_ratio", "ratio", "packets missing or empty")
        delivered = sum(1 for p in packets if p.get("delivered") is True)
        return Metric("packet_delivery_ratio", delivered / len(packets), "ratio", "measured")

    def _latency(self, telemetry: dict) -> list[Metric]:
        packets = telemetry.get("packets") or []
        latencies = [
            p["latency_ms"]
            for p in packets
            if p.get("delivered") is True and isinstance(p.get("latency_ms"), (int, float))
        ]
        if not latencies:
            reason = "no delivered packet carried a numeric latency_ms"
            return [unavailable("latency_ms_mean", "ms", reason), unavailable("latency_ms_p95", "ms", reason)]
        mean_m = Metric("latency_ms_mean", statistics.mean(latencies), "ms", "measured")
        # p95 via nearest-rank on sorted latencies — simple, no numpy needed.
        ordered = sorted(latencies)
        idx = max(0, math.ceil(0.95 * len(ordered)) - 1)
        p95 = Metric("latency_ms_p95", ordered[idx], "ms", "measured")
        return [mean_m, p95]

    def _connectivity_downtime(self, telemetry: dict) -> Metric:
        samples = telemetry.get("link_samples")
        if not samples:
            return unavailable("connectivity_downtime_s", "s", "link_samples missing or empty")
        by_poi: dict[Any, list[dict]] = {}
        for s in samples:
            if "poi" not in s or "t_s" not in s or "connected" not in s:
                continue
            by_poi.setdefault(s["poi"], []).append(s)
        if not by_poi:
            return unavailable(
                "connectivity_downtime_s", "s", "no link_samples entry had poi/t_s/connected"
            )
        total_downtime = 0.0
        for poi_samples in by_poi.values():
            poi_samples.sort(key=lambda s: s["t_s"])
            for i in range(len(poi_samples) - 1):
                if poi_samples[i]["connected"] is False:
                    total_downtime += poi_samples[i + 1]["t_s"] - poi_samples[i]["t_s"]
        return Metric(
            "connectivity_downtime_s",
            total_downtime,
            "s",
            "approx",
            "downtime after each PoI's last sample is not counted (no end timestamp to close the interval)",
        )

    def _relay_reallocations(self, telemetry: dict) -> Metric:
        """assignments is one row PER RELAY per tick: {t_s, relay_uav, serves:[poi_id,...]}.

        A "reallocation" is relay_uav's serves-set changing between its own
        consecutive records (not a single fleet-wide value compared tick to
        tick — that would conflate every relay's rows with each other).
        """
        assignments = telemetry.get("assignments")
        if not assignments:
            return unavailable("relay_reallocations", "count", "assignments missing or empty")
        valid = [a for a in assignments if "t_s" in a and "relay_uav" in a and "serves" in a]
        if not valid:
            return unavailable(
                "relay_reallocations", "count", "no assignment record had t_s/relay_uav/serves"
            )
        by_relay: dict[Any, list[dict]] = {}
        for a in valid:
            by_relay.setdefault(a["relay_uav"], []).append(a)
        changes = 0
        for relay_records in by_relay.values():
            relay_records.sort(key=lambda a: a["t_s"])
            prev_serves = None
            for a in relay_records:
                cur_serves = set(a["serves"])
                if prev_serves is not None and cur_serves != prev_serves:
                    changes += 1
                prev_serves = cur_serves
        return Metric("relay_reallocations", changes, "count", "measured")

    # poi_report_dropout: ruling A23's report-gated uav_dropout event (see
    # uavx/RULES_NOTES.md section 12). Telemetry predating A23 simply
    # reports 0 for it -- no row there ever carries that tag.
    _REALLOCATION_CAUSES = ("relay_failure", "relay_recharge", "poi_report_dropout", "surveyor_drift")

    def _relay_reallocations_by_cause(self, telemetry: dict) -> list[Metric]:
        """Same reallocation count as _relay_reallocations() above, split by
        WHY each change happened. `cause` (uavx/schema.py's Assignment
        TypedDict, additive) is tagged by survey.py itself at the point
        reconfig.reconfigure() runs -- see uavx/RULES_NOTES.md -- not
        inferred here from event timing. This never changes what
        relay_reallocations means or how it's computed; it's a second,
        independent read of the same assignments[] data.

        Telemetry with no 'cause' field at all (any run predating this
        breakdown, or uavx/legacy/sim.py's assignments, which never had it)
        reports every cause bucket 'unavailable' -- the plain total above
        still works unaffected either way.
        """
        names = [f"relay_reallocations_{c}" for c in self._REALLOCATION_CAUSES]
        assignments = telemetry.get("assignments")
        if not assignments:
            return [unavailable(n, "count", "assignments missing or empty") for n in names]
        valid = [a for a in assignments if "t_s" in a and "relay_uav" in a and "serves" in a]
        if not valid:
            return [unavailable(n, "count", "no assignment record had t_s/relay_uav/serves") for n in names]
        if not any("cause" in a for a in valid):
            reason = "no assignment record carries a 'cause' tag (telemetry predates this breakdown)"
            return [unavailable(n, "count", reason) for n in names]

        by_relay: dict[Any, list[dict]] = {}
        for a in valid:
            by_relay.setdefault(a["relay_uav"], []).append(a)
        counts = {c: 0 for c in self._REALLOCATION_CAUSES}
        unclassified = 0
        for relay_records in by_relay.values():
            relay_records.sort(key=lambda a: a["t_s"])
            prev_serves = None
            for a in relay_records:
                cur_serves = set(a["serves"])
                if prev_serves is not None and cur_serves != prev_serves:
                    cause = a.get("cause")
                    if cause in counts:
                        counts[cause] += 1
                    else:
                        unclassified += 1
                prev_serves = cur_serves

        metrics = [Metric(f"relay_reallocations_{c}", n, "count", "measured") for c, n in counts.items()]
        if unclassified:
            metrics.append(Metric(
                "relay_reallocations_unclassified", unclassified, "count", "measured",
                "a reallocation occurred but its assignment row had no recognized 'cause' value",
            ))
        return metrics

    def _recovery_time(self, telemetry: dict) -> list[Metric]:
        events = telemetry.get("events")
        if not events:
            reason = "events missing or empty"
            return [
                unavailable("recovery_time_s_mean", "s", reason),
                unavailable("recovery_time_s_max", "s", reason),
            ]
        ordered = sorted(
            (e for e in events if "t_s" in e and "type" in e and "uav" in e), key=lambda e: e["t_s"]
        )
        # Pair each uav_fail/uav_recharge with the next restored event for the
        # SAME uav. A down-event with no matching restored event in the log is
        # not scored (we don't know when/if it recovered — inventing a number
        # is worse than omitting it from the average).
        recoveries = []
        open_downs: dict[Any, dict] = {}
        # Ruling A23 (uavx/RULES_NOTES.md section 12): every down-event's
        # own recovery, in order, so each fault event is reported
        # separately -- mean/max above are a pooled summary only.
        per_event: list[dict] = []
        for e in ordered:
            uav = e["uav"]
            if e["type"] in self._DOWN_EVENT_TYPES:
                rec = {"type": e["type"], "t_s": e["t_s"], "recovery_s": None}
                per_event.append(rec)
                open_downs[uav] = rec
            elif e["type"] == "restored" and uav in open_downs:
                rec = open_downs.pop(uav)
                rec["recovery_s"] = e["t_s"] - rec["t_s"]
                recoveries.append(rec["recovery_s"])
        per_event_metrics = self._recovery_time_per_event(per_event)
        if not recoveries:
            reason = "no uav_fail/uav_recharge/uav_dropout event had a matching later 'restored' event for the same uav"
            return [
                unavailable("recovery_time_s_mean", "s", reason),
                unavailable("recovery_time_s_max", "s", reason),
            ] + per_event_metrics
        return [
            Metric("recovery_time_s_mean", statistics.mean(recoveries), "s", "measured",
                   "pooled across every down-event type -- see recovery_time_s_<event> for each one separately"),
            Metric("recovery_time_s_max", max(recoveries), "s", "measured",
                   "pooled across every down-event type -- see recovery_time_s_<event> for each one separately"),
        ] + per_event_metrics

    _DOWN_EVENT_TYPES = ("uav_fail", "uav_recharge", "uav_dropout")

    def _recovery_time_per_event(self, per_event: list[dict]) -> list[Metric]:
        """One metric per down-event, never pooled: recovery_time_s_<type>
        for the first event of each type, recovery_time_s_<type>_<n> for any
        later one. A type with no event in this run, or an event with no
        matching 'restored', is reported 'unavailable' with the reason --
        never silently dropped."""
        metrics: list[Metric] = []
        for etype in self._DOWN_EVENT_TYPES:
            of_type = [r for r in per_event if r["type"] == etype]
            if not of_type:
                metrics.append(unavailable(f"recovery_time_s_{etype}", "s", f"no {etype} event in this run"))
                continue
            for n, rec in enumerate(of_type, start=1):
                name = f"recovery_time_s_{etype}" if n == 1 else f"recovery_time_s_{etype}_{n}"
                if rec["recovery_s"] is None:
                    metrics.append(unavailable(name, "s", f"{etype} at t={rec['t_s']:.1f}s had no matching later 'restored' event"))
                else:
                    metrics.append(Metric(name, rec["recovery_s"], "s", "measured"))
        return metrics

    def _poi_completion(self, telemetry: dict) -> Metric:
        # poi_completion is its OWN top-level list, not merged into pois[].
        completion = telemetry.get("poi_completion")
        if not completion:
            return unavailable("poi_completion", "ratio", "poi_completion missing or empty")
        fractions = [
            c["served_fraction"] for c in completion if isinstance(c.get("served_fraction"), (int, float))
        ]
        if not fractions:
            return unavailable(
                "poi_completion", "ratio", "no poi_completion entry had a numeric served_fraction"
            )
        return Metric("poi_completion", statistics.mean(fractions), "ratio", "measured")

    def _priority_weighted_score(self, telemetry: dict, params: dict) -> Metric:
        # Priority weights come from telemetry["pois"][i]["priority"] by default
        # -- this must work with params={}, which is what the CLI always passes.
        # params["priorities"], if given, overrides the telemetry weight for a
        # matching poi id (useful for offline what-if re-scoring).
        pois = telemetry.get("pois")
        completion = telemetry.get("poi_completion")
        if not pois or not completion:
            return unavailable(
                "priority_weighted_score", "ratio", "pois or poi_completion missing/empty"
            )
        override = (params or {}).get("priorities") or {}
        priority_by_poi: dict[Any, float] = {}
        for p in pois:
            poi_id = p.get("id")
            if poi_id is None:
                continue
            weight = override.get(poi_id, p.get("priority"))
            if isinstance(weight, (int, float)):
                priority_by_poi[poi_id] = weight
        fraction_by_poi = {
            c["poi"]: c["served_fraction"]
            for c in completion
            if "poi" in c and isinstance(c.get("served_fraction"), (int, float))
        }
        num = 0.0
        den = 0.0
        for poi_id, weight in priority_by_poi.items():
            if poi_id not in fraction_by_poi:
                continue
            num += weight * fraction_by_poi[poi_id]
            den += weight
        if den == 0:
            return unavailable(
                "priority_weighted_score",
                "ratio",
                "no PoI had both a priority weight and a matching served_fraction",
            )
        return Metric("priority_weighted_score", num / den, "ratio", "measured")


PROFILES: dict[str, MetricProfile] = {
    "ring": RingProfile(),
    "uavx": UavxProfile(),
}
