"""Ruling A23 -- report-gated fault-event selection (uavx/RULES_NOTES.md
section 12).

The core check: for both the rewired 40% link_degraded event and the new
75% uav_dropout event, the relay the event targets was actually in the
carrying_relay_ids of the report that selected it -- and, independently of
that stored field, was on the logged chains[] path at that report's tick.
That is what makes "verifiably carrying live traffic" a checked claim rather
than an asserted one.

Run from the repo root:  python3 -m unittest uavx.tests.test_a23_fault_selection
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest

from uavx import config, survey
from score.profiles import PROFILES

# (n_uav, seed) runs chosen from a seed scan (see RULES_NOTES.md section 12).
# Re-picked 24 Sep 2026 for the R_COMM_M=150->100 ruling: the old seeds'
# behaviour was measured at 150 m and no longer holds at 100 m (relay
# spacing r_comm*LINK_SOFT_BAND_FRAC dropped from 120 m to 80 m, changing
# which seeds land each fault event where). Re-scanned seeds 1-120 at the
# new range and picked the first seed matching each required behaviour:
#   (5, 41):  degrade fires; dropout fires AND is restored (was (5, 16))
#   (5, 58):  degrade fires; dropout fires, never restored (was (5, 42))
#   (4, 9):   the A20 4-UAV evidence config -- degrade fires, dropout cannot
#             fire (was (4, 101))
#   (4, 3):   degrade passes over a report with no relay on its chain, then
#             no later report succeeds -- degrade never fires, logged
#             (was (4, 16))
FIRES_DEGRADE = [(5, 41), (5, 58), (4, 9)]
FIRES_DROPOUT = [(5, 41), (5, 58)]
DROPOUT_CANNOT_FIRE = [(4, 9)]

_cache: dict[tuple[int, int], dict] = {}


def _run(n_uav: int, seed: int) -> dict:
    if (n_uav, seed) not in _cache:
        _cache[(n_uav, seed)] = survey.run(seed=seed, n_uav=n_uav)
    return _cache[(n_uav, seed)]


def _close(a: float, b: float) -> bool:
    return abs(a - b) < config.TICK_S / 2


class ReportGatedSelection(unittest.TestCase):
    def _check_event_targets_carrying_relay(self, telemetry: dict, event_type: str, earliest_key: str) -> dict:
        events = [e for e in telemetry["events"] if e["type"] == event_type]
        self.assertEqual(len(events), 1, f"expected exactly one {event_type} event")
        ev = events[0]

        # The report that selected this event, looked up by poi + report time.
        visit = next(
            v for v in telemetry["visits"]
            if v["poi"] == ev["poi"] and v["reported_t_s"] is not None and _close(v["reported_t_s"], ev["report_t_s"])
        )

        # THE A23 check: the target was carrying that report.
        self.assertIn(ev["uav"], visit["carrying_relay_ids"])

        # Independent of the stored field: the target was on the logged live
        # chain at the exact tick that report succeeded.
        chain = next(c for c in telemetry["chains"] if _close(c["t_s"], visit["reported_t_s"]))
        self.assertIn(ev["uav"], survey._relay_ids_on_chain(chain["path"]))
        self.assertEqual(sorted(visit["carrying_relay_ids"]), sorted(survey._relay_ids_on_chain(chain["path"])))

        # Gated on the ~40%/~75% mark, and fires at or just after report
        # completion (the next tick), not on an arbitrary later tick.
        earliest = telemetry["fault_schedule"][earliest_key]
        self.assertGreaterEqual(visit["reported_t_s"], earliest - config.TICK_S / 2)
        self.assertTrue(_close(ev["t_s"], visit["reported_t_s"] + config.TICK_S))

        # It is the FIRST usable report at/after the mark: every earlier
        # report past the mark was passed over with a logged reason.
        passed_over_t = {n["t_s"] for n in telemetry["fault_notes"] if n["event"] == event_type and n["t_s"] is not None}
        for v in telemetry["visits"]:
            rt = v["reported_t_s"]
            if rt is not None and earliest - config.TICK_S / 2 <= rt < visit["reported_t_s"] - config.TICK_S / 2:
                self.assertTrue(any(_close(rt, t) for t in passed_over_t), f"report at t={rt} skipped without a note")
        return ev

    def test_degrade_targets_a_relay_carrying_the_report(self):
        for n_uav, seed in FIRES_DEGRADE:
            with self.subTest(n_uav=n_uav, seed=seed):
                self._check_event_targets_carrying_relay(_run(n_uav, seed), "link_degraded", "link_degraded_earliest_t_s")

    def test_relay_less_report_is_passed_over_and_logged(self):
        # (4, 3): the first report after 40% had the surveyor linked
        # straight to base, and no later report succeeded -- in a 240-run
        # seed scan every pass-over looked like this, so the "fires on a
        # LATER report" half of retry-forward is covered by the synthetic
        # test below instead.
        t = _run(4, 3)
        notes = [n for n in t["fault_notes"] if n["event"] == "link_degraded" and n["t_s"] is not None]
        self.assertTrue(notes)
        for n in notes:
            skipped = next(v for v in t["visits"] if v["reported_t_s"] is not None and _close(v["reported_t_s"], n["t_s"]))
            self.assertEqual(skipped["carrying_relay_ids"], [])
        self.assertFalse([e for e in t["events"] if e["type"] == "link_degraded"])
        self.assertTrue([n for n in t["fault_notes"] if n["event"] == "link_degraded" and n["t_s"] is None])

    def test_retry_forward_selects_next_report_with_active_carrying_relays(self):
        visits = [
            {"poi": 0, "reported_t_s": 10.0, "carrying_relay_ids": [2]},   # before the mark
            {"poi": 1, "reported_t_s": 50.0, "carrying_relay_ids": []},    # no relays -> pass over
            {"poi": 2, "reported_t_s": 60.0, "carrying_relay_ids": [3]},   # relay 3 failed -> pass over
            {"poi": 3, "reported_t_s": 70.0, "carrying_relay_ids": [3, 4]},  # usable: only 4 is active
        ]
        report_log = [(0, 50), (1, 250), (2, 300), (3, 350)]
        status = {2: "active", 3: "failed", 4: "active"}
        notes: list[dict] = []
        picked, cursor = survey._next_report_target(report_log, 0, 200, visits, status, "uav_dropout", notes)
        self.assertEqual(picked, (3, [4]))
        self.assertEqual(cursor, 4)
        self.assertEqual([n["t_s"] for n in notes], [50.0, 60.0])
        # Blocked (another fault's recovery still open): passed over with a note, not held for later.
        notes = []
        picked, cursor = survey._next_report_target(report_log, 3, 200, visits, status, "uav_dropout", notes, blocked_reason="busy")
        self.assertIsNone(picked)
        self.assertEqual(cursor, 4)
        self.assertIn("busy", notes[0]["note"])

    def test_dropout_targets_a_relay_carrying_the_report(self):
        for n_uav, seed in FIRES_DROPOUT:
            with self.subTest(n_uav=n_uav, seed=seed):
                t = _run(n_uav, seed)
                ev = self._check_event_targets_carrying_relay(t, "uav_dropout", "uav_dropout_earliest_t_s")
                # Full dropout, not a degradation: the relay is off every
                # chain after it drops, and gets no more assignment rows.
                for c in t["chains"]:
                    if c["t_s"] > ev["t_s"] + config.TICK_S / 2:
                        self.assertNotIn(ev["uav"], survey._relay_ids_on_chain(c["path"]))
                self.assertFalse([a for a in t["assignments"] if a["relay_uav"] == ev["uav"] and a["t_s"] > ev["t_s"] + config.TICK_S / 2])
                # Its own cause tag, distinct from the other three.
                self.assertTrue([a for a in t["assignments"] if a["cause"] == "poi_report_dropout"])

    def test_dropout_that_cannot_fire_is_logged_not_forced(self):
        for n_uav, seed in DROPOUT_CANNOT_FIRE:
            with self.subTest(n_uav=n_uav, seed=seed):
                t = _run(n_uav, seed)
                self.assertFalse([e for e in t["events"] if e["type"] == "uav_dropout"])
                earliest = t["fault_schedule"]["uav_dropout_earliest_t_s"]
                self.assertFalse([v for v in t["visits"] if v["reported_t_s"] is not None and v["reported_t_s"] >= earliest])
                notes = [n["note"] for n in t["fault_notes"] if n["event"] == "uav_dropout" and n["t_s"] is None]
                self.assertEqual(len(notes), 1)
                self.assertIn("dropout event could not fire -- no report succeeded after 75% mission time", notes[0])


class PerEventMetrics(unittest.TestCase):
    """A23: recovery_time_s and relay_reallocations reported separately per
    event, never only pooled."""

    def test_each_fault_event_has_its_own_recovery_and_reallocation_metric(self):
        metrics = {m.name: m for m in PROFILES["uavx"].compute(_run(5, 41), {})}
        for name in ("recovery_time_s_uav_fail", "recovery_time_s_uav_recharge", "recovery_time_s_uav_dropout"):
            self.assertEqual(metrics[name].quality, "measured", name)
        causes = ("relay_failure", "relay_recharge", "poi_report_dropout", "surveyor_drift")
        self.assertEqual(
            sum(metrics[f"relay_reallocations_{c}"].value for c in causes),
            metrics["relay_reallocations"].value,
        )

    def test_unrestored_dropout_is_unavailable_not_invented(self):
        metrics = {m.name: m for m in PROFILES["uavx"].compute(_run(5, 58), {})}
        self.assertEqual(metrics["recovery_time_s_uav_dropout"].quality, "unavailable")


class EventSetFlag(unittest.TestCase):
    """--events a23-only (uavx/RULES_NOTES.md section 12): only the two A23
    events run; the default "all" is unchanged."""

    def test_a23_only_has_no_fail_or_recharge(self):
        t = survey.run(seed=1, n_uav=4, events="a23-only")
        types = {e["type"] for e in t["events"]}
        self.assertFalse(types & {"uav_fail", "uav_recharge"})
        self.assertTrue({"link_degraded", "uav_dropout"} <= types)
        self.assertEqual(t["conditions"]["event_set"], "a23-only")

    def test_default_still_runs_all_four(self):
        t = _run(5, 58)
        self.assertEqual(t["conditions"]["event_set"], "all")
        self.assertTrue({"uav_fail", "uav_recharge", "link_degraded", "uav_dropout"} <= {e["type"] for e in t["events"]})

    def test_unknown_event_set_rejected(self):
        with self.assertRaises(ValueError):
            survey.run(seed=1, n_uav=4, events="nope")


class ScenarioFile(unittest.TestCase):
    SCENARIO = os.path.join(os.path.dirname(__file__), "..", "..", "claude", "scenario_config.json")

    def test_loads_the_drafted_scenario_file_shape(self):
        pois = survey.load_scenario(self.SCENARIO)
        with open(self.SCENARIO) as f:
            raw = json.load(f)["pois"]
        self.assertEqual(len(pois), 6)
        for p in raw:
            pid = int(p["id"][4:])
            self.assertEqual(pois[pid]["pos"], tuple(p["pos"]))
            self.assertEqual(pois[pid]["priority"], float(p["priority"]))

    def test_run_uses_the_scenario_file_layout(self):
        t = survey.run(seed=1, n_uav=4, events="a23-only", scenario_path=self.SCENARIO)
        loaded = survey.load_scenario(self.SCENARIO)
        self.assertEqual({p["id"]: ((p["x_m"], p["y_m"]), p["priority"]) for p in t["pois"]},
                         {i: (p["pos"], p["priority"]) for i, p in loaded.items()})
        self.assertEqual(t["conditions"]["scenario"], self.SCENARIO)

    def test_malformed_or_mismatched_scenario_raises_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as d:
            for bad in ({"pois": [{"id": "poi_1", "priority": 3}]},
                        {"pois": [{"id": "alpha", "priority": 3, "pos": [1.0, 2.0]}]},
                        {"r_comm_m": 120.0, "pois": [{"id": "poi_1", "priority": 3, "pos": [1.0, 2.0]}]}):
                path = os.path.join(d, "bad.json")
                with open(path, "w") as f:
                    json.dump(bad, f)
                with self.assertRaises(ValueError, msg=bad):
                    survey.load_scenario(path)

if __name__ == "__main__":
    unittest.main()
