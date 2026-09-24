# ui/ — read-only swarm telemetry viewer

**Ruling A16 (hard safety constraint):** this UI is read-only. It never
sends a command, never arms, never modifies vehicle state. The HTTP server
answers `GET` only (everything else gets `405`), binds to `127.0.0.1` only,
and its only input is a telemetry JSON file path on disk — never a socket
or serial connection. It never imports `pymavlink` or anything from
`swarm/` (the flight commander's control path) or `uavx/`; it only ever
calls `open()` on the file named by `--telemetry`. This is what keeps it
provably incapable of reproducing the port-sharing bug that caused a
GCS-failsafe RTL in an earlier sprint (`SWARM_OPERATIONS.md`, ruling A13).

**Ruling A17:** v1 ships with exactly ONE panel (`relay_map`). The registry
below exists so a second panel is a pure addition later — see the example
at the bottom of this file.

## Run it

```
python3 -m ui.server --telemetry uavx/logs/survey_telemetry_seed42.json --port 8080 --poll-ms 500
```

Then open `http://127.0.0.1:8080/` in a browser. `--telemetry` is the only
required flag; `--port` defaults to `8080`, `--poll-ms` (how often the
browser re-fetches `/api/state`) defaults to `500`.

The server re-reads the telemetry file from disk on every `/api/state`
request — point `--telemetry` at a file a running simulation is actively
writing and the page updates live. See `ui/DATA_CONTRACT.md` for exactly
which fields it reads and how it degrades when one is missing.

## Files

- `ui/server.py` — the HTTP server (stdlib `http.server` only). Serves the
  page shell, the static JS, and `/api/state`.
- `ui/adapters.py` — reads the telemetry JSON and extracts positions /
  chain / pois. The only file that touches the telemetry file's shape.
- `ui/static/index.html` — the page shell (status bar + panel containers).
- `ui/static/app.js` — the panel registry + polling loop. Panel-agnostic;
  never edited to add a panel.
- `ui/static/panels/relay_map.js` — the one v1 panel.
- `ui/DATA_CONTRACT.md` — the field-by-field contract this UI reads.

## The panel registry

A panel is a browser-side object registered once, at script-load time:

```js
window.UI.registerPanel({
  id: "some_unique_id",        // used as the DOM container id
  title: "Human-Readable Title",
  order: 10,                    // panels are sorted ascending by this
  render(state, rootEl) {
    // state: the parsed /api/state JSON (see ui/DATA_CONTRACT.md)
    // rootEl: this panel's own <div class="panel-body"> -- clear/rebuild
    //         it however you like; nothing else on the page touches it.
  },
});
```

`ui/static/app.js` builds one `<section class="panel">` per registered
panel (in `order`), polls `GET /api/state` every `--poll-ms` milliseconds,
and calls every panel's `render(state, rootEl)` on each poll, inside a
`try/catch`. **A panel that throws is caught and shown as an error inside
its own container** — it never breaks the rest of the page or the other
panels.

### Adding a second panel later (worked example)

Say a future sprint wants a plain status table (one row per UAV: id, role,
last-known position). None of `app.js`, `index.html`'s shell logic, or
`relay_map.js` change. Three steps:

1. Create `ui/static/panels/status_table.js`. `state.positions` is the
   FULL recorded time series (every tick, see `ui/DATA_CONTRACT.md`), so a
   panel that only wants "right now" needs to pick out the latest tick
   itself, the same way `relay_map.js` picks whichever tick it's currently
   displaying:

    ```js
    "use strict";
    (function () {
      function render(state, rootEl) {
        rootEl.innerHTML = "";
        if (!state.positions_available) {
          rootEl.textContent = "positions unavailable";
          return;
        }
        const latestT = Math.max(...state.positions.map((p) => p.t_s));
        const latest = state.positions.filter((p) => p.t_s === latestT);
        const table = document.createElement("table");
        table.innerHTML = "<tr><th>uav</th><th>role</th><th>x_m</th><th>y_m</th></tr>";
        for (const p of latest) {
          const row = document.createElement("tr");
          row.innerHTML = `<td>${p.uav}</td><td>${p.role || "?"}</td><td>${p.x_m.toFixed(1)}</td><td>${p.y_m.toFixed(1)}</td>`;
          table.appendChild(row);
        }
        rootEl.appendChild(table);
      }

      window.UI.registerPanel({ id: "status_table", title: "UAV Status", order: 10, render });
    })();
    ```

2. Add one `<script>` tag to `ui/static/index.html`, after the existing
   panel scripts:

    ```html
    <script src="/static/panels/status_table.js"></script>
    ```

3. That's it. `app.js` picks it up automatically on next page load and
   places it after `relay_map` (`order: 0` < `order: 10`).

If the new panel needs a telemetry field `ui/adapters.py` doesn't extract
yet, add it there (and document it in `ui/DATA_CONTRACT.md`) — that's the
one place allowed to know the telemetry file's raw shape; panels only ever
read the already-extracted `state` object.

## Known gaps / where I had to guess

- **Rulings A16/A17 aren't in `SWARM_OPERATIONS.md`'s amendment log as of
  this writing** — the log there ends at A13 (5 Sep 2026). The constraints
  those rulings state are self-consistent, strictly *more* restrictive
  than anything already ratified (read-only, localhost-only, file-path-only),
  and don't contradict CLAUDE.md, so this was built to them as given rather
  than blocked on the paper trail — but the operations doc should get an
  actual A16/A17 entry so this doesn't drift.
- **`uavx/sim.py`'s telemetry has no `positions`/`chains`** (only
  `uavx/survey.py`'s does — see `uavx/RULES_NOTES.md` §6). Verified this
  UI against `uavx/logs/survey_telemetry_seed42.json`, not a `sim.py`
  output file, since that's the only real telemetry in the repo carrying
  the fields this panel reads.
- **Playback controls (added for full-series replay):** speed (0.5x/1x/2x/4x)
  is applied to the RECORDED tick spacing (`ticks[1] - ticks[0]`), not a
  hardcoded rate — nothing in `ui/` may import `uavx/config.py`'s
  `TICK_HZ`, so the panel infers the cadence from the data itself. Playback
  stops (doesn't loop) at the last recorded tick, since this is a replay of
  a specific run's evidence, not a looping demo animation; that wasn't
  specified either way, so this was the more conservative guess. Scrubbing
  the slider mid-play pauses playback rather than fighting the drag.
- **`chain`/`chain_available` renamed to `chains`/`chains_available`** in
  `/api/state`, since the value is now a list (every tick) instead of a
  single dict (the latest tick) — a straight rename felt clearer than
  keeping the old singular name for a now-plural value. `relay_map.js` is
  the only consumer (ruling A17: one panel), so nothing else needed
  updating, but a future second panel reading `chain` (singular) would need
  to use `chains` instead — flagged here and in `ui/DATA_CONTRACT.md`.
- **Chain-reachability check** (whether `path`'s last node is really the
  surveyor) relies on cross-referencing `positions[]` for a `role:
  "surveyor"` entry. If `positions` is unavailable but `chains` isn't, the
  panel can't perform that check and falls back to trusting a non-empty
  `path` at face value — documented in `ui/DATA_CONTRACT.md`.
