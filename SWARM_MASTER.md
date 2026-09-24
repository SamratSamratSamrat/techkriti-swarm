# Techkriti Anti-Drone Swarm — Master Architecture Document
**Owner:** Samrat (Grade 8) · **Teammate:** Siddharth · **Event:** Techkriti, IIT Kanpur
**Status of this document:** SETTLED. These decisions were argued out and closed. Do not re-litigate them in sub-chats — if new evidence appears, escalate to the Master chat.

---

## 1. Mission
Demonstrate an autonomous counter-drone intercept, outdoors: a surveillance drone ("the Eye") detects a hostile drone using only its downward camera, and a swarm of 5 interceptor drones autonomously forms and holds a moving ring around the threat — no contact, no target cooperation.

## 2. The Fleet (7 aircraft roles, 6 owned)
| Role | Airframe | Job |
|---|---|---|
| **The Eye** (x1) | Samrat's old drone, "Z2500" frame, bottom camera hole — **payload capacity unverified, see Open Questions** | Hovers 30–40 m above the arena, camera straight down, streams video to laptop. Detection only. Never joins the formation. |
| **Interceptors** (x5) | Identical sub-250 g builds: SpeedyBee F405 Mini stack, 3.5" frame (TPU 3D-printed), 1404 motors, Matek M10Q-5883 GPS+compass, ESP32 DroneBridge telemetry, ELRS RX, 4S 650–850 mAh | Form the ring. "Captain" is a software role, not special hardware. Any unit can be swapped for any role. |
| **The Threat** (x1) | Samrat's existing FPV quad, flown manually by a teammate | Plays the hostile drone. Broadcasts nothing. Never touched. |

## 3. Settled Architecture Decisions
1. **Outdoor, GPS-based.** Shared coordinate frame comes free from GPS. Standard M10 accuracy (~1.5–2 m) → ring spacing 4–5 m minimum. No RTK (cost).
2. **ArduPilot Copter 4.6** on every aircraft. Unique `SYSID_THISMAV` 1–6, EKF3, geofence + battery failsafes mandatory.
3. **Laptop is the brain.** Python + **pymavlink** commander sends `SET_POSITION_TARGET_GLOBAL_INT` per drone at ~5 Hz. `mavlink-router` splits streams to the script and Mission Planner. **DroneKit is dead — never use it.**
4. **Links:** ESP32 DroneBridge (MAVLink over Wi-Fi, one router) for telemetry; **hardware safety is 900 MHz ELRS** — all receivers bound to Samrat's RadioMaster Boxer, one global kill switch. Safety never depends on Wi-Fi. Known risk: 2.4 GHz congestion at the fest → upgrade path is SiK multipoint (+₹12k), decided by field testing, not pre-spent.
5. **Detection = the Eye only.** No target GPS feed at the event (Samrat's explicit requirement — realism). Overhead geometry chosen because near-nadir viewing makes tilt error nearly harmless and the ring is a horizontal problem. Detection method: movement-against-background + a learned object detector trained on our own footage. A ₹1,200 ESP32+GPS beacon exists as a **development tool and emergency fallback only**.
6. **Mission Learning Loop.** Every flight: ArduPilot dataflash logs + laptop mission logs + Eye footage → scoring script (ring error, time-to-intercept, detection range) → parameter tuning + detector retraining. The demo includes the improvement graph across missions.
7. **No LLM/ML in the flight control loop.** Too slow, non-deterministic, needs internet. ML lives in exactly two places: the detector (perception) and between-mission analysis. Claude Code is a build tool, not a runtime component.
8. **SITL before spending.** ArduPilot SITL multi-vehicle in WSL2 Ubuntu (`sim_vehicle.py -v ArduCopter --count 5 --auto-sysid`), Mission Planner as map view. Hardware is purchased only after the full mission runs in sim. Gazebo optional later for simulated camera work.
9. **Regulatory:** interceptors stay sub-250 g (nano class). Confirm Techkriti's outdoor autonomous flight permissions EARLY — an indoor/netted ruling would break the GPS assumption and forces a Master-chat redesign.

## 4. Rejected Alternatives (do not resurrect without new evidence)
- **Indoor optical-flow swarm (original Gemini proposal):** no shared coordinate frame; flow drifts; drones would collide.
- **Per-drone LiDAR for target distance:** cheap LiDARs sense one thin line; a moving target is never in it. GPS subtraction gives inter-drone distance free.
- **Two ground tripod cameras (triangulation):** technically sound and easier, but **overruled by Samrat** — wants airborne, self-contained, realistic sensing. Overhead Eye replaces it.
- **Two sideways-looking camera drones:** tilt error × distance wrecks triangulation; sideways view = ground-clutter background. Overhead nadir view fixes both.
- **YOLO from a shaky airborne captain as the primary plan / heavy heterogeneous captain:** fragile; ground compute + identical fleet chosen instead.
- **DroneKit, ROS 2/Aerostack2 (this project), 2 km engagement claims, RTK:** dead / overkill for timeline / physically unaffordable / too costly.

## 5. Open Questions (owned by Master chat)
1. Z2500 frame specs — wheelbase, motor size, battery → is it the Eye or donor parts? (Needs ~60–80 g payload: Pi Zero 2 + camera, 10+ min hover.)
2. Eye's Wi-Fi video range from 40 m altitude — test early.
3. Techkriti flight rules and arena — ask organizers now.
4. Detector approach details: motion detection + which lightweight learned model, trained on own footage.

## 6. Budget Snapshot
5 × interceptor ≈ ₹16,300 each; ground (router, beacon, spares) ≈ ₹12,700; Eye refit (Pi Zero 2 + cam + mount) ≈ ₹5,000. **Total ≈ ₹99,000.** Owned already: laptop, RadioMaster Boxer, FPV threat quad, charger, 3D printer access.

## 7. Build Order
1. SITL: 1 drone scripted takeoff → 5 drones → ring around fixed point → ring follows moving point (beacon-simulated input).
2. Commander hardened: state machine (IDLE→SEARCH→TRACK→RING→RTL), failsafes, lost-target = hold.
3. Buy interceptor #1, replicate sim mission on real hardware, then build remaining 4.
4. Eye refit + detection pipeline on recorded footage, then live.
5. Integration, learning-loop iterations, demo rehearsals with improvement graph.

## 8. Project Chat Structure
- **Master (this chat's successor):** cross-domain conflicts, open questions, architecture changes. Model: Fable 5.
- **Hardware:** part selection, India sourcing, weight budgets. Model: Sonnet 5.
- **Software:** architecture of the commander + detector; writes prompts for Claude Code (CLI runs Sonnet 5, escalate to Opus 4.8 when stuck). Chat model: Opus 4.8.
- **Simulation:** SITL setup, mission runs, log debugging. Model: Sonnet 5, escalate after 3 failed fixes.
- Rule: cheap model for loops, expensive model for decisions. Any chat touching another chat's domain flags it for Master instead of deciding.

---

## Appendix A — Project Description (paste into project settings)
> Building an autonomous anti-drone demonstration for Techkriti (IIT Kanpur): an overhead surveillance drone visually detects a hostile FPV quad (no GPS cooperation) and a swarm of 5 identical sub-250 g ArduPilot interceptors autonomously rings it at 4–5 m, commanded by a Python/pymavlink ground station, with a mission learning loop that measurably improves detection and formation accuracy every flight. SWARM_MASTER.md in project knowledge is the settled constitution — chats must not re-litigate its decisions; conflicts escalate to the Master chat. Builder is a Grade 8 student with FPV experience; explain technical terms in simple words on first use.

## Appendix B — Kickoff Prompts

### B1. Hardware chat (Sonnet 5)
> You are the hardware-selection chat for my anti-drone swarm project. Read SWARM_MASTER.md in project knowledge first — the architecture is settled, your job is parts, not design. Scope: exact components, current India pricing/availability, weight budgets, and build sheets for (a) 5 identical sub-250 g interceptors per the spec in §2, (b) the Eye's camera payload (Pi Zero 2 + camera + mount + power), (c) ground gear. Start by producing a gram-by-gram weight budget for one interceptor proving it lands under 250 g with battery — if it can't, flag it to the Master chat instead of changing the design. Search the web for current prices; don't trust memory. If a part choice affects software or simulation, flag it for the Master chat. Explain any technical term simply the first time you use it.

### B2. Software chat (Opus 4.8)
> You are the software-architecture chat for my anti-drone swarm project. Read SWARM_MASTER.md in project knowledge first — decisions in it are closed. Scope: design the ground commander (Python + pymavlink, 5 Hz guided setpoints, state machine IDLE→SEARCH→TRACK→RING→RTL), the ring-formation geometry around a moving target, and the Eye's detection pipeline (movement-against-background + a lightweight learned detector trained on our own footage; pixel → ground position from a near-overhead camera). I work by having you write prompts that I paste into Claude Code in my CLI — so your main output is precise, self-contained Claude Code prompts plus explanations I can understand (I'm in Grade 8; strong at math, explain jargon on first use, and teach me the geometry rather than hiding it). First task: draft the CLAUDE.md file for my Claude Code workspace, then the prompt for module 1 — connect to 5 SITL vehicles and print live heartbeat + position per SYSID. Never remove or weaken failsafe logic in anything you design.

### B3. Simulation chat (Sonnet 5)
> You are the simulation chat for my anti-drone swarm project. Read SWARM_MASTER.md in project knowledge first. Scope: everything SITL — setup, running multi-vehicle missions, reading logs, debugging sim behavior. I'm on Windows 11; first task: walk me through installing WSL2 Ubuntu 24.04 + ArduPilot SITL step by step, one checkpoint at a time (I confirm each before you continue), until `sim_vehicle.py -v ArduCopter --count 5 --auto-sysid` runs and Mission Planner shows 5 drones on the map. Simple words, explain each command's purpose in one line. If a sim problem looks like a hardware or architecture issue, flag it for the Master chat instead of solving it yourself. After 3 failed attempts at the same bug, tell me to escalate the model.

*End of master document.*
