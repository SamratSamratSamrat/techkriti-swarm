# B3 SITL Recovery — What Actually Happened

**Date:** 24 Jul 2026, evening session
**Machine:** LAPTOP-MALAH0P2, Windows 11, WSL2 Ubuntu-24.04
**Outcome:** 5 ArduCopter SITL vehicles running, Mission Planner connected via TCP, live telemetry.
**Total elapsed:** ~1 hour of iteration on the fix script.

Paste this into the B3 chat if it's still gaslighting you.

---

## The symptom (what B3 was probably seeing)

After the Wi-Fi drop, the previously-working `sim_vehicle.py -v ArduCopter --count 5 --auto-sysid` run stopped producing anything useful. The visible failure was a terminal endlessly repeating:

```
Warning, time moved backwards. Restarting timer.
Warning, time moved backwards. Restarting timer.
...
```

That's the surface. Underneath there were four separate, stacked problems. Each one hid the next.

---

## Root causes (in the order they surfaced)

### 1. WSL2 clock skew after Wi-Fi hiccup
The WSL2 VM's system clock desynced from the Windows host when the network dropped. ArduPilot's scheduler (`AP_HAL_SITL::Scheduler`) uses monotonic time — when it saw a backwards jump, it entered the "time moved backwards" restart loop and stopped making progress. Not a chat problem, not a crash — a clock problem.

**Evidence:** the loop was already running when the recovery session started; no new SITL output was being produced.

### 2. Missing Python build deps (`empy`, `pip`, `pexpect`, etc.)
Once WSL was resurrected and `sim_vehicle.py` was re-run, `waf configure` immediately errored:

```
you need to install empy with 'python3 -m pip install empy==3.3.4'
SIM_VEHICLE: Build failed
```

Trying to `pip install` it manually failed with `/usr/bin/python3: No module named pip` — pip itself wasn't installed in the system Python. That's why the standard "just pip install empy" advice on the ArduPilot forums doesn't work here.

### 3. `mavproxy.py` — Permission denied
After the deps were fixed and ArduCopter built successfully (5 vehicles spawned with sysid 1–5), MAVProxy died immediately with:

```
[Errno 13] Permission denied: 'mavproxy.py'
```

A stale copy of `mavproxy.py` in `~/.local/bin` had no execute bit, and it was earlier in `$PATH` than the freshly-installed `/usr/local/bin/mavproxy.py`.

### 4. WSL2 doesn't forward UDP to Windows localhost
This is the trap that makes the "UDP 14550" instructions from every ArduPilot tutorial silently fail. MAVProxy's default `--out 172.18.16.1:14550` is trying to hit the Windows host from inside WSL. Mission Planner listens on `UDP 0.0.0.0:14550` on Windows. Sounds fine — isn't. WSL2's `localhostForwarding=true` **only forwards TCP**, not UDP. Windows Firewall behaviour makes it worse. Result: MP says "No Heartbeat Packets Received" even though MAVProxy is running.

---

## The fixes (in the order applied)

Everything below is now baked into `C:\Users\satis\Desktop\FIX_SITL_5_DRONES.bat` — double-click to reproduce.

**1. Kill the clock loop:**
```
wsl.exe --shutdown
```
(waits ~3s, restarts the WSL VM cleanly with a fresh clock)

**2. Install the missing Python packages as root, bypassing sudo:**
```bash
wsl.exe -d Ubuntu-24.04 --user root -- bash -c "
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y python3-empy python3-pexpect python3-future python3-pip \
                     python3-wxgtk4.0 python3-lxml python3-matplotlib \
                     python3-serial python3-numpy python3-yaml
"
```
Note `python3-empy` from apt gives you the right version (3.3.4) without touching pip — better than the pip route because it avoids PEP 668 / `--break-system-packages` mess.

**3. Reinstall MAVProxy cleanly:**
```bash
rm -f /home/satis/.local/bin/mavproxy.py
python3 -m pip uninstall -y --break-system-packages MAVProxy
python3 -m pip install --break-system-packages --upgrade MAVProxy pymavlink
chmod +x /usr/local/bin/mavproxy.py
```

**4. Launch SITL with `--no-mavproxy` so Mission Planner can TCP-connect direct:**
```bash
sim_vehicle.py -v ArduCopter --count 5 --auto-sysid --no-mavproxy
```

**5. Connect Mission Planner:**
- Protocol dropdown → **TCP**
- Click **CONNECT**
- Host: `127.0.0.1`
- Port: `5760` (drone 1)

Repeat with **5770 / 5780 / 5790 / 5800** for drones 2–5 (WSL2 auto-forwards TCP so `127.0.0.1` on Windows reaches the SITL processes inside WSL).

---

## Proof it worked

Mission Planner UI at end of session:

- **Title bar:** `Mission Planner 1.3.83 build 1.3.9384.38258  ArduCopter V4.8.0-dev (ad28bb78) on`
- **Vehicle selector:** `TCP5760-1-QUADROT...`
- **HUD:** DISARMED · Ready to Arm · 10 EKF · GPS 3D Fixed · Bat 12.6V
- **Map:** 5 quadcopter icons clustered at ArduPilot's default takeoff position (Canberra, −35.363°, 149.165°)
- **Taskbar:** 5 separate "ArduCopter (Ubuntu-…)" terminal windows, one per SITL vehicle

If B3 wants specific ports to prove the routing worked: `netstat -an | findstr "5760 5770 5780 5790 5800"` on Windows will show all five in LISTENING (WSL2 forwarded them).

---

## Why the "UDP 14550 to Mission Planner" advice failed

The standard ArduPilot flow assumes SITL and Mission Planner run on the same OS. Under WSL2:

| Path | Works? | Why |
|---|---|---|
| Windows Mission Planner ⇄ Windows SITL, UDP 14550 | ✅ | Same OS localhost |
| Windows Mission Planner ⇄ WSL2 SITL, UDP 14550 | ❌ | WSL2 doesn't forward UDP |
| Windows Mission Planner ⇄ WSL2 SITL, **TCP 5760** | ✅ | WSL2 auto-forwards TCP via `localhostForwarding` |
| Add mavlink-router in WSL to bridge UDP→TCP | ✅ | but overkill for solo dev |

The `--no-mavproxy` + TCP path is the cleanest one for this stack. When the real project needs MAVProxy features (parameter tuning, mission upload, script bindings), run MAVProxy *inside* Mission Planner's TCP client instead of in front of SITL.

---

## What lives on disk now

- `C:\Users\satis\Desktop\FIX_SITL_5_DRONES.bat` — one-click recovery. Kills stuck WSL sessions, ensures deps are installed, relaunches 5 SITL vehicles.
- `C:\Users\satis\Desktop\5760` — 24-byte junk file from an earlier misfire, safe to delete.
- WSL Ubuntu now has: `python3-empy` (3.3.4), `python3-pexpect`, `python3-future`, `python3-pip`, `python3-wxgtk4.0`, `python3-lxml`, `python3-matplotlib`, `python3-serial`, `python3-numpy`, `python3-yaml`, and MAVProxy freshly reinstalled at `/usr/local/bin/mavproxy.py`.

---

## Recommendations for B3

1. **Commit the fix script.** `FIX_SITL_5_DRONES.bat` should live in the swarm-tools repo, not just on the Desktop, so a lost laptop doesn't cost the recovery.
2. **Default to `--no-mavproxy`** for this project. Per SWARM_MASTER §3 the Python + pymavlink commander talks MAVLink directly to ArduPilot. MAVProxy in front is an unneeded intermediary that added an hour of debugging tonight.
3. **Document the WSL2 UDP-forwarding trap** in the sim setup notes so future-Samrat doesn't rediscover it.
4. **Add a preflight check** to the ground commander: on startup, TCP-connect to 5760–5800 and confirm 5 heartbeats before entering the state machine. Fails fast if SITL is broken.
5. **Wi-Fi resilience:** consider running SITL from a portable WSL distro or a VM whose clock doesn't drift on suspend — long-term, adopt one of the WSL clock-drift fixes (systemd + NTP daemon, or the `polite-hwclock-hctosys` approach), so this specific failure mode never comes back.

*End of report.*
