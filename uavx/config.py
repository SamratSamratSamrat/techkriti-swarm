"""Tunable parameters for the UAV-X relay-swarm simulation.

Every value below is either:
  - REAL: taken directly from UAV-X__Resilient_BVLOS_Swarm_Challenge_compressed.pdf,
    with a comment citing which requirement it came from, or
  - PROVISIONAL: a placeholder used only because the rulebook is silent on
    that parameter. Provisional values are guesses, not rules -- change them
    freely.

See uavx/RULES_NOTES.md for the full rulebook-vs-assumption reconciliation.
"""

from __future__ import annotations

import random

# --- REAL: "Evaluation Criteria" (rulebook page 5). Weights sum to 1.0.
# Not enforced anywhere in the sim itself -- kept here so a future scoring
# script has the real numbers instead of guessing them.
EVAL_WEIGHTS = {
    "mission_completion": 0.25,
    "communication_resilience": 0.25,
    "autonomous_relay_role_management": 0.20,
    "fault_recovery_reconfiguration": 0.15,
    "safety_collision_avoidance": 0.10,
    "innovation_technical_merit": 0.05,
}

# --- REAL: exactly one GCS. The Mission Scenario (rulebook page 3) says a
# single "Ground Control Station (GCS) is established outside the affected
# area" -- this is not a team choice.
N_BASE = 1

# --- REAL (A26, 24 Sep 2026): fleet count is still not fixed by the
# rulebook's mission-constraints slide, but A20 already settled this for the
# whole project (3 interceptors + 1 Eye = 4 aircraft, Eye relay-eligible per
# A21). N_UAV's default here has been stale -- every real evidence run has
# passed --n-uav 4 explicitly since A20. Fixed to match rather than left
# pointing at an unused placeholder.
N_UAV = 4

# --- N_POI stays 6 (PROVISIONAL, unchanged) here -- NOT corrected to the
# rulebook's real 10. Same reason as MAX_RELAY_SPEED_MPS above: run()'s
# internal random-scenario path (test_a23_fault_selection.py's `_run()`
# calls survey.run() with no --scenario file, so it draws from
# `_poi_scenario()`, which reads config.N_POI) has seeds re-picked tonight
# against a 6-PoI layout -- changing this to 10 silently reshuffles every
# seed's behaviour and broke the same 6/13 tests. N_POI_A26 = 10 (further
# down this file) is the real rulebook value, used ONLY by run_dynamic()'s
# _dynamic_poi_scenario(). See uavx/RULES_NOTES.md section 14.
N_POI = 6
POI_PRIORITY_LEVELS = (1.0, 2.0, 3.0)  # low / medium / high, drawn uniformly per PoI -- still PROVISIONAL, rulebook doesn't give a priority scheme

# --- REAL (A26, 24 Sep 2026): "Operational area - 1000m x 1000m" per the
# rulebook slide. ARENA_HALF_EXTENT_M=500 already matched this by
# coincidence (it was sized independently, before this slide was seen -- see
# the superseded comment this replaces in git history at commit ad4351c).
# Local flat-earth metres, arena centered at the origin (no lat/lon needed --
# see RULES_NOTES.md on why this sim skips the geometry.py metre<->latlon
# conversion that CLAUDE.md's flight-commander project uses).
ARENA_HALF_EXTENT_M = 500.0  # PoIs are drawn from within this half-extent

# --- REAL (A26, 24 Sep 2026): "Operational center [base] -- 75m -- Operational
# area" per the rulebook diagram. Base sits OUTSIDE the arena, not at its
# center. The diagram shows base to the west of the arena, roughly level with
# its vertical center -- exact attachment point beyond that isn't given, so
# "centered on the near edge" is our reading, not a rulebook number.
BASE_OFFSET_M = 75.0
# WARNING checked against link.py's real BFS reach, not eyeballed: with
# N_UAV=4 (3 usable relays) and R_COMM_M=100 below, the longest possible
# relay chain (base -> r1 -> r2 -> r3 -> surveyor, 4 hops) reaches at most
# 4 * 100 = 400 m from base in a straight line. Base sits
# ARENA_HALF_EXTENT_M + BASE_OFFSET_M = 575 m from arena center and up to
# ~1186 m from the far corner. Most of the 1000x1000 arena -- roughly its
# eastern two-thirds -- is therefore PHYSICALLY UNREACHABLE by any relay
# chain regardless of PoI placement or assignment strategy, not a bug to fix
# in assign.py/reconfig.py. See uavx/RULES_NOTES.md section 13 (A26) for the
# full reachable-region math and how PoI placement accounts for it.

# --- PROVISIONAL: geofence. The rulebook only says (page 5, Safety) that
# UAVs "should not go out of geo-fenced areas" -- no numeric fence is given.
GEOFENCE_HALF_EXTENT_M = ARENA_HALF_EXTENT_M * 1.1

# --- REAL (A26, 24 Sep 2026): "Max comm range - 100m" per the rulebook
# slide -- this was already set to 100.0 from the earlier 150->100 ruling
# (24 Sep 2026, before this slide was seen) for unrelated reasons; now
# directly confirmed rather than guessed. LINK_SOFT_BAND_FRAC remains
# PROVISIONAL -- the rulebook gives a hard cutoff, not a fade-band shape;
# organizers "will provide communication assumptions" (page 5) beyond the
# single number on this slide, which have not arrived.
R_COMM_M = 100.0
LINK_SOFT_BAND_FRAC = 0.8  # PDR is 1.0 out to R_COMM_M * this fraction

# --- MAX_RELAY_SPEED_MPS stays 12.0 (PROVISIONAL, unchanged) here --
# NOT corrected to the rulebook's real 5 m/s. First draft of A26 did change
# this constant globally and it broke 6/13 of test_a23_fault_selection.py's
# tests, because run()'s static-scenario path (tonight's already-verified
# R_COMM_M 150->100 CP1-CP5 evidence pack, section 13) reads this same
# constant and its seeds were timed against 12 m/s. Same split as
# DWELL_S_A26 below: MAX_RELAY_SPEED_MPS_A26 = 5.0 (further down this file)
# is the real rulebook value, used ONLY by run_dynamic(). This one stays a
# guess until run()'s path is deliberately re-verified at 5 m/s too -- see
# uavx/RULES_NOTES.md section 14.
MAX_RELAY_SPEED_MPS = 12.0
# --- REAL (A26, 24 Sep 2026): "UAV max flight time - 20 mins" per the
# rulebook slide. This ALREADY equalled 1200s (20 min) as a PROVISIONAL
# guess -- confirmed correct by coincidence, now REAL. Still "not yet
# enforced by sim.py" -- was fine to leave unenforced as a guess; now that
# it's a real rulebook limit, enforcing it (a relay/surveyor that runs out of
# charge mid-mission) is flagged as a known gap, not built tonight -- see
# uavx/RULES_NOTES.md section 13 (A26).
RELAY_BATTERY_S = 20 * 60.0
RELAY_RECHARGE_S = 5 * 60.0     # time a recharging relay stays unavailable -- still PROVISIONAL, no rulebook number

# --- REAL (A26, 24 Sep 2026): "Mission operation - 45 mins" per the
# rulebook slide. survey.py currently sizes its own run length dynamically
# from the route (_estimate_duration_s) rather than reading this constant --
# kept here as the rulebook's stated ceiling for reference / for a future
# hard-cutoff check ("land by 45 min"), not yet wired in as an enforced cap.
MISSION_DURATION_S = 45 * 60.0

# --- REAL (A26, 24 Sep 2026), not yet enforced -- known gaps, see
# uavx/RULES_NOTES.md section 13 (A26):
MIN_SEPARATION_M = 20.0   # "Min distance between vehicles - 20m"
MAX_ALTITUDE_M = 100.0    # "Operational height max - 100m" -- sim is 2D (x,y
                           # only, see link.py's distance()); altitude is not
                           # modeled and all aircraft are treated as flying at
                           # a common altitude band for comm-range purposes.
MAX_DETECT_TO_REPORT_S = 10.0  # "Max time between POI detection and
                           # reporting to center - 10s" -- conflicts with
                           # DWELL_S=15.0 below; see the DWELL_S comment.

# --- PROVISIONAL: simulation clock.
TICK_HZ = 5.0
TICK_S = 1.0 / TICK_HZ
RUN_DURATION_S = 180.0

# --- PROVISIONAL: packet generation, once a PoI is connected.
PACKET_RATE_HZ = 1.0
BASE_LATENCY_MS = 20.0
PER_HOP_LATENCY_MS = 15.0
LATENCY_JITTER_MS = 5.0

# --- PROVISIONAL: surveyor mission (Path C+ -- one surveyor UAV visits
# priority-ordered PoIs, dwells, reports; see uavx/survey.py). The rulebook
# says the swarm must "survey" PoIs and "continuously relay information back
# to the GCS" (p.3) -- it does not say surveying is a discrete
# fly/dwell/report cycle per PoI, nor does it give a dwell duration, a report
# timeout, or an arrival tolerance. Checked against RULES_NOTES.md and the
# rulebook PDF directly (grepped for "dwell", "loiter", "report", "timeout",
# "second", "minute" -- none of those describe a per-PoI cycle or a number)
# before writing these: all four values below are guesses, not rulebook
# values, and are ours to tune freely.
DWELL_S = 15.0            # PROVISIONAL: time spent at a PoI before reporting
REPORT_TIMEOUT_S = 20.0   # PROVISIONAL: how long to keep retrying a report
ARRIVAL_TOLERANCE_M = 3.0  # PROVISIONAL: "close enough" to count as arrived

# --- A26 (24 Sep 2026), dynamic-spawn mission model ONLY (run(dynamic_spawn=True)
# -- see survey.py). The rulebook's MAX_DETECT_TO_REPORT_S=10.0 (above) makes
# the DWELL_S=15/REPORT_TIMEOUT_S=20 pair above impossible to satisfy (dwell
# alone already exceeds the whole deadline), so the dynamic-spawn path uses
# its own short dwell instead of reusing those two constants. The static-
# scenario path (dynamic_spawn=False, everything CP1-CP6 was verified
# against tonight) is untouched -- still DWELL_S=15/REPORT_TIMEOUT_S=20,
# unaffected by this section. PROVISIONAL split of the 10s budget: the
# rulebook gives the total deadline, not how much of it is "dwelling" vs.
# "reporting" -- 3s dwell (quick sensor read) leaves 7s of margin for a
# report to get through once the surveyor is back in range, which is enough
# for several TICK_S=0.2s retries without being so long it eats the whole
# budget before a single report attempt happens.
DWELL_S_A26 = 3.0

# --- A26 (24 Sep 2026), dynamic-spawn mission model ONLY (run(dynamic_spawn=True)):
# "Max speed - 5m/s" per the rulebook slide -- REAL, but kept as its own
# constant rather than overwriting MAX_RELAY_SPEED_MPS above (see that
# constant's comment for why: doing so broke 6/13 of
# test_a23_fault_selection.py's tests, which time run()'s static-scenario
# path -- tonight's already-verified R_COMM_M 150->100 evidence pack --
# against 12 m/s).
MAX_RELAY_SPEED_MPS_A26 = 5.0
SURVEYOR_SPEED_MPS_A26 = MAX_RELAY_SPEED_MPS_A26

# --- A26 (24 Sep 2026), dynamic-spawn mission model ONLY: "Number of POIs -
# 10" per the rulebook slide -- REAL, but kept separate from N_POI above for
# the same reason as MAX_RELAY_SPEED_MPS_A26 (see N_POI's comment).
N_POI_A26 = 10
# Reuse the existing relay speed cap rather than invent a second number --
# the rulebook gives no separate speed for a "surveyor" role, and one UAV
# moving under the same flight-time constraint as a relay is the simplest
# reading of "identical" UAVs performing different roles.
SURVEYOR_SPEED_MPS = MAX_RELAY_SPEED_MPS
# PROVISIONAL: safety margin added on top of the estimated travel+dwell+
# report time so the tick loop always runs long enough for the last PoI's
# report-timeout window to fully close. Sizing knob only, not a rulebook value.
SURVEY_DURATION_MARGIN_FRAC = 0.15

# --- PROVISIONAL: comms-degradation event (uavx/survey.py only), distinct
# from uav_fail/uav_recharge -- degrades ONE currently-active hop's link
# quality for a time window WITHOUT removing the UAV from the chain (the
# relay stays alive; only that hop's PDR dips). Same status as
# R_COMM_M/LINK_SOFT_BAND_FRAC above: the rulebook says organizers "will
# provide communication assumptions" (p.5) but none are in this document, so
# this is a Stage-1 demo/stress knob, not a rulebook value. See
# uavx/RULES_NOTES.md Task 2.
DEGRADE_FRACTION = 0.40          # when (as a fraction of estimated mission duration) the degradation starts
DEGRADE_DURATION_S = 15.0        # how long the degradation window lasts
DEGRADE_EXTRA_DISTANCE_M = 120.0  # added to the affected hop's effective distance for the window

# --- PROVISIONAL: ruling A23's second fault event (uavx/survey.py only) --
# a full dropout (not a degradation) of one relay that was carrying a PoI
# report when that report succeeded. Fires at the first report success at or
# after this fraction of the estimated mission duration. Same status as
# DEGRADE_FRACTION above: A23 says "~75%", the exact number is ours to tune.
# See uavx/RULES_NOTES.md section 12.
DROPOUT_FRACTION = 0.75

# --- Reproducibility: every run seeds from this value so telemetry is
# deterministic (Stage 1 asks for reproducibility -- rulebook page 4).
RNG_SEED = 42


def new_rng(seed: int = RNG_SEED) -> random.Random:
    return random.Random(seed)
