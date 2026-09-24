#!/usr/bin/env python3
"""Independent PRE-FLIGHT gate for the 3-interceptor/1-target (4 SITL vehicle)
rerun. Standalone, read-only-except-for-A7: connects transiently to each
vehicle's SERIAL0 port, confirms heartbeat (Module 1 check), sets and reads
back MAV_GCS_SYSID=250 (the correct post-A14 name -- NOT SYSID_MYGCS), logs
CONFIRMED/TIMEOUT per vehicle explicitly, then disconnects cleanly so
tools/ring.py can own SERIAL0 afterward (A13 - one client per socket)."""
import time
from pymavlink import mavutil

PORTS = [5760, 5770, 5780, 5790]
EXPECTED_SYSIDS = [1, 2, 3, 4]
GCS_SYSID = 250
OUT = "/home/satis/swarm-defence/sitl_run_3uav/preflight_gate3.txt"

lines = []
def log(s):
    print(s, flush=True)
    lines.append(s)

def set_and_readback(conn, sysid, name, value, timeout=10):
    conn.mav.param_set_send(sysid, 1, name.encode('ascii').ljust(16, b'\x00')[:16],
                             float(value), mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
    end = time.time() + timeout
    while time.time() < end:
        conn.mav.param_request_read_send(sysid, 1, name.encode('ascii').ljust(16, b'\x00')[:16], -1)
        deadline = min(end, time.time() + 3.0)
        while time.time() < deadline:
            m = conn.recv_match(type=['PARAM_VALUE'], blocking=True, timeout=0.5)
            if m is None:
                continue
            pid = m.param_id
            if isinstance(pid, bytes):
                pid = pid.decode(errors='ignore')
            pid = pid.rstrip('\x00').rstrip()
            if pid == name:
                return int(round(m.param_value)) == int(value)
    return False

log('=== PRE-FLIGHT: independent MAV_GCS_SYSID gate + Module 1 heartbeat check ===')
log(f'{"sysid":<7}{"port":<7}{"heartbeat":<12}{"MAV_GCS_SYSID":<16}')

results = {}
for sysid_expected, port in zip(EXPECTED_SYSIDS, PORTS):
    cs = f'tcp:127.0.0.1:{port}'
    try:
        conn = mavutil.mavlink_connection(cs, source_system=GCS_SYSID)
        hb = conn.wait_heartbeat(timeout=20)
        if hb is None:
            log(f'{sysid_expected:<7}{port:<7}{"MISSING":<12}{"N/A":<16}')
            results[sysid_expected] = ('MISSING', 'N/A')
            conn.close()
            continue
        actual_sysid = conn.target_system
        hb_status = 'OK'
        ok = set_and_readback(conn, actual_sysid, 'MAV_GCS_SYSID', GCS_SYSID, timeout=10)
        status = 'CONFIRMED' if ok else 'TIMEOUT'
        log(f'{actual_sysid:<7}{port:<7}{hb_status:<12}{status:<16}')
        results[actual_sysid] = (hb_status, status)
        conn.close()
    except Exception as e:
        log(f'{sysid_expected:<7}{port:<7}{"ERROR: " + str(e):<12}{"N/A":<16}')
        results[sysid_expected] = ('ERROR', 'N/A')

n_hb_ok = sum(1 for v in results.values() if v[0] == 'OK')
n_confirmed = sum(1 for v in results.values() if v[1] == 'CONFIRMED')
log('')
log(f'Module 1 heartbeat: {n_hb_ok}/4 OK')
log(f'A7/A14 MAV_GCS_SYSID=250: {n_confirmed}/4 CONFIRMED')
if n_hb_ok < 4:
    log('GATE RESULT: FAIL -- not all vehicles show OK heartbeat. Stop, do not proceed to Module 3.')
else:
    log('GATE RESULT: Module 1 heartbeat gate PASSED (4/4 OK).')
    if n_confirmed < 4:
        log('NOTE: not all vehicles confirmed MAV_GCS_SYSID by readback -- see per-vehicle table above.')

with open(OUT, 'w') as f:
    f.write('\n'.join(lines) + '\n')
print(f'\nwritten to {OUT}')
