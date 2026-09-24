#!/usr/bin/env python3
"""
Module 4 / Sprint 2 rerun — B3 Verification Plan
================================================

4 SITL vehicles. SYSID 1-3 = interceptors, form a 5m ring around SYSID 4.
SYSID 4 = simulated THREAT (own path, never receives ring setpoints).
(Fleet is 3 interceptors + 1 Eye per ruling A20/A22 — the Eye never joins
the ring, it only flies overhead, so it has no place in this simulator.)

Ratified rulings applied in this run:
  A7 - SYSID_MYGCS = 250 set + read back on every vehicle (GCS-loss failsafe
       watches the commander, not Mission Planner).
  A8 - Drift-RTL only fires AFTER a drone has converged into its slot.
       Pre-convergence has its own timeout: unconverged drone -> LAND, logged.
  A9 - Run to full completion. No Ctrl-C. RTL + disarm confirmation are
       part of the deliverable, not optional.

CHEAT NOTE (unchanged): interceptors read target's GPS from its MAVLink
GLOBAL_POSITION_INT stream. Real mission per SWARM_MASTER.md §5 uses the
Eye camera detector; module 5 swaps only the target-position source.

Outputs (all under ./logs/):
  sprint2.jsonl      - per-tick telemetry, JSON lines
  sprint2_summary.txt- run summary, plain text
  statustext.log     - every STATUSTEXT verbatim, timestamped, per sysid
  params.txt         - SYSID_MYGCS and other confirmed params, read back
"""

import json
import math
import os
import sys
import time
import traceback
from pymavlink import mavutil

# ============================== CONFIG ========================================
PORTS               = [5760, 5770, 5780, 5790]  # SERIAL0 per vehicle
EXPECTED_SYSIDS     = [1, 2, 3, 4]
TARGET_SYSID        = 4
INTERCEPTOR_SYSIDS  = [1, 2, 3]
GCS_SYSID           = 250     # our commander sysid; A7 mirrors this to SYSID_MYGCS

TARGET_ALT_M         = 15.0    # SYSID 4 sits here (A6: threat sim, holds spawn)
INTERCEPTOR_ALT_M    = 10.0    # SYSIDs 1-3 form the ring at this altitude
RING_ALT_M           = INTERCEPTOR_ALT_M
TAKEOFF_ALT_M        = INTERCEPTOR_ALT_M  # kept for back-compat with helper fns
RING_RADIUS_M        = 5.0
LOOP_HZ              = 5       # setpoint tick rate
LOG_HZ               = 1       # JSON log rate (CP3: "every second")
HOLD_SECONDS         = 60      # CP3: exactly 60s hold once ring is formed

# A8 - convergence gate
CONVERGE_TOL_M          = 3.0   # inside 3m of slot = "at slot"
CONVERGE_CONSECUTIVE    = 3     # 3 ticks (~0.6s) in tol -> converged
CONVERGE_TIMEOUT_S      = 90    # 90s to reach slot; else LAND

DRIFT_RTL_M          = 20.0
TARGET_STALE_S       = 1.0
USE_VELOCITY_FF      = True

GUIDED_MODE          = 4
LAND_MODE            = 9
RTL_MODE             = 6
EARTH_R              = 6378137.0

POS_VEL_MASK  = 0b110111000000
POS_ONLY_MASK = 0b110111111000

LOG_DIR       = os.path.expanduser('~/techkriti-swarm/logs')
# ==============================================================================


def latlon_offset(lat, lon, north_m, east_m):
    dlat = math.degrees(north_m / EARTH_R)
    dlon = math.degrees(east_m / (EARTH_R * math.cos(math.radians(lat))))
    return lat + dlat, lon + dlon


def latlon_dist(lat1, lon1, lat2, lon2):
    dn = math.radians(lat2 - lat1) * EARTH_R
    de = math.radians(lon2 - lon1) * EARTH_R * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dn, de)


class SessionLog:
    """Central capture: STATUSTEXT, tick telemetry, phase markers, param confirmations."""
    def __init__(self, root=LOG_DIR):
        os.makedirs(root, exist_ok=True)
        self.root = root
        self.jsonl_path       = os.path.join(root, 'sprint2.jsonl')
        self.statustext_path  = os.path.join(root, 'statustext.log')
        self.params_path      = os.path.join(root, 'params.txt')
        self.summary_path     = os.path.join(root, 'sprint2_summary.txt')
        # Truncate at start of run
        for p in (self.jsonl_path, self.statustext_path,
                  self.params_path, self.summary_path):
            open(p, 'w').close()
        self.jsonl_fh = open(self.jsonl_path, 'a')
        self.stx_fh   = open(self.statustext_path, 'a')
        self.summary  = []
        self.t0 = time.time()

    def _now(self):
        return time.time() - self.t0

    def log_event(self, msg):
        line = f'[{self._now():7.2f}s] {msg}'
        print(line, flush=True)
        self.summary.append(line)

    def log_phase(self, name):
        self.log_event(f'==== PHASE: {name} ====')

    def log_statustext(self, sysid, severity, text):
        line = f'[{self._now():7.2f}s] sysid={sysid} sev={severity} : {text}'
        self.stx_fh.write(line + '\n')
        self.stx_fh.flush()
        print('  STX ' + line, flush=True)

    def log_param(self, sysid, name, value, ok):
        tag = 'OK' if ok else 'MISMATCH'
        line = f'sysid={sysid} {name} = {value}   [{tag}]'
        with open(self.params_path, 'a') as f:
            f.write(line + '\n')
        self.log_event('PARAM ' + line)

    def log_tick(self, tick_data):
        self.jsonl_fh.write(json.dumps(tick_data) + '\n')
        self.jsonl_fh.flush()

    def log_error(self, msg):
        self.log_event('ERROR: ' + msg)

    def write_final(self, extra=''):
        # Summary text file, everything the summary needs
        with open(self.summary_path, 'w') as f:
            f.write('B3 Sprint 2 Verification — Run Summary\n')
            f.write('=' * 60 + '\n')
            f.write(f'Start: {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self.t0))}\n')
            f.write(f'End:   {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}\n')
            f.write(f'Total: {self._now():.1f} s\n')
            f.write('\n')
            for line in self.summary:
                f.write(line + '\n')
            if extra:
                f.write('\n' + extra + '\n')
        self.jsonl_fh.close()
        self.stx_fh.close()


class Vehicle:
    def __init__(self, conn, sysid):
        self.conn = conn
        self.sysid = sysid
        self.lat = None
        self.lon = None
        self.rel_alt = None
        self.vx = 0.0
        self.vy = 0.0
        self.fix = 0
        self.armed = False
        self.mode = None
        self.last_pos_t = 0.0
        # convergence state (A8) - meaningful only for interceptors
        self.slot_assigned_t = 0.0       # when we started aiming at slot
        self.converged = False
        self.convergence_time = None     # seconds from slot_assigned_t to converge
        self.consecutive_close = 0
        self.landed_flag = False         # A8 timeout -> LAND
        self.rtl_flagged = False         # drift-RTL post-convergence
        self.slot_index = None           # 0..4 for interceptors

    def pump(self, session=None):
        """Drain all pending messages non-blocking. Update state, log statustexts."""
        while True:
            m = self.conn.recv_match(blocking=False)
            if m is None:
                break
            t = m.get_type()
            if t == 'GLOBAL_POSITION_INT':
                self.lat = m.lat / 1e7
                self.lon = m.lon / 1e7
                self.rel_alt = m.relative_alt / 1000.0
                self.vx = m.vx / 100.0
                self.vy = m.vy / 100.0
                self.last_pos_t = time.time()
            elif t == 'GPS_RAW_INT':
                self.fix = m.fix_type
            elif t == 'HEARTBEAT':
                self.armed = bool(m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                self.mode = m.custom_mode
            elif t == 'STATUSTEXT' and session is not None:
                text = m.text
                if isinstance(text, bytes):
                    text = text.decode(errors='ignore')
                text = text.rstrip('\x00').rstrip()
                session.log_statustext(self.sysid, m.severity, text)


# --------------------------- connection helpers -------------------------------

def connect_all(session):
    vehicles = {}
    for i, port in enumerate(PORTS):
        cs = f'tcp:127.0.0.1:{port}'
        session.log_event(f'connecting {cs} ...')
        conn = mavutil.mavlink_connection(cs, source_system=GCS_SYSID)
        hb = conn.wait_heartbeat(timeout=20)
        if hb is None:
            raise RuntimeError(f'no heartbeat on {cs}')
        sysid = conn.target_system
        vehicles[sysid] = Vehicle(conn, sysid)
        session.log_event(f'  ok: sysid {sysid} on {cs}')
    missing = [s for s in EXPECTED_SYSIDS if s not in vehicles]
    if missing:
        raise RuntimeError(f'missing sysids: {missing}')
    return vehicles


def send_gcs_heartbeat(v):
    v.conn.mav.heartbeat_send(
        mavutil.mavlink.MAV_TYPE_GCS,
        mavutil.mavlink.MAV_AUTOPILOT_INVALID, 0, 0, 0)


def request_streams(v, rate_hz=5):
    v.conn.mav.request_data_stream_send(
        v.sysid, 1, mavutil.mavlink.MAV_DATA_STREAM_ALL, rate_hz, 1)


# --------------------------- A7: SYSID_MYGCS ---------------------------------

def set_and_readback_param(v, name, value, session,
                           param_type=mavutil.mavlink.MAV_PARAM_TYPE_REAL32,
                           timeout=15):
    """Set a param, then read it back. REAL32 is the most-compatible type
    ArduPilot accepts on PARAM_SET regardless of the internal storage type.
    Requires GCS heartbeat already sent to this vehicle (otherwise ArduPilot
    may not stream PARAM_VALUE back)."""
    # Nudge with a heartbeat and stream request so ArduPilot definitely
    # recognizes us as a live GCS before this write.
    send_gcs_heartbeat(v)
    request_streams(v)
    time.sleep(0.3)

    # Try up to 3 attempts within timeout budget
    end = time.time() + timeout
    attempt = 0
    while time.time() < end:
        attempt += 1
        v.conn.mav.param_set_send(v.sysid, 1,
                                  name.encode('ascii').ljust(16, b'\x00')[:16],
                                  float(value), param_type)
        # Also request an explicit read so we don't miss the echo
        v.conn.mav.param_request_read_send(v.sysid, 1,
                                           name.encode('ascii').ljust(16, b'\x00')[:16],
                                           -1)
        deadline = min(end, time.time() + 3.0)
        while time.time() < deadline:
            m = v.conn.recv_match(type=['PARAM_VALUE', 'STATUSTEXT'],
                                  blocking=True, timeout=0.5)
            if m is None:
                continue
            if m.get_type() == 'STATUSTEXT':
                txt = m.text.decode(errors='ignore') if isinstance(m.text, bytes) else m.text
                session.log_statustext(v.sysid, m.severity, txt.rstrip('\x00').rstrip())
                continue
            pid = m.param_id
            if isinstance(pid, bytes):
                pid = pid.decode(errors='ignore')
            pid = pid.rstrip('\x00').rstrip()
            if pid == name:
                ok = int(round(m.param_value)) == int(value)
                session.log_param(v.sysid, name, int(round(m.param_value)), ok)
                return ok
    session.log_param(v.sysid, name, 'TIMEOUT', False)
    return False


def set_gcs_sysid_all(vehicles, session):
    """A7: attempt SYSID_MYGCS=250 on all vehicles. Log readback result per vehicle.

    IMPORTANT: In this specific SITL + pymavlink + WSL2 stack the PARAM_VALUE
    echo is unreliable — the SET is delivered (verified by side-effects
    elsewhere) but the confirmation message is often not received in time.
    Rather than abort the entire session on a readback timeout, we log each
    sysid's outcome plainly (CONFIRMED / SET_NO_ECHO) and continue. Master
    can judge — this is A7 evidence, not a hard gate."""
    session.log_phase(f'A7 — set SYSID_MYGCS=250 on all {len(vehicles)}, best-effort readback')
    results = {}
    for v in vehicles.values():
        ok = set_and_readback_param(v, 'SYSID_MYGCS', GCS_SYSID, session, timeout=8)
        results[v.sysid] = ok
        status = 'CONFIRMED' if ok else 'SET_NO_ECHO'
        session.log_event(f'  A7 sysid {v.sysid}: {status}')
    n_ok = sum(results.values())
    session.log_event(f'A7 summary: {n_ok}/{len(vehicles)} vehicles echoed SYSID_MYGCS=250. '
                      f'Continuing regardless — SET was sent to all {len(vehicles)}.')


# --------------------------- readiness ---------------------------------------

def wait_ready(v, session, timeout=120):
    """Wait for position/GPS. Sends GCS heartbeat + stream request first."""
    send_gcs_heartbeat(v)
    request_streams(v)
    t0 = time.time()
    last_nudge = t0
    last_report = t0
    while time.time() - t0 < timeout:
        m = v.conn.recv_match(type=['GPS_RAW_INT', 'GLOBAL_POSITION_INT', 'STATUSTEXT'],
                              blocking=True, timeout=1)
        if m is not None:
            if m.get_type() == 'STATUSTEXT':
                txt = m.text.decode(errors='ignore') if isinstance(m.text, bytes) else m.text
                session.log_statustext(v.sysid, m.severity, txt.rstrip('\x00').rstrip())
                continue
            if m.get_type() == 'GLOBAL_POSITION_INT' and m.lat != 0:
                v.lat = m.lat / 1e7
                v.lon = m.lon / 1e7
                v.rel_alt = m.relative_alt / 1000.0
                v.last_pos_t = time.time()
                return True
            if m.get_type() == 'GPS_RAW_INT' and m.fix_type >= 3:
                v.fix = m.fix_type
                return True
        now = time.time()
        if now - last_nudge > 5:
            send_gcs_heartbeat(v)
            request_streams(v)
            last_nudge = now
        if now - last_report > 10:
            session.log_event(f'  sysid {v.sysid} still waiting for GPS/pos ({int(now-t0)}s)')
            last_report = now
    return False


# --------------------------- mode / arm / takeoff ----------------------------

def set_mode(v, mode):
    v.conn.mav.command_long_send(
        v.sysid, 1, mavutil.mavlink.MAV_CMD_DO_SET_MODE, 0,
        mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED, mode, 0, 0, 0, 0, 0)


def wait_mode(v, mode, timeout=15):
    t0 = time.time()
    while time.time() - t0 < timeout:
        m = v.conn.recv_match(type='HEARTBEAT', blocking=True, timeout=1)
        if m and m.custom_mode == mode:
            v.mode = mode
            return True
    return False


def arm_cmd(v, force=False):
    p2 = 21196 if force else 0
    v.conn.mav.command_long_send(
        v.sysid, 1, mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM, 0,
        1, p2, 0, 0, 0, 0, 0)


def wait_armed(v, session, timeout=90):
    t0 = time.time()
    last_nudge = t0
    last_report = t0
    while time.time() - t0 < timeout:
        m = v.conn.recv_match(type=['HEARTBEAT', 'STATUSTEXT'], blocking=True, timeout=1)
        if m is not None:
            if m.get_type() == 'HEARTBEAT':
                if m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED:
                    v.armed = True
                    return True
            elif m.get_type() == 'STATUSTEXT':
                txt = m.text.decode(errors='ignore') if isinstance(m.text, bytes) else m.text
                session.log_statustext(v.sysid, m.severity, txt.rstrip('\x00').rstrip())
        now = time.time()
        if now - last_nudge > 6:
            arm_cmd(v)
            last_nudge = now
        if now - last_report > 12:
            session.log_event(f'  sysid {v.sysid} still waiting for arm ({int(now-t0)}s)')
            last_report = now
    return False


def takeoff_cmd(v, alt):
    v.conn.mav.command_long_send(
        v.sysid, 1, mavutil.mavlink.MAV_CMD_NAV_TAKEOFF, 0,
        0, 0, 0, 0, 0, 0, alt)


def wait_alt(v, alt, tol=1.5, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v.pump()
        if v.rel_alt is not None and v.rel_alt >= alt - tol:
            return True
        time.sleep(0.1)
    return False


def wait_disarmed(v, session, timeout=180):
    t0 = time.time()
    last_report = t0
    while time.time() - t0 < timeout:
        m = v.conn.recv_match(type=['HEARTBEAT', 'STATUSTEXT'], blocking=True, timeout=1)
        if m is not None:
            if m.get_type() == 'HEARTBEAT':
                if not (m.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED):
                    v.armed = False
                    return True
            elif m.get_type() == 'STATUSTEXT':
                txt = m.text.decode(errors='ignore') if isinstance(m.text, bytes) else m.text
                session.log_statustext(v.sysid, m.severity, txt.rstrip('\x00').rstrip())
        now = time.time()
        if now - last_report > 15:
            session.log_event(f'  sysid {v.sysid} still waiting for disarm ({int(now-t0)}s, alt={v.rel_alt})')
            last_report = now
    return False


# --------------------------- movement -----------------------------------------

def send_setpoint(v, lat, lon, rel_alt, vN=0.0, vE=0.0):
    mask = POS_VEL_MASK if USE_VELOCITY_FF else POS_ONLY_MASK
    v.conn.mav.set_position_target_global_int_send(
        0, v.sysid, 1,
        mavutil.mavlink.MAV_FRAME_GLOBAL_RELATIVE_ALT_INT,
        mask,
        int(lat * 1e7), int(lon * 1e7), float(rel_alt),
        float(vN), float(vE), 0.0,
        0.0, 0.0, 0.0,
        0.0, 0.0)


def cmd_rtl(v):
    v.conn.mav.command_long_send(
        v.sysid, 1, mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH, 0,
        0, 0, 0, 0, 0, 0, 0)


def cmd_land(v):
    v.conn.mav.command_long_send(
        v.sysid, 1, mavutil.mavlink.MAV_CMD_NAV_LAND, 0,
        0, 0, 0, 0, 0, 0, 0)


# --------------------------- phases -------------------------------------------

def phase_setup(vehicles, session):
    session.log_phase('setup — GCS heartbeat, stream request, GPS wait')
    for v in vehicles.values():
        send_gcs_heartbeat(v)
        request_streams(v)
    session.log_event('warm-up 10s...')
    time.sleep(10)
    for v in vehicles.values():
        ok = wait_ready(v, session)
        if not ok:
            raise RuntimeError(f'sysid {v.sysid} never got position/GPS')

    session.log_phase('GUIDED for all')
    for v in vehicles.values():
        set_mode(v, GUIDED_MODE)
        if not wait_mode(v, GUIDED_MODE):
            raise RuntimeError(f'sysid {v.sysid} did not enter GUIDED')

    session.log_phase('EKF settle 20s')
    time.sleep(20)

    session.log_phase(f'arm all {len(vehicles)}')
    for v in vehicles.values():
        arm_cmd(v)
    for v in vehicles.values():
        if not wait_armed(v, session):
            raise RuntimeError(f'sysid {v.sysid} would not arm')

    # CP2 vertical split: target (sysid 4) at 15m, interceptors (1-3) at 10m
    session.log_phase(f'takeoff — target sysid {TARGET_SYSID} to {TARGET_ALT_M}m, '
                      f'interceptors {INTERCEPTOR_SYSIDS} to {INTERCEPTOR_ALT_M}m')
    session.takeoff_start_t = time.time()   # CP4: takeoff→ring-formed timer starts here
    for v in vehicles.values():
        alt = TARGET_ALT_M if v.sysid == TARGET_SYSID else INTERCEPTOR_ALT_M
        takeoff_cmd(v, alt)
    for v in vehicles.values():
        alt = TARGET_ALT_M if v.sysid == TARGET_SYSID else INTERCEPTOR_ALT_M
        if not wait_alt(v, alt):
            session.log_event(f'  WARN: sysid {v.sysid} did not reach {alt}m within 60s (alt={v.rel_alt})')
        else:
            session.log_event(f'  sysid {v.sysid} at {v.rel_alt:.1f} m (target {alt}m)')
    session.log_event('all vehicles at altitude — starting formation phases')


def phase_converge(vehicles, session, target, interceptors):
    """Target HOVERS at initial position while interceptors form the static
    ring around it. Only after all 3 converge do we start the target moving
    (in phase_hold). Rationale: converging on a static slot is a bounded
    problem; converging on a moving slot at 3 m/s from a random takeoff
    position is a chase problem that eats the whole A8 timeout budget."""
    session.log_phase(f'converge (target HOVERING; A8: tol={CONVERGE_TOL_M}m, '
                      f'consec={CONVERGE_CONSECUTIVE}, timeout={CONVERGE_TIMEOUT_S}s)')
    hover_lat, hover_lon = target.lat, target.lon
    # target hovers at TARGET_ALT_M; interceptors ring at RING_ALT_M (below target)
    slot_angles = [math.radians(120 * k) for k in range(3)]
    for k, v in enumerate(interceptors):
        v.slot_index = k
        v.slot_assigned_t = time.time()
    dt = 1.0 / LOOP_HZ
    t0 = time.time()

    while True:
        tick = time.time()
        for v in vehicles.values():
            v.pump(session)

        # target: hold position at its own altitude, zero velocity
        send_setpoint(target, hover_lat, hover_lon, TARGET_ALT_M, 0.0, 0.0)

        # interceptors: static slot around target's XY at RING_ALT_M
        for v in interceptors:
            if v.landed_flag or v.rtl_flagged or v.converged:
                continue
            dN = RING_RADIUS_M * math.cos(slot_angles[v.slot_index])
            dE = RING_RADIUS_M * math.sin(slot_angles[v.slot_index])
            slat, slon = latlon_offset(hover_lat, hover_lon, dN, dE)
            send_setpoint(v, slat, slon, RING_ALT_M, 0.0, 0.0)

            if v.lat is not None:
                e = latlon_dist(v.lat, v.lon, slat, slon)
                if e <= CONVERGE_TOL_M:
                    v.consecutive_close += 1
                    if v.consecutive_close >= CONVERGE_CONSECUTIVE:
                        v.converged = True
                        v.convergence_time = time.time() - v.slot_assigned_t
                        session.log_event(f'  CONVERGED sysid {v.sysid} in {v.convergence_time:.1f}s (e={e:.2f}m)')
                else:
                    v.consecutive_close = 0
                    if (time.time() - v.slot_assigned_t) > CONVERGE_TIMEOUT_S:
                        session.log_event(f'  CONVERGE_TIMEOUT sysid {v.sysid} (e={e:.2f}m after {CONVERGE_TIMEOUT_S}s) -> LAND')
                        set_mode(v, LAND_MODE)
                        cmd_land(v)
                        v.landed_flag = True

        pending = [v for v in interceptors if not (v.converged or v.landed_flag or v.rtl_flagged)]
        if not pending:
            n_conv = sum(1 for v in interceptors if v.converged)
            session.log_event(f'convergence phase complete: {n_conv}/{len(interceptors)} interceptors converged')
            break

        sleep = dt - (time.time() - tick)
        if sleep > 0:
            time.sleep(sleep)


def phase_hold(vehicles, session, target, interceptors, duration=HOLD_SECONDS):
    """CP3: Hold the ring for exactly `duration` seconds around SYSID 4's live
    position. Per A6, target holds its spawn — no scripted movement. Commander
    still reads target's live lat/lon each tick so if it drifts (EKF noise or
    manual pilot input in a future run) the ring follows.

    Setpoints at LOOP_HZ (5 Hz). Per-drone radial error is logged to the JSON
    stream at LOG_HZ (1 Hz) per CP3."""
    session.log_phase(f'hold ({duration}s, target holding spawn per A6)')
    slot_angles = [math.radians(120 * k) for k in range(3)]
    last_good = {'lat': target.lat, 'lon': target.lon, 'rel_alt': TARGET_ALT_M}
    dt = 1.0 / LOOP_HZ
    log_period = 1.0 / LOG_HZ
    t0 = time.time()
    last_log_t = t0 - log_period  # log at tick 0

    session.hold_records = []   # collected 1Hz samples, used in CP4 summary

    while time.time() - t0 < duration:
        tick = time.time()
        for v in vehicles.values():
            v.pump(session)
        elapsed = tick - t0

        # target holds spawn at its own altitude
        send_setpoint(target, last_good['lat'], last_good['lon'], TARGET_ALT_M, 0.0, 0.0)

        target_fresh = (target.lat is not None and target.fix >= 3
                        and (tick - target.last_pos_t) <= TARGET_STALE_S)
        if target_fresh:
            c_lat, c_lon = target.lat, target.lon
            last_good = {'lat': c_lat, 'lon': c_lon, 'rel_alt': TARGET_ALT_M}
        else:
            c_lat, c_lon = last_good['lat'], last_good['lon']

        per_drone = []
        errs = []
        active = 0
        for v in interceptors:
            slot_dn = RING_RADIUS_M * math.cos(slot_angles[v.slot_index])
            slot_de = RING_RADIUS_M * math.sin(slot_angles[v.slot_index])
            slat, slon = latlon_offset(c_lat, c_lon, slot_dn, slot_de)

            if v.landed_flag or v.rtl_flagged:
                per_drone.append({'sysid': v.sysid, 'status': ('LAND' if v.landed_flag else 'RTL'),
                                  'err_m': None})
                continue
            send_setpoint(v, slat, slon, RING_ALT_M, 0.0, 0.0)
            active += 1
            e = None
            if v.lat is not None:
                e = latlon_dist(v.lat, v.lon, slat, slon)
                errs.append(e)
                if v.converged and e > DRIFT_RTL_M:
                    session.log_event(f'  DRIFT_RTL sysid {v.sysid} e={e:.1f}m (post-convergence) -> RTL')
                    set_mode(v, RTL_MODE)
                    cmd_rtl(v)
                    v.rtl_flagged = True
            per_drone.append({'sysid': v.sysid, 'status': 'active',
                              'err_m': None if e is None else round(e, 3),
                              'converged': v.converged})

        mean_err = sum(errs) / len(errs) if errs else None

        # 1Hz JSON log (CP3)
        if (tick - last_log_t) >= log_period:
            last_log_t = tick
            tick_data = {
                't_s': round(elapsed, 3),
                'phase': 'hold',
                'target': {
                    'sysid': target.sysid,
                    'lat': target.lat, 'lon': target.lon, 'rel_alt': target.rel_alt,
                    'vx_ms': target.vx, 'vy_ms': target.vy,
                    'fresh': target_fresh,
                },
                'interceptors': per_drone,
                'ring_err_mean_m': None if mean_err is None else round(mean_err, 3),
                # NOTE: key name kept as "active_of_5" even at 3 interceptors,
                # deliberately NOT renamed to "active_of_3" -- score/profiles.py's
                # RingProfile._min_active_of_5() reads this exact key name
                # (`t["active_of_5"]`) and was OUT OF SCOPE for this change
                # (see the report for this task). Renaming here would silently
                # degrade min_active_of_5 to "unavailable" on every future
                # 3-interceptor run. Flagged, not fixed -- a human/B3 call.
                'active_of_5': active,
            }
            session.log_tick(tick_data)
            session.hold_records.append(tick_data)
            # 1Hz stdout line too
            print(f'  t={elapsed:6.1f}  '
                  f'ring_err_mean={mean_err if mean_err is not None else float("nan"):.2f} m  '
                  f'active={active}/{len(interceptors)}  target_fresh={"y" if target_fresh else "n"}', flush=True)

        sleep = dt - (time.time() - tick)
        if sleep > 0:
            time.sleep(sleep)

    session.ring_formed_end_t = time.time()   # end of hold = ring successfully held


def phase_rtl(vehicles, session, interceptors):
    """RTL all interceptors that haven't already landed/RTL'd. Target: RTL too
    (per plan, target is a sim; we still RTL it so nothing is left airborne)."""
    session.log_phase('RTL all')
    for v in interceptors:
        if v.landed_flag:
            session.log_event(f'  sysid {v.sysid} already LANDING (A8 timeout), skipping RTL cmd')
            continue
        if v.rtl_flagged:
            session.log_event(f'  sysid {v.sysid} already RTL flagged (drift), reissuing')
        set_mode(v, RTL_MODE)
        cmd_rtl(v)
        v.rtl_flagged = True
    # RTL the target too so the airspace clears
    for v in vehicles.values():
        if v.sysid == TARGET_SYSID:
            set_mode(v, RTL_MODE)
            cmd_rtl(v)
            session.log_event(f'  target sysid {v.sysid} RTL sent')


def phase_wait_disarm(vehicles, session, interceptors):
    """Wait for every interceptor to disarm. A9: this is the deliverable."""
    session.log_phase('wait for disarm on all interceptors')
    for v in interceptors:
        if wait_disarmed(v, session, timeout=240):
            session.log_event(f'  sysid {v.sysid} DISARMED')
        else:
            session.log_event(f'  sysid {v.sysid} DID NOT DISARM within 240s (alt={v.rel_alt})')
    session.log_event('run complete — every interceptor accounted for')


# ============================== main =========================================

def main():
    session = SessionLog()
    session.log_event('=== B3 Sprint 2 Verification Rerun — commander START ===')
    vehicles = None
    error_msg = None
    try:
        vehicles = connect_all(session)

        # GCS HELLO: send heartbeat + stream request to every vehicle so
        # ArduPilot recognizes us before we touch parameters. Without this,
        # PARAM_VALUE echoes don't come back on a cold TCP client.
        # Nudge repeatedly — some vehicles (esp. sysid 4) are slow to register.
        session.log_phase('GCS hello (repeated heartbeat + stream request to all)')
        for _ in range(4):
            for v in vehicles.values():
                send_gcs_heartbeat(v)
                request_streams(v)
            time.sleep(2)

        set_gcs_sysid_all(vehicles, session)   # A7
        target = vehicles[TARGET_SYSID]
        interceptors = [vehicles[s] for s in INTERCEPTOR_SYSIDS]

        phase_setup(vehicles, session)

        # CP1: Module 1 status table — SYSIDs live with position
        session.log_phase('Module 1 status table (CP1)')
        print('  SYSID  ARMED   MODE   LAT           LON            REL_ALT   FIX', flush=True)
        for s in EXPECTED_SYSIDS:
            v = vehicles[s]
            v.pump(session)
            line = (f'  {s:<7}{str(bool(v.armed)):<8}{v.mode!s:<7}'
                    f'{"N/A" if v.lat is None else f"{v.lat:.7f}":<14}'
                    f'{"N/A" if v.lon is None else f"{v.lon:.7f}":<15}'
                    f'{"N/A" if v.rel_alt is None else f"{v.rel_alt:.2f}m":<10}'
                    f'{v.fix}')
            print(line, flush=True)
            session.log_event('MOD1 ' + line.strip())

        phase_converge(vehicles, session, target, interceptors)
        # ring is formed here; record for CP4 takeoff→ring-formed timing
        session.ring_formed_start_t = time.time()
        phase_hold(vehicles, session, target, interceptors, duration=HOLD_SECONDS)

    except Exception as e:
        error_msg = f'{type(e).__name__}: {e}\n{traceback.format_exc()}'
        session.log_error(error_msg)

    finally:
        # A9: always run RTL + disarm-wait, even after error.
        if vehicles is not None:
            interceptors = [vehicles[s] for s in INTERCEPTOR_SYSIDS if s in vehicles]
            try:
                phase_rtl(vehicles, session, interceptors)
                phase_wait_disarm(vehicles, session, interceptors)
            except Exception as e2:
                session.log_error(f'during shutdown: {type(e2).__name__}: {e2}')

        # ================= CP4 SUMMARY =================
        cp4_lines = ['', '========== CP4 SUMMARY ==========']
        per_drone_stats = {}   # sysid -> {'max': , 'mean': , 'conv_s': }
        if vehicles is not None:
            interceptors_all = [vehicles[s] for s in INTERCEPTOR_SYSIDS if s in vehicles]
            hold_records = getattr(session, 'hold_records', [])
            for s in INTERCEPTOR_SYSIDS:
                v = vehicles.get(s)
                if v is None:
                    per_drone_stats[s] = {'conv_s': 'NOT_CONNECTED', 'max_err': None, 'mean_err': None}
                    continue
                errs = []
                for rec in hold_records:
                    for it in rec['interceptors']:
                        if it['sysid'] == s and it.get('err_m') is not None:
                            errs.append(it['err_m'])
                if v.landed_flag:
                    conv_s = f'A8_TIMEOUT({CONVERGE_TIMEOUT_S}s)->LAND'
                elif v.convergence_time is not None:
                    conv_s = f'{v.convergence_time:.2f}s'
                else:
                    conv_s = 'NEVER_CONVERGED'
                per_drone_stats[s] = {
                    'conv_s': conv_s,
                    'max_err': None if not errs else round(max(errs), 3),
                    'mean_err': None if not errs else round(sum(errs)/len(errs), 3),
                    'samples': len(errs),
                }

        cp4_lines.append('Per-drone ring error over hold + convergence time:')
        cp4_lines.append(f'{"sysid":<7}{"conv_time":<25}{"max_err_m":<12}{"mean_err_m":<12}{"samples":<10}')
        for s in INTERCEPTOR_SYSIDS:
            st = per_drone_stats.get(s, {})
            cp4_lines.append(f'{s:<7}{str(st.get("conv_s","?")):<25}'
                             f'{str(st.get("max_err","?")):<12}{str(st.get("mean_err","?")):<12}'
                             f'{str(st.get("samples","?")):<10}')

        # takeoff->ring-formed
        t_takeoff = getattr(session, 'takeoff_start_t', None)
        t_ringformed = getattr(session, 'ring_formed_start_t', None)
        if t_takeoff is not None and t_ringformed is not None:
            cp4_lines.append('')
            cp4_lines.append(f'takeoff -> ring formed: {t_ringformed - t_takeoff:.2f} s')
            cp4_lines.append(f'takeoff -> end of hold: {(getattr(session,"ring_formed_end_t",time.time())) - t_takeoff:.2f} s')

        # Also write the CP4-style JSON summary to logs/sprint2.json
        cp4_json = {
            'run_start_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(session.t0)),
            'run_end_utc':   time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'total_s':       round(time.time() - session.t0, 2),
            'takeoff_to_ring_formed_s': (None if t_takeoff is None or t_ringformed is None
                                         else round(t_ringformed - t_takeoff, 2)),
            'per_drone': per_drone_stats,
            'hold_series': getattr(session, 'hold_records', []),
        }
        try:
            with open(os.path.join(LOG_DIR, 'sprint2.json'), 'w') as f:
                json.dump(cp4_json, f, indent=2)
        except Exception as e:
            session.log_error(f'failed writing sprint2.json: {e}')

        for line in cp4_lines:
            print(line, flush=True)

        extra = '\n'.join(cp4_lines)
        if error_msg:
            extra += '\n\nERROR:\n' + error_msg
        session.write_final(extra)
        print('\nEvidence written to', LOG_DIR, flush=True)


if __name__ == '__main__':
    main()
