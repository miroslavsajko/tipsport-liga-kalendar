#!/usr/bin/env python3
"""Railway entrypoint: scrape on a schedule, serve the .ics files, push to GitHub.

One process, two jobs:

  - a background thread scrapes every SCRAPE_INTERVAL_HOURS and then pushes
    the results to GitHub, so the Pages URLs keep working;
  - an HTTP server on $PORT serves the same files directly, so a calendar
    can subscribe to this service without going through GitHub at all.

Railway needs the port bound promptly, so the server starts first and the
first scrape runs in the background behind it.
"""

import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import publish_github
import update_calendar


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


PORT = int(_env("PORT", "8080"))
OUTPUT_DIR = Path(_env("SCRAPER_OUTPUT_DIR", "/data/ics"))
INTERVAL_HOURS = float(_env("SCRAPE_INTERVAL_HOURS", "24"))
# Retry sooner than the normal cadence when a run produced nothing usable.
RETRY_MINUTES = float(_env("SCRAPE_RETRY_MINUTES", "60"))

_shutdown = threading.Event()

STATUS = {
    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "last_run_at": None,
    "last_run_ok": None,
    "last_error": None,
    "teams_written": 0,
    "failures": [],
    "github": "not attempted",
    "runs": 0,
}


def run_once() -> None:
    STATUS["runs"] += 1
    started = datetime.now(timezone.utc)
    print(f"[scrape] starting run {STATUS['runs']}", flush=True)

    try:
        written, failures = update_calendar.scrape_all(OUTPUT_DIR)
        STATUS["teams_written"] = len(written)
        STATUS["failures"] = failures
        STATUS["last_run_ok"] = not failures
        STATUS["last_error"] = None
        print(
            f"[scrape] wrote {len(written)} team(s), {len(failures)} failure(s)",
            flush=True,
        )
    except Exception as e:
        STATUS["last_run_ok"] = False
        STATUS["last_error"] = str(e)
        print(f"[scrape] run failed: {e}", file=sys.stderr, flush=True)
    finally:
        STATUS["last_run_at"] = started.isoformat(timespec="seconds")

    # Push whatever was written, even a partial set — stale files for the
    # teams that failed are better than none for the teams that worked.
    if publish_github.configured():
        try:
            STATUS["github"] = publish_github.publish(OUTPUT_DIR)
        except Exception as e:
            STATUS["github"] = f"failed: {e}"
            print(f"[github] {e}", file=sys.stderr, flush=True)
        else:
            print(f"[github] {STATUS['github']}", flush=True)
    else:
        STATUS["github"] = "disabled (no GITHUB_TOKEN / GITHUB_REPOSITORY)"


def scheduler() -> None:
    while not _shutdown.is_set():
        run_once()
        if STATUS["last_run_ok"]:
            wait = INTERVAL_HOURS * 3600
        else:
            wait = RETRY_MINUTES * 60
            print(f"[scrape] run had failures; retrying in {wait / 60:.0f}m", flush=True)
        # Event.wait so SIGTERM does not have to sit out the whole interval.
        _shutdown.wait(wait)


class Handler(BaseHTTPRequestHandler):
    server_version = "tipsport-liga-kalendar"

    def log_message(self, fmt, *args):
        print(f"[http] {self.address_string()} {fmt % args}", flush=True)

    def _send(self, code, body, ctype, extra=None):
        payload = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0].strip("/")

        if path in ("", "index.html"):
            return self._send(200, self._index(), "text/html; charset=utf-8")

        if path in ("healthz", "status"):
            import json

            healthy = STATUS["last_run_at"] is None or bool(STATUS["teams_written"])
            return self._send(
                200 if healthy else 503,
                json.dumps(STATUS, indent=2),
                "application/json; charset=utf-8",
            )

        if path.endswith(".ics"):
            # Basename only: no traversal out of the output directory.
            target = OUTPUT_DIR / Path(path).name
            if target.is_file():
                return self._send(
                    200,
                    target.read_bytes(),
                    "text/calendar; charset=utf-8",
                    {"Cache-Control": "public, max-age=3600"},
                )
            return self._send(404, "Calendar not found\n", "text/plain; charset=utf-8")

        return self._send(404, "Not found\n", "text/plain; charset=utf-8")

    def _index(self) -> str:
        rows = []
        for team, (_id, slug) in sorted(update_calendar.TEAMS.items()):
            f = OUTPUT_DIR / f"{slug}.ics"
            if f.is_file():
                stamp = datetime.fromtimestamp(
                    f.stat().st_mtime, timezone.utc
                ).strftime("%Y-%m-%d %H:%M UTC")
                state = f'<a href="/{slug}.ics">{slug}.ics</a>'
            else:
                stamp, state = "—", "<em>not generated yet</em>"
            rows.append(f"<tr><td>{team}</td><td>{state}</td><td>{stamp}</td></tr>")

        last = STATUS["last_run_at"] or "not yet"
        return f"""<!doctype html><meta charset="utf-8">
<title>Tipsport liga calendars</title>
<style>
 body{{font:15px/1.5 system-ui,sans-serif;margin:2rem auto;max-width:52rem;padding:0 1rem}}
 table{{border-collapse:collapse;width:100%}}
 td,th{{text-align:left;padding:.4rem .6rem;border-bottom:1px solid #ddd}}
 code{{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}}
</style>
<h1>Tipsport liga calendars</h1>
<p>Subscribe in your calendar app to any URL below. Last scrape: <code>{last}</code>
 &middot; <a href="/healthz">status</a></p>
<table><tr><th>Team</th><th>File</th><th>Updated</th></tr>{''.join(rows)}</table>
"""


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def stop(signum, _frame):
        print(f"[service] signal {signum}, shutting down", flush=True)
        _shutdown.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    threading.Thread(target=scheduler, daemon=True, name="scheduler").start()

    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.daemon_threads = True
    print(f"[service] serving {OUTPUT_DIR} on port {PORT}", flush=True)

    threading.Thread(target=server.serve_forever, daemon=True, name="http").start()
    _shutdown.wait()
    server.shutdown()


if __name__ == "__main__":
    main()
