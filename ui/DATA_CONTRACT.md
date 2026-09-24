# ui/ Data Contract

`ui/adapters.py` reads a telemetry JSON file and extracts exactly the
fields below. Nothing else in the file is read. This is the contract the
ground commander must emit later when this UI points at real (not
simulated) telemetry — anything not listed here, the panel never looks at.

Today the file is produced by `uavx/survey.py`; `uavx/sim.py` (the ring/PoI
mission) does not emit `positions`/`chains` at all, so pointing this UI at
a `sim.py` telemetry file degrades to "positions unavailable, chain
unavailable, pois available" — that's the graceful-degradation path
working as designed, not a bug.

All three top-level fields below are **independently optional**: a
telemetry document may have any subset of them. `ui/adapters.py` reports
what it found; it never fabricates a value for what's missing and never
raises on a missing/malformed field (see `extract_state()`).

## Top-level fields

### `positions` (array) — OPTIONAL

One row per UAV per simulated tick, across the whole run. `ui/adapters.py`
passes through **every valid entry, for every recorded tick** — the full
time series, not just the latest one. The panel (`relay_map.js`) owns
picking which tick to display, via its playback controls (play/pause, a
scrub slider, a speed selector); it groups these rows by `t_s` itself to
build one frame per tick.

**If absent, empty, or every entry fails validation:** `positions_available:
false`, `positions: null` in `/api/state`. The panel shows "positions
unavailable" and draws no UAV dots (PoI markers, if available, still draw).

Each entry:

| field | type | units | required (per entry) | if absent |
|---|---|---|---|---|
| `t_s` | number | seconds since mission start | yes | entry is dropped from consideration |
| `uav` | `"base"` (string) or a UAV id (number) | -- | yes | entry is dropped |
| `x_m` | number | metres, local flat-earth frame, base station at (0, 0) | yes | entry is dropped |
| `y_m` | number | metres, same frame | yes | entry is dropped |
| `role` | `"base"` \| `"surveyor"` \| `"relay"` | -- | no (see below) | entry still drawn, but as a generic relay-style dot; the panel cannot tell it's the base/surveyor for styling or for chain-reachability checking |

An entry missing `t_s`, `uav`, `x_m`, or `y_m` is silently dropped from
that tick's snapshot (not from the whole array) — the rest of the tick's
UAVs still render.

### `chains` (array) — OPTIONAL

The single active `base -> relay -> ... -> surveyor` chain, one entry per
tick. `ui/adapters.py` passes through **every valid entry, for every
recorded tick** — the full time series, not just the latest one. The panel
looks up the entry matching whichever tick it's currently displaying.

**If absent, empty, or every entry fails validation:** `chains_available:
false`, `chains: null`. The panel shows the **NO ACTIVE CHAIN** banner
instead of drawing chain edges (UAV dots and PoI markers, if available,
still draw). A tick that has positions but no matching `chains[]` entry
(a gap between the two series) is treated the same way, just for that one
tick.

Each entry:

| field | type | units | required (per entry) | if absent |
|---|---|---|---|---|
| `t_s` | number | seconds | yes | entry is dropped |
| `path` | array of node names | -- | yes | entry is dropped |
| `link_quality` | array of numbers | ratio, 0.0-1.0 (packet delivery ratio) | yes | entry is dropped |

`path` node names: `"base"` (literal), `"relay_<id>"` (a relay UAV, `<id>`
matching a `positions[]` entry's `uav`), or a bare number (the surveyor's
own `uav` id — the same value a `positions[]` entry with `role:
"surveyor"` carries). `len(link_quality) == len(path) - 1` (one PDR value
per hop). An **empty `path`** (`[]`) means "no live chain right now" — this
is the normal way `chains[]` represents a disconnected tick, and the panel
treats it as the NO ACTIVE CHAIN case for that tick, not an error.

For whichever tick it's currently displaying, the panel checks that
`path`'s last element equals the surveyor's `uav` id (from that same
tick's `positions[]` rows, when available) — a chain that doesn't actually
terminate at the surveyor is treated as NO ACTIVE CHAIN even if `path` is
non-empty. If `positions[]` isn't available to cross-check against, the
panel trusts `path` as given. A relay whose id is on the chain `path` for
the currently-displayed tick is drawn as part of the active chain; a relay
that ISN'T on that tick's `path` (but chain data is otherwise present) is
drawn as idle/standby, in neutral styling — it's spare capacity, not a
fault. Only an actual NO ACTIVE CHAIN tick (empty/missing/non-terminating
`path`) gets the failure banner, and that banner is mission-level, not
applied to individual relay dots.

### `pois` (array) — OPTIONAL

Static context markers (drawn small, unobtrusive, and first so the fleet
renders on top of them). The panel reads only `id`/`x_m`/`y_m` — a real
`pois[]` entry may carry other fields (e.g. `priority`); they're ignored
here.

**If absent, empty, or every entry fails validation:** `pois_available:
false`, `pois: null`. No PoI markers drawn; everything else is unaffected.

Each entry:

| field | type | units | required (per entry) | if absent |
|---|---|---|---|---|
| `id` | number | -- | yes | entry is dropped |
| `x_m` | number | metres, same frame as `positions[].x_m` | yes | entry is dropped |
| `y_m` | number | metres, same frame | yes | entry is dropped |

## `/api/state` response shape

What `ui/server.py` actually sends the browser (the extracted state, plus
the staleness wrapper it adds). `positions` and `chains` are the FULL
recorded series (every tick in the file), not a single snapshot:

```json
{
  "t_s": 12.4,
  "positions": [
    { "t_s": 0.0, "uav": "base", "x_m": 0.0, "y_m": 0.0, "role": "base" },
    { "t_s": 0.2, "uav": "base", "x_m": 0.0, "y_m": 0.0, "role": "base" },
    { "t_s": 12.4, "uav": "base", "x_m": 0.0, "y_m": 0.0, "role": "base" }
  ],
  "positions_available": true,
  "chains": [
    { "t_s": 0.0, "path": [], "link_quality": [] },
    { "t_s": 12.4, "path": ["base", 1], "link_quality": [1.0] }
  ],
  "chains_available": true,
  "pois": [ { "id": 0, "x_m": -101.6, "y_m": -121.9 } ],
  "pois_available": true,
  "stale": false,
  "error": null
}
```

`t_s` at the top level is still the single **latest** tick across whichever
series are available — a freshness indicator for the status bar ("how far
has this file's recording gotten"), unrelated to which tick the panel
happens to have scrubbed to right now (that's client-side-only state inside
`relay_map.js`, never sent to or read from the server).

`stale` / `error` are added by `ui/server.py`, not `ui/adapters.py`:
`stale: true` means the most recent read of the telemetry file failed
(missing, malformed JSON, or caught mid-write), and the rest of the object
is the **last successfully-read state** instead — `error` names why the
last read failed. On the very first read ever (no prior good state exists
yet), the fallback is the all-`*_available: false` empty state.

## Minimal complete example document

The smallest `telemetry.json` that exercises all three fields (one UAV
snapshot, one chain, one PoI):

```json
{
  "positions": [
    { "t_s": 0.0, "uav": "base", "x_m": 0.0, "y_m": 0.0, "role": "base" },
    { "t_s": 0.0, "uav": 1, "x_m": 42.0, "y_m": 10.0, "role": "surveyor" },
    { "t_s": 0.0, "uav": 2, "x_m": 20.0, "y_m": 5.0, "role": "relay" }
  ],
  "chains": [
    { "t_s": 0.0, "path": ["base", "relay_2", 1], "link_quality": [0.95, 0.55] }
  ],
  "pois": [
    { "id": 0, "x_m": 40.0, "y_m": 12.0 }
  ]
}
```

This renders: base at the origin, relay 2 mid-chain (its hop from base
green at 0.95), the surveyor at the far end (its hop from relay 2 amber at
0.55), and one PoI marker near the surveyor.
