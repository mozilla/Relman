#!/usr/bin/env python3
"""Serve the dashboard on this machine (macOS, Linux or Windows).

    python3 dashboard/run.py          # http://relman.localhost:8100
    python3 dashboard/run.py 9000     # another port

On Windows the command is usually `python` rather than `python3`.

A plain static file server: the page itself talks to Bugzilla and the other
sources. Open it at relman.localhost rather than localhost: browsers treat
each *.localhost name as its own origin, so other dev servers on localhost
cannot read the API key this page keeps in local storage.
"""

import functools
import http.server
import sys
from pathlib import Path

SITE = Path(__file__).resolve().parent / "site"


class Handler(http.server.SimpleHTTPRequestHandler):
    # The Windows registry can map .js to text/plain, and browsers refuse to
    # run an ES module served as anything but JavaScript.
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript"}

    def end_headers(self):
        # Without this, browsers keep their own copy of a module and an edit
        # to a check doesn't show until a hard reload. Unchanged files still
        # come back as a cheap 304.
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8100
    try:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", port), functools.partial(Handler, directory=str(SITE)))
    except OSError as e:
        sys.exit(f"Could not listen on port {port} ({e.strerror}). Try another: run.py 9000")
    print(f"Release Management Dashboard: http://relman.localhost:{port}/  (Ctrl+C to stop)", flush=True)
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
