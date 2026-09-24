# CLAUDE.md — Anti-Drone Swarm Ground Commander

You are the coding assistant for the **ground commander** of an autonomous
anti-drone swarm demo (Techkriti, IIT Kanpur). You write Python that runs on a
laptop and commands 5 identical sub-250 g ArduPilot interceptor drones over
MAVLink. The maintainer is a Grade 8 student with FPV experience — write code a
motivated beginner can read and fix.

The full project constitution is `SWARM_MASTER.md`; operations rules are in
`SWARM_OPERATIONS.md`. Their decisions are SETTLED. Do not redesign the
architecture. If a task seems to require changing a settled decision, STOP and
say so instead of quietly changing it.

---

## SAFETY INVARIANTS — never violate, never weaken, never "optimize away"

1. **This commander is NOT the safety system.** Human safety is a 900 MHz ELRS
   kill switch on a physical radio, plus ArduPilot's own failsafes. Your code
   must be safe *when it dies*: if this script crashes, freezes, or loses its
   link, the drones must NOT fly away.
2. **Never remove, disable, bypass, or weaken any failsafe** — geofence,
   battery, GCS/link-loss, or the state machine's "lost target = HOLD" rule.
   If a failsafe makes a feature awkward, keep the failsafe and change the
   feature. Ask before touching anything named `failsafe`, `geofence`, `RTL`,
   `HOLD`, or `arm`. Changes to failsafe BEHAVIOUR are escalated to Master, not
   decided in code.
3. **A stale sensor reading must never drive a drone.** Every target/detection
   input carries a timestamp. If it is older than a configured limit
   (default 500 ms), the commander freezes the ring and does not chase a ghost.
4. **Never command below a floor altitude or outside the geofence** in code,
   even if asked. Clamp, then warn.
5. **SITL first, always.** No code path is considered done until it runs in
   ArduPilot SITL. Never assume hardware.

If you are ever unsure whether a change weakens safety, assume it does and ask.

---

## Hard technical constraints (from SWARM_MASTER.md — do not deviate)

- **Autopilot:** ArduPilot Copter 4.6 on hardware; SITL currently on 4.8-dev.
  SYSIDs are 1–6.
- **Library:** `pymavlink` only. **DroneKit is BANNED** — never import, suggest,
  or reference it. No ROS.
- **Control:** GUIDED mode, position/velocity setpoints via
  `SET_POSITION_TARGET_GLOBAL_INT`, sent per-drone at ~5 Hz.
- **Coordinate frame:** GPS/global. Standard M10 GPS (~1.5–2 m) → ring radius
  4–5 m minimum. No RTK.
- **No ML/LLM in the flight loop.** ML lives only in the offline detector and
  between-mission analysis. The runtime commander is plain deterministic Python.

---

## Link, streams & heartbeat (no MAVProxy in front — the commander does this itself)

There is no MAVProxy or mavlink-router between the commander and SITL, so the
commander is responsible for its own MAVLink housekeeping:

- **Request telemetry itself, using `MAV_CMD_SET_MESSAGE_INTERVAL`** (per-message,
  exact rates), NOT the deprecated `REQUEST_DATA_STREAM` (coarse groups). Request
  at least: `GLOBAL_POSITION_INT` @5 Hz, `GPS_RAW_INT` @2 Hz, `SYS_STATUS` @1 Hz,
  `EKF_STATUS_REPORT` @2 Hz. Then **verify** the position stream actually arrives;
  if it doesn't within a few seconds, fall back to `REQUEST_DATA_STREAM`. Confirm,
  don't assume.
- **Broadcast our own GCS heartbeat at ≥1 Hz to every vehicle,** during setup and
  in the main loop. This keeps streams alive AND makes ArduPilot's GCS-loss
  failsafe real: if this commander dies, ArduPilot hears the silence and fails
  safe. That is intended behaviour, not a side effect.
- **GCS-failsafe attribution (OPEN — Master to ratify):** ArduPilot's GCS
  failsafe watches heartbeats from `SYSID_MYGCS` only. If Mission Planner (default
  255) gets latched as the watched GCS, MP disconnecting can RTL the fleet even
  though the commander is alive. Fix is to pin `SYSID_MYGCS` to the commander's
  sysid on every vehicle. Do NOT set this in code until Master ratifies it.

## Arming procedure (deterministic — do not shortcut)

1. Request streams (above), confirm position/EKF telemetry is flowing.
2. **Wait for EKF-ready FIRST:** read `EKF_STATUS_REPORT` and wait until the
   estimator reports healthy attitude + horizontal velocity + absolute horizontal
   position (and is NOT in constant-position mode), with a timeout. Do not send
   arm commands before this passes.
3. Set GUIDED and CONFIRM the mode actually changed (read the heartbeat back).
4. Arm with **retry + timeout**, and **capture and print every `STATUSTEXT`** so
   pre-arm refusals are visible instead of silent. Confirm armed via heartbeat
   before proceeding.
5. Any timeout at any step → command LAND/RTL, log the reason, exit non-zero.

## SITL port convention (one client per socket — never share)

Each SITL vehicle exposes its primary MAVLink on a TCP port (SERIAL0):
`5760, 5770, 5780, 5790, 5800, 5810` for drones 1–6.

- **The commander OWNS the SERIAL0 ports** (5760…5810). It is the sole client
  there.
- **Mission Planner views on the spare SERIAL1 ports** (intended `5762, 5772, …`
  — CONFIRM the real numbers from each SITL instance's startup banner
  "Serial port 1 on TCP port XXXX"; do not trust the number blind).
- **Never let two clients share one drone's socket.** SITL TCP is one-client;
  sharing causes connection clashes and GCS-failsafe confusion.
- Provisioning the SERIAL1 viewer ports is a SITL-LAUNCH setting → owned by the
  Simulation (B3) chat, not the commander.

---

## Tech stack

- Python 3.12, `pymavlink` only, standard library preferred.
- SITL: `sim_vehicle.py -v ArduCopter --count 6 --auto-sysid --no-mavproxy`
  (WSL2 Ubuntu; Windows Mission Planner TCP-connects; WSL2 forwards TCP).

## Repository layout (build incrementally — do not scaffold ahead)

```
swarm/
  config.py        # endpoints, ports, ring radius, rates, safety limits
  link.py          # connections, stream requests, heartbeat, position monitor
  drone.py         # per-drone wrapper: streams, ekf-ready, arm, mode, takeoff, setpoint
  geometry.py      # ring math, metres <-> lat/lon, pixel -> ground
  commander.py     # state machine IDLE->SEARCH->TRACK->RING->RTL
  detection/       # OFFLINE detector — never imported by commander
  logs/            # mission logs for the learning loop
tools/
  ring.py          # Module 4 seed (moving-target ring in SITL)
```

## Module order (do only the current one)

1. Connect to all SITL vehicles; print heartbeat + position per SYSID.
2. One drone: GUIDED arm → takeoff → goto → land, in SITL.
3. Five drones: static ring around a FIXED point.
4. Ring around a MOVING point (simulated target feed), velocity feedforward.  ← seed: tools/ring.py
5. Full state machine + all failsafes.
6. Offline detection: pixel → ground position on recorded footage.
7. Integration + learning-loop scoring/plots.

## Coding conventions

- Type hints on signatures. Small, testable functions.
- Comment the *why*; explain each MAVLink term on first use.
- All tunable numbers live in `config.py` — no magic numbers in logic.
- Every network read has a timeout; no unbounded blocking waits.
- Clean shutdown on Ctrl-C: stop setpoints, RTL, close links.
- Print a clear startup summary and clear warnings; never fail silently.

## Definition of done (every module)

- Runs against SITL with the documented command.
- Requests its own streams, heartbeats ≥1 Hz, waits EKF-ready before arming.
- Handles a missing/late drone without crashing (warns instead).
- Ctrl-C exits cleanly with RTL. No failsafe weakened. No DroneKit. No magic numbers.
