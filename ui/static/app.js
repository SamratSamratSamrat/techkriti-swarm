// ui/static/app.js -- page shell + panel registry.
//
// The registry pattern: any panel file calls UI.registerPanel({id, title,
// order, render(state, rootEl)}) once, at load time. This file owns the
// page shell (status bar + one container <section> per registered panel)
// and the polling loop; it never contains panel-specific drawing code.
// A new panel is a new file + a new <script> tag in index.html -- nothing
// here changes. See ui/README.md for a worked second-panel example.
"use strict";

(function () {
  const panels = [];

  function registerPanel(panel) {
    if (!panel || typeof panel.id !== "string" || typeof panel.render !== "function") {
      console.error("UI.registerPanel: ignoring invalid panel registration", panel);
      return;
    }
    panels.push(panel);
    panels.sort((a, b) => (a.order || 0) - (b.order || 0));
    buildShell();
  }

  function containerId(panel) {
    return "panel-" + panel.id;
  }

  function buildShell() {
    const root = document.getElementById("panels");
    root.innerHTML = "";
    for (const panel of panels) {
      const section = document.createElement("section");
      section.className = "panel";
      section.id = containerId(panel);

      const heading = document.createElement("h2");
      heading.textContent = panel.title || panel.id;
      section.appendChild(heading);

      const body = document.createElement("div");
      body.className = "panel-body";
      section.appendChild(body);

      root.appendChild(section);
    }
  }

  function renderAll(state) {
    for (const panel of panels) {
      const container = document.getElementById(containerId(panel));
      if (!container) continue;
      const body = container.querySelector(".panel-body");
      try {
        panel.render(state, body);
      } catch (err) {
        // A panel that throws is caught here and shown as an error inside
        // its OWN container -- it never takes the rest of the page down.
        body.innerHTML = "";
        const pre = document.createElement("pre");
        pre.className = "panel-error";
        pre.textContent = 'panel "' + panel.id + '" failed to render: ' + (err && err.stack ? err.stack : String(err));
        body.appendChild(pre);
        console.error("panel render error:", panel.id, err);
      }
    }
  }

  function updateStatusBar(state) {
    const bar = document.getElementById("status-bar");
    if (!bar) return;
    if (state.stale) {
      bar.className = "stale";
      bar.textContent =
        "STALE -- " + (state.error || "no reason given") + (state.t_s != null ? " (last good state at t=" + state.t_s + "s)" : " (no good state read yet)");
    } else {
      bar.className = "live";
      bar.textContent = "live -- t=" + (state.t_s != null ? state.t_s + "s" : "n/a");
    }
  }

  // A26: the server tags each state with a file version; sending it back
  // lets the server reply {"unchanged": true} instead of re-sending a
  // multi-megabyte replay every poll. Panels are only re-rendered when the
  // file actually changed (their own playback timers keep running).
  let lastVersion = null;

  async function poll() {
    try {
      const url = lastVersion ? "/api/state?v=" + encodeURIComponent(lastVersion) : "/api/state";
      const res = await fetch(url, { cache: "no-store" });
      const state = await res.json();
      updateStatusBar(state);
      if (state.unchanged) return;
      lastVersion = state.version || null;
      renderAll(state);
    } catch (err) {
      const bar = document.getElementById("status-bar");
      if (bar) {
        bar.className = "stale";
        bar.textContent = "STALE -- UI could not reach the server: " + err;
      }
      console.error("poll failed:", err);
    }
  }

  function start(pollMs) {
    poll();
    setInterval(poll, pollMs || 500);
  }

  window.UI = { registerPanel, start };
})();
