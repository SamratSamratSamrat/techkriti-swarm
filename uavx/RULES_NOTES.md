# UAV-X Rulebook Notes

Source: `UAV-X__Resilient_BVLOS_Swarm_Challenge_compressed.pdf` (repo root),
the Techfest 2026-27 / PUSHPAK Grand Challenge 1 rulebook. Read in full
before writing any code under `uavx/`. This file records what the rulebook
actually says (in my own words, not verbatim), where it's silent, and how
that lines up with the provisional assumptions the sprint prompt handed in.

## 1. What the rulebook actually says

**UAVs / relays and base station.** The rulebook never gives a fixed UAV
count. The Mission Scenario (p.3) says a GCS "deploys a fleet of UAVs" —
no number. It does fix the base station count implicitly: "A Ground Control
Station (GCS) is established outside the affected area" (singular, p.3).
Team size is capped at 5 members (p.5) — that's people, not UAVs, and easy
to conflate with N_UAV=5 by coincidence.

**Arena / area size.** Not specified anywhere in the document. No bounding
box, no units, no reference scenario map.

**PoI definitions and priority assignment.** PoIs are named conceptually —
"designated Points of Interest (PoIs)" the swarm must survey (p.3) — and the
swarm must "prioritize newly emerging high-priority regions" (p.4). No PoI
count, no positions, and critically **no rule for how a priority weight is
assigned** to a PoI. It's presented as an input the swarm reacts to, not
something the team defines from scratch, but the document that would define
it (organizer-provided "mission specifications, benchmark scenarios" — see
below) isn't this one.

**Exact scoring formula.** Not given. The rulebook provides two things, both
short of a formula:
- Stage-level weights (p.5, "Evaluation Criteria"): Mission Completion 25%,
  Communication Resilience 25%, Autonomous Relay & Role Management 20%,
  Fault Recovery & Swarm Reconfiguration 15%, Safety & Collision Avoidance
  10%, Innovation & Technical Merit 5%.
- A list of "Performance Metrics" per category (p.5) — e.g. under Mission:
  "completion rate, completion time, priority-weighted mission score" — named
  but with no weighting formula, no normalization, no thresholds.

So "priority-weighted mission score" is confirmed as a *named* metric the
organizers care about, but the exact arithmetic (how priority combines with
completion, whether it's linear, capped, normalized per-PoI or per-mission)
is not in this document.

**Link / connectivity model.** Not prescribed. The rulebook states (p.5,
directly after Simulation Framework) that "the organizers will provide
mission specifications, benchmark scenarios, communication assumptions,
evaluation metrics, and a standardized log format" — i.e. a comms model is
coming from them, but is not present in the document available now.

**Stage 1 deliverable.** Both a written artifact and a working simulation,
explicitly (p.4, "Format of Competition — Stage 1: Preliminary Design
Verification"):
- 6–8 page technical proposal
- Software architecture
- Working proof-of-concept simulation
- Source code
- Installation instructions
- Demonstration video

Evaluation focus at Stage 1: "feasibility, novelty, reproducibility, and
preliminary communication-aware autonomy" (p.4). Simulation framework is
open choice — "any open-source simulation framework such as PX4 SITL,
ArduPilot SITL, Gazebo, AirSim, Isaac Sim, ROS/ROS 2, Webots, CoppeliaSim,
ns-3, or equivalent" (p.5) — a plain-Python discrete-event sim (what's built
here) is not named but isn't excluded either; nothing in the rulebook
requires a physics engine for Stage 1.

**UAV failure / recharge / reconfiguration requirements.** Explicit and
central to the challenge, not an add-on (p.4): the swarm must "dynamically
assign relay UAVs," "reconfigure the network when communication degrades or
UAVs fail or return to home for recharging," and "prioritize newly emerging
high-priority regions." Safety constraints (p.5) add: "No UAV should be
without charge and they should not go out of geo-fenced areas." Stage 2
(p.4) explicitly adds "hidden disturbances such as UAV failures,
communication outages, packet loss, and new emergency tasks" on top of this
— confirming failure/reconfig is graded at Stage 1 and stress-tested at
Stage 2.

## 2. Provisional assumptions vs. the rulebook

| Assumption (sprint prompt) | Status | Note |
|---|---|---|
| N_UAV relays (default 5), 1 base station | **PARTIALLY CONFIRMED** | N_BASE=1 is confirmed by the rulebook's singular GCS. N_UAV=5 remains a guess — the rulebook never fixes a fleet size. |
| M PoIs each with a priority weight | **OPEN** | PoIs-have-priority is confirmed conceptually ("prioritize... high-priority regions"); the actual count, layout, and priority-assignment rule are still ours to guess until the organizers' "mission specifications" arrive. |
| 2D positions in metres, local frame, reuse geometry.py's metre<->latlon | **CHANGED** | `swarm/geometry.py` doesn't exist yet in this repo (the flight-commander project's own module order hasn't reached it — see `swarm/` layout in CLAUDE.md). `uavx/` therefore works purely in local flat metres with no lat/lon conversion at all, which is simpler and matches the rulebook's silence on real-world coordinates. If `geometry.py` is built later and the team wants georeferenced PoIs, this can be swapped in without touching `link.py`/`assign.py` — they only consume `(x, y)` tuples. |
| Link model: r_comm=150m threshold + soft band [0.8·r_comm, r_comm] | **OPEN, unchanged** | Confirmed open by the rulebook itself — organizers say they "will provide communication assumptions," not present in this document. Kept as documented guess in `config.py`/`link.py` pending that model. |
| A PoI is CONNECTED if base->...->PoI path exists with every hop UP | **OPEN, unchanged** | No rulebook statement either confirms or contradicts this; it's the standard multi-hop reading of "resilient multi-hop aerial communication network" (p.3) and is what's implemented. |

## 3. Scoring formula vs. `score/profiles.py`

`score/` was empty when `uavx/` was started; it has since been populated by
the other session (not edited here — read-only, per the parallel-safety
rule). `score/profiles.py` now has a `UavxProfile` with a
`_priority_weighted_score` method, so this is now comparable — and it does
**not** match the telemetry contract this file's sim.py emits. Concretely,
in `score/profiles.py`'s `UavxProfile` (lines ~352-499):

| Field / behaviour | `uavx/schema.py` contract (what `sim.py` emits) | What `UavxProfile` actually reads | Effect |
|---|---|---|---|
| link sample PoI key | `link_samples[i]["poi"]` | `s["poi_id"]` | `_connectivity_downtime` finds zero valid samples (`by_poi` stays empty) → always `unavailable`. |
| PoI priority location | `pois[i] = {"id", "priority"}`; per-tick coverage is a separate `poi_completion` list `{"poi", "served_fraction"}` | expects `served_fraction` to already be a key on each `pois[i]` entry, alongside `id` | `_poi_completion` and `_priority_weighted_score` both read `p["served_fraction"]` off `pois`, which our `pois` entries never have → both always `unavailable`. |
| Priority weights | embedded per-PoI in `pois[i]["priority"]` | read from `params["priorities"]`, a dict passed into `compute()` — **not** taken from telemetry at all | `harness.py`'s `cmd_ingest` calls `profile.compute(telemetry, params={})` (harness.py line 72) — always an empty dict — so `_priority_weighted_score` is `unavailable` on every CLI ingest regardless of what's in the telemetry file. |
| Assignment record shape | one row per relay per tick: `{"t_s", "relay_uav", "serves":[poi_id,...]}` | one row per tick with a single `a.get("assignment")` value, compared for equality tick-to-tick to count reallocations | `_relay_reallocations` will count every one of our per-relay rows as a "change" (since `"assignment"` is never a key we set, `cur` is always `None`, and `prev is not None and cur != prev` is `False`... actually both are `None` after the first row, so it undercounts to 0 rather than crashing — either way it doesn't measure what we intend). |
| Event shape | `{"t_s", "type": "uav_fail"/"uav_recharge"/"restored", "uav": relay_id}` | expects `type == "failure"` (not `"uav_fail"`) and keys failures/recoveries by `e.get("poi_id")`, not by UAV id | `_recovery_time` never matches our `"uav_fail"`/`"uav_recharge"` events to our `"restored"` events (wrong type string AND wrong join key) → always `unavailable`. |
| Packets | `{"t_s","poi","delivered","latency_ms"}` | reads `delivered`/`latency_ms` only | **This one matches.** `_packet_delivery_ratio` and `_latency` will work correctly against our telemetry. |

**Net effect:** running `python3 -m score.harness ingest <file> --profile uavx`
against `sim.py`'s output today will silently score packet-delivery-ratio
and latency correctly, and report every other UAVX metric
(`connectivity_downtime_s`, `relay_reallocations`, `recovery_time_s_*`,
`poi_completion`, `priority_weighted_score`) as `unavailable` — not an
error, just quietly empty. This is a genuine reconciliation the user needs
to make, not something to silently "fix" by guessing which side is right:
either `score/profiles.py`'s `UavxProfile` was written against an earlier
or different draft of this contract, or it's still a stub pending a shared
field-name agreement. Two options once both sides are looked at together:
(a) change `uavx/schema.py`/`sim.py` to match `UavxProfile`'s field names
(`poi_id` in link_samples, `served_fraction`+priority merged onto `pois`
entries, a single `assignment` value per tick, `"failure"`/`poi_id`-keyed
events, and pass real priorities via `params` at ingest time), or (b) fix
`UavxProfile` to match the contract in this file. Either is a one-file
change; this file just makes sure it's a deliberate one.

On the rulebook side, the underlying metric name is confirmed real:
"priority-weighted mission score" appears verbatim among the Performance
Metrics (p.5), but as a named metric only — the organizers give no formula
for it, so nothing here is "wrong" relative to the rulebook, only relative
to `UavxProfile`'s current implementation.

## 4. What was built and how to verify it

- `uavx/config.py` — all tunables, each commented REAL (with page citation)
  or PROVISIONAL.
- `uavx/link.py` — `pdr()`/`link_quality()` (threshold + linear-taper model,
  reasoning in the module docstring) and `build_connectivity_graph()` (BFS
  multi-hop reachability from base to every PoI).
- `uavx/assign.py` — `RelayAssigner` interface + `GreedyChainAssigner`
  (serve-highest-priority-first, extend a base->relay->...->PoI chain one
  hop at a time). Swap in a smarter solver later via `get_assigner()`.
- `uavx/reconfig.py` — re-assigns only the relays that lost their spot on
  the backbone after a `uav_fail`/`uav_recharge` event; relays still doing
  useful work keep their exact position (minimises reallocation count).
- `uavx/sim.py` — 5 Hz discrete-time loop: moves relays, samples
  connectivity, generates packets, fires two scripted events (one permanent
  failure, one recharge round-trip), reconfigures, and watches for weighted
  coverage to recover (logs a `restored` event).
- `uavx/schema.py` — the telemetry contract as `TypedDict`s plus a cheap
  `validate()` shape check `sim.py` runs before writing its output.

**Verified by running it:** `python3 -m uavx.sim` from the repo root
produces `uavx/logs/telemetry_seed42.json` (seeded, reproducible). The run
printed:
- 640 packets sent, 640 delivered (only PoIs that are ever connected
  generate packets, and they're all inside the reliable band, so 100%
  delivery on those is expected, not a bug)
- events: `uav_fail` on relay 5 at t=45s → `restored` at t=45s (that relay
  wasn't on any live chain, so losing it cost nothing); `uav_recharge` on
  relay 4 at t=99s → `restored` at t=99s, same reasoning
- `poi_completion`: 3 of 6 PoIs fully connected the whole run, 1 partially
  (0.55 — it only came into range once its relay finished travelling there),
  2 never connected

To feed this into the scoring harness later (not run here — that's
`score/`'s territory):
```
python3 -m score.harness ingest uavx/logs/telemetry_seed42.json --profile uavx
```

## 5. Open items worth flagging back to the team

- **N_UAV is still a guess.** If the organizers' benchmark scenarios (once
  released) specify a fleet size, `config.N_UAV` is the one line to change.
- **Recharge is modeled as an instant availability toggle, not a flight.**
  A "recharging" relay freezes in place and is excluded from the
  connectivity graph rather than actually flying home and back. This kept
  `sim.py` from needing a second travel phase per relay for Stage 1; if
  Stage 2's graded scenarios need the geometry of the trip home to matter
  (e.g. it temporarily blocks a corridor, or its RTB path itself needs
  comms), this is the place to extend.
- **`ARENA_HALF_EXTENT_M` (500 m) is sized relative to `R_COMM_M` (150 m)
  and `N_UAV` (5) purely so the demo run shows partial coverage and real
  reconfig activity** — it's a demo-quality knob, not a rulebook value.
- **Battery life (`RELAY_BATTERY_S`) is defined in `config.py` but not yet
  enforced by `sim.py`** — nothing currently forces a relay to RTB on its
  own from running out of charge; only the two scripted events trigger
  status changes today. Worth closing before this claims to satisfy "No UAV
  should be without charge" (p.5) as anything more than a config knob.

## 6. Path C+ — surveyor mission (`uavx/survey.py`)

A second, separate mission built on the same `link.py`/`assign.py`/
`reconfig.py` layer: one surveyor UAV visits priority-ordered PoIs (fly,
dwell, report, move on); the other `N_UAV - 1` UAVs are relays whose
connectivity objective is the surveyor's *live* position instead of a set
of static PoIs. `sim.py`'s ring/PoI mission is untouched and unrelated —
this is a new entry point, `python3 -m uavx.survey`, not a change to
`sim.py`'s behaviour.

**Rulebook check before writing any of this (grepped the PDF directly, not
just this file, for "dwell", "loiter", "report", "timeout", "second",
"minute", "formula", "weight", "score", "priorit" — full results below):**
the rulebook never describes surveying as a discrete fly/dwell/report cycle
per PoI at all. It says the UAVs "must survey designated Points of
Interest ... and *continuously* relay information back to the GCS" (p.3) —
continuous relay, not a batch dwell-then-report model. The dwell/report
design here is the sprint's own choice (Path C+ from the task prompt), not
a rulebook requirement, and is worth flagging back to the team: if later
scenarios expect continuous telemetry rather than periodic reports, this
mission's report semantics would need revisiting. No count/duration/
formula for any of the following was found anywhere in the document.

**New config (`uavx/config.py`), all PROVISIONAL (rulebook silent on every
one of these):**
- `DWELL_S = 15.0` — time spent at a PoI before attempting to report.
- `REPORT_TIMEOUT_S = 20.0` — how long to keep retrying a report before
  giving up on it permanently.
- `ARRIVAL_TOLERANCE_M = 3.0` — "close enough" to a PoI to start dwelling.
- `SURVEYOR_SPEED_MPS = MAX_RELAY_SPEED_MPS` — reused, not a new guess; the
  rulebook gives no separate speed for a "surveyor" role and the UAVs are
  identical hardware.
- `SURVEY_DURATION_MARGIN_FRAC = 0.15` — sizing knob for the tick loop
  (estimated travel + dwell + one report-timeout margin, plus 15%), not a
  mission parameter.

**Route-scheduler formula (`survey.RouteScheduler` / `GreedyPriorityDistance`):**
at each step, visit the unvisited PoI maximizing
`priority / (distance_from_current_position + epsilon)`, computed once at
mission start (positions/priorities are static — no online re-planning).
PROVISIONAL — the rulebook confirms PoIs carry a priority ("prioritize
newly emerging high-priority regions", p.4) but gives no routing formula.
`RouteScheduler` is an ABC so a smarter planner can be swapped in via
`get_scheduler()` without touching `survey.run()`.

**Dwell / report semantics:** on arrival (within `ARRIVAL_TOLERANCE_M`),
the surveyor dwells for `DWELL_S`. When dwell completes, it checks the
CURRENT `chains[]` entry (a live base-to-surveyor path right now): if
connected, the PoI is reported immediately (`served_fraction=1.0`,
`reported_t_s` logged). If not, `served_fraction=0.5` ("visited,
unreported") and connectivity is re-checked every tick until
`REPORT_TIMEOUT_S` elapses — if it flushes, `served_fraction` becomes 1.0
and `reported_t_s` is logged at that later time; if it times out,
`served_fraction` stays 0.5 permanently. The surveyor moves to the next PoI
the instant `DWELL_S` elapses regardless of report status — movement never
blocks on connectivity. A PoI the route never reaches before the run ends
gets `served_fraction=0.0` (doesn't happen in the seed-42 demo run — the
route always finishes all `N_POI` PoIs well before the tick budget runs out).

**Three new top-level schema fields (additive only — `pois[]` gained
`x_m`/`y_m`, nothing existing was renamed or removed; `score/profiles.py`'s
`UavxProfile` never reads any of the three, confirmed by running the
harness against survey telemetry — see below):**
- `positions[]` — every UAV's `(x_m, y_m)` every tick, including the
  stationary base, tagged `role: "base"|"surveyor"|"relay"`.
- `chains[]` — the single active `base -> relay -> ... -> <surveyor_id>`
  path per tick, with one `link_quality` (PDR) value per hop.
- `visits[]` — one row per PoI visited: `arrive_t_s`, `dwell_end_t_s`,
  `reported_t_s` (`null` if it timed out unreported). Analysis-only.

**The relay/surveyor adapter (the actual "Path C+" re-purposing):** two
different calls into the *unchanged* `assign.py`/`reconfig.py`, matching
how `sim.py` already separates them:
- **Every tick**, `assign.get_assigner().assign()` is called fresh with a
  single-entry `{surveyor_id: surveyor_current_pos}` in place of the
  multi-PoI dict it normally takes — this is the "chase the moving target"
  objective, recomputed continuously as the surveyor moves.
- **Only on a `uav_fail`/`uav_recharge` event**, `reconfig.reconfigure()` is
  called instead (exactly as `sim.py` already does), because it
  specifically minimises how many relays move — reconfiguration still
  triggers on those events only, not on every tick's target drift.

Neither module's internals were touched; `survey._chain_to_surveyor()` is a
thin wrapper around `link.build_connectivity_graph()` (same BFS, unchanged)
that swaps its generic trailing `"poi"` placeholder for the surveyor's real
UAV id and recovers per-hop PDRs (the BFS only returns their product) from
the same node positions it already computed.

**Task 3 fix — dynamic failure target, and a bug found while building it:**
the scripted `uav_fail`/`uav_recharge` events now pick their target relay
from `_relay_ids_on_chain(chain.path)` at the moment the event fires (the
chain computed that same tick, from where relays actually are), not a
relay id sampled up front — this is what the last run's fixed-ID events
missed entirely, per the prompt that started this session.

While wiring that up, the recovery-watch check (`events: "restored"`) had
the same failure mode the *previous* zero-value bug had, for a different
reason: it compared the post-event connectivity against `chain.connected`
from the **same tick's** `chain` variable — but that `chain` was computed
*before* the failure/recharge was applied (it's literally the baseline
snapshot), so `cur == baseline` was true by construction and every event
"restored" on its own tick, immediately, regardless of what actually
happened. Fixed by stamping `watch["event_tick"] = tick` and only
evaluating recovery on later ticks, once a freshly-recomputed `chain`
reflects the real post-event relay positions/status.

**Verified by running it** (`python3 -m uavx.survey`, seed 42, from the
repo root):
- All 6 PoIs visited and reported, in priority/distance order: PoI 0
  (priority 2.0) → PoI 3 (1.0) → PoI 5 (3.0) → PoI 2 (1.0) → PoI 1 (1.0) →
  PoI 4 (1.0). (PoI 5 has the highest priority in this seed but wasn't
  visited first — `GreedyPriorityDistance` is a local greedy, not a global
  optimum; it took PoI 0 first because PoI 0's priority/distance ratio from
  the base was higher than PoI 5's.)
- 171 packets sent, 147 delivered (0.86 PDR) — lower than `sim.py`'s 100%
  because the surveyor mission generates traffic during travel too, not
  only once a PoI is fully connected, so some ticks are mid-transition.
- Two scripted events: `uav_fail` on relay 2 at t=68.6s, `restored` one
  tick later (t=68.8s); `uav_recharge` on relay 4 at t=151.0s, `restored`
  one tick later (t=151.2s). Both chosen relays genuinely were on the live
  chain at fire time (Task 3's fix), but recovery was still near-instant —
  worth being honest about why rather than presenting it as a dramatic
  resilience demo: with the continuous per-tick `assign()` re-solve
  (needed to chase the moving surveyor), the other active relays are
  already well-spread across the field at all times, so losing any single
  one usually leaves an alternate BFS path already physically in place.
  Real disruption would need either fewer spare relays, a narrower
  `R_COMM_M`, or failing more than one relay at once — none of which this
  Stage-1 proof-of-concept scenario does.

**`python3 -m score.harness ingest uavx/logs/survey_telemetry_seed42.json
--profile uavx` (as literally written in the task prompt / earlier in this
file) does NOT work** — `score/harness.py` does `import db` as a bare
top-level import, which only resolves when `score/` itself is on
`sys.path` (i.e. run from inside `score/`), not when it's imported as the
`score` package from the repo root. This is pre-existing in `score/` (not
touched here, per the read-only rule) and affects `sim.py`'s ring-mission
telemetry identically — it's not specific to the survey mission's schema
additions. The working equivalent, matching `score/README.md`'s own
documented usage:
```
cd score && python3 harness.py ingest ../uavx/logs/survey_telemetry_seed42.json --profile uavx
```
Full metrics table from that run:

| metric | value | quality |
|---|---|---|
| packet_delivery_ratio | 0.8596 | measured |
| latency_ms_mean | 55.66 ms | measured |
| latency_ms_p95 | 82.55 ms | measured |
| connectivity_downtime_s | 47.8 s | approx |
| relay_reallocations | **20** | measured |
| recovery_time_s_mean | 0.2 s | measured |
| recovery_time_s_max | 0.2 s | measured |
| poi_completion | 1.0 | measured |
| priority_weighted_score | 1.0 | measured |

`relay_reallocations = 20 > 0` — confirmed, and for a real reason this
time: `assignments[]`'s per-relay `serves` set genuinely changes as (a) the
PoI the surveyor is currently traveling to/dwelling at changes, and (b)
which physical relay id ends up on the live chain shifts as the per-tick
`assign()` re-solve reassigns hop positions from its relay pool. `Section 3`
above (the field-name mismatch between `uavx/schema.py` and the old
`UavxProfile`) is now stale — `score/profiles.py` was updated by the other
session (visible in its file timestamp) to match `uavx/schema.py`'s field
names before this survey work started; every metric above came back
`measured`/`approx`, none `unavailable`.

## 7. Stress-test run — genuinely hard failure + comms degradation + A19 position-hold

This session made three changes to `uavx/survey.py` (`uavx/` only; `score/`
and `ui/` were read-only per the prompt), producing one new telemetry file:
`uavx/logs/stress_test_seed101.json`. It is a separate file from
`uavx/logs/survey_telemetry_seed42.json` on purpose — see below.

### Task 1 — making the relay failure genuinely hard

The old dynamic-failure targeting (Section 6 above, "Task 3 fix") picked
any relay that happened to be on the live chain when the event fired. That
is correct but not necessarily a stress test: with the per-tick continuous
`assign()` re-solve needed to chase the moving surveyor, an idle relay is
often already close enough to instantly plug the gap (Section 6's own
verification of the old behaviour: failures "restored" one tick later,
near-instantly).

Added `_scan_hard_failure_candidates()` (`uavx/survey.py`): before firing
the event, dry-run the surveyor/relay chase with no events at all (same
seed, same route — nothing has diverged yet, so this exactly reproduces
what the real run will compute up to whichever tick gets chosen) and
collect every `(tick, relay_id)` pair on the live chain where **no other
currently-idle relay is within `r_comm` of the failing relay's position**.
`run()` now raises if that search comes back empty, instead of silently
accepting an instant-swap "failure" — see the docstring on `run()` and the
error message it raises.

`GreedyChainAssigner.assign()` (`uavx/assign.py`) parks every relay it
doesn't currently need at the base position (`relay_positions.setdefault(rid,
base_pos)`) rather than spreading idle relays through the field. That turns
out to matter a lot here: idle relays sit at/near base, so failing a
relay on a LATER hop (far from base, close to the surveyor) is naturally
hard — there's nothing nearby to replace it — while failing the FIRST hop
(close to base) would often be trivial. This was **not obvious before
running it**, and it means the standard config did not need tightening for
this run:

- Standard config (`config.N_UAV=5`, `config.R_COMM_M=150.0`), no
  overrides, seed 101: `conditions.n_uav=5`, `conditions.r_comm_m=150.0` in
  the output file — identical to the standard scoring baseline's config.
  **This run's config does NOT differ from the standard baseline** — Task
  1's tightening fallback (`run(n_uav=..., r_comm_m=...)`, wired to
  `--n-uav`/`--r-comm-m` on `python3 -m uavx.survey`) exists and was
  exercised during development, but wasn't needed for the file actually
  shipped. If a future seed's scan comes back empty, use those flags rather
  than editing `config.py`'s global defaults, and update this note with
  whatever was needed.
- Relay 3 failed at t=67.2s. At fire time, no idle relay was within
  `r_comm_m` of it (checked and printed by `_print_resilience_report()`,
  derived straight from the written telemetry — not asserted, verified).
  Chain restored at t=88.0s — **20.8s (104 ticks)**, not the old ~0.2s.

**Only** the *targeting logic* changed (which relay/tick), not the standard
demo config — so re-running `python3 -m uavx.survey` with no flags will now
also search for a genuinely hard failure using the standard config (and, per
the reasoning above, will likely still find one), which **would** produce
different `uav_fail`/`restored` timing than the currently-checked-in
`survey_telemetry_seed42.json` if it's ever regenerated. That file was
**not** regenerated in this session (`ls -la uavx/logs/` still shows its
Sep-18 timestamp) — only the new `stress_test_seed101.json` was written, to
a distinct seed and a distinct filename specifically so nobody confuses the
two.

### Task 2 — comms-degradation event (`link_degraded` / `link_restored`)

New config, `uavx/config.py` (all PROVISIONAL, same status as
`R_COMM_M`/`LINK_SOFT_BAND_FRAC` — the rulebook's promised "communication
assumptions" aren't in the document available now):
`DEGRADE_FRACTION=0.40`, `DEGRADE_DURATION_S=15.0`,
`DEGRADE_EXTRA_DISTANCE_M=120.0`.

`_chain_to_surveyor()` (`uavx/survey.py`) gained two additive, default-off
parameters (`degraded_relay`, `degrade_extra_distance_m`): when set, the
ONE hop terminating at that relay (its incoming edge) gets
`degrade_extra_distance_m` added to its effective distance before the PDR
lookup — simulated via increased effective distance, as suggested in the
task. Connectivity (`chain.connected`, the BFS reachability) is computed on
the **undegraded** distances, so this never breaks the chain or removes the
UAV — only that one hop's `chains[].link_quality` entry dips. No new
telemetry field was needed, as expected; `events[]` gained two new `type`
values (`"link_degraded"`, `"link_restored"`) as an annotation marker,
additive per `Event.type`'s existing `str` typing.

Scheduled relative to `fail_tick` (not `recharge_tick`): first tried at
`DEGRADE_FRACTION` (0.40, between `_FAIL_FRACTION`=0.25 and
`_RECHARGE_FRACTION`=0.55) so it lands in the recovered-and-stable window
after the hard failure but before the recharge event further thins
redundancy. It was originally scheduled after `recharge_tick`, which broke
during development: `RELAY_RECHARGE_S`=300s outlives most survey runs, so a
recharged relay never returns, and for one seed (101, before this
reordering) the chain was permanently down for the entire remaining run —
no active on-chain relay ever existed to degrade, so the event silently
never fired. Fixed two ways, both now in `run()`: (a) the schedule reorder
above, and (b) the fire check now retries every tick from
`degrade_earliest_tick` onward instead of a single fixed tick, so a
transient disconnection doesn't permanently skip the event for the rest of
the run.

**score/'s current `_recovery_time` reader only recognizes
`uav_fail`/`uav_recharge`/`restored` event types (see `score/profiles.py`).
`link_degraded`/`link_restored` will NOT currently be picked up as a scored
recovery event.** This is intentional scope for this task, not a bug —
extending `_recovery_time` (or adding a separate degradation-aware metric)
to `score/profiles.py` is a `score/`-side change, out of scope here per the
read-only rule, and would need its own reconciliation pass the way Section
3 above did for the original field-name mismatch.

Verified in `stress_test_seed101.json`: relay 2's incoming hop degraded
t=115.4s → restored t=130.4s (15.0s, exactly `DEGRADE_DURATION_S`). The
`chains[]` entries for `base -> relay_2` during that window show
`link_quality` dropping to 0.0 for several ticks (not just a partial dip) —
relay 2's hop distance at the time was close enough to base that adding
120m pushed it past `r_comm_m`=150 entirely. That's an honest report of
what `DEGRADE_EXTRA_DISTANCE_M`=120 actually does to a short hop, not a
tuned-for-effect number; a smaller value would give a partial dip instead
of a floor-to-zero one, worth revisiting if the demo wants that instead.

### Task 3 — A19 position-hold (paper-only, NOT a failsafe)

Added `_hold_isolated_relays()` (`uavx/survey.py`), called from `run()`'s
per-tick relay-movement step: for each active relay, reuse
`link.build_connectivity_graph()` unchanged (same BFS as everywhere else in
this codebase) to check whether it has ANY path back to base through the
OTHER active relays. If not, that relay is skipped this tick's movement
call (`_step_toward` is not applied) — it holds its exact position instead
of continuing toward its assigned target, until a path is restored on a
later tick. This mirrors the paper's stated design ("a UAV that loses
every path to the GCS holds position while the other relays reposition to
restore the chain") for visual/demo consistency.

**This is explicitly NOT a simulation of ArduPilot's GCS-loss failsafe.**
CLAUDE.md's safety invariants (`swarm/` — the flight-commander project)
require that failsafe to live on real hardware: a 900 MHz ELRS kill switch
plus ArduPilot's own failsafe state machine, gated behind CLAUDE.md's
Module 4-5 (`swarm/commander.py`'s full state machine + failsafes), reading
real `EKF_STATUS_REPORT`/heartbeat telemetry over MAVLink via `pymavlink`.
None of that exists here: `uavx/` is pure Python, no MAVLink, no autopilot
instance, no hardware. This function only changes where a dot is drawn on
a plot for the paper's benefit — it must never be read as satisfying, or
standing in for, any of CLAUDE.md's actual failsafe requirements.

### Task 4 — verification, reported honestly

Run: `python3 -m uavx.survey --seed 101 --out stress_test_seed101.json`.
Printed by `_print_resilience_report()` (reads straight from the written
telemetry, no numbers adjusted after the fact):

```
relay 3 FAILED at t=67.2s
  idle relay already in comms range at fire time: NO -- genuine gap
  chain restored at t=88.0s (20.8s / 104 ticks later)
comms DEGRADED on relay 2's incoming hop: t=115.4s -> 130.4s
```

`python3 -m score.harness ingest uavx/logs/stress_test_seed101.json
--profile uavx` — **worked as literally written** (the package-import
form), unlike Section 6's note above. That confirms the `score/harness.py`
bare `import db` fix (a separate session's work, this morning, per its file
timestamp) has landed — `score/`'s README invocation
(`cd score && python3 harness.py ingest ...`) is no longer needed. Full
metrics table:

| metric | value | quality |
|---|---|---|
| packet_delivery_ratio | 0.7037 | measured |
| latency_ms_mean | 53.46 ms | measured |
| latency_ms_p95 | 69.37 ms | measured |
| connectivity_downtime_s | 121.2 s | approx |
| relay_reallocations | **19** | measured |
| recovery_time_s_mean | **20.8 s** | measured |
| recovery_time_s_max | **20.8 s** | measured |
| poi_completion | 0.8333 | measured |
| priority_weighted_score | 0.8571 | measured |

`recovery_time_s_mean/max = 20.8s`, well over the old ~0.2s and nowhere
near the "still under ~1 second" case the task asked to flag honestly if
it happened — it didn't happen here, so nothing further was tightened.
`poi_completion` dropped to 0.83 (5 of 6) and two PoIs (1, 4) ended
"visited, UNREPORTED (timed out)" in the printed summary — the reduced
redundancy after `uav_fail` + the later `uav_recharge` (which, as always,
doesn't return within the run — `RELAY_RECHARGE_S`=300s) genuinely cost
mission completion for this seed, not just a chain-restore delay. That's
a real, honestly-observed side effect of stacking a hard failure on top of
this scenario's redundancy, not a bug.

## 8. `sim.py` superseded — moved to `uavx/legacy/`

`sim.py` was the original static-PoI relay simulator: it proved relay
assignment/reconfiguration (`assign.py`/`reconfig.py`) in isolation, against
a fixed set of PoIs rather than a moving target, and its
scripted-failure-missing-the-chain bug (the fixed-ID event pick landing on a
relay never on any live chain, so the failure cost nothing) was found and
fixed in `survey.py` (Section 6 above, "Task 3 fix"), not in `sim.py` itself.
It is superseded as of 18 Sep 2026 and has been moved to `uavx/legacy/sim.py`
(telemetry it produced moved to `uavx/legacy/logs/`), kept for
reproducibility/history, not as an active entry point — `survey.py` is the
Stage-1 simulation to run. Contents unchanged by the move; `uavx/legacy/` is
not imported by anything else under `uavx/`.

**Update, 22 Sep 2026:** the top-level `README.md` (Stage-1 installation
instructions) now exists at the repo root, states `survey.py` (not
`sim.py`) is the Stage-1 simulation to run, and was verified end-to-end
from a fresh shell (setup → `python3 -m uavx.survey` → `python3 -m
score.harness ingest ... --profile uavx` → `python3 -m ui.server
--telemetry ...`) with no deviations from what it documents.

## 9. Ruling A20 (3 interceptors + 1 Eye) — stress-test evidence regenerated, plus two fixes

Master ruling A20 finalized the fleet at 3 interceptors + 1 Eye (4 airframes
total). In `uavx/survey.py`'s model that's `n_uav=4` (`--n-uav 4`):
`surveyor_id = uav_ids[0]` is the Eye, `relay_ids = uav_ids[1:]` (3 of them)
are the interceptors acting as relays. `uavx/logs/stress_test_seed101.json`
(the old 5-UAV evidence) is now stale for that reason and stays on disk
**only** as historical reference — `uavx/logs/stress_test_4uav_seed101.json`
is the current evidence file.

### Task 1 — missions.db dedup conflict: root cause, fix, confirmation

**Root cause:** `score/harness.py`'s `cmd_ingest()` did
`source_path = Path(args.telemetry_json)` — the argv string, verbatim,
never resolved. `db.sha1_of_file()` hashes file BYTES (path-independent,
unaffected), but `db.ingest_run()` stores `str(source_file)` as-is and
`_same_content()` compares it as a plain string. So the exact same file,
ingested once as `uavx/logs/stress_test_seed101.json` (from the repo root)
and once as `../uavx/logs/stress_test_seed101.json` (from inside `score/`,
back when `harness.py` still worked as a bare script pre-db-import-fix),
hashes to the SAME run_id but stores two DIFFERENT `source_file` strings
for it — which `_same_content()`/`ingest_run()`'s dedup check correctly
flagged as a conflict. The check wasn't wrong; the stored path was
genuinely inconsistent.

**Fix (`score/harness.py`, one line):**
`source_path = Path(args.telemetry_json).resolve()` — normalizes to an
absolute, resolved path immediately, before it's used for anything
downstream (hashing, storage, dedup comparison), so the same real file
always gets the same `source_file` string regardless of cwd or
relative-path spelling at the call site.

**Verified:** ingested a fresh (never-before-seen) test file three
different ways — a plain relative path, a `./`-and-`..`-mangled relative
spelling, and an absolute path with a redundant `./` segment — from
`python3 -m score.harness`. All three normalized to the identical absolute
path: `inserted` once, `unchanged` twice, exactly one row in `missions.db`,
no conflict. (Test row deleted afterward — it wasn't real evidence.)

**Existing 5-UAV rows confirmed untouched, not duplicated:** queried
`missions.db` directly before and after the fix —
`stress_test_seed101.json`'s two rows (`8394778d...` and `61c07ff8...`,
two different content-hash generations from the session that produced
that file — not a bug, the file's bytes genuinely differed between
regenerations that session) have the exact same `run_id`, `source_file`,
`ingested_utc`, and metric values as before this session touched anything.
`sprint2.json`'s `ring`-profile row is likewise untouched.

**One expected, one-time transition consequence, NOT a recurrence of the
bug — don't `--force` past this either, read the db directly instead:**
re-running `python3 -m score.harness ingest uavx/logs/stress_test_seed101.json
--profile uavx` AFTER the fix now itself throws the "different source
file" error, because the OLD row still has the PRE-fix relative-path
string (`uavx/logs/stress_test_seed101.json`) and the FIXED code now wants
to store the resolved absolute path for it. This is expected: the fix
normalizes go-forward behavior, it doesn't retroactively rewrite old rows'
stored strings (and shouldn't — that would mean "touching" data a
verification step is explicitly supposed to leave untouched). When Task
4's report below needed that old row's numbers, they were read straight
out of `missions.db` with a read-only query instead of re-ingesting the
file. If this file is ever intentionally re-ingested later, `--force` is
the correct, honest one-time reconciliation of its stored path string to
the new canonical form — that's a deliberate, understood exception, not
"papering over" the root cause a second time.

### Task 2 — 4-UAV stress-test rerun

`python3 -m uavx.survey --seed 101 --n-uav 4 --out stress_test_4uav_seed101.json`.
Tried seed 101 first (same seed as the old 5-UAV evidence, for the closest
possible apples-to-apples comparison) — worked on the first try, no
tightening needed:

- `_scan_hard_failure_candidates()` (Task 1 from the earlier stress-test
  session, unchanged this session) found a genuine gap at the standard
  `R_COMM_M=150.0` — **not tightened**, per A9: relay 3 fails at t=67.2s,
  confirmed no idle relay within `r_comm_m` of it at fire time.
- Comms-degradation event fired cleanly too: relay 2's incoming hop
  degraded t=115.4s → restored t=130.4s (15.0s window, matching
  `DEGRADE_DURATION_S`).
- `conditions` in the written file: `{"n_uav": 4, "r_comm_m": 150.0}` —
  confirms the actual config used, in the evidence file itself.

Not assumed, checked: `python3 -m uavx.survey --help` first, to confirm
`--n-uav` was still the current flag name before using it (it was,
unchanged since the last stress-test session).

### Task 3 — reallocation-cause breakdown

**Where it's tagged (`uavx/survey.py`, at the point it happens, not
guessed from timing afterward):** the tick loop already tracks a
`watch`/recovery-window from the moment a `uav_fail`/`uav_recharge`
reconfigure() call fires until the matching `restored` event confirms the
chain is back — that's ground-truth simulation state, not an inference. A
new parallel variable, `fault_window_cause`, opens at the exact same
moment `watch` does (tagged with whichever event triggered it:
`"relay_failure"` or `"relay_recharge"`) and closes at the exact same
moment `watch` clears. Every `assignments[]` row logged while a fault
window is open carries that cause; every row logged outside one defaults
to `"surveyor_drift"` (the routine per-tick continuous-tracker re-solve,
driven only by the surveyor moving).

**Why not tag by comparing `reconfig.reconfigure()`'s output directly
(tried first, reverted):** the obvious first attempt was tagging whichever
relays `reconfigure()`'s own `moved_relay_ids` (or a fresh
before/after-chain-membership comparison using its `served_pois`) touched,
on the SAME tick it's called. Both attempts produced **zero**
`relay_failure`-tagged rows on a run with a confirmed `uav_fail` event —
traced it to `chain` (and therefore `on_chain`, and therefore this tick's
logged `serves`) being computed BEFORE the event fires each tick (the
existing "chain reflects the pre-event baseline" pattern, documented
already in Section 6 above) — so the tick a fault fires on never shows a
serves-set change itself; the change lands 1+ ticks later, once
`relay_pos` has physically moved. Tagging only the firing tick's row
missed every actual reallocation. The window approach fixes this by
covering every row until the SAME ground-truth signal (`watch`/restored)
confirms recovery, however many ticks that takes.

**`uavx/schema.py`:** `Assignment` gained `cause: NotRequired[str]` —
additive, `NotRequired` specifically so `uavx/legacy/sim.py`'s
assignments (which never had it, and never will — legacy, not touched)
keep validating.

**`score/profiles.py`:** `UavxProfile._relay_reallocations_by_cause()`
replicates `_relay_reallocations()`'s exact counting logic (same
per-relay, sorted, consecutive-row serves comparison) and additionally
tallies the `cause` of each detected change into
`relay_reallocations_relay_failure` / `relay_reallocations_relay_recharge`
/ `relay_reallocations_surveyor_drift` metrics. `relay_reallocations`
itself is untouched — same method, same computation, called first, same
result. If NO assignment record carries a `cause` key at all (any
telemetry predating this change, including the old 5-UAV file), all three
breakdown metrics report `unavailable` with an honest reason — verified
directly against `stress_test_seed101.json`'s raw content: the total came
back `19, measured` (unchanged) while all three breakdown metrics came
back `None, unavailable, "telemetry predates this breakdown"`. No crash,
nothing fabricated.

Verified on the new 4-UAV file: total `relay_reallocations=17` splits as
`relay_failure=3`, `relay_recharge=0`, `surveyor_drift=14` — sums exactly
to the total, cross-checked by independently replicating the counting
logic in a throwaway script against both the 4-UAV and 5-UAV in-memory
runs before touching `profiles.py` at all.

### Task 4 — numbers (5-UAV vs. 4-UAV), see the session report for the full comparison table

Full side-by-side numbers (recovery_time_s, relay_reallocations +
breakdown, packet_delivery_ratio, and every other `UavxProfile` metric) are
in this session's chat report, not duplicated here — this file is the
rulebook/implementation reconciliation record, not the evidence report
itself. Headline: recovery_time_s went from 20.8s (5-UAV) to 21.6s
(4-UAV) — barely different, not the "fewer relays should clearly hurt
recovery" story one might expect going in; see the full report for why
(fewer relays means a NARROWER search space for `_scan_hard_failure_candidates`,
not necessarily a longer recovery once the specific candidate fires) and
for what DID move (poi_completion, packet_delivery_ratio, connectivity
downtime).

### Task 5 — Eye/perception simulator: does not exist in this repo

Grepped the full repo (not just `uavx/`) for "eye", "perception",
"detector", "camera", "B4" in filenames, code, and comments/docstrings.
No Python file, module, or directory anywhere implements a perception
pipeline, a detector, or a camera simulator — `swarm/`'s own planned
layout (CLAUDE.md) lists a `swarm/detection/` module that **does not exist
on disk yet** (`swarm/` currently contains only `swarm/logs/`). No file in
the repo mentions "B4" at all. `SWARM_OPERATIONS.md` ruling A18 states
this outright, already ratified: *"Detection is modelled for Stage 1, not
built. The commander ingests a target position with realistic noise
injected. No cameras exist in hardware or in simulation."* Whatever
learning-loop numbers the paper's B4 section cites, there is currently no
code in this repository that produces them — this needs to be either
built before the paper claims it, or the paper needs to say plainly that
detection is modelled/simulated for Stage 1, per A18's own wording.

## 10. Rulings A21/A22 — `tools/ring.py` retargeted to 3 interceptors, and a `latency_ms_p95` investigation

A21 (Eye is relay-capable; the 4-UAV UAV-X stress test in §9 stands as
final evidence, no N_UAV=3 rerun) and A22 (ring formation reruns at 3
interceptors — fleet is 3 interceptors + 1 Eye per A20, Eye never joins
the ring) don't touch `uavx/` — this section is about `tools/ring.py`
(the `swarm/` SITL ring demo) and a `score/profiles.py` metric question,
not the UAV-X simulation. Recorded here anyway since `uavx/RULES_NOTES.md`
is where this project's paper-evidence bookkeeping lives.

**`tools/ring.py` retargeted, 4 SITL vehicles (SYSID 1-3 interceptors,
SYSID 4 target), not run:** `PORTS`/`EXPECTED_SYSIDS`/`TARGET_SYSID`/
`INTERCEPTOR_SYSIDS` updated; both `slot_angles` occurrences (`phase_converge`
and `phase_hold`) changed from `72°×range(5)` to `120°×range(3)`, confirmed
identical in both places. Every other hardcoded "5"/"6" that would have
printed a wrong number or contradicted the code was also swept and fixed
(log/docstring text in `set_gcs_sysid_all`, `phase_setup`, `phase_converge`,
`phase_hold`) — beyond the 5 explicitly-listed changes, but in the same
spirit as "the docstring shouldn't contradict the code." Flagged, NOT
changed: the `active_of_5` JSON key in `phase_hold`'s tick log —
`score/profiles.py`'s `RingProfile._min_active_of_5()` reads that exact
key name; renaming it would silently degrade `min_active_of_5` to
`unavailable` on every future 3-interceptor run, and `score/` was out of
scope for this change. Left commented in place instead. Full grep results
(everything outside `ring.py` that still encodes the old 6-vehicle/5-interceptor
assumption): `CLAUDE.md:109` (`sim_vehicle.py --count 6`),
`SWARM_OPERATIONS.md:146` (ruling A6, "the real fleet remains 5
interceptors"), and `sprint2.json` (63 historical `"active_of_5": 5`
entries, from the OLD 5-interceptor evidence run — not touched, it's
historical data). None of these were edited — updating ratified-ruling
docs or historical evidence wasn't asked for here and isn't this prompt's
call to make.

**Explicitly out of reach:** the actual SITL launcher
(`sim_vehicle.py --count 6 ...`, invoked from `RUN_RING_DEMO.bat` on the
Desktop) is outside this repo entirely — updating `--count` to match the
new 4-vehicle fleet has to happen there, separately, before any rerun.
This session did not launch SITL, did not run `ring.py`, and did not
touch `missions.db` or `score/harness.py`, per this prompt's scope.

**`latency_ms_p95` — investigated, confirmed NOT a bug:** computed in
`score/profiles.py`'s `UavxProfile._latency()` — nearest-rank percentile
(`ordered[ceil(0.95*n)-1]`) over every delivered packet's `latency_ms`.
Verified the *raw per-packet data* from both `stress_test_seed101.json`
(5-UAV) and `stress_test_4uav_seed101.json` (4-UAV) directly, not just the
computed metric:
- The first 50 packets of both runs (`t_s` up to the fail-event recovery,
  ~t=88s) are byte-for-byte identical, including every `latency_ms` value.
- After that point, every corresponding packet in the 4-UAV run is
  shifted by a constant ~1s (the 4-UAV recovery took 0.8s longer — see
  §9's Task 4 numbers) but carries the IDENTICAL `poi`/`delivered`/
  `latency_ms` value as its 5-UAV counterpart at the un-shifted time.
- Root cause of the shared sequence: `_poi_scenario()`/the route plan
  don't depend on `n_uav` at all (same PoIs, same order, same seed), and
  an idle "spare" relay never joins the active chain (established in §9)
  — so the ACTIVE chain's hop-count, and therefore every packet's
  `latency_ms` draw, is the same regardless of whether the broader pool
  has 4 or 5 relays in it. The RNG draw sequence for packet delivery/
  jitter stays aligned between runs; only the wall-clock `t_s` label
  shifts.
- The specific packet landing at the p95 rank (index 72 of 76 delivered,
  the 4th-highest value) is exactly this kind of shifted-but-identical
  packet: `t_s=90.0` (5-UAV) / `t_s=91.0` (4-UAV), `poi=5`, chain
  `['base','relay_2','relay_4',1]` (3 hops) in both, formula
  `20 + 3*15 = 65` plus a jitter draw of `4.3697...` = `69.36974280579028`
  ms in both files, to full float precision.
- Confirmed the latency FORMULA itself isn't the bug: hop-count genuinely
  varies per packet (5-UAV delivered-packet hop distribution `{1:13, 2:34,
  3:29}`, 4-UAV `{1:13, 2:35, 3:28}` — nearly identical, off by one packet
  right at the boundary where the timing shift lands, exactly as expected).

**Verdict: report it as-is, but the paper should EXPLAIN it, not just cite
it.** `latency_ms_p95` identical to 2 decimal places across two different
fleet sizes is real and reproducible, not a coincidence in the "got lucky"
sense and not a bug to fix — it's a direct, provable consequence of how
this simulation is built (fleet-size-independent PoI/route generation,
idle relays never touching the active chain, a shared RNG prefix). But it
means `latency_ms_p95`, under the CURRENT latency model
(`BASE_LATENCY_MS + hops×PER_HOP_LATENCY_MS + jitter`), is really testing
"worst hop-count geometry this route happens to need," not "does fleet
size affect tail latency" — citing 69.37ms for both configs without that
context could read as an error or cherry-picking to a reviewer. `mean`
differs slightly (53.46 vs 53.16, both 4th-sig-fig apart) because the
packet populations only PARTIALLY overlap (107 vs 108 total sent, diverging
after the timing shift) — that number is a more honest "did anything
actually differ" signal than `p95` is, for this pair of runs specifically.

## 11. Simulation limitation — all nodes modeled identically, no altitude dimension (Eye's relay advantage not represented)

Documentation-only note, added per a direct request to state this plainly
before it goes in the paper. Checked directly against the code, not
inferred: `uavx/link.py`'s `Point = tuple[float, float]` — every position
in this simulation (base, every relay, and the surveyor) is a 2D `(x, y)`
in flat ground metres. There is no altitude/`z` field anywhere in
`Point`, in `uavx/schema.py`'s `UavPosition`/`PoiSpec` telemetry records,
or anywhere else in `uavx/`.

The simulation models all `N_UAV` nodes — the surveyor (which stands in
for the Eye in this simulation, `uav_ids[0]` in `uavx/survey.py`'s `run()`)
and every relay — as identical: the same `config.R_COMM_M` comms radius is
passed uniformly to `link.pdr()`/`link.build_connectivity_graph()` for
every hop regardless of which node is which, and `config.SURVEYOR_SPEED_MPS
= MAX_RELAY_SPEED_MPS` (`config.py`, reused verbatim, not a second
number) gives the surveyor/Eye node the same speed cap as a relay. Nothing
in `Point`, `link.py`, or `config.py` distinguishes the surveyor/Eye's
comms range or link quality from a relay's.

The Eye's physical relay advantage claimed in ruling A21 (overhead
position, longer effective range) is not represented by any parameter in
this model. A21's own text — "the 4-UAV UAV-X stress test in §9 stands as
final evidence, no N_UAV=3 rerun" — treats the §9 4-UAV result as evidence
for that advantage, but §9's actual change (Task 2, "4-UAV stress-test
rerun") was `--n-uav 4` only: one fewer identical node in the same flat,
altitude-blind model, not an overhead-position or extended-range
parameter added anywhere. The 4-UAV stress-test evidence therefore tests
"the swarm's resilience to a reduced relay count" (5 total nodes → 4), not
"the Eye's specific placement advantage" — those are different claims,
and only the first one has code behind it in this repository today.

## 12. Ruling A23 — PoI scenario + two report-gated fault events

### A23's requirement (recorded here for the first time)

Until this section, A23 was written down nowhere under `uavx/` (nor anywhere
else in this repo; grepped). **Caveat on "verbatim":** the original ruling
text is not in the repo either, so the wording below is A23 exactly as it
was restated in the task that implemented it, not a quote from a ruling
document. If the original surfaces, replace this block with it and diff
the two.

> A23 requires: **6 PoIs**, with **priority tiers** and a **spread of
> reachability** (some easy, some hard to reach from the GCS); **two fault
> events**, at **~40%** and **~75%** of mission time, each hitting a relay
> that is **"verifiably carrying live traffic"** at the time; and
> **`recovery_time_s` and `relay_reallocations` reported separately per
> event** (not pooled).

Status against the code as of this section:

| A23 item | Status |
|---|---|
| 6 PoIs | Already true: `config.N_POI = 6`. |
| Priority tiers | Already true: `config.POI_PRIORITY_LEVELS = (1.0, 2.0, 3.0)`, drawn uniformly per PoI (so a seed can get all-one-tier; not enforced). |
| Reachability spread | Partly true: `_poi_scenario()` draws distance uniformly in `[R_COMM_M, ARENA_HALF_EXTENT_M]`, so some spread is likely, but no tier of "reachable directly / needs 1 relay / needs 2+" is enforced. **Not changed this session.** |
| ~40% event on a relay carrying live traffic | `link_degraded`, **rewired** this session (below). |
| ~75% event on a relay carrying live traffic | `uav_dropout`, **new** this session (below). |
| Per-event `recovery_time_s` | **Was NOT true before this session.** `recovery_time_s_mean/max` pooled `uav_fail` + `uav_recharge`. Now per-event too (below). |
| Per-event `relay_reallocations` | Already true for `uav_fail`/`uav_recharge` (§9 Task 3 cause breakdown); extended to `uav_dropout`. `link_degraded` gets none by construction (below). |

`uav_fail` (25%) and `uav_recharge` (55%) are **not** A23 events and were
not touched.

### "Verifiably carrying live traffic" — option (a), not option (b)

Two readings were considered:

- **(a) chain membership at the report-success tick** — what was built.
  At the exact tick a PoI report succeeds (`survey.py`'s two
  `reported_t_s` sites, now both `_record_report_success()`), the relay ids
  on the live chain that tick (`_relay_ids_on_chain(chain.path)`) are stored
  on the visit as `carrying_relay_ids` (`schema.py`'s `Visit`, additive,
  `NotRequired`). A fault event then picks its target from a specific
  report's `carrying_relay_ids`.
- **(b) a modeled report-transmission window** — **not** built. This would
  give a report a real duration and fire the fault while bytes are in flight.

**Why (a):** time. This was taken under ruling A5's crunch-time shortcut:
the cheap, honest version now, not the full transmission model. **What (a)
does and doesn't claim:** reports in this sim are instantaneous (success
is one tick's `chain.connected`), and the fault is applied on the tick
*after* the report succeeds, because report success is decided in step 9/10
of the tick loop, after step 6 where events fire. A fault under this model
therefore lands **at or just after report completion, not strictly
"during" it**. The claim this supports is "the relay we hit was carrying
live traffic one tick (0.2 s) ago, provably". It does not support "we hit it
mid-transmission".

### 40% event — `link_degraded` rewired

Before: fired on the first tick at or after `DEGRADE_FRACTION` where *any*
relay was on the chain, with no link to any report. Now: fires one tick
after the first report success at or after `degrade_earliest_tick`
(unchanged: `max(40%, fail_tick+1)`), choosing (`rng.choice`, as before)
from that report's `carrying_relay_ids` that are still `active`. If that
report has no relays (surveyor linked straight to base, which does
happen) or none still active, it moves on report by report
(`_next_report_target()`), logging each pass-over to telemetry's new
`fault_notes`. Degradation mechanics (`DEGRADE_DURATION_S`,
`DEGRADE_EXTRA_DISTANCE_M`, incoming-hop effective distance) are
unchanged. The event now also carries `poi` and `report_t_s`.

Side effect, measured over seeds 1–60: the old trigger fired in 35/60
(4-UAV) and 55/60 (5-UAV) runs; the new one fires in **29/60** and
**50/60**. Runs where it doesn't fire now get a
`"degradation event could not fire -- ..."` note. The old code logged
nothing in that case. It also moves in time: 4-UAV seed 101 degraded at
t=115.4s under the old trigger and at t=152.6s now (first report after
40% succeeded at t=152.4s).

### 75% event — `uav_dropout` (new)

`config.DROPOUT_FRACTION = 0.75`. It fires one tick after the first report
success at or after `dropout_earliest_tick = max(75%, fail_tick+1)`,
choosing from that report's still-active `carrying_relay_ids`. It uses
`uav_fail`'s removal path: `relay_status -> "failed"`, then
`reconfig.reconfigure()`, plus a recovery `watch` closed by a `restored`
event. Its assignment rows carry the new cause tag
**`poi_report_dropout`**, counted by `score/profiles.py` as
`relay_reallocations_poi_report_dropout`. Event type is `uav_dropout`.
It won't fire while another fault's recovery watch is still open (that
would overwrite the other fault's pending `restored`). Such a report is
passed over with a note instead of the dropout firing late against it.

**Known risk, confirmed in practice:** after the 55% `uav_recharge`,
`RELAY_RECHARGE_S=300` outlives the run, and the chain often never comes
back. When no report succeeds at or after 75%, the dropout does **not**
fire and the run logs, in `fault_notes`:
`"dropout event could not fire -- no report succeeded after 75% mission
time (t>=...s)"` (also printed by `python3 -m uavx.survey`). It is never
forced onto an arbitrary tick. Measured over seeds 1–60:

| fleet | dropout fired | dropout fired AND restored |
|---|---|---|
| 4 UAV (A20 fleet) | **5/60** | **0/60** |
| 5 UAV | 16/60 | 7/60 |

At the A20 fleet size, the 75% event fires in few runs and has never
been observed to recover. In both of the checked-in-evidence
configs (4-UAV and 5-UAV, seed 101) it cannot fire. This is an honest
limitation of option (a) combined with the 55% recharge, not something
to paper over. If A23 evidence needs an observed 75% recovery, either
pick a seed where it fires and recovers (5-UAV seeds 16, 23, 26, 31, 35,
45, 58), or revisit the 55% recharge / `RELAY_RECHARGE_S`. Both are
decisions for the team, not made here.

### Per-event reporting (`score/profiles.py`)

- `recovery_time_s_uav_fail`, `recovery_time_s_uav_recharge`,
  `recovery_time_s_uav_dropout`: one metric per down-event (a second event
  of the same type gets `_2`, ...). A type with no event, or an event with
  no matching `restored`, is `unavailable` with the reason, never dropped.
- `recovery_time_s_mean/max` are kept (existing DB rows use them). They
  now also include `uav_dropout` and carry a reason string saying they're
  pooled. For telemetry predating A23 their values are unchanged (4-UAV
  seed 101 file: 21.6 s, same as §9).
- `relay_reallocations_poi_report_dropout`: new cause bucket, sums with the
  other three to `relay_reallocations`.
- **`link_degraded` has no per-event recovery time or reallocation count,
  and that's by construction, not an omission.** The degrade window never
  breaks connectivity (BFS runs on undegraded distances, §7 Task 2) and
  never triggers `reconfigure()`. There is nothing to recover from and no
  fault-driven reallocation. `link_restored` is a timer
  (`DEGRADE_DURATION_S`), not a measured recovery. Tagging the window's
  rows with a degrade cause would mislabel `surveyor_drift` changes. If A23
  is read as requiring a *measured* recovery for the 40% event, the degrade
  mechanics themselves would have to change, which was out of scope here.

### Telemetry additions (all additive / `NotRequired`)

`Visit.carrying_relay_ids`, `Event.poi` / `Event.report_t_s` (report-gated
events only), top-level `fault_notes` (list of `{t_s, event, note}`) and
`fault_schedule` (`link_degraded_earliest_t_s`,
`uav_dropout_earliest_t_s`). No checked-in log in `uavx/logs/` was
regenerated. They still reflect the old 40% trigger and have no
dropout.

### Tests

`python3 -m unittest uavx.tests.test_a23_fault_selection` (stdlib
`unittest`, since pytest isn't installed). For both events it checks the
target was in the selecting report's `carrying_relay_ids`, **and**,
independently, on the logged `chains[]` path at that report's tick. It
also checks that the report was at or after the 40%/75% mark, that the
event fired on the next tick, and that every earlier eligible report was
passed over with a note. Further checks: the dropout is a full removal
(off every later chain), the not-fired case is logged rather than forced,
retry-forward is exercised (synthetic `_next_report_target` test, since a
240-run seed scan never produced a pass-over followed by a later fire),
and the per-event metrics are separate.

### 4-UAV dropout seed scan (seeds 1–300) — the 75% dropout cannot recover

**For Master.** The run was `survey.run(seed, n_uav=4)` for every seed in
1–300, standard config with all four events active (25% `uav_fail`, 40%
`link_degraded`, 55% `uav_recharge`, 75% `uav_dropout`). Raw per-seed data
is saved as `uavx/logs/a23_dropout_seed_scan_4uav_seeds1-300.json` (one
row per seed: `fired`, `rec` = recovery s or null, `mark75`, `total`, and
for fired seeds `t_drop`, `left`, `fail`, `recharge`, `dropped`,
`rch_back` = recharge start + 300 s).

- All 300 seeds ran (no hard-failure-candidate errors).
- **Dropout fired: 38/300.** Seeds 1, 23, 31, 41, 58, 69, 70, 81, 91, 93,
  99, 104, 110, 111, 112, 124, 144, 145, 152, 155, 159, 166, 177, 180,
  194, 200, 205, 206, 210, 214, 219, 225, 232, 235, 236, 244, 254, 259.
  The other 262 logged "no report succeeded after 75% mission time".
- **Fired AND recovered: 0/38.** So no A23 evidence pack was generated at
  4 UAVs.

**The block is structural: event stacking on a 3-relay fleet.** At
N_UAV=4 there are exactly 3 relays (2, 3, 4). In **all 38** fired runs, the
25% `uav_fail` (always relay 3, permanent), the 55% `uav_recharge` and the
75% `uav_dropout` each took a *different* relay, leaving **zero active
relays** after the dropout. The only way back is the recharging relay
returning after `RELAY_RECHARGE_S`=300 s, and the mission ends first:

| measure, over the 38 fired runs | min | median | max |
|---|---|---|---|
| mission time left after the 75% mark | 59.8 s | 75.4 s | 94.2 s |
| mission time left after the dropout fired | 52.4 s | 60.1 s | 70.4 s |
| time between the recharge starting and the dropout firing | 55.2 s | 75.7 s | 99.2 s |
| how long after mission end the recharging relay would return | 130.4 s | 164.1 s | 192.4 s |

**Distance was checked and ruled out.** After the dropout, the surveyor
came within `r_comm_m`=150 m of base in 0/38 runs, so no direct link was
possible. It ended 154–267 m from base in all 38, always within 2 × `r_comm_m`,
so a single returning relay could geometrically bridge it. What's missing
is a live relay within the mission's remaining time, not reach.

Nothing was changed in response (`RELAY_RECHARGE_S` and mission length are
untouched). The decision is referred to Master. Options on the table are
a longer mission, a shorter recharge, or not stacking all four events in
one mission, since A23 only requires the 40% and 75% events.

### `--events a23-only` — running only A23's two events

`python3 -m uavx.survey --events a23-only` (or `run(..., events="a23-only")`;
`EVENT_SETS` in `uavx/survey.py`) runs **only** A23's two fault events: the
40% `link_degraded` and the 75% `uav_dropout`. The pre-existing 25%
`uav_fail` (including its `_scan_hard_failure_candidates()` search) and
55% `uav_recharge` do not run at all. Nothing earlier has to be waited for,
so the A23 events are scheduled at their plain 40%/75% fractions. The
option is recorded in each run's telemetry as `conditions.event_set`.

**Why it exists:** A23 asks for two fault events. It never asked for all
four to run in one mission; that combination happened by default, not by
decision. On the A20 fleet (N_UAV=4, so 3 relays), stacking all four left
zero relays after the dropout, and the seed scan above got 0/38
recoveries. With `a23-only`, the same seeds 1–300 at N_UAV=4 give: dropout
fired 300/300, **fired and recovered 163/300**, recovery 0.2 / 7.6 / 19.0 s
(min / median / max). The 137 that don't recover are a different limit.
In all of them the surveyor ended 368–499 m from base. 61 of those were
beyond two relays' 450 m maximum reach. The other 76 were within it but
didn't recover in the 42–70 s left; that cause has not been diagnosed.
The flag reproduces the scratch experiment behind these numbers exactly
(0/300 mismatches).

**The default is unchanged.** `--events all` is still the default and
still runs all four events exactly as before. The §7/§9 stress-test
evidence (`stress_test_seed101.json`, `stress_test_4uav_seed101.json`) was
produced by it and remains valid for what it was used for: a hard,
genuinely-unpluggable relay failure plus recharge, stacked, to stress
recovery under reduced redundancy. `a23-only` is an additional option
for A23 evidence, not a replacement.

### `--scenario PATH` — fixed PoI layout from a file

`python3 -m uavx.survey --scenario PATH` (`run(..., scenario_path=PATH)`)
reads the PoI layout from a JSON file via `load_scenario()` instead of
drawing one with `_poi_scenario(rng)`. It reads the shape of
`claude/scenario_config.json` (B2's draft): `{"base_pos": [x, y],
"r_comm_m": ..., "pois": [{"id": "poi_<n>", "priority", "pos": [x, y],
...}]}`. Only `id`/`priority`/`pos` are used. `hop_class`,
`distance_from_base_m`, `_note` and the check blocks are metadata and are
ignored. `"poi_<n>"` becomes integer PoI id `n`. If the file gives
`base_pos` or `r_comm_m`, they must match the run's. A malformed or
mismatched file raises `ValueError`; it never silently falls back to the
random layout.
The source is recorded as `conditions.scenario` (the path, or
`"internal_random"`). Without the flag nothing changes. Note that with a
scenario file the seed no longer affects PoI placement, only packet
delivery and relay choice (`rng.choice` in the fault events).

### Scenario layout regenerated against the real assigner, and the CP1–CP5 evidence pack

**Edge rule vs. relay placement.** `link.is_up()` (`link.py:58`) is a
plain `distance <= r_comm` cutoff, as B2's draft assumed. But
`GreedyChainAssigner` (`assign.py:64`) spaces relays
`r_comm × LINK_SOFT_BAND_FRAC` = 120 m apart. The relays a PoI *actually*
needs is therefore not the edge-rule count. In B2's v1 draft, poi_3
(280 m) needed 2 relays, not 1, and poi_5 (430 m) needed all 3. That
failed A23's "≥2 one-relay". The coordinates in
`claude/scenario_config.json` were regenerated and verified against the
real assigner's output (surveyor parked at each PoI, 3 relays available).
`hop_class` was renamed to `relays_used_actual` so the two counts can't
be confused again. The result is 2 PoIs needing no relay (poi_1, poi_2),
2 needing one (poi_6, poi_4) and 2 needing two (poi_3, poi_5). Every path
has PDR 1.0, every PoI is still reachable with 2 relays (the post-dropout
fleet), and the priorities stay 3 × priority-3 and 3 × priority-1.
`LINK_SOFT_BAND_FRAC`, `R_COMM_M` and the relay spacing are unchanged, so
no existing evidence is invalidated.

**How the target relay is selected, and one limit.** The relay is chosen at
runtime from live state: `_record_report_success()` stores the relays on
that tick's live chain, and `_next_report_target()` filters them by live
`relay_status`. Nothing is precomputed. The fault *fires* one tick
(0.2 s) after the report succeeds, however (option (a) above). Across
a23-only seeds 1–300 at N_UAV=4, the target was still on the live chain at
the fire tick in 300/300 dropouts but only **298/300 degradations**. In
2 runs the degraded relay had already left the chain. That is not fixed;
it's recorded here.

**Evidence pack** (all in `uavx/logs/`, none overwrote an existing file):
`stage1_run.json` from
`python3 -m uavx.survey --seed 1 --n-uav 4 --events a23-only --scenario claude/scenario_config.json --out stage1_run.json`,
then `python3 -m uavx.evidence_pack` (refuses to overwrite) for
`scenario_config.json` (byte-identical copy), `event_metrics.json`
and `stage1_summary.txt`. Seed 1 recovered under this layout:

| event | node | t | selected from | recovery_time_s | relay_reallocations |
|---|---|---|---|---|---|
| 40% `link_degraded` | relay_3 | 104.6 s | PoI 5 report, t=104.4 s, carried by [2, 3] | n/a by construction | 0 by construction (3 routine `surveyor_drift` changes fell inside its window) |
| 75% `uav_dropout` | relay_2 | 205.4 s | PoI 4 report, t=205.2 s, carried by [2] | **6.6 s** (restored 212.0 s via relay_3) | **1** |

Run-level: PDR 0.7956, latency mean 44.3 ms / p95 63.9 ms, poi_completion
1.0 (6/6 reported), connectivity downtime 26.2 s (approx).
