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

# --- PROVISIONAL: the rulebook only ever says "a fleet of UAVs" (page 3),
# with no fixed count.
N_UAV = 5

# --- PROVISIONAL: number, layout, and priority distribution of Points of
# Interest. The rulebook defines PoIs conceptually (pages 3-4: "designated
# Points of Interest", "prioritize newly emerging high-priority regions")
# but gives no count, positions, or the rule for assigning each one's
# priority weight.
N_POI = 6
POI_PRIORITY_LEVELS = (1.0, 2.0, 3.0)  # low / medium / high, drawn uniformly per PoI

# --- PROVISIONAL: arena size. The rulebook never states an area or
# coordinate bound. Local flat-earth metres, base station fixed at the
# origin (no lat/lon needed -- see RULES_NOTES.md on why this sim skips the
# geometry.py metre<->latlon conversion that CLAUDE.md's flight-commander
# project uses).
ARENA_HALF_EXTENT_M = 500.0  # PoIs are drawn from within this half-extent
# Chosen relative to R_COMM_M and N_UAV below so a chain of relays can just
# about span the arena (N_UAV=5 relays * ~120 m/hop =~ 600 m) -- large
# enough that reconfig.py has real work to do, not so large that most PoIs
# are unreachable regardless of assignment.

# --- PROVISIONAL: geofence. The rulebook only says (page 5, Safety) that
# UAVs "should not go out of geo-fenced areas" -- no numeric fence is given.
GEOFENCE_HALF_EXTENT_M = ARENA_HALF_EXTENT_M * 1.1

# --- PROVISIONAL: link / comms model. The rulebook states the organizers
# "will provide ... communication assumptions" (page 5) but that model was
# not present in the document available when this was written. Until it
# arrives we use a threshold + linear-taper model, parameterised by a single
# comms radius (see link.py for the physical reasoning behind the shape).
R_COMM_M = 150.0
LINK_SOFT_BAND_FRAC = 0.8  # PDR is 1.0 out to R_COMM_M * this fraction

# --- PROVISIONAL: relay motion / battery. Not specified by the rulebook
# beyond "UAVs having limited flight time" (page 3) and "No UAV should be
# without charge" (page 5, Safety) -- the actual numbers are our guess.
MAX_RELAY_SPEED_MPS = 12.0
RELAY_BATTERY_S = 20 * 60.0     # not yet enforced by sim.py -- see RULES_NOTES.md
RELAY_RECHARGE_S = 5 * 60.0     # time a recharging relay stays unavailable

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
