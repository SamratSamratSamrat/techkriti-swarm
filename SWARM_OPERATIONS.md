# SWARM_OPERATIONS.md — Roles, Boundaries, Timeline
**Companion to SWARM_MASTER.md (the constitution). This file governs HOW we work. Every chat reads both before acting.**
**Status: ACTIVE. Amendments only via the Master chat.**

---

## 1. The End State (Definition of Done — October)

> **Five interceptor drones autonomously form and hold a moving ring around Samrat's FPV quad while a teammate flies it, outdoors, with all failsafes armed and the mission logged and scored.**

That single sentence is the October goal. Read it carefully — it also defines what October does **NOT** require:
- **No Eye, no camera detection by October.** The target feed for the October milestone is the ₹1,200 GPS beacon taped to the FPV quad (its approved development role per SWARM_MASTER §5). Camera detection replaces the beacon **after** October, before Techkriti. The swarm doesn't know or care where the target position comes from — that was the whole point of the layered design.
- No demo polish, no improvement graph, no audience screen. Those come in the Techkriti phase.

A chat pulling work forward from the Techkriti phase into the October phase is scope creep. Flag it.

## 2. Role Charters — what each chat does, and where it STOPS

Every chat is a specialist, not a general. The test for any task: *"Is this mine?"* If there is any doubt, it is not yours — flag it to Master in one line and stop.

### Master chat
- **Does:** rules on conflicts between chats; owns Open Questions; amends SWARM_MASTER and this file; makes trade-off decisions (budget, scope, schedule); reviews phase gates.
- **Does NOT:** write code, pick parts, run sim sessions. If Master starts doing a sub-chat's homework, the structure has failed.

### Hardware chat (B1)
- **Does:** exact parts, India pricing/stock, weight budgets, build sheets, wiring plans, sourcing alternatives when items go out of stock.
- **Does NOT:** change the architecture (frame material, radio bands, sensor strategy — those are Master rulings, even when hardware data motivates them); does not opine on software or sim; does not adjust budget totals by silently swapping quality tiers — cost changes are flagged, not absorbed.
- **Currently: PAUSED** pending Samrat's budget ruling (see Amendment Log A3). No purchases before the Phase 1 gate.

### Software chat (B2)
- **Does:** designs the commander, geometry, state machine, detection pipeline; writes every Claude Code / Cowork prompt; explains the concepts so Samrat can rederive them; maintains CLAUDE.md.
- **Does NOT:** run sessions or debug live sim output (that's Simulation's lane); does not choose hardware; does not relax any safety rule in CLAUDE.md — ever, for anyone.

### Simulation chat (B3)
- **Does:** turns module prompts into session plans (checkpoints, "done when" criteria, data to capture); judges Cowork's results, logs, and tuning notes; declares modules passed or failed.
- **Does NOT:** execute work (that is Cowork's lane now), redesign modules (send it back to B2), change safety parameters to make a test pass (flag to Master), or declare a module done without the module's "done when" evidence.

### Cowork (the Worker — executes for ALL chats)
- **Does:** all hands-on execution for every chat: edits code, runs SITL, reads output, produces logs and evidence. Thinking chats (Master/B1/B2/B3) generate prompts and judgments; Cowork does the work. Any chat needing something *done* writes a Cowork prompt; Samrat carries it.
- **Does NOT:** make design decisions, deviate from the prompt it was given, or touch anything named failsafe, geofence, RTL, or arm beyond what its prompt specifies. If a prompt is ambiguous, it asks instead of improvising.

### Helper chat ("idiot-proofer")
- **Does:** quick foundational questions (what a command does, how to open Ubuntu, what a term means). Cheap, fast, no project decisions.
- **Does NOT:** make any decision that other chats would record. If the answer would change a file in the repo or a line in a project doc, it belongs in a real chat.

### Escalation rules (unchanged, restated)
1. Any cross-domain question → Master, in one line, immediately — don't solve it "just this once."
2. Same bug, 3 failed attempts → escalate the model (Sonnet 5 → Opus 4.8) or the chat (sub-chat → Master).
3. Anything touching failsafes, arming, geofence → never weakened, only escalated.

## 3. The Working Loop (who prompts whom)

```
Samrat decides intent (with Master)
   → thinking chat produces a Cowork prompt
     (B2 for code/modules, B1 for hardware tasks, B3 for session plans)
      → Cowork EXECUTES (code, SITL runs, logs, evidence)
      → B3 judges sim results; the owning chat judges everything else
   → Master reviews at phase gates only
```
**Amendment A5:** Cowork is the single executor for the whole project. Thinking chats think; Cowork does. In time-boxed sprints, Master may hand Cowork a self-contained sprint prompt directly, provided every safety rule in CLAUDE.md travels inside the prompt and B3 still judges the evidence afterward.

Samrat is the only carrier of information between chats. No chat should assume another chat saw anything unless Samrat pasted it.

## 4. Timeline (REVISED 5 September 2026)

P1 slipped ~3 weeks. Per the slippage rule, everything shifts and no gate is skipped. Revised phases:

| Phase | Dates | Work | GATE to pass |
|---|---|---|---|
| **P1 — Sim complete** | now → 26 Sep | Modules 3–5 in SITL. A3 budget ruling still OPEN. | Full mission in sim: 5 drones ring a MOVING target, realistic GPS + wind (A12), every failsafe tested. **Only then: order interceptor #1.** |
| **P2 — First real drone** | 27 Sep → 25 Oct | Build interceptor #1, bench tests (props off), kill-switch drills, real Module 2 on dry days. **A7 confirmed before first flight (A10).** | One real drone repeats its sim behavior. **Only then: order the other 4.** |
| **P3 — Real swarm** | 26 Oct → 30 Nov | Build ×4, real static ring, then moving ring (beacon on a person/RC car first). | 5 real drones hold a moving ring. |
| **P4 — END STATE** | Dec | Beacon on the FPV quad, teammate flies it, swarm rings it. Learning loop scores every mission. | **The §1 sentence, on video, with logs.** |
| **P5 — Techkriti phase** | Jan → fest | Eye refit, detection pipeline (trained on P3/P4 footage — record everything now), beacon→camera swap, improvement graph, rehearsals. | Demo-ready. |

**The October end state in §1 is now a December end state.** Stated plainly rather than quietly: the sentence is unchanged, the date moved. Spring-break build time lands inside P2/P3, which is where hands-on work belongs — but only if A3 is ruled and parts are ordered in time.

**Slippage rule (unchanged):** dates may slip; **gate order may not.** A late, working swarm beats an on-time crash compilation.

**Standing risks:** SpeedyBee stack stock (Hardware watches, doesn't buy), flight windows, school workload, A3 unresolved. Master owns re-planning if any of these bite.

## 4a. Sprint Plan — Weeks 1–3 (5 → 26 September)

Three weeks to the P1 gate. One module per week, plus the viewing capability Samrat has asked for.

### Week 1 (5 – 12 Sep) — Close Module 3, get eyes on the ring
**Goal: Module 3 stamped, and the ring visible live in Mission Planner.**
1. **A7 diagnosis (Cowork, standalone):** second independent client reads `SYSID_MYGCS` on one vehicle. Reports the raw number only. This decides whether A7 is "echo broken" or "never applied."
2. **B2:** audit the param-set path in `ring.py` against the pump/drain race; identify SITL parameters governing GPS accuracy class and wind for the A12 profile.
3. **B3:** judge the Module 3 pack; rule on whether the missing Mission Planner screenshot and unopened SERIAL1 block the stamp; **provision a read-only viewing endpoint** (second TCP port per vehicle, or MAVProxy map as the known-good fallback) — commander ports 5760–5810 stay exclusive.
4. **Cowork:** re-run Module 3 with the viewing endpoint live; capture the ring screenshot that was missing from the pack.
**Week 1 done when:** B3 stamps Module 3, and Samrat has watched the pentagon form on a map in real time.

### Week 2 (13 – 19 Sep) — Module 4 + honest numbers
**Goal: moving-target ring, measured under realistic conditions.**
1. **A12 re-baseline:** re-run Module 3 with GPS degraded to M10 class and wind enabled. Whatever ring error this produces is **mission #1 of the learning loop** — the RTK figures are discarded.
2. **Module 4:** ring tracks a moving SYSID 6 (the prior sprint's 20 m circle at 3 m/s is the reference case), under the same degraded conditions, with velocity feedforward.
3. **Learning loop, first real use:** scoring script reads both runs and reports ring error, convergence time, time-to-ring.
**Week 2 done when:** B3 stamps Module 4 on degraded-GPS evidence, and two scored missions exist.

### Week 3 (20 – 26 Sep) — Module 5 and the P1 gate
**Goal: every failsafe proven, gate passed.**
1. **Module 5:** full state machine IDLE→SEARCH→TRACK→RING→RTL with each failsafe deliberately triggered — target-feed death (must HOLD, not chase), commander death (fleet must fail safe — this is also the behavioural proof of A7), battery, geofence, single-drone loss with the ring reforming.
2. **Repo hardening:** README, `requirements.txt`, pinned deps, 6-vehicle launcher, install instructions verified from a clean shell.
3. **Master reviews the P1 gate** on B3's stamps for Modules 3, 4, 5.
**Week 3 done when:** P1 gate passes → interceptor #1 may be ordered (A3 permitting).

**If Techfest eligibility comes back positive**, the UAV-X fork decision is made at the end of Week 1 — not before, and never at the cost of these three weeks.

## 4b. Path C+ — the UAV-X Stage 1 approach (adopted 17 Sep 2026)

**The decision in one sentence:** we keep the anti-drone containment mission, add a multi-hop relay layer *inside* it, and also implement UAV-X's own PoI survey mission on the same engine — so the submission completes their mission while the counter-UAS ring is presented as the demonstrated extension.

**Paths considered and rejected:**
- **Path A — submit the ring project unchanged.** Rejected: "mission completion" is 25% of the UAV-X rubric and is defined by *their* scenario (survey designated Points of Interest, prioritise emerging high-priority regions). A containment ring scores zero there however good the relay is, and it leaves us dependent on an organiser exemption whose exact wording is still unconfirmed.
- **Path B — fork a separate disaster-response project.** Rejected: ~2 weeks of throwaway code, pauses Modules 4–5 for longer, and duplicates infrastructure we already have.
- **Path C — ring plus relay only.** Superseded by C+ after council review, for the Path A reason above.

**Why the relay layer is ours, not a competition bolt-on.** Interceptors pursuing a target beyond the ground station's radio range lose their command link. The fix is drones passing MAVLink traffic through each other in a chain back to the GCS, rebuilt automatically when a drone drops out or moves out of range. This is a genuine gap in our own architecture that Techkriti would have forced us to solve anyway. UAV-X moved the date, not the requirement.

**Why the PoI survey mission is cheap.** "Fly to a waypoint, dwell, report, move to the next by priority" is *simpler* than ring geometry and runs on the existing commander, relay layer and scoring harness. Estimated 1–2 days on top of the relay work. It converts mission completion from a near-certain zero into scored points and removes the exemption dependency.

**Scoring map (UAV-X rubric → our system):**

| Criterion | Weight | What answers it |
|---|---|---|
| Mission completion | 25% | PoI survey mission: all points visited, priority-weighted ordering, within time |
| Communication resilience | 25% | Multi-hop relay chain; packet delivery ratio, latency, connectivity downtime |
| Autonomous relay & role management | 20% | Dynamic relay assignment and reassignment; role changes logged |
| Fault recovery & swarm reconfiguration | 15% | Deliberate UAV dropout and link degradation, chain rebuild, ring re-spacing |
| Safety & collision avoidance | 10% | Minimum inter-UAV separation, geofence, battery and GCS-loss failsafes |
| Innovation & technical merit | 5% | Counter-UAS containment as the demonstrated extension; evidence discipline |

**Reproducibility is a graded criterion and an existing strength.** The A9 evidence standard, logged telemetry, run-to-completion rule, and the separation of executor (Cowork) from judge (B3) are unusual for any team at this stage. This goes in the paper as its own section, not as a footnote.

**Timeline to 27 Sep:** relay layer complete and scored by 21 Sep; PoI survey mission on top of it; UI v1 by 23 Sep; demonstration run recorded 24 Sep; paper drafted in parallel from day one and finalised 26 Sep. **Modules 4–5 and the P1 gate resume 28 Sep.**

## 5. Amendment Log (rulings since SWARM_MASTER v1)

- **A1 — Frames are carbon.** TPU full frames rejected on weight (50–60 g vs ~30 g) and flex (tuning problems). TPU allowed only for non-structural printed parts (GPS mast, antenna mounts, camera mount). *Supersedes the TPU frame wording in SWARM_MASTER §2.*
- **A2 — Cowork executes simulation sessions.** B3 plans and judges; Cowork runs code against SITL. (This file, §2–3.)
- **A3 — OPEN: budget.** Real total ≈ ₹1,26,000 vs ₹99,000 snapshot. Options: (a) fund the gap, (b) 4 interceptors (square ring, saves ~₹22k), (c) cheaper FC/motors with quality risk. **Samrat rules by 1 Aug.** Hardware paused until then.
- **A4 — October end state and phase gates.** (§1, §4 of this file.)
- **A5 — Cowork is the single executor for the whole project.** Thinking chats think; Cowork does. In time-boxed sprints, Master may hand Cowork a self-contained sprint prompt directly, provided every safety rule in CLAUDE.md travels inside the prompt and B3 still judges the evidence afterward. (§2–3 of this file.)
- **A6 — Six SITL vehicles ratified, SIM ONLY.** In simulation, SYSID 6 is the **simulated THREAT** — a stand-in target that hovers/moves so the ring has something real to track. It is NOT the Eye, it never joins the formation, and it never receives ring setpoints. SWARM_MASTER §2 is intact: the real fleet remains 5 interceptors; the Eye never flies in SITL (it is a camera platform, not a swarm member). Resolves B3's scope flag of 2 Aug; the sprint's `--count 6` was Master-authorized.
- **A7 — SYSID_MYGCS pinned to the commander (250) on every vehicle, SITL and hardware.** RATIFIED. The GCS-loss failsafe must watch the entity actually commanding the fleet, not whichever Mission Planner client happens to attach. With this set: MP disconnecting is harmless; the commander dying still fails the fleet safe — exactly the intended design in CLAUDE.md. Mission Planner remains a read-only viewer on SERIAL1 ports.
- **A8 — DRIFT_RTL_REQUIRE_CONVERGED = True.** RATIFIED with one condition: the not-yet-converged phase must carry its own timeout — a drone that cannot reach its slot within the configured limit goes to LAND/RTL and is reported. No drone may loiter unconverged indefinitely. Any future change to either value returns to Master.
- **A9 — Evidence standard (restated as a ruling).** No module passes without B3's pre-specified "done when" artifacts: the JSON log, the run summary, and verbatim STATUSTEXT. A build log ending in Ctrl-C is not evidence. Cowork sessions must run to completion — through RTL and disarm — before the terminal is touched.

- **A10 — Module 3 passes on flight behaviour; A7 is not a sim gate but IS a hardware gate.** The 5 Sep rerun met its mission goals (6 vehicles connected, ring formed at 5 m/72°, held 60 s, converged 2.21 s each, clean RTL and disarm at 211 s, A9 satisfied). `SYSID_MYGCS` readback failed 0/6. Ruling: a mis-attributed GCS failsafe cannot injure anyone in SITL, so A7 does **not** block Module 4 or 5 in simulation. A7 confirmed by independent readback is a **hard gate before P2** — no real drone flies without it. Formal module stamp remains B3's.
- **A11 — mavlink-router REJECTED pending root cause.** Cowork proposed adopting mavlink-router to fix the A7 echo and SERIAL1 exposure. Rejected for now on two grounds: (i) it is new infrastructure proposed for a bug nobody has root-caused — the evidence cannot yet distinguish "PARAM_VALUE echo lost" from "PARAM_SET never applied," and Cowork's report asserts the former without showing the side-effect it claims verifies it; (ii) the proposal routes Mission Planner over UDP 14550–14555, and WSL2 does not forward UDP — the exact failure already documented in SWARM_B3_SITL_RECOVERY.md. Diagnose first. If mavlink-router is later adopted, **TCP endpoints only.**
- **A12 — No baseline or published figure may come from a default-SITL run.** The 5 Sep run reported `FIX 6` (RTK Fixed, centimetre-class) and zero wind, producing mean ring errors of 4.6–6.8 cm. Real hardware is an M10-class module at roughly 1.5–2 m, outdoors, in wind. That run therefore measures the geometry and the plumbing, **not achievable accuracy.** Ruling: (i) the learning-loop baseline must come from a run with GPS degraded to M10 class and wind enabled; (ii) no sub-metre accuracy figure from default SITL may appear in any competition submission, proposal, or improvement graph; (iii) the 4–5 m ring spacing in SWARM_MASTER §3.1 stands and remains untested — Week 2 tests it.
- **A13 — Mission Planner is a read-only viewer on its own endpoint, never a commander port.** Commander owns TCP 5760/5770/5780/5790/5800/5810 exclusively. `ports.txt` (5 Sep) confirms SERIAL1 was never opened under `--no-mavproxy`, so a viewing endpoint must be provisioned before Mission Planner is attached to a live mission. Provisioning is B3's lane; MAVProxy's map module is the known-good fallback. Attaching MP to a commander port reproduces the Sprint 1 GCS-failsafe RTL.

- **A14 — The parameter is `MAV_GCS_SYSID`, not `SYSID_MYGCS`.** ArduCopter 4.8.0-dev renamed it; ArduPilot silently discards PARAM_SET for unknown names, with no ack and no error — which is the exact "0/6 vehicles ever echo" symptom seen across every prior sprint. Confirmed independently via a raw MAVProxy FTP parameter dump on a clean boot: `MAV_GCS_SYSID` present at default 255, `SYSID_MYGCS` absent from all ~1,387 parameters. A7's intent is unchanged; only the name was wrong. **A10's hardware gate is now SATISFIED** — all six vehicles confirmed `MAV_GCS_SYSID = 250` by readback across two independent connection cycles (12 Sep). This also retroactively explains the Sprint 1 failsafe RTL: at the default of 255, any attaching client could be treated as the commanding ground station.
- **A15 — Tooling split.** Claude Code writes repo code; Cowork runs SITL sessions and captures evidence. B2 writes the Claude Code prompts (Master does not write them directly — a lapse on 17 Sep, corrected). B3 plans and judges sessions.
- **A16 — The mission UI is READ-ONLY.** It never sends commands, never arms, never modifies vehicle state, and imports nothing from the commander's control path. A web surface that can command drones is a new path into arming and control logic; that is designed deliberately after Stage 1, never in a 10-day sprint.
- **A17 — UI v1 is ONE panel.** A top-down map showing drone positions and the target, with lines between drones representing the current relay chain, colour-coded by link quality, rooted at the ground station, updating live as the chain reconfigures. No status table, no camera panel, no styling effort. Rationale: the UI scores no points directly, but the demonstration video does, and chain reconfiguration is the single most valuable visible behaviour (45% of the rubric across communication resilience and relay management). A panel-registry pattern is required so later panels drop in without touching existing ones.
- **A18 — Detection is modelled for Stage 1, not built.** The commander ingests a target position with realistic noise injected. No cameras exist in hardware or in simulation. This is stated plainly in the paper as a modelled sensor with the airborne implementation as Phase 2 — standard research practice, not a concealment. Rejected again for this sprint: airborne cameras for triangulation (tilt error of 1–2° at 100 m produces metres of bearing error, and a sideways view puts the target against ground clutter instead of clean sky). No vision work before 28 Sep.

*End of operations document. Last amended 17 September 2026 (A14–A18, §4b Path C+, UAV-X Stage 1 approach).*
