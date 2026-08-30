#!/usr/bin/env python3
"""Replay a past F1 session through the plugin and print the board.

Live timing can normally only be tested while cars are on track. This
fetches the real archived data for a finished session and steps a fake
clock through it, so you can see exactly what the board would have shown,
minute by minute, without waiting for a race weekend.

    python3 tools/replay.py                     # last completed race
    python3 tools/replay.py --session 11353     # a specific session_key
    python3 tools/replay.py --list 2026         # find session keys
    python3 tools/replay.py --session 11353 --step 5 --board flagship

Standard library only — no pip install, runs on a stock macOS or Pi Python.
Run it from the plugin directory.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import ssl
import sys
import types
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PLUGIN_DIR = Path(__file__).resolve().parent.parent
OPENF1 = "https://api.openf1.org/v1"


def ensure_requests() -> None:
    """The plugin imports `requests`; provide a stdlib stand-in if it's absent.

    Only the pieces the plugin actually touches are needed, and `get` is
    monkey-patched by the replay anyway — this exists so `import requests`
    at the top of the plugin doesn't fail on a stock system Python.
    """
    try:
        import requests  # noqa: F401
        return
    except ImportError:
        pass

    module = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    class HTTPError(RequestException):
        pass

    class _Response:
        def __init__(self, body: bytes, status: int, headers: Dict[str, str]):
            self._body, self.status_code, self.headers = body, status, headers

        def raise_for_status(self):
            if self.status_code >= 400:
                raise HTTPError(f"HTTP {self.status_code}")

        def json(self):
            return json.loads(self._body or b"null")

        def iter_content(self, chunk_size=65536):
            for index in range(0, len(self._body), chunk_size):
                yield self._body[index:index + chunk_size]

        def close(self):
            return None

    def get(url, params=None, timeout=30, **kwargs):
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return _Response(response.read(), response.status, dict(response.headers))
        except urllib.error.HTTPError as exc:
            return _Response(exc.read(), exc.code, dict(exc.headers or {}))
        except ssl.SSLCertVerificationError as exc:
            raise SystemExit(
                "SSL certificate verification failed.\n"
                "On macOS this usually means Python's certificates aren't installed. Run:\n"
                '  /Applications/Python\\ 3.*/Install\\ Certificates.command\n'
                f"({exc})"
            )
        except Exception as exc:  # noqa: BLE001
            raise RequestException(str(exc)) from exc

    module.get = get
    module.RequestException = RequestException
    module.HTTPError = HTTPError
    sys.modules["requests"] = module


ensure_requests()
import requests  # noqa: E402  (real module, or the stub installed above)


# --------------------------------------------------------------------------
# Load the plugin without needing the FiestaBoard host application
# --------------------------------------------------------------------------
@dataclass
class PluginResult:
    available: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    formatted_lines: Optional[List[str]] = None


class PluginBase:
    def __init__(self, manifest):
        self._manifest, self._config = manifest or {}, {}

    @property
    def manifest(self):
        return self._manifest

    @property
    def config(self):
        return self._config

    @config.setter
    def config(self, value):
        self._config = value or {}

    @property
    def refresh_seconds(self):
        return None

    def validate_config(self, config):
        return []

    def cleanup(self):
        return None

    def on_config_change(self, old, new):
        return None

    def get_formatted_display(self):
        return None

    def get_data(self):
        return self.fetch_data()


def load_plugin():
    src, plugins, base = (types.ModuleType(n) for n in ("src", "src.plugins", "src.plugins.base"))
    base.PluginBase, base.PluginResult = PluginBase, PluginResult
    plugins.base, src.plugins = base, plugins
    sys.modules.update({"src": src, "src.plugins": plugins, "src.plugins.base": base})
    spec = importlib.util.spec_from_file_location("f1_replay", PLUGIN_DIR / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["f1_replay"] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Archived data, served back to the plugin as of a simulated clock
# --------------------------------------------------------------------------
def get(endpoint: str, **params) -> Any:
    response = requests.get(f"{OPENF1}/{endpoint}", params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse(value) -> Optional[datetime]:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class Archive:
    """Serves the session's real data, truncated to a simulated 'now'."""

    def __init__(self, session_key: int):
        print(f"Downloading session {session_key} ...", file=sys.stderr)
        self.sessions = get("sessions", session_key=session_key)
        if not self.sessions:
            raise SystemExit(f"No session with key {session_key}")
        self.session = self.sessions[0]
        self.year = self.session["year"]
        self.calendar = get("sessions", year=self.year)
        self.drivers = get("drivers", session_key=session_key)
        self.position = get("position", session_key=session_key)
        self.intervals = get("intervals", session_key=session_key)
        self.stints = get("stints", session_key=session_key)
        self.laps = get("laps", session_key=session_key)
        self.race_control = get("race_control", session_key=session_key)
        self.result = get("session_result", session_key=session_key)
        for label, rows in (
            ("position", self.position), ("intervals", self.intervals),
            ("laps", self.laps), ("race_control", self.race_control),
        ):
            print(f"  {label:<13} {len(rows):>6} rows", file=sys.stderr)

    def router(self, now: datetime):
        """A requests.get replacement answering as the API would have at `now`."""

        def upto(rows, field="date"):
            out = []
            for row in rows:
                moment = parse(row.get(field))
                if moment is None or moment <= now:
                    out.append(row)
            return out

        def respond(payload):
            class Response:
                headers: Dict[str, str] = {}
                status_code = 200

                def raise_for_status(self):
                    pass

                def json(self):
                    return payload

                def iter_content(self, size=None):
                    yield json.dumps(payload).encode()

                def close(self):
                    pass

            return Response()

        def router(url, params=None, timeout=None, **kwargs):
            params = params or {}
            if "/sessions" in url:
                return respond(self.calendar)
            if "/drivers" in url:
                return respond(self.drivers)
            if "/position" in url:
                return respond(self._filter_dates(upto(self.position), params))
            if "/intervals" in url:
                return respond(self._filter_dates(upto(self.intervals), params))
            if "/stints" in url:
                return respond(self.stints)
            if "/laps" in url:
                rows = upto(self.laps, "date_start")
                number = params.get("driver_number")
                if number is not None:
                    rows = [r for r in rows if str(r.get("driver_number")) == str(number)]
                return respond(rows)
            if "/race_control" in url:
                rows = upto(self.race_control)
                if params.get("scope"):
                    rows = [r for r in rows if r.get("scope") == params["scope"]]
                if params.get("category"):
                    rows = [r for r in rows if r.get("category") == params["category"]]
                return respond(rows)
            if "/session_result" in url:
                # Only exists once the session has actually finished.
                end = parse(self.session.get("date_end"))
                return respond(self.result if end and now > end else [])
            return respond([])

        return router

    @staticmethod
    def _filter_dates(rows, params):
        """Honour the API's date> / date>= filters the plugin may send."""
        for key, value in params.items():
            if not key.startswith("date"):
                continue
            bound = parse(value)
            if bound is None:
                continue
            if key in ("date>", "date>="):
                rows = [r for r in rows if (parse(r.get("date")) or bound) >= bound]
            elif key in ("date<", "date<="):
                rows = [r for r in rows if (parse(r.get("date")) or bound) <= bound]
        return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", type=int, help="OpenF1 session_key to replay")
    ap.add_argument("--list", type=int, metavar="YEAR", help="list session keys for a year and exit")
    ap.add_argument("--step", type=int, default=10, help="minutes between samples (default 10)")
    ap.add_argument("--board", choices=["note", "flagship"], default="note")
    ap.add_argument("--timezone", default="Australia/Sydney")
    args = ap.parse_args()

    if args.list:
        for row in get("sessions", year=args.list):
            if not row.get("is_cancelled"):
                print(f"{row['session_key']:>6}  {row['date_start'][:16]}  "
                      f"{row['circuit_short_name']:<20} {row['session_name']}")
        return 0

    session_key = args.session
    if session_key is None:
        now = datetime.now(timezone.utc)
        races = [
            s for s in get("sessions", year=now.year)
            if s.get("session_name") == "Race" and not s.get("is_cancelled")
            and (parse(s.get("date_end")) or now) < now
        ]
        if not races:
            raise SystemExit("No completed race this year; pass --session")
        session_key = races[-1]["session_key"]

    f1 = load_plugin()
    archive = Archive(session_key)
    manifest = json.loads((PLUGIN_DIR / "manifest.json").read_text())

    start = parse(archive.session["date_start"])
    end = parse(archive.session["date_end"]) or (start + timedelta(hours=2))
    width = 15 if args.board == "note" else 22

    print(f"\n{archive.session['session_name']} — {archive.session['circuit_short_name']} "
          f"({archive.session['country_code']}), {start:%Y-%m-%d}")
    print(f"replaying {start:%H:%M} to {end:%H:%M} UTC every {args.step} min, "
          f"{args.board} board\n")

    from unittest.mock import patch

    moment = start - timedelta(minutes=10)
    previous = None
    while moment <= end + timedelta(minutes=20):
        plugin = f1.F1Plugin(manifest)
        plugin.config = {"board": args.board, "display_mode": "auto",
                         "fallback_mode": "countdown", "timezone": args.timezone}

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return moment.astimezone(tz) if tz else moment

        with patch.object(f1, "datetime", Clock), \
             patch.object(f1.requests, "get", side_effect=archive.router(moment)):
            result = plugin.fetch_data()

        stamp = f"{moment:%H:%M}"
        if not result.available:
            print(f"{stamp}  UNAVAILABLE — {result.error}")
        else:
            lines = result.formatted_lines
            rendered = " | ".join(
                "".join("#" if f1.COLOR_TOKEN.fullmatch(t) else t for t in f1.tiles(line)).ljust(width)
                for line in lines[:3]
            )
            marker = "" if rendered == previous else "  <-- changed"
            print(f"{stamp}  [{result.data['mode']:<9}] {rendered}{marker}")
            previous = rendered
        moment += timedelta(minutes=args.step)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
