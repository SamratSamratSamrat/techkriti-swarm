#!/usr/bin/env python3
"""ui/server.py -- read-only local viewer for uavx telemetry JSON.

HARD SAFETY CONSTRAINTS (ruling A16): this server is READ-ONLY. It never
sends a command, never arms anything, never touches vehicle state. It only
ever opens the telemetry file named by --telemetry (a plain JSON file on
disk); it takes no socket or serial connection of any kind, and it never
imports pymavlink or anything from swarm/ or uavx/. It answers GET only
(everything else gets 405) and binds to 127.0.0.1 only, never 0.0.0.0.

Usage:
    python3 -m ui.server --telemetry <path> [--port 8080] [--poll-ms 500]
"""

from __future__ import annotations

import argparse
import http.server
import json
import threading
from pathlib import Path

from ui import adapters

STATIC_DIR = Path(__file__).parent / "static"

_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}


class StateStore:
    """Re-reads the telemetry file on demand and always has *something* to
    hand back: the freshly-read state on success, or the last good state
    (marked stale) if this read failed. Never raises out of get()."""

    def __init__(self, telemetry_path: str, poll_ms: int):
        self.telemetry_path = telemetry_path
        self.poll_ms = poll_ms
        self._lock = threading.Lock()
        self._last_good: dict | None = None

    def get(self) -> dict:
        try:
            telemetry = adapters.load_telemetry(self.telemetry_path)
            state = adapters.extract_state(telemetry)
        except Exception as e:  # noqa: BLE001 -- file is external/mutable input; never crash the server on it
            with self._lock:
                fallback = dict(self._last_good) if self._last_good is not None else adapters.empty_state()
            fallback["stale"] = True
            fallback["error"] = f"{type(e).__name__}: {e}"
            return fallback

        state["stale"] = False
        state["error"] = None
        with self._lock:
            self._last_good = state
        return state


def _safe_static_path(rel_path: str) -> Path | None:
    """Resolve a request path to a file under STATIC_DIR, refusing anything
    that would escape it. The only filesystem access driven by request
    input in this server, so it gets its own guard."""
    static_root = STATIC_DIR.resolve()
    candidate = (static_root / rel_path.lstrip("/")).resolve()
    if candidate != static_root and static_root not in candidate.parents:
        return None
    if not candidate.is_file():
        return None
    return candidate


def make_handler(store: StateStore) -> type:
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "SwarmUI/1.0"
        protocol_version = "HTTP/1.1"

        def _write(self, body: bytes, status: int, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, obj: dict, status: int = 200) -> None:
            self._write(json.dumps(obj).encode("utf-8"), status, "application/json")

        def do_GET(self) -> None:
            if self.path in ("/", "/index.html"):
                html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
                html = html.replace("__POLL_MS__", str(store.poll_ms))
                self._write(html.encode("utf-8"), 200, "text/html; charset=utf-8")
                return

            if self.path == "/api/state":
                self._send_json(store.get())
                return

            if self.path.startswith("/static/"):
                path = _safe_static_path(self.path[len("/static/") :])
                if path is None:
                    self.send_error(404, "not found")
                    return
                content_type = _STATIC_CONTENT_TYPES.get(path.suffix, "application/octet-stream")
                self._write(path.read_bytes(), 200, content_type)
                return

            self.send_error(404, "not found")

        def _reject_write(self) -> None:
            body = json.dumps({"error": "this server is read-only; GET only"}).encode("utf-8")
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            self._reject_write()

        def do_PUT(self) -> None:
            self._reject_write()

        def do_DELETE(self) -> None:
            self._reject_write()

        def do_PATCH(self) -> None:
            self._reject_write()

        def do_HEAD(self) -> None:
            self._reject_write()

        def do_OPTIONS(self) -> None:
            self._reject_write()

        def log_message(self, fmt: str, *args) -> None:
            http.server.BaseHTTPRequestHandler.log_message(self, fmt, *args)

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only viewer for uavx telemetry JSON.")
    parser.add_argument("--telemetry", required=True, help="path to a telemetry JSON file (read every request)")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--poll-ms", type=int, default=500, help="browser poll interval for /api/state")
    args = parser.parse_args()

    store = StateStore(args.telemetry, args.poll_ms)
    handler_cls = make_handler(store)
    # 127.0.0.1 only -- never 0.0.0.0 -- this is a local instrument, not a service.
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler_cls)
    print(f"swarm UI (read-only) on http://127.0.0.1:{args.port}  telemetry={args.telemetry}  poll={args.poll_ms}ms")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
