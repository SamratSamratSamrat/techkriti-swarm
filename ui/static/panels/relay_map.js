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

  const SPEEDS = [0.5, 1, 2, 4];
  const MIN_INTERVAL_MS = 20; // floor so a ~0 recorded dt (or a huge speed) can't spin the browser

  function qualityColor(q) {
    if (typeof q !== "number" || Number.isNaN(q)) return "#666";
    if (q >= GOOD_THRESHOLD) return COLOR_GOOD;
    if (q >= DEGRADED_THRESHOLD) return COLOR_DEGRADED;
    return COLOR_BAD;
  }

  // Deterministic projection from the current frame's own data -- no
  // history, no randomness -- so screen positions only move as far as the
  // underlying x_m/y_m actually moved between polls. PoIs are static and
  // usually dominate the bounding box, which keeps the view from
  // panning/zooming every tick even though it's recomputed every tick.
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
    const scale = Math.min((WIDTH / 2) / halfX, (HEIGHT / 2) / halfY);

    return function project(x, y) {
      return [WIDTH / 2 + (x - cx) * scale, HEIGHT / 2 - (y - cy) * scale]; // y inverted: north up
    };
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

  // --- Playback state (module-scoped: persists across app.js's poll-driven
  // render() calls, which is what lets play/scrub/speed work independently
  // of the ~500ms poll cadence) ---
  let dom = null;                       // cached DOM refs, built once per rootEl
  let latestState = null;               // most recent /api/state payload
  let ticks = [];                       // sorted distinct t_s values across positions+chains
  let positionsByTick = new Map();      // t_s -> array of position rows at that tick
  let chainByTick = new Map();          // t_s -> chain row at that tick
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
    const intervalMs = Math.max(MIN_INTERVAL_MS, (dtS * 1000) / speed);
    playbackTimer = setInterval(() => {
      currentTickIndex += 1;
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

    const svgContainer = document.createElement("div");
    rootEl.appendChild(svgContainer);

    const banner = document.createElement("div");
    rootEl.appendChild(banner);

    const legend = document.createElement("div");
    rootEl.appendChild(legend);

    return { root: rootEl, notes, playBtn, slider, readout, speedSelect, svgContainer, banner, legend };
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

    const points = [];
    for (const p of framePositions) points.push([p.x_m, p.y_m]);
    for (const p of pois) points.push([p.x_m, p.y_m]);

    if (points.length === 0) {
      const msg = document.createElement("div");
      msg.textContent = "no spatial data available for this tick (positions and pois both unavailable)";
      msg.style.color = "#ffcc66";
      dom.svgContainer.appendChild(msg);
      return;
    }

    const project = computeProjection(points);
    const svg = svgEl("svg", { width: WIDTH, height: HEIGHT, viewBox: `0 0 ${WIDTH} ${HEIGHT}`, style: "background:#0b0e11;border:1px solid #2c333b;" });

    // --- PoI markers (small, unobtrusive, drawn first so the fleet sits on top) ---
    for (const poi of pois) {
      const [x, y] = project(poi.x_m, poi.y_m);
      svg.appendChild(svgEl("rect", { x: x - 2.5, y: y - 2.5, width: 5, height: 5, fill: "#555f6b", stroke: "#8a929b", "stroke-width": 0.5 }));
      svg.appendChild(textEl(x + 5, y - 4, "poi" + poi.id, { fill: "#8a929b", size: 9 }));
    }

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
        const mx = (ax + bx) / 2, my = (ay + by) / 2;
        svg.appendChild(svgEl("rect", { x: mx - 14, y: my - 8, width: 28, height: 12, fill: "#0b0e11", opacity: 0.85 }));
        svg.appendChild(textEl(mx, my + 2, typeof q === "number" ? q.toFixed(2) : "?", { fill: color, size: 10, anchor: "middle" }));
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
        svg.appendChild(textEl(x + 9, y + 4, "BASE", { fill: "#58a6ff", size: 11 }));
      } else if (p.role === "surveyor") {
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: "#e3b341", stroke: "#e6e6e6", "stroke-width": 1.5 }));
        svg.appendChild(textEl(x + 10, y + 4, "S" + p.uav + " surveyor", { fill: "#e3b341", size: 11 }));
      } else if (p.role === "relay" && offlineRelayIds.has(p.uav)) {
        // Dropped out / failed: red with an X, never the idle-grey styling.
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 7, fill: COLOR_BAD, stroke: "#e6e6e6", "stroke-width": 1 }));
        svg.appendChild(svgEl("line", { x1: x - 4, y1: y - 4, x2: x + 4, y2: y + 4, stroke: "#ffffff", "stroke-width": 2 }));
        svg.appendChild(svgEl("line", { x1: x - 4, y1: y + 4, x2: x + 4, y2: y - 4, stroke: "#ffffff", "stroke-width": 2 }));
        svg.appendChild(textEl(x + 10, y + 4, "R" + p.uav + " OFFLINE", { fill: COLOR_BAD, size: 12 }));
      } else if (p.role === "relay") {
        const onChain = onChainRelayIds.has(p.uav);
        const fill = onChain ? COLOR_RELAY : COLOR_IDLE;
        const label = onChain ? ("R" + p.uav) : ("R" + p.uav + " idle/standby");
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 5.5, fill: fill, stroke: "#e6e6e6", "stroke-width": 1 }));
        svg.appendChild(textEl(x + 8, y + 4, label, { fill: fill, size: 11 }));
      } else {
        // role missing/unknown -- generic relay-style dot, can't tell idle
        // status without role (see ui/DATA_CONTRACT.md).
        svg.appendChild(svgEl("circle", { cx: x, cy: y, r: 5.5, fill: COLOR_RELAY, stroke: "#e6e6e6", "stroke-width": 1 }));
        svg.appendChild(textEl(x + 8, y + 4, "R" + p.uav, { fill: COLOR_RELAY, size: 11 }));
      }
    }

    dom.svgContainer.appendChild(svg);

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
      '<span style="color:#8a929b">&#9632;</span> PoI';
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

    if (!hasInitializedIndex && ticks.length > 0) {
      currentTickIndex = 0; // default: paused at tick 0 on load
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
