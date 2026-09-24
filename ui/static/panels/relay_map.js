// ui/static/panels/relay_map.js -- the v1 panel (ruling A17: one panel
// only). Combines the fleet map and the active relay chain into a single
// view, per the spec: they're the same question ("can base reach the
// surveyor right now, and through whom?") asked two ways, so they stay one
// panel rather than two that have to be cross-referenced by eye.
//
// Reads only state.positions / state.chains / state.pois, exactly as
// produced by ui/adapters.py -- see ui/DATA_CONTRACT.md for the field
// contract. Never assumes a field is present; every read is guarded.
//
// state.positions / state.chains are now the FULL recorded time series
// (every tick in the file), not just the latest one -- this panel owns
// picking which tick to display, via the playback controls below (play/
// pause, scrub slider, speed selector). Default on load: paused at tick 0.
"use strict";

(function () {
  const WIDTH = 760;
  const HEIGHT = 560;
  const MARGIN_FRAC = 0.10;
  const MIN_SPAN_M = 20; // fallback span so a near-identical-points frame never divides by ~0

  const GOOD_THRESHOLD = 0.7;
  const DEGRADED_THRESHOLD = 0.4;
  const COLOR_GOOD = "#3fb950";
  const COLOR_DEGRADED = "#d4a72c";
  const COLOR_BAD = "#f85149";
  const COLOR_RELAY = "#79c0ff";
  const COLOR_IDLE = "#6e7681"; // neutral grey -- idle/standby, NOT a fault

  // 10x/30x/60x added for A26: a 45-minute rulebook mission would take 11
  // minutes to watch at the old 4x maximum. Above ~10x the timer can't fire
  // fast enough for one tick per frame, so playback advances several ticks
  // per frame instead (see schedulePlayback) -- same sim time per real
  // second, fewer redraws.
  const SPEEDS = [0.5, 1, 2, 4, 10, 30, 60];
  const MIN_INTERVAL_MS = 20; // floor so a ~0 recorded dt (or a huge speed) can't spin the browser

  // A26 operational-area / PoI-status styling (only used when the run
  // carries arena + spawn data, i.e. uavx/survey.py's run_dynamic()).
  // Red PoI dots match the rulebook's own diagram.
  const COLOR_POI_WAITING = "#f85149";   // spawned, not yet reached -- red, as in the rulebook diagram
  const COLOR_POI_ACTIVE = "#e3b341";    // surveyor there / report in flight
  const COLOR_POI_REPORTED = "#3fb950";  // report reached base on time
  const COLOR_POI_MISSED = "#8a929b";    // reached, but report missed the 10 s deadline
  const COLOR_ARENA = "#3d4650";
  const NEW_POI_HIGHLIGHT_S = 15;        // how long a freshly-spawned PoI gets a "NEW" ring

  function qualityColor(q) {
    if (typeof q !== "number" || Number.isNaN(q)) return "#666";
    if (q >= GOOD_THRESHOLD) return COLOR_GOOD;
    if (q >= DEGRADED_THRESHOLD) return COLOR_DEGRADED;
    return COLOR_BAD;
  }

  // Deterministic projection, fitted to the bounds of the whole recorded
  // run (every position at every tick + every PoI -- see runPoints), so the
  // view never pans or zooms during replay and screen positions move only
  // as far as the underlying x_m/y_m actually moved.
  function computeProjection(points) {
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (const [x, y] of points) {
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
    if (!Number.isFinite(minX)) {
      // No points at all -- centre on the origin with the minimum span.
      minX = maxX = minY = maxY = 0;
    }
    const cx = (minX + maxX) / 2;
    const cy = (minY + maxY) / 2;
    // Fit each axis to its own canvas dimension and take the tighter of the
    // two scales (one scale for both axes keeps distances true). The old
    // version sized everything to WIDTH only, which pushed points off the
    // top/bottom of the (shorter) canvas for any layout taller than wide.
    const halfX = (Math.max(maxX - minX, MIN_SPAN_M) / 2) * (1 + MARGIN_FRAC);
    const halfY = (Math.max(maxY - minY, MIN_SPAN_M) / 2) * (1 + MARGIN_FRAC);
    // Fixed pixel room for labels, which are drawn to the right of (and a
    // little above/below) their markers -- so the outermost labels aren't
    // clipped by the tightly-fitted canvas edge.
    const PAD_L = 16, PAD_R = 44, PAD_Y = 16;
    const scale = Math.min((WIDTH - PAD_L - PAD_R) / (2 * halfX), (HEIGHT - 2 * PAD_Y) / (2 * halfY));
    // Size the canvas to the data's own aspect ratio (within WIDTH x HEIGHT)
    // instead of a fixed 760x560 box, so a tall layout doesn't leave dead
    // space at the sides. One scale for both axes: distances stay true.
    const w = Math.round(2 * halfX * scale + PAD_L + PAD_R);
    const h = Math.round(2 * halfY * scale + 2 * PAD_Y);

    const project = function (x, y) {
      return [PAD_L + halfX * scale + (x - cx) * scale, PAD_Y + halfY * scale - (y - cy) * scale]; // y inverted: north up
    };
    project.width = w;
    project.height = h;
    return project;
  }

  function svgEl(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  function textEl(x, y, content, opts) {
    opts = opts || {};
    const el = svgEl("text", {
      x: x,
      y: y,
      fill: opts.fill || "#cfd6dd",
      "font-size": opts.size || 10,
      "font-family": "ui-monospace, monospace",
      "text-anchor": opts.anchor || "start",
    });
    el.textContent = content;
    return el;
  }

  // Marker label that won't overlap an earlier label in the same frame: if
  // its (approximate, monospace) box hits one already placed, it steps down
  // one line at a time until clear. Same text/colour/size as textEl -- only
  // the y position changes. Edge PDR labels don't use this (they sit on
  // their own background box mid-edge).
  function labelEl(x, y, content, opts) {
    opts = opts || {};
    const size = opts.size || 10;
    const w = content.length * size * 0.62;
    let x0 = opts.anchor === "end" ? x - w : opts.anchor === "middle" ? x - w / 2 : x;
    // Keep the whole label inside the canvas (shift sideways if it would clip).
    const dx = x0 < 2 ? 2 - x0 : x0 + w > canvasW - 2 ? canvasW - 2 - (x0 + w) : 0;
    x0 += dx;
    x += dx;
    const hits = (yy) => placedLabels.some((b) => x0 < b.x1 && x0 + w > b.x0 && yy - size < b.y1 && yy + 2 > b.y0);
    let yy = y;
    for (let i = 0; i < 12 && hits(yy); i++) yy += size + 3;
    placedLabels.push({ x0: x0, x1: x0 + w, y0: yy - size, y1: yy + 2 });
    return textEl(x, yy, content, opts);
  }

  // node name (as it appears in chain.path) -> [x_m, y_m], or null if this
  // frame's positions don't have it. "base" is literal; "relay_<id>" is a
  // link.py-style node name; anything else (a bare number) is the
  // surveyor's own uav id, per uavx/schema.py's Chain contract.
  function resolveNode(node, positionsByUav, basePos) {
    if (node === "base") return basePos;
    if (typeof node === "string" && node.startsWith("relay_")) {
      const id = Number(node.slice("relay_".length));
      return positionsByUav.has(id) ? positionsByUav.get(id) : null;
    }
    return positionsByUav.has(node) ? positionsByUav.get(node) : null;
  }

  // --- Fault events (A17/CP6 on-screen markers) ---
  // Fault events are numbered in time order ("EVENT 1", "EVENT 2", ...), each
  // paired with the event that ends it: link_degraded -> the next
  // link_restored for the same uav (a timer, not a recovery); uav_fail /
  // uav_dropout / uav_recharge -> the next "restored" for the same uav, i.e.
  // the chain recovering via other relays. recoveryS is computed from the
  // raw t_s floats, never from rounded display strings.
  const FAULT_TYPES = { link_degraded: "link DEGRADED", uav_dropout: "DROPOUT", uav_fail: "FAILED", uav_recharge: "RECHARGE" };

  function pairFaultEvents(events) {
    const sorted = events.slice().sort((a, b) => a.t_s - b.t_s);
    const out = [];
    for (const e of sorted) {
      if (!(e.type in FAULT_TYPES)) continue;
      const endType = e.type === "link_degraded" ? "link_restored" : "restored";
      const end = sorted.find((x) => x.type === endType && x.uav === e.uav && x.t_s > e.t_s) || null;
      out.push({
        n: out.length + 1,
        ev: e,
        end: end,
        recoveryS: end && endType === "restored" ? end.t_s - e.t_s : null,
      });
    }
    return out;
  }

  // --- A26 PoI status at replay time t ---
  // Returns null if the PoI hasn't spawned yet (it isn't drawn at all --
  // the rulebook says PoIs appear at random times, so showing them early
  // would misrepresent what the swarm knew). Otherwise one of:
  //   "waiting"  -- spawned, surveyor not there yet (red, as in the diagram)
  //   "active"   -- surveyor arrived, report not yet through, deadline open
  //   "reported" -- report reached base before the 10 s deadline
  //   "missed"   -- reached, but the deadline passed with no report
  // Everything comes from recorded timestamps; nothing is re-simulated.
  function poiStatusAt(poi, visit, t) {
    if (typeof poi.spawn_t_s === "number" && t < poi.spawn_t_s - 1e-6) return null;
    if (!visit || t < visit.arrive_t_s - 1e-6) return "waiting";
    if (typeof visit.reported_t_s === "number" && visit.reported_t_s <= t + 1e-6) return "reported";
    if (t >= visit.report_deadline_t_s - 1e-6) return "missed";
    return "active";
  }

  function fmtClock(s) {
    const m = Math.floor(s / 60);
    const ss = Math.floor(s - m * 60);
    return String(m).padStart(2, "0") + ":" + String(ss).padStart(2, "0");
  }

  // --- Playback state (module-scoped: persists across app.js's poll-driven
  // render() calls, which is what lets play/scrub/speed work independently
  // of the ~500ms poll cadence) ---
  let dom = null;                       // cached DOM refs, built once per rootEl
  let latestState = null;               // most recent /api/state payload
  let ticks = [];                       // sorted distinct t_s values across positions+chains
  let positionsByTick = new Map();      // t_s -> array of position rows at that tick
  let chainByTick = new Map();          // t_s -> chain row at that tick
  let runPoints = [];                   // [x, y] of every position (all ticks) + every PoI -- projection bounds
  let placedLabels = [];                // this frame's label boxes, for overlap staggering
  let canvasW = WIDTH;                  // this frame's canvas width, for label clamping
  let currentTickIndex = 0;
  let hasInitializedIndex = false;      // true once ticks has been non-empty at least once
  let playing = false;
  let speed = 1;
  let playbackTimer = null;

  function buildIndices(state) {
    const posByTick = new Map();
    const chByTick = new Map();
    const tickSet = new Set();

    if (state.positions_available) {
      for (const p of state.positions) {
        tickSet.add(p.t_s);
        if (!posByTick.has(p.t_s)) posByTick.set(p.t_s, []);
        posByTick.get(p.t_s).push(p);
      }
    }
    if (state.chains_available) {
      for (const c of state.chains) {
        tickSet.add(c.t_s);
        chByTick.set(c.t_s, c);
      }
    }
    return { ticks: Array.from(tickSet).sort((a, b) => a - b), posByTick, chByTick };
  }

  function schedulePlayback() {
    if (playbackTimer) {
      clearInterval(playbackTimer);
      playbackTimer = null;
    }
    if (!playing || ticks.length < 2) return;
    const dtS = ticks[1] - ticks[0]; // recorded tick spacing, straight from the data -- no hardcoded rate
    const tickMs = (dtS * 1000) / speed;
    const intervalMs = Math.max(MIN_INTERVAL_MS, tickMs);
    const step = Math.max(1, Math.round(intervalMs / tickMs)); // >1 only at high speeds
    playbackTimer = setInterval(() => {
      currentTickIndex += step;
      if (currentTickIndex >= ticks.length) {
        // Stop at the last recorded tick rather than looping -- this is a
        // replay of a finished (or in-progress) run, not a looping demo.
        currentTickIndex = ticks.length - 1;
        stopPlayback();
      }
      drawFrame();
    }, intervalMs);
  }

  function stopPlayback() {
    playing = false;
    if (playbackTimer) {
      clearInterval(playbackTimer);
      playbackTimer = null;
    }
    if (dom) dom.playBtn.textContent = "Play";
  }

  function buildSkeleton(rootEl) {
    rootEl.innerHTML = "";

    const notes = document.createElement("div");
    notes.style.fontSize = "11px";
    notes.style.color = "#8a929b";
    notes.style.marginBottom = "6px";
    rootEl.appendChild(notes);

    const controls = document.createElement("div");
    controls.style.display = "flex";
    controls.style.alignItems = "center";
    controls.style.gap = "8px";
    controls.style.marginBottom = "6px";
    controls.style.fontSize = "11px";
    controls.style.color = "#cfd6dd";

    const playBtn = document.createElement("button");
    playBtn.type = "button";
    playBtn.textContent = "Play";
    playBtn.style.cursor = "pointer";
    playBtn.addEventListener("click", () => {
      playing = !playing;
      playBtn.textContent = playing ? "Pause" : "Play";
      schedulePlayback();
    });

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = "0";
    slider.max = "0";
    slider.value = "0";
    slider.step = "1";
    slider.style.flex = "1";
    slider.addEventListener("input", () => {
      // Scrubbing pauses playback -- otherwise the timer fights the user's drag.
      if (playing) stopPlayback();
      currentTickIndex = Number(slider.value);
      drawFrame();
    });

    const readout = document.createElement("span");
    readout.style.minWidth = "170px";
    readout.style.fontFamily = "ui-monospace, monospace";

    const speedSelect = document.createElement("select");
    for (const s of SPEEDS) {
      const opt = document.createElement("option");
      opt.value = String(s);
      opt.textContent = s + "x";
      if (s === 1) opt.selected = true;
      speedSelect.appendChild(opt);
    }
    speedSelect.addEventListener("change", () => {
      speed = Number(speedSelect.value);
      schedulePlayback();
    });

    controls.appendChild(playBtn);
    controls.appendChild(slider);
    controls.appendChild(readout);
    controls.appendChild(speedSelect);
    rootEl.appendChild(controls);

    const missionBar = document.createElement("div");
    missionBar.style.fontSize = "12px";
    missionBar.style.fontFamily = "ui-monospace, monospace";
    missionBar.style.marginBottom = "6px";
    rootEl.appendChild(missionBar);

    const svgContainer = document.createElement("div");
    rootEl.appendChild(svgContainer);

    const banner = document.createElement("div");
    rootEl.appendChild(banner);

    const eventLog = document.createElement("div");
    rootEl.appendChild(eventLog);

    const legend = document.createElement("div");
    rootEl.appendChild(legend);

    return { root: rootEl, notes, playBtn, slider, readout, speedSelect, missionBar, svgContainer, banner, eventLog, legend };
  }

  function updateNotes(state) {
    const missing = [];
    if (!state.positions_available) missing.push("positions unavailable");
    if (!state.chains_available) missing.push("chain unavailable");
    if (!state.pois_available) missing.push("pois unavailable");
    dom.notes.textContent = missing.length ? missing.join(" -- ") : "positions, chain, and pois all present";
  }

  function drawFrame() {
    const state = latestState;
    if (!state || !dom) return;

    const maxIdx = Math.max(0, ticks.length - 1);
    dom.slider.max = String(maxIdx);
    dom.slider.disabled = ticks.length <= 1;
    dom.playBtn.disabled = ticks.length <= 1;
    dom.slider.value = String(currentTickIndex);
    dom.speedSelect.value = String(speed);

    const t = ticks.length > 0 ? ticks[currentTickIndex] : null;
    dom.readout.textContent = t !== null
      ? "tick " + currentTickIndex + " / " + maxIdx + "   t=" + t.toFixed(1) + "s"
      : "no ticks recorded";

    const framePositions = t !== null && positionsByTick.has(t) ? positionsByTick.get(t) : [];
    const frameChain = t !== null && chainByTick.has(t) ? chainByTick.get(t) : null;
    const pois = state.pois_available ? state.pois : [];

    dom.svgContainer.innerHTML = "";
    dom.banner.innerHTML = "";

    // Fit to the WHOLE run (every recorded position + every PoI), not just
    // this frame, so the view is fixed for the replay instead of re-zooming.
    const points = runPoints.length ? runPoints : framePositions.map((p) => [p.x_m, p.y_m]);

    if (points.length === 0) {
      const msg = document.createElement("div");
      msg.textContent = "no spatial data available for this tick (positions and pois both unavailable)";
      msg.style.color = "#ffcc66";
      dom.svgContainer.appendChild(msg);
      return;
    }

    const project = computeProjection(points);
    const svg = svgEl("svg", { width: project.width, height: project.height, viewBox: `0 0 ${project.width} ${project.height}`, style: "background:#0b0e11;border:1px solid #2c333b;" });
    placedLabels = [];
    canvasW = project.width;

    // --- positions lookup, for resolving chain nodes ---
    const positionsByUav = new Map();
    let basePos = null;
    for (const p of framePositions) {
      if (p.role === "base" || p.uav === "base") {
        basePos = [p.x_m, p.y_m];
      } else {
        positionsByUav.set(p.uav, [p.x_m, p.y_m]);
      }
    }

    const arena = state.arena_available ? state.arena : null;
    const dynamicPois = pois.some((p) => typeof p.spawn_t_s === "number");

    // --- A26: operational area, 75 m gap, relay reach (drawn first, underneath everything) ---
    if (arena && typeof arena.half_extent_m === "number") {
      const h = arena.half_extent_m;
      const [x0, y0] = project(-h, h);
      const [x1, y1] = project(h, -h);
      const r = Math.min(x1 - x0, y1 - y0) * 0.06; // rounded corners, like the rulebook diagram
      svg.appendChild(svgEl("rect", { x: x0, y: y0, width: x1 - x0, height: y1 - y0, rx: r, ry: r, fill: "#11161c", stroke: COLOR_ARENA, "stroke-width": 2 }));
      // Title sits just ABOVE the box -- inside it, it collided with PoIs spawned near the top edge.
      svg.appendChild(textEl((x0 + x1) / 2, y0 - 7, "Operational area  " + (2 * h) + " m x " + (2 * h) + " m", { fill: "#8a929b", size: 12, anchor: "middle" }));

      if (basePos && typeof arena.max_chain_reach_m === "number") {
        // Farthest a full-fleet chain can ever reach -- anything outside
        // this dashed circle cannot be reported, whatever the relays do.
        const [bx, by] = project(basePos[0], basePos[1]);
        const [rx] = project(basePos[0] + arena.max_chain_reach_m, basePos[1]);
        const rpx = rx - bx;
        svg.appendChild(svgEl("circle", { cx: bx, cy: by, r: rpx, fill: "#3fb950", "fill-opacity": 0.05, stroke: "#3fb950", "stroke-opacity": 0.55, "stroke-width": 1.2, "stroke-dasharray": "6 5" }));
        svg.appendChild(textEl(bx + rpx * 0.72, by - rpx * 0.72, "max relay reach " + arena.max_chain_reach_m + " m", { fill: "#3fb950", size: 10 }));
      }

      if (basePos && typeof arena.base_offset_m === "number") {
        // Dimension line base -> arena edge, like the rulebook's "75m" arrow.
        const [bx, by] = project(basePos[0], basePos[1]);
        const [ex] = project(-h, basePos[1]);
        const ay = by + 18;
        svg.appendChild(svgEl("line", { x1: bx, y1: ay, x2: ex, y2: ay, stroke: "#58a6ff", "stroke-width": 1.2 }));
        svg.appendChild(svgEl("path", { d: `M ${bx + 5} ${ay - 3} L ${bx} ${ay} L ${bx + 5} ${ay + 3}`, fill: "none", stroke: "#58a6ff", "stroke-width": 1.2 }));
        svg.appendChild(svgEl("path", { d: `M ${ex - 5} ${ay - 3} L ${ex} ${ay} L ${ex - 5} ${ay + 3}`, fill: "none", stroke: "#58a6ff", "stroke-width": 1.2 }));
        svg.appendChild(textEl((bx + ex) / 2, ay + 13, arena.base_offset_m + " m", { fill: "#58a6ff", size: 11, anchor: "middle" }));
      }
    }

    // --- PoI markers (drawn before the fleet so drones sit on top) ---
    const visitByPoi = new Map();
    if (state.visits_available) for (const v of state.visits) visitByPoi.set(v.poi, v);
    const poiCounts = { spawned: 0, waiting: 0, active: 0, reported: 0, missed: 0 };
    for (const poi of pois) {
      const [x, y] = project(poi.x_m, poi.y_m);
      if (!dynamicPois || t === null) {
        // Static run: unchanged styling.
        svg.appendChild(svgEl("rect", { x: x - 2.5, y: y - 2.5, width: 5, height: 5, fill: "#555f6b", stroke: "#8a929b", "stroke-width": 0.5 }));
        svg.appendChild(labelEl(x + 5, y - 4, "poi" + poi.id, { fill: "#8a929b", size: 9 }));
        continue;
      }
      const status = poiStatusAt(poi, visitByPoi.get(poi.id), t);
      if (status === null) continue; // not spawned yet -- invisible, the swarm doesn't know it exists
      poiCounts.spawned += 1;
      poiCounts[status] += 1;
      const pr = typeof poi.priority === "number" ? " P" + poi.priority.toFixed(0) : "";
      if (status === "waiting") {
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: COLOR_POI_WAITING, stroke: "#1a1f24", "stroke-width": 1.5 }));
      } else if (status === "active") {
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: COLOR_POI_WAITING, stroke: COLOR_POI_ACTIVE, "stroke-width": 3 }));
      } else if (status === "reported") {
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: COLOR_POI_REPORTED, stroke: "#1a1f24", "stroke-width": 1.5 }));
        svg.appendChild(svgEl("path", { d: `M ${x - 3.5} ${y} L ${x - 1} ${y + 3} L ${x + 4} ${y - 3}`, fill: "none", stroke: "#0b0e11", "stroke-width": 2 }));
      } else {
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: "none", stroke: COLOR_POI_MISSED, "stroke-width": 2 }));
        svg.appendChild(svgEl("line", { x1: x - 3.5, y1: y - 3.5, x2: x + 3.5, y2: y + 3.5, stroke: COLOR_POI_MISSED, "stroke-width": 1.5 }));
        svg.appendChild(svgEl("line", { x1: x - 3.5, y1: y + 3.5, x2: x + 3.5, y2: y - 3.5, stroke: COLOR_POI_MISSED, "stroke-width": 1.5 }));
      }
      const fresh = t - poi.spawn_t_s < NEW_POI_HIGHLIGHT_S;
      if (fresh && status === "waiting") {
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 13, fill: "none", stroke: COLOR_POI_WAITING, "stroke-width": 1.5, "stroke-dasharray": "3 3" }));
      }
      const labelColor = status === "reported" ? COLOR_POI_REPORTED : status === "missed" ? COLOR_POI_MISSED : COLOR_POI_WAITING;
      const suffix = fresh && status === "waiting" ? "  NEW" : status === "missed" ? "  missed" : status === "reported" ? "  reported" : "";
      svg.appendChild(labelEl(x + 10, y + 4, "PoI " + poi.id + pr + suffix, { fill: labelColor, size: 10 }));
    }

    // --- A26 mission bar: clock + PoI tally (only for dynamic-spawn runs) ---
    if (dynamicPois && t !== null) {
      const total = arena && typeof arena.mission_duration_s === "number" ? arena.mission_duration_s : null;
      dom.missionBar.innerHTML =
        '<span style="color:#cfd6dd">mission clock ' + fmtClock(t) + (total ? " / " + fmtClock(total) : "") + '</span>&nbsp;&nbsp;|&nbsp;&nbsp;' +
        '<span style="color:#cfd6dd">PoIs spawned ' + poiCounts.spawned + "/" + pois.length + '</span>&nbsp;&nbsp;' +
        '<span style="color:' + COLOR_POI_REPORTED + '">reported ' + poiCounts.reported + '</span>&nbsp;&nbsp;' +
        '<span style="color:' + COLOR_POI_MISSED + '">missed ' + poiCounts.missed + '</span>&nbsp;&nbsp;' +
        '<span style="color:' + COLOR_POI_WAITING + '">waiting ' + (poiCounts.waiting + poiCounts.active) + '</span>';
    } else {
      dom.missionBar.innerHTML = "";
    }

    // Figure out the surveyor's uav id (if we can), used both to draw it
    // distinctly and to check whether the chain actually reaches it.
    let surveyorUav = null;
    for (const p of framePositions) {
      if (p.role === "surveyor") surveyorUav = p.uav;
    }

    // --- active chain (this tick) ---
    const path = frameChain && Array.isArray(frameChain.path) ? frameChain.path : [];
    const linkQuality = frameChain && Array.isArray(frameChain.link_quality) ? frameChain.link_quality : [];
    const reachesSurveyor = path.length >= 2 && surveyorUav !== null && path[path.length - 1] === surveyorUav;
    const chainOk = !!frameChain && path.length >= 2 && (surveyorUav === null || reachesSurveyor);

    if (chainOk) {
      for (let i = 0; i < path.length - 1; i++) {
        const a = resolveNode(path[i], positionsByUav, basePos);
        const b = resolveNode(path[i + 1], positionsByUav, basePos);
        const q = linkQuality[i];
        if (!a || !b) {
          console.warn("relay_map: chain hop references a UAV not in this tick's positions", path[i], path[i + 1]);
          continue;
        }
        const [ax, ay] = project(a[0], a[1]);
        const [bx, by] = project(b[0], b[1]);
        const color = qualityColor(q);
        svg.appendChild(svgEl("line", { x1: ax, y1: ay, x2: bx, y2: by, stroke: color, "stroke-width": 2.5 }));
        // A26: on the full 1000 m arena a hop can be only ~30 px long, and
        // its PDR label then sits on top of the drone labels. Skip the
        // number on very short hops -- the line colour still carries it.
        if (Math.hypot(bx - ax, by - ay) >= 40) {
          const mx = (ax + bx) / 2, my = (ay + by) / 2;
          svg.appendChild(svgEl("rect", { x: mx - 14, y: my - 8, width: 28, height: 12, fill: "#0b0e11", opacity: 0.85 }));
          svg.appendChild(textEl(mx, my + 2, typeof q === "number" ? q.toFixed(2) : "?", { fill: color, size: 10, anchor: "middle" }));
        }
      }
    }

    // Relay ids actually on the active chain this tick -- everything else
    // is idle/standby (healthy spare capacity), NOT a fault. Empty when the
    // chain isn't ok, which correctly makes every relay render as idle in
    // that case -- the NO ACTIVE CHAIN banner below is what flags the
    // mission-level failure; individual relay dots never carry alarm
    // styling on their own.
    const onChainRelayIds = new Set();
    if (chainOk) {
      for (const node of path) {
        if (typeof node === "string" && node.startsWith("relay_")) {
          onChainRelayIds.add(Number(node.slice("relay_".length)));
        }
      }
    }

    // Relays that are OFFLINE at this tick: a uav_fail / uav_dropout event at
    // or before t. A later "restored" event means the CHAIN recovered (via
    // other relays), not that this relay came back, so it doesn't clear it.
    const offlineRelayIds = new Set();
    if (state.events_available && t !== null) {
      for (const e of state.events) {
        if ((e.type === "uav_fail" || e.type === "uav_dropout") && e.t_s <= t + 1e-6) offlineRelayIds.add(e.uav);
      }
    }

    // --- UAV dots (drawn after the chain so they sit on top of the lines) ---
    for (const p of framePositions) {
      const [x, y] = project(p.x_m, p.y_m);
      if (p.role === "base") {
        svg.appendChild(svgEl("rect", { x: x - 6, y: y - 6, width: 12, height: 12, fill: "#58a6ff", stroke: "#e6e6e6", "stroke-width": 1.5 }));
        // A26: the rulebook calls the base the "Operational center".
        if (arena) {
          // Below the 75 m arrow: the relays line up level with base, so a
          // label beside or just above it lands on top of them.
          svg.appendChild(labelEl(x - 8, y + 48, "Operational center (BASE)", { fill: "#58a6ff", size: 11 }));
        } else {
          svg.appendChild(labelEl(x + 9, y + 4, "BASE", { fill: "#58a6ff", size: 11 }));
        }
      } else if (p.role === "surveyor") {
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: "#e3b341", stroke: "#e6e6e6", "stroke-width": 1.5 }));
        svg.appendChild(labelEl(x + 10, y + 4, "S" + p.uav + " surveyor", { fill: "#e3b341", size: 11 }));
      } else if (p.role === "relay" && offlineRelayIds.has(p.uav)) {
        // Dropped out / failed: red with an X, never the idle-grey styling.
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: COLOR_BAD, stroke: "#e6e6e6", "stroke-width": 1 }));
        svg.appendChild(svgEl("line", { x1: x - 4, y1: y - 4, x2: x + 4, y2: y + 4, stroke: "#ffffff", "stroke-width": 2 }));
        svg.appendChild(svgEl("line", { x1: x - 4, y1: y + 4, x2: x + 4, y2: y - 4, stroke: "#ffffff", "stroke-width": 2 }));
        svg.appendChild(labelEl(x + 10, y + 4, "R" + p.uav + " OFFLINE", { fill: COLOR_BAD, size: 12 }));
      } else if (p.role === "relay") {
        const onChain = onChainRelayIds.has(p.uav);
        const fill = onChain ? COLOR_RELAY : COLOR_IDLE;
        const label = onChain ? ("R" + p.uav) : ("R" + p.uav + " idle/standby");
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 5.5, fill: fill, stroke: "#e6e6e6", "stroke-width": 1 }));
        svg.appendChild(labelEl(x + 8, y + 4, label, { fill: fill, size: 11 }));
      } else {
        // role missing/unknown -- generic relay-style dot, can't tell idle
        // status without role (see ui/DATA_CONTRACT.md).
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 5.5, fill: COLOR_RELAY, stroke: "#e6e6e6", "stroke-width": 1 }));
        svg.appendChild(labelEl(x + 8, y + 4, "R" + p.uav, { fill: COLOR_RELAY, size: 11 }));
      }
    }

    // --- fault-event markers (A17/CP6), drawn last so they sit on top ---
    const faults = state.events_available && t !== null ? pairFaultEvents(state.events) : [];
    for (const f of faults) {
      if (f.ev.t_s > t + 1e-6) continue;
      const nodePos = positionsByUav.get(f.ev.uav);
      const ended = f.end && f.end.t_s <= t + 1e-6;
      if (nodePos && !(ended && f.ev.type === "link_degraded")) {
        const [x, y] = project(nodePos[0], nodePos[1]);
        const color = f.ev.type === "link_degraded" ? COLOR_DEGRADED : COLOR_BAD;
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 13, fill: "none", stroke: color, "stroke-width": 2, "stroke-dasharray": "4 3" }));
        svg.appendChild(labelEl(x - 16, y - 16, "E" + f.n + " " + FAULT_TYPES[f.ev.type] + " t=" + f.ev.t_s.toFixed(1) + "s", { fill: color, size: 12, anchor: "end" }));
      }
      if (ended && f.recoveryS !== null && surveyorUav !== null && positionsByUav.has(surveyorUav)) {
        const sp = positionsByUav.get(surveyorUav);
        const [x, y] = project(sp[0], sp[1]);
        svg.appendChild(labelEl(x + 10, y + 20, "E" + f.n + " RECOVERED t=" + f.end.t_s.toFixed(1) + "s (" + f.recoveryS.toFixed(1) + "s)", { fill: COLOR_GOOD, size: 12 }));
      }
    }

    dom.svgContainer.appendChild(svg);

    // --- event log (A17/CP6): every fault event reached so far. Raw t_s /
    // recovery floats ride along as data-* attributes so the display can be
    // checked against uavx/logs/event_metrics.json exactly, not rounded. ---
    dom.eventLog.innerHTML = "";
    dom.eventLog.style.marginTop = "8px";
    dom.eventLog.style.fontSize = "12px";
    dom.eventLog.style.fontFamily = "ui-monospace, monospace";
    const reached = faults.filter((f) => f.ev.t_s <= t + 1e-6);
    if (!reached.length) {
      dom.eventLog.textContent = faults.length ? "events: none yet at this tick" : "events: none in this run";
      dom.eventLog.style.color = "#8a929b";
    }
    for (const f of reached) {
      const row = document.createElement("div");
      row.className = "fault-event";
      row.dataset.eventN = String(f.n);
      row.dataset.eventType = f.ev.type;
      row.dataset.node = "relay_" + f.ev.uav;
      row.dataset.tS = String(f.ev.t_s);
      let text = "EVENT " + f.n + "  t=" + f.ev.t_s.toFixed(1) + "s  relay_" + f.ev.uav + "  " + FAULT_TYPES[f.ev.type];
      let color = f.ev.type === "link_degraded" ? COLOR_DEGRADED : COLOR_BAD;
      if (f.end && f.end.t_s <= t + 1e-6) {
        row.dataset.endType = f.end.type;
        row.dataset.endTS = String(f.end.t_s);
        if (f.recoveryS !== null) {
          row.dataset.recoveryS = String(f.recoveryS);
          text += "  ->  chain RECOVERED t=" + f.end.t_s.toFixed(1) + "s, recovery " + f.recoveryS.toFixed(1) + "s";
          color = COLOR_GOOD;
        } else {
          text += "  ->  link restored t=" + f.end.t_s.toFixed(1) + "s";
        }
      }
      row.textContent = text;
      row.style.color = color;
      dom.eventLog.appendChild(row);
    }

    if (!chainOk) {
      const banner = document.createElement("div");
      banner.textContent = frameChain
        ? "NO ACTIVE CHAIN -- base cannot currently reach the surveyor"
        : "NO ACTIVE CHAIN -- chain data unavailable for this tick";
      banner.style.marginTop = "8px";
      banner.style.padding = "6px 10px";
      banner.style.background = "#2b0f0f";
      banner.style.color = COLOR_BAD;
      banner.style.border = "1px solid " + COLOR_BAD;
      banner.style.fontWeight = "bold";
      banner.style.fontSize = "12px";
      dom.banner.appendChild(banner);
    }

    // --- legend (always shown, not data-dependent) ---
    dom.legend.innerHTML = "";
    dom.legend.style.marginTop = "8px";
    dom.legend.style.fontSize = "11px";
    dom.legend.style.color = "#cfd6dd";
    dom.legend.innerHTML =
      '<span style="color:' + COLOR_GOOD + '">&#9632;</span> link quality &ge; 0.70&nbsp;&nbsp;' +
      '<span style="color:' + COLOR_DEGRADED + '">&#9632;</span> 0.40&ndash;0.69&nbsp;&nbsp;' +
      '<span style="color:' + COLOR_BAD + '">&#9632;</span> &lt; 0.40&nbsp;&nbsp;|&nbsp;&nbsp;' +
      '<span style="color:#58a6ff">&#9632;</span> base&nbsp;&nbsp;' +
      '<span style="color:' + COLOR_RELAY + '">&#9679;</span> relay (on chain)&nbsp;&nbsp;' +
      '<span style="color:' + COLOR_IDLE + '">&#9679;</span> relay idle/standby&nbsp;&nbsp;' +
      '<span style="color:' + COLOR_BAD + '">&#10005;</span> relay OFFLINE&nbsp;&nbsp;' +
      '<span style="color:#e3b341">&#9679;</span> surveyor&nbsp;&nbsp;' +
      (dynamicPois
        ? '<br><span style="color:' + COLOR_POI_WAITING + '">&#9679;</span> PoI waiting (appears at its random spawn time)&nbsp;&nbsp;' +
          '<span style="color:' + COLOR_POI_REPORTED + '">&#9679;</span> reported within 10 s&nbsp;&nbsp;' +
          '<span style="color:' + COLOR_POI_MISSED + '">&#9675;</span> reached, missed 10 s deadline&nbsp;&nbsp;' +
          '<span style="color:#3fb950">- - -</span> max relay reach'
        : '<span style="color:#8a929b">&#9632;</span> PoI');
  }

  function render(state, rootEl) {
    if (!dom || dom.root !== rootEl) {
      dom = buildSkeleton(rootEl);
    }
    latestState = state;

    const idx = buildIndices(state);
    ticks = idx.ticks;
    positionsByTick = idx.posByTick;
    chainByTick = idx.chByTick;
    runPoints = [];
    if (state.positions_available) for (const p of state.positions) runPoints.push([p.x_m, p.y_m]);
    if (state.pois_available) for (const p of state.pois) runPoints.push([p.x_m, p.y_m]);
    // A26: fit the whole operational area, not just where things happened
    // to go -- otherwise an arena the swarm mostly can't reach gets cropped.
    if (state.arena_available && typeof state.arena.half_extent_m === "number") {
      const h = state.arena.half_extent_m;
      runPoints.push([-h, -h], [h, h]);
    }

    if (!hasInitializedIndex && ticks.length > 0) {
      currentTickIndex = 0; // default: paused at tick 0 on load
      // Optional deep link: open paused at the recorded tick nearest
      // "#t=<seconds>" (e.g. http://127.0.0.1:8080/#t=205.4). Read once.
      const m = /(?:^#|&)t=([0-9.]+)/.exec(window.location.hash || "");
      if (m) {
        const want = Number(m[1]);
        let best = 0;
        for (let i = 1; i < ticks.length; i++) {
          if (Math.abs(ticks[i] - want) < Math.abs(ticks[best] - want)) best = i;
        }
        currentTickIndex = best;
      }
      hasInitializedIndex = true;
    }
    if (currentTickIndex > ticks.length - 1) currentTickIndex = Math.max(0, ticks.length - 1);
    if (currentTickIndex < 0) currentTickIndex = 0;

    updateNotes(state);
    drawFrame();
  }

  window.UI.registerPanel({
    id: "relay_map",
    title: "Relay Map + Active Chain",
    order: 0,
    render: render,
  });
})();
