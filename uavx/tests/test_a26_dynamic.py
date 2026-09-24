"""Ruling A26 -- the rulebook-accurate dynamic-spawn mission
(uavx/survey.py::run_dynamic(), uavx/RULES_NOTES.md section 14).

Checks every rulebook constraint A26 claims run_dynamic() respects, straight
from the telemetry it writes -- so a later edit that quietly breaks one of
them fails here instead of in front of a judge. Constraints A26 explicitly
lists as NOT yet enforced (20 m separation, 100 m altitude, 20-min battery)
are deliberately not asserted here.

Run from the repo root:  python3 -m unittest uavx.tests.test_a26_dynamic
"""

from __future__ import annotations

import math
import unittest

from uavx import config, link, survey

SEEDS = (1, 2, 3, 4, 5)
_cache: dict[int, dict] = {}


def _run(seed: int) -> dict:
    if seed not in _cache:
        _cache[seed] = survey.run_dynamic(seed=seed, n_uav=4)
    return _cache[seed]


class RulebookGeometry(unittest.TestCase):
    def test_base_is_75m_outside_the_arena(self):
        t = _run(1)
        base = t["conditions"]["base_pos"]
        h = config.ARENA_HALF_EXTENT_M
        self.assertEqual(h, 500.0, "rulebook: operational area 1000 m x 1000 m")
        self.assertLess(base[0], -h, "base must sit outside the arena, not in it")
        self.assertAlmostEqual(-h - base[0], 75.0, msg="rulebook: 75 m from base to the operational area")

    def test_ten_pois_all_inside_the_arena(self):
        for seed in SEEDS:
            pois = _run(seed)["pois"]
            self.assertEqual(len(pois), 10, "rulebook: number of POIs - 10")
            for p in pois:
                self.assertLessEqual(abs(p["x_m"]), config.ARENA_HALF_EXTENT_M)
                self.assertLessEqual(abs(p["y_m"]), config.ARENA_HALF_EXTENT_M)

    def test_mission_is_45_minutes(self):
        self.assertAlmostEqual(_run(1)["total_s"], 45 * 60.0)


class DynamicSpawning(unittest.TestCase):
    def test_spawn_times_are_random_and_inside_the_mission(self):
        for seed in SEEDS:
            spawns = [p["spawn_t_s"] for p in _run(seed)["pois"]]
            self.assertTrue(all(0.0 <= s < config.MISSION_DURATION_S for s in spawns))
            self.assertGreater(len(set(round(s, 1) for s in spawns)), 1, "PoIs must not all spawn at once")

    def test_no_poi_is_visited_before_it_spawns(self):
        for seed in SEEDS:
            t = _run(seed)
            spawn = {p["id"]: p["spawn_t_s"] for p in t["pois"]}
            for v in t["visits"]:
                self.assertGreaterEqual(v["arrive_t_s"], spawn[v["poi"]] - config.TICK_S, f"seed {seed}: PoI {v['poi']} reached before it existed")

    def test_each_poi_visited_at_most_once(self):
        # Guards the first-draft bug (34 visits for 10 PoIs): a PoI with a
        # pending report was re-picked as its own next target.
        for seed in SEEDS:
            pois_visited = [v["poi"] for v in _run(seed)["visits"]]
            self.assertEqual(len(pois_visited), len(set(pois_visited)), f"seed {seed}: a PoI was re-visited")


class ReportDeadline(unittest.TestCase):
    def test_every_report_lands_within_10s_of_arrival(self):
        for seed in SEEDS:
            for v in _run(seed)["visits"]:
                if v["reported_t_s"] is None:
                    continue
                self.assertLessEqual(v["reported_t_s"] - v["arrive_t_s"], config.MAX_DETECT_TO_REPORT_S + 1e-6, f"seed {seed}: PoI {v['poi']} reported late")

    def test_reported_pois_were_inside_relay_reach(self):
        # The physical ceiling behind the low completion rate: nothing
        # outside n_uav * r_comm of base can ever be reported.
        for seed in SEEDS:
            t = _run(seed)
            base = tuple(t["conditions"]["base_pos"])
            reach = t["conditions"]["max_chain_reach_m"]
            pos = {p["id"]: (p["x_m"], p["y_m"]) for p in t["pois"]}
            for v in t["visits"]:
                if v["reported_t_s"] is not None:
                    self.assertLessEqual(link.distance(base, pos[v["poi"]]), reach + config.ARRIVAL_TOLERANCE_M)


class SpeedLimit(unittest.TestCase):
    def test_no_uav_exceeds_5_mps(self):
        for seed in SEEDS:
            last: dict = {}
            for p in _run(seed)["positions"]:
                if p["role"] == "base":
                    continue
                prev = last.get(p["uav"])
                if prev is not None and p["t_s"] > prev[0]:
                    v = math.hypot(p["x_m"] - prev[1], p["y_m"] - prev[2]) / (p["t_s"] - prev[0])
                    self.assertLessEqual(v, 5.0 + 1e-6, f"seed {seed}: uav {p['uav']} at {v:.2f} m/s, t={p['t_s']}")
                last[p["uav"]] = (p["t_s"], p["x_m"], p["y_m"])


class Determinism(unittest.TestCase):
    def test_same_seed_same_run(self):
        a = survey.run_dynamic(seed=7, n_uav=4)
        b = survey.run_dynamic(seed=7, n_uav=4)
        for k in ("run_start_utc", "run_end_utc"):
            a.pop(k)
            b.pop(k)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
