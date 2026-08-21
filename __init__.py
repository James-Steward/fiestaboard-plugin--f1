"""Formula 1 plugin for FiestaBoard.

Data sources (both free, no API key required):

* OpenF1 (https://openf1.org)      - session calendar, live timing, tyres, flags
* Jolpica-F1 (https://api.jolpi.ca) - driver and constructor championship standings
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

try:  # pragma: no cover - exercised implicitly by the host application
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python < 3.9 fallback
    ZoneInfo = None  # type: ignore[assignment]

    class ZoneInfoNotFoundError(Exception):  # type: ignore[no-redef]
        """Raised when the zoneinfo database is unavailable."""


from src.plugins.base import PluginBase, PluginResult

logger = logging.getLogger(__name__)

OPENF1_BASE = "https://api.openf1.org/v1"
JOLPICA_BASE = "https://api.jolpi.ca/ergast/f1"

REQUEST_TIMEOUT = 12

# Both upstreams are unauthenticated and community-run, so treat every
# response as untrusted input rather than assuming it is well-behaved.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 20_000
MAX_CACHE_ENTRIES = 64

# Vestaboard's printable character set (uppercase only, plus these symbols).
ALLOWED_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !@#$()-+&=;:'\"%,./?°"
)

# A coloured tile is written as {NN} in template output and occupies exactly
# one tile on the board, even though it is four characters of text.
COLOR_TOKEN = re.compile(r"\{\d{2}\}")
COLOR_SPLIT = re.compile(r"(\{\d{2}\})")

RED, ORANGE, YELLOW, GREEN, BLUE, VIOLET, WHITE, BLACK = 63, 64, 65, 66, 67, 68, 69, 70

# A Note fits exactly five colour tiles beside a code and a three-digit score.
# The Flagship has room for more, but capping it keeps every row the same
# shape whether the label is HAAS or RED BULL.
MAX_SWATCH_TILES = 5

# Team colours, expressed in the seven tiles a Vestaboard can actually show.
# Multi-colour teams cycle, so a five-tile block reads as a repeating pattern
# and stays distinguishable from a solid one.
TEAM_COLORS = {
    "MCLAREN": [ORANGE],
    "FERRARI": [RED],
    "MERCEDES": [GREEN, WHITE],
    "RED BULL": [BLUE, RED],
    "ASTON": [GREEN],
    "ALPINE": [VIOLET],
    "WILLIAMS": [BLUE, WHITE],
    "VCARB": [BLUE, WHITE, RED],
    "AUDI": [WHITE],
    "SAUBER": [GREEN],
    "HAAS": [WHITE, RED],
    "CADILLAC": [WHITE, BLACK],
}

# Board geometry.
BOARD_WIDTH = {"note": 15, "flagship": 22}
BOARD_ROWS = {"note": 3, "flagship": 6}

# Tyre compound -> single board character.
TYRE_CHARS = {
    "SOFT": "S",
    "MEDIUM": "M",
    "HARD": "H",
    "INTERMEDIATE": "I",
    "WET": "W",
    "TEST_UNKNOWN": "?",
    "UNKNOWN": "?",
}

# Best-effort scheduled race distances, keyed by OpenF1 circuit_short_name.
# Used only to render "L34/72" style lap counters; a missing entry simply
# renders as "L34" instead. Kept deliberately conservative - circuits whose
# 2026 distance is not yet established are omitted rather than guessed.
RACE_LAPS = {
    "Melbourne": 58,
    "Shanghai": 56,
    "Suzuka": 53,
    "Sakhir": 57,
    "Jeddah": 50,
    "Miami": 57,
    "Montreal": 70,
    "Monte Carlo": 78,
    "Catalunya": 66,
    "Spielberg": 71,
    "Silverstone": 52,
    "Spa-Francorchamps": 44,
    "Hungaroring": 70,
    "Zandvoort": 72,
    "Monza": 53,
    "Baku": 51,
    "Singapore": 62,
    "Austin": 56,
    "Mexico City": 71,
    "Interlagos": 71,
    "Las Vegas": 50,
    "Lusail": 57,
    "Yas Marina Circuit": 58,
}

# Board-friendly constructor names (Jolpica constructorId -> short label).
TEAM_SHORT = {
    "mclaren": "MCLAREN",
    "mercedes": "MERCEDES",
    "ferrari": "FERRARI",
    "red_bull": "RED BULL",
    "aston_martin": "ASTON",
    "alpine": "ALPINE",
    "williams": "WILLIAMS",
    "rb": "VCARB",
    "racing_bulls": "VCARB",
    "sauber": "SAUBER",
    "audi": "AUDI",
    "haas": "HAAS",
    "cadillac": "CADILLAC",
}

# Three-letter codes used on the Note, where a full team name would leave no
# room for the colour block. Racing Bulls keeps its conventional timing code.
TEAM_CODE = {
    "mclaren": "MCL",
    "mercedes": "MER",
    "ferrari": "FER",
    "red_bull": "RBR",
    "aston_martin": "AST",
    "alpine": "ALP",
    "williams": "WIL",
    "rb": "RBT",
    "racing_bulls": "RBT",
    "sauber": "SAU",
    "audi": "AUD",
    "haas": "HAA",
    "cadillac": "CAD",
}

# OpenF1 team_name -> short label.
TEAM_SHORT_LIVE = {
    "McLaren": "MCLAREN",
    "Mercedes": "MERCEDES",
    "Ferrari": "FERRARI",
    "Red Bull Racing": "RED BULL",
    "Aston Martin": "ASTON",
    "Alpine": "ALPINE",
    "Williams": "WILLIAMS",
    "Racing Bulls": "VCARB",
    "RB": "VCARB",
    "Kick Sauber": "SAUBER",
    "Audi": "AUDI",
    "Haas F1 Team": "HAAS",
    "Haas": "HAAS",
    "Cadillac": "CADILLAC",
}

SESSION_SHORT = {
    "Race": "RACE",
    "Sprint": "SPRINT",
    "Qualifying": "QUALI",
    "Sprint Qualifying": "SQUALI",
    "Sprint Shootout": "SQUALI",
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
    "Day 1": "TEST1",
    "Day 2": "TEST2",
    "Day 3": "TEST3",
}


def _fold(text: str) -> str:
    """Uppercase, strip diacritics, and replace anything the board can't show."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c if c in ALLOWED_CHARS else " " for c in stripped.upper())


def sanitize(text: Any, limit: Optional[int] = None, collapse: bool = True) -> str:
    """Fold text down to characters a split-flap board can actually show.

    ``collapse`` squeezes runs of whitespace, which is right for raw API
    values but wrong for already-laid-out rows where the padding *is* the
    column alignment.

    Containers render as "" rather than their Python repr: an upstream field
    that arrives as a dict should show nothing, not ``{'evil': 1}``.
    """
    cleaned = _fold(_text(text))
    cleaned = " ".join(cleaned.split()) if collapse else cleaned.rstrip()
    return cleaned[:limit] if limit is not None else cleaned


def tiles(text: str) -> List[str]:
    """Split a laid-out row into board tiles.

    A colour code such as ``{66}`` is four characters of template text but
    occupies exactly one tile, so width maths has to count tiles, not
    characters.
    """
    out: List[str] = []
    index = 0
    while index < len(text):
        match = COLOR_TOKEN.match(text, index)
        if match:
            out.append(match.group(0))
            index = match.end()
        else:
            out.append(text[index])
            index += 1
    return out


def fit(text: str, width: int) -> str:
    """Truncate a laid-out row to ``width`` tiles, keeping padding and colours."""
    rebuilt = "".join(
        piece if COLOR_TOKEN.fullmatch(piece) else _fold(piece)
        for piece in COLOR_SPLIT.split(str(text))
    )
    return "".join(tiles(rebuilt)[:width]).rstrip()


def swatch(team: Any, count: int) -> str:
    """A run of ``count`` coloured tiles in the team's colours, or '' if it won't fit."""
    if count < 1:
        return ""
    cycle = TEAM_COLORS.get(sanitize(team))
    if not cycle:
        return ""
    return "".join("{%d}" % cycle[i % len(cycle)] for i in range(count))


def _clean_fragment(value: Any) -> str:
    """Board-safe text with colour codes left intact and whitespace collapsed."""
    if value is None:
        return ""
    joined = "".join(
        piece if COLOR_TOKEN.fullmatch(piece) else _fold(piece)
        for piece in COLOR_SPLIT.split(str(value))
    )
    return " ".join(joined.split())


def pad_row(left: Any, right: Any, width: int) -> str:
    """Left/right justify two fragments inside a row, measured in board tiles."""
    left = _clean_fragment(left)
    right = _clean_fragment(right)
    left_tiles, right_tiles = tiles(left), tiles(right)
    if len(left_tiles) + len(right_tiles) + 1 > width:
        left_tiles = left_tiles[: max(0, width - len(right_tiles) - 1)]
        left = "".join(left_tiles)
    gap = width - len(left_tiles) - len(right_tiles)
    return left + " " * max(1, gap) + right


class F1Plugin(PluginBase):
    """Live F1 timing, race countdown, and championship standings."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._cache: Dict[str, Tuple[datetime, Any]] = {}

    @property
    def plugin_id(self) -> str:
        return "f1"

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        errors: List[str] = []

        board = config.get("board", "note")
        if board not in BOARD_WIDTH:
            errors.append(f"Invalid board size: {board}. Use 'note' or 'flagship'.")

        mode = config.get("display_mode", "auto")
        if mode not in ("auto", "live", "countdown", "drivers", "constructors"):
            errors.append(f"Invalid display mode: {mode}")

        fallback = config.get("fallback_mode", "countdown")
        if fallback not in ("countdown", "drivers", "constructors"):
            errors.append(f"Invalid fallback mode: {fallback}")

        tz_name = config.get("timezone")
        if tz_name:
            try:
                self._zone(tz_name)
            except Exception:
                errors.append(f"Invalid timezone: {tz_name}")

        for key, low, high in (
            ("countdown_step_minutes", 1, 60),
            ("live_window_minutes", 0, 120),
            ("live_refresh_seconds", 10, 300),
            ("standings_refresh_seconds", 300, 86400),
            ("refresh_seconds", 10, 3600),
        ):
            value = config.get(key)
            if value is None:
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                errors.append(f"{key} must be a whole number")
                continue
            if not low <= number <= high:
                errors.append(f"{key} must be between {low} and {high}")

        return errors

    def on_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        self._cache.clear()

    def cleanup(self) -> None:
        self._cache.clear()

    @staticmethod
    def _zone(name: str):
        if ZoneInfo is None:  # pragma: no cover - only on very old runtimes
            return timezone.utc
        return ZoneInfo(name)

    def _tz(self):
        name = self.config.get("timezone") or os.getenv("F1_TIMEZONE") or "UTC"
        try:
            return self._zone(name)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            logger.warning("Unknown timezone %s, falling back to UTC", name)
            return timezone.utc

    # ------------------------------------------------------------------
    # HTTP helpers with per-key TTL caching
    # ------------------------------------------------------------------

    def _cached(self, key: str, ttl_seconds: int, loader):
        now = datetime.now(timezone.utc)
        hit = self._cache.get(key)
        if hit and (now - hit[0]).total_seconds() < ttl_seconds:
            return hit[1]
        value = loader()
        self._cache[key] = (now, value)
        self._evict()
        return value

    def _evict(self) -> None:
        """Keep the cache bounded.

        Keys include the session id, so a long-running board would otherwise
        accumulate one roster, one snapshot and one flag entry per session for
        the whole season and never release them.
        """
        if len(self._cache) <= MAX_CACHE_ENTRIES:
            return
        for key, _ in sorted(self._cache.items(), key=lambda item: item[1][0])[
            : len(self._cache) - MAX_CACHE_ENTRIES
        ]:
            self._cache.pop(key, None)

    @staticmethod
    def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET and parse JSON, refusing to buffer an unreasonable response.

        Both APIs are community-run and unauthenticated. A compromised or
        simply broken upstream returning an enormous body would otherwise be
        read straight into memory on someone's Pi or NAS.
        """
        response = requests.get(url, params=params or {}, timeout=REQUEST_TIMEOUT, stream=True)
        try:
            response.raise_for_status()

            declared = getattr(response, "headers", {}).get("Content-Length")
            if declared and str(declared).isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                raise ValueError(f"{url} declared {declared} bytes, over the {MAX_RESPONSE_BYTES} limit")

            # Test doubles frequently implement json() but not the streaming
            # interface, so fall back rather than depend on the mock's shape.
            if not hasattr(response, "iter_content"):
                return response.json()

            chunks: List[bytes] = []
            total = 0
            for chunk in response.iter_content(64 * 1024):
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise ValueError(f"{url} exceeded the {MAX_RESPONSE_BYTES} byte limit")
                chunks.append(chunk)
        finally:
            closer = getattr(response, "close", None)
            if callable(closer):
                closer()

        return json.loads(b"".join(chunks) or b"null")

    def _openf1(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        data = self._get_json(f"{OPENF1_BASE}/{endpoint}", params)
        if not isinstance(data, list):
            return []
        # Guard the loops downstream as well as the transfer itself.
        return [row for row in data[:MAX_ROWS] if isinstance(row, dict)]

    def _jolpica(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = self._get_json(f"{JOLPICA_BASE}/{path}", params)
        if not isinstance(data, dict):
            return {}
        mrdata = data.get("MRData", {})
        return mrdata if isinstance(mrdata, dict) else {}

    # ------------------------------------------------------------------
    # Calendar
    # ------------------------------------------------------------------

    def _sessions(self, year: int) -> List[Dict[str, Any]]:
        def load() -> List[Dict[str, Any]]:
            rows = self._openf1("sessions", {"year": year})
            usable = []
            for row in rows:
                if row.get("is_cancelled"):
                    continue
                start = _parse_iso(row.get("date_start"))
                end = _parse_iso(row.get("date_end"))
                if not start:
                    continue
                row["_start"] = start
                row["_end"] = end or start + timedelta(hours=2)
                usable.append(row)
            usable.sort(key=lambda r: r["_start"])
            return usable

        return self._cached(f"sessions:{year}", 6 * 3600, load)

    def _calendar(self, now: datetime) -> List[Dict[str, Any]]:
        rows = self._sessions(now.year)
        if not rows or rows[-1]["_end"] < now:
            # Late December / pre-season: peek at the following year too.
            try:
                rows = rows + self._sessions(now.year + 1)
            except requests.RequestException:
                pass
        if not self.config.get("include_practice", True):
            rows = [r for r in rows if r.get("session_type") != "Practice"]
        return rows

    def _find_sessions(
        self, now: datetime
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Return (live session, next session, next race)."""
        window = timedelta(minutes=int(self.config.get("live_window_minutes", 15) or 0))
        rows = self._calendar(now)

        live = None
        upcoming = None
        next_race = None

        for row in rows:
            if row["_start"] - window <= now <= row["_end"] + window:
                live = row
            if row["_start"] > now:
                if upcoming is None:
                    upcoming = row
                if next_race is None and row.get("session_name") == "Race":
                    next_race = row
        return live, upcoming, next_race

    # ------------------------------------------------------------------
    # Live timing
    # ------------------------------------------------------------------

    def _live_snapshot(self, session: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        key = session.get("session_key")
        ttl = int(self.config.get("live_refresh_seconds", 20) or 20)

        def load() -> Dict[str, Any]:
            return self._fetch_live(session, now)

        return self._cached(f"live:{key}", ttl, load)

    def _fetch_live(self, session: Dict[str, Any], now: datetime) -> Dict[str, Any]:
        key = session.get("session_key")

        roster = self._cached(
            f"roster:{key}",
            3600,
            lambda: {
                n: d
                for d in self._openf1("drivers", {"session_key": key})
                for n in [_as_int(d.get("driver_number"))]
                if n is not None
            },
        )

        since = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S")
        positions = self._openf1("position", {"session_key": key, "date>": since})
        latest_pos: Dict[int, Dict[str, Any]] = {}
        for row in positions:
            number = _as_int(row.get("driver_number"))
            if number is None:
                continue
            current = latest_pos.get(number)
            if current is None or str(row.get("date")) >= str(current.get("date")):
                latest_pos[number] = row

        finished = False
        if not latest_pos:
            # Session over (or not started): fall back to the classification.
            for row in self._openf1("session_result", {"session_key": key}):
                number = _as_int(row.get("driver_number"))
                if number is not None and row.get("position") is not None:
                    latest_pos[number] = row
                    finished = True

        intervals = self._openf1(
            "intervals",
            {"session_key": key, "date>": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")},
        )
        latest_int: Dict[int, Dict[str, Any]] = {}
        for row in intervals:
            number = _as_int(row.get("driver_number"))
            if number is None:
                continue
            current = latest_int.get(number)
            if current is None or str(row.get("date")) >= str(current.get("date")):
                latest_int[number] = row

        stints = self._openf1("stints", {"session_key": key})
        latest_stint: Dict[int, Dict[str, Any]] = {}
        for row in stints:
            number = _as_int(row.get("driver_number"))
            if number is None:
                continue
            current = latest_stint.get(number)
            if current is None or (row.get("stint_number") or 0) >= (current.get("stint_number") or 0):
                latest_stint[number] = row

        entries: List[Dict[str, Any]] = []
        for number, pos_row in latest_pos.items():
            position = _as_int(pos_row.get("position"))
            if position is None:
                # One unparseable row must not blank the whole board.
                logger.debug("Skipping row with unusable position: %r", pos_row.get("position"))
                continue
            driver = roster.get(number, {})
            if not isinstance(driver, dict):
                driver = {}
            interval_row = latest_int.get(number, {})
            stint = latest_stint.get(number, {})

            if finished:
                gap_value = pos_row.get("gap_to_leader")
                interval_value = None
                if pos_row.get("dnf"):
                    gap_value = "DNF"
                elif pos_row.get("dns"):
                    gap_value = "DNS"
                elif pos_row.get("dsq"):
                    gap_value = "DSQ"
            else:
                gap_value = interval_row.get("gap_to_leader")
                interval_value = interval_row.get("interval")

            compound = _text(stint.get("compound")).upper()
            tyre = TYRE_CHARS.get(compound, "")
            tyre_age = stint.get("tyre_age_at_start")

            code = sanitize(driver.get("name_acronym") or f"#{number}", 3)
            team_name = _text(driver.get("team_name"))
            entry = {
                "position": str(position),
                "code": code,
                "number": sanitize(number, 2),
                "name": sanitize(driver.get("last_name") or driver.get("full_name") or code, 12),
                "team": sanitize(TEAM_SHORT_LIVE.get(team_name, team_name), 9),
                "gap": _format_gap(gap_value, leader=position == 1),
                "interval": _format_gap(interval_value, leader=position == 1),
                "tyre": tyre,
                "tyre_age": sanitize("" if tyre_age is None else tyre_age, 2),
                "_position": position,
            }
            entry["line"] = pad_row(
                f"{entry['position']} {entry['code']}", entry["gap"] or entry["interval"], 22
            )
            entries.append(entry)

        entries.sort(key=lambda e: e["_position"])
        for entry in entries:
            entry.pop("_position", None)

        return {
            "entries": entries,
            "finished": finished,
            "lap": self._current_lap(session, entries, now),
            "flag": self._track_status(session, now),
        }

    def _current_lap(
        self, session: Dict[str, Any], entries: List[Dict[str, Any]], now: datetime
    ) -> Tuple[str, str]:
        """Return (current lap, total laps) as strings; either may be blank."""
        total = ""
        if session.get("session_name") == "Race":
            laps = RACE_LAPS.get(_text(session.get("circuit_short_name")))
            if laps:
                total = str(laps)

        if not entries:
            return "", total

        leader_number = None
        for entry in entries:
            if entry["position"] == "1":
                leader_number = entry["number"]
                break
        if leader_number is None:
            return "", total

        def load() -> str:
            rows = self._openf1(
                "laps", {"session_key": session.get("session_key"), "driver_number": leader_number}
            )
            # max() over mixed str/int would raise; coerce and drop the rest.
            numbers = [n for n in (_as_int(r.get("lap_number")) for r in rows) if n is not None]
            return str(max(numbers)) if numbers else ""

        try:
            current = self._cached(
                f"lap:{session.get('session_key')}",
                int(self.config.get("live_refresh_seconds", 20) or 20),
                load,
            )
        except requests.RequestException:
            current = ""
        return current, total

    def _track_status(self, session: Dict[str, Any], now: datetime) -> str:
        key = session.get("session_key")

        def load() -> str:
            status = ""
            try:
                safety = self._openf1("race_control", {"session_key": key, "category": "SafetyCar"})
                flags = self._openf1("race_control", {"session_key": key, "scope": "Track"})
            except requests.RequestException:
                return ""

            latest_flag = flags[-1] if flags else None
            latest_sc = safety[-1] if safety else None

            if latest_flag:
                status = sanitize(latest_flag.get("flag") or "", 13)
            if latest_sc and latest_flag:
                if str(latest_sc.get("date")) >= str(latest_flag.get("date")):
                    message = sanitize(latest_sc.get("message") or "")
                    if "VIRTUAL" in message:
                        status = "VSC" if "DEPLOYED" in message else status
                    elif "DEPLOYED" in message:
                        status = "SAFETY CAR"
            return status

        return self._cached(f"flag:{key}", 30, load)

    # ------------------------------------------------------------------
    # Championship standings
    # ------------------------------------------------------------------

    def _standings(self, now: datetime) -> Dict[str, Any]:
        ttl = int(self.config.get("standings_refresh_seconds", 1800) or 1800)

        def load() -> Dict[str, Any]:
            return self._fetch_standings(now.year)

        return self._cached("standings", ttl, load)

    def _fetch_standings(self, year: int) -> Dict[str, Any]:
        drivers = self._standings_list(year, "driverstandings")
        constructors = self._standings_list(year, "constructorstandings")
        if not drivers and not constructors:
            # Pre-season: show last completed championship instead of nothing.
            drivers = self._standings_list(year - 1, "driverstandings")
            constructors = self._standings_list(year - 1, "constructorstandings")
            year = year - 1
        return {"season": year, "drivers": drivers, "constructors": constructors}

    def _standings_list(self, year: int, resource: str) -> List[Dict[str, Any]]:
        try:
            mrdata = self._jolpica(f"{year}/{resource}/", {"limit": 40})
        except requests.RequestException as exc:
            logger.warning("Standings fetch failed for %s %s: %s", year, resource, exc)
            return []

        table = mrdata.get("StandingsTable")
        lists = table.get("StandingsLists") if isinstance(table, dict) else None
        if not isinstance(lists, list) or not lists or not isinstance(lists[0], dict):
            return []
        block = lists[0]
        rows: List[Dict[str, Any]] = []

        if resource == "driverstandings":
            leader_points = None
            for row in _dict_rows(block.get("DriverStandings")):
                driver = row.get("Driver")
                driver = driver if isinstance(driver, dict) else {}
                teams = [t for t in _dict_rows(row.get("Constructors"))]
                points = _to_number(row.get("points"))
                if leader_points is None:
                    leader_points = points
                team_id = _text(teams[0].get("constructorId")) if teams else ""
                team_name = _text(teams[0].get("name")) if teams else ""
                entry = {
                    "position": sanitize(row.get("position"), 2),
                    "code": sanitize(driver.get("code") or driver.get("familyName"), 3),
                    "name": sanitize(driver.get("familyName"), 12),
                    "team": sanitize(TEAM_SHORT.get(_text(team_id), _text(team_name)), 9),
                    "points": _format_points(points),
                    "wins": sanitize(row.get("wins"), 2),
                    "gap": _format_points(leader_points - points) if leader_points is not None else "",
                    "round": sanitize(block.get("round"), 2),
                }
                entry["line"] = pad_row(
                    f"{entry['position']} {entry['code']} {entry['team']}", entry["points"], 22
                )
                rows.append(entry)
        else:
            leader_points = None
            for row in _dict_rows(block.get("ConstructorStandings")):
                team = row.get("Constructor")
                team = team if isinstance(team, dict) else {}
                points = _to_number(row.get("points"))
                if leader_points is None:
                    leader_points = points
                constructor_id = _text(team.get("constructorId"))
                entry = {
                    "position": sanitize(row.get("position"), 2),
                    "name": sanitize(team.get("name"), 14),
                    "short": sanitize(TEAM_SHORT.get(constructor_id, _text(team.get("name"))), 9),
                    "code": sanitize(TEAM_CODE.get(constructor_id, _text(team.get("name"))[:3]), 3),
                    "points": _format_points(points),
                    "wins": sanitize(row.get("wins"), 2),
                    "gap": _format_points(leader_points - points) if leader_points is not None else "",
                    "round": sanitize(block.get("round"), 2),
                }
                entry["line"] = pad_row(f"{entry['position']} {entry['short']}", entry["points"], 22)
                rows.append(entry)

        return rows

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def fetch_data(self) -> PluginResult:
        try:
            now = datetime.now(timezone.utc)
            tz = self._tz()

            live_session, next_session, next_race = self._find_sessions(now)

            live_data: Dict[str, Any] = {"entries": [], "finished": False, "lap": ("", ""), "flag": ""}
            if live_session is not None:
                try:
                    live_data = self._live_snapshot(live_session, now)
                except requests.RequestException as exc:
                    logger.warning("Live timing unavailable: %s", exc)

            try:
                standings = self._standings(now)
            except requests.RequestException as exc:
                logger.warning("Standings unavailable: %s", exc)
                standings = {"season": now.year, "drivers": [], "constructors": []}

            data = self._assemble(now, tz, live_session, next_session, next_race, live_data, standings)
            mode = self._resolve_mode(bool(live_session and live_data["entries"]))
            # Exposed to templates, so it has to be board-printable too.
            data["mode"] = mode.upper()

            lines = self._build_lines(mode, data)
            for index in range(6):
                data[f"line{index + 1}"] = lines[index] if index < len(lines) else ""

            return PluginResult(available=True, data=data, formatted_lines=lines)

        except requests.RequestException as exc:
            logger.warning("F1 network error: %s", exc)
            return PluginResult(available=False, error="F1 data source unavailable")
        except Exception as exc:  # noqa: BLE001 - fetch_data must never raise
            logger.exception("Unexpected error in f1 plugin")
            return PluginResult(available=False, error=str(exc))

    def _assemble(
        self,
        now: datetime,
        tz,
        live_session: Optional[Dict[str, Any]],
        next_session: Optional[Dict[str, Any]],
        next_race: Optional[Dict[str, Any]],
        live_data: Dict[str, Any],
        standings: Dict[str, Any],
    ) -> Dict[str, Any]:
        entries = live_data["entries"]
        lap_current, lap_total = live_data["lap"]

        if live_session and entries and not live_data["finished"]:
            status = "LIVE"
        elif live_session:
            status = "SOON"
        else:
            status = "OFF"

        podium = {f"p{i}": "" for i in (1, 2, 3)}
        for entry in entries[:3]:
            slot = f"p{entry['position']}"
            if slot in podium:
                podium[slot] = entry["code"]

        session_name = live_session.get("session_name", "") if live_session else ""
        circuit = live_session.get("circuit_short_name", "") if live_session else ""
        country = live_session.get("country_code", "") if live_session else ""

        countdown_target = next_session or next_race
        delta = (countdown_target["_start"] - now) if countdown_target else None
        days, hours, minutes = _split_delta(delta)

        drivers = standings.get("drivers", [])
        constructors = standings.get("constructors", [])
        wdc = drivers[0] if drivers else {}
        wdc2 = drivers[1] if len(drivers) > 1 else {}
        wcc = constructors[0] if constructors else {}
        wcc2 = constructors[1] if len(constructors) > 1 else {}

        lap_display = ""
        if lap_current:
            lap_display = f"L{lap_current}/{lap_total}" if lap_total else f"L{lap_current}"

        return {
            "status": status,
            "season": str(standings.get("season", now.year)),
            "round": sanitize(wdc.get("round", ""), 5),
            "session_name": sanitize(session_name, 16),
            "session_short": SESSION_SHORT.get(_text(session_name), sanitize(session_name, 6)),
            "session_type": sanitize(live_session.get("session_type", "") if live_session else "", 10),
            "circuit": sanitize(circuit, 18),
            "country": sanitize(country, 3),
            "gp_name": sanitize(f"{country} GP", 22) if country else "",
            "flag": sanitize(live_data.get("flag", ""), 13),
            "track_status": sanitize(live_data.get("flag", ""), 13),
            "lap": lap_display,
            "lap_current": sanitize(lap_current, 2),
            "lap_total": sanitize(lap_total, 2),
            "leader": entries[0]["code"] if entries else "",
            "leader_name": entries[0]["name"] if entries else "",
            "leader_team": entries[0]["team"] if entries else "",
            "p1": podium["p1"],
            "p2": podium["p2"],
            "p3": podium["p3"],
            "gap_p2": entries[1]["gap"] if len(entries) > 1 else "",
            "gap_p3": entries[2]["gap"] if len(entries) > 2 else "",
            "next_gp": sanitize(
                f"{_text(countdown_target.get('country_code'))} GP" if countdown_target else "", 22
            ),
            "next_circuit": sanitize(
                countdown_target.get("circuit_short_name", "") if countdown_target else "", 18
            ),
            "next_country": sanitize(
                countdown_target.get("country_code", "") if countdown_target else "", 3
            ),
            "next_session": SESSION_SHORT.get(
                _text(countdown_target.get("session_name")) if countdown_target else "",
                sanitize(countdown_target.get("session_name") if countdown_target else "", 6),
            ),
            # A numeric month keeps the weekday and still fits a Note in one
            # format, so both board sizes render the same thing.
            "next_local_date": _local(countdown_target, tz, "%a %d/%m") if countdown_target else "",
            # The colon matters: without it "0030" reads as a year, not a time.
            "next_local_time": _local(countdown_target, tz, "%H:%M") if countdown_target else "",
            "countdown": _format_countdown(
                days, hours, minutes, int(self.config.get("countdown_step_minutes", 15) or 1)
            ),
            "countdown_days": days,
            "countdown_hours": hours,
            "countdown_minutes": minutes,
            "wdc_leader": wdc.get("code", ""),
            "wdc_leader_name": wdc.get("name", ""),
            "wdc_team": wdc.get("team", ""),
            "wdc_points": wdc.get("points", ""),
            "wdc_gap": wdc2.get("gap", ""),
            "wcc_leader": wcc.get("short", ""),
            "wcc_points": wcc.get("points", ""),
            "wcc_gap": wcc2.get("gap", ""),
            "live": entries,
            "drivers": drivers,
            "constructors": constructors,
            "updated": datetime.now(tz).strftime("%H:%M"),
        }

    def _resolve_mode(self, has_live: bool) -> str:
        mode = self.config.get("display_mode", "auto")
        if mode != "auto":
            return mode
        if has_live:
            return "live"
        return self.config.get("fallback_mode", "countdown")

    # ------------------------------------------------------------------
    # Pre-built board lines
    # ------------------------------------------------------------------

    def _build_lines(self, mode: str, data: Dict[str, Any]) -> List[str]:
        board = self.config.get("board", "note")
        width = BOARD_WIDTH.get(board, 15)
        rows = BOARD_ROWS.get(board, 3)

        if mode == "live":
            lines = self._live_lines(data, width, rows)
        elif mode == "countdown":
            lines = self._countdown_lines(data, width, rows)
        elif mode == "constructors":
            # A Note has no room for a full team name plus a colour block, so
            # it falls back to the three-letter code.
            label = "short" if width > 15 else "code"
            lines = self._table_lines(
                data.get("constructors", []), "WCC", label, width, rows, color_key="short"
            )
        else:
            lines = self._table_lines(
                data.get("drivers", []), "WDC", "code", width, rows, color_key="team"
            )

        lines = [fit(line, width) for line in lines][:rows]
        while len(lines) < rows:
            lines.append("")
        return lines

    def _live_lines(self, data: Dict[str, Any], width: int, rows: int) -> List[str]:
        entries = data.get("live", [])
        header_bits = [data.get("country") or data.get("circuit", ""), data.get("session_short", "")]
        header = " ".join(b for b in header_bits if b)
        tail = data.get("lap") or data.get("flag") or data.get("status", "")
        lines = [pad_row(header, tail, width)]

        if width <= 15:
            top = entries[:3]
            lines.append(" ".join(f"{e['position']}{e['code']}" for e in top))
            gaps = [e["gap"] or e["interval"] for e in top[1:]]
            lines.append(("GAP " + " ".join(g for g in gaps if g)).strip())
        else:
            for entry in entries[: rows - 1]:
                right = (entry["gap"] or entry["interval"] or "")
                if entry["tyre"]:
                    right = f"{right} {entry['tyre']}".strip()
                left = f"{entry['position']} {entry['code']} {entry['team']}".strip()
                # Rather than slice a team name mid-word, drop it entirely.
                if len(left) + len(right) + 2 > width:
                    left = f"{entry['position']} {entry['code']}"
                lines.append(pad_row(left, right, width))
        return lines

    def _countdown_lines(self, data: Dict[str, Any], width: int, rows: int) -> List[str]:
        circuit = data.get("next_circuit") or data.get("next_country") or "F1"
        countdown = data.get("countdown", "")
        session = data.get("next_session", "")
        # "SAT 22/08 00:30" is exactly 15 tiles, so both boards share it.
        when = " ".join(x for x in (data.get("next_local_date", ""), data.get("next_local_time", "")) if x)

        if width <= 15:
            lines = [
                fit(f"NEXT {circuit}", width),
                # Right-aligned rather than "SQUALI IN 12D 23H", which is 17
                # tiles. Line 1 already establishes that this is the countdown.
                pad_row(session, countdown, width),
                fit(when, width),
            ]
        else:
            lines = [
                pad_row("NEXT UP", data.get("next_country", ""), width),
                fit(circuit, width),
                pad_row(session, countdown, width),
                fit(when, width),
                pad_row("WDC", f"{data.get('wdc_leader', '')} {data.get('wdc_points', '')}".strip(), width),
                pad_row("WCC", f"{data.get('wcc_leader', '')} {data.get('wcc_points', '')}".strip(), width),
            ]
        return lines[:rows]

    def _table_lines(
        self,
        rows_data: List[Dict[str, Any]],
        title: str,
        label_key: str,
        width: int,
        rows: int,
        color_key: Optional[str] = None,
    ) -> List[str]:
        lines: List[str] = []
        if width > 15:
            lines.append(pad_row(f"{title} STANDINGS", _text(rows_data[0].get("round")) if rows_data else "", width))

        available = rows - len(lines)
        for entry in rows_data[:available]:
            left = f"{entry.get('position', '')} {entry.get(label_key, '')}".strip()
            if width > 15 and entry.get("team"):
                left = f"{left} {entry['team']}"
            points = entry.get("points", "")

            # The colour block sits between the label and the points with a
            # blank tile either side, capped so rows stay uniform. A long name
            # or a half-point total shrinks it rather than overflowing.
            room = min(MAX_SWATCH_TILES, width - len(left) - len(points) - 2)
            block = swatch(entry.get(color_key, ""), room) if color_key else ""
            lines.append(pad_row(f"{left} {block}" if block else left, points, width))
        return lines

    def get_formatted_display(self) -> Optional[List[str]]:
        result = self.get_data()
        if isinstance(result, PluginResult):
            return result.formatted_lines
        return None


# ----------------------------------------------------------------------
# Module-level helpers
# ----------------------------------------------------------------------


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _text(value: Any) -> str:
    """A scalar coerced to str, and anything else to "".

    Upstream fields are used as dict lookup keys and have string methods called
    on them. A field that arrives as a dict or list would otherwise raise
    "unhashable type" or AttributeError and take the whole board offline.
    """
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value)


def _dict_rows(value: Any) -> List[Dict[str, Any]]:
    """Only the dict rows from something an API claimed was a list of them."""
    if not isinstance(value, list):
        return []
    return [row for row in value[:MAX_ROWS] if isinstance(row, dict)]


def _as_int(value: Any) -> Optional[int]:
    """int() that returns None instead of raising on anything unusable.

    Upstream rows are untrusted: a position of "P1", None, a dict or a NaN
    would otherwise propagate an exception up to fetch_data and take the whole
    display offline rather than dropping one row.
    """
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if -1000 < number < 1000 else None


def _to_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    # NaN and infinities format as "nan"/"inf" and would reach the board.
    return number if -1e9 < number < 1e9 else 0.0


def _format_points(value: Any) -> str:
    number = _to_number(value)
    if abs(number - round(number)) < 0.01:
        return str(int(round(number)))
    return f"{number:g}"


def _format_gap(value: Any, leader: bool = False) -> str:
    if isinstance(value, str):
        return sanitize(value, 7)
    if value is None:
        return "LEADER" if leader else ""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return sanitize(value, 7)
    if number != number or abs(number) == float("inf"):  # NaN / infinity
        return ""
    if number == 0:
        return "LEADER" if leader else "+0.0"
    if number >= 60:
        return f"+{number / 60:.0f}M"
    return f"+{number:.1f}"


def _split_delta(delta: Optional[timedelta]) -> Tuple[str, str, str]:
    if delta is None or delta.total_seconds() <= 0:
        return "", "", ""
    total_minutes = int(delta.total_seconds() // 60)
    days, remainder = divmod(total_minutes, 60 * 24)
    hours, minutes = divmod(remainder, 60)
    return str(days), str(hours), str(minutes)


def _format_countdown(days: str, hours: str, minutes: str, step: int = 15) -> str:
    """Coarsest useful granularity, floored.

    A split-flap board makes noise every time it changes, so this is tuned to
    change as rarely as it can while staying useful: hourly above an hour, and
    in ``step``-minute buckets below one. The exact start time sits on the row
    underneath, so precision here buys very little.
    """
    if not any((days, hours, minutes)):
        return ""
    if days and days != "0":
        return f"{days}D {hours}H"

    remaining = int(minutes or 0)
    if step > 1:
        remaining = (remaining // step) * step

    if hours and hours != "0":
        # Zero-padded so the width never changes: fewer tiles move per update.
        return f"{hours}H {remaining:02d}M"
    return f"{remaining}M" if remaining > 0 else "SOON"


def _local(session: Optional[Dict[str, Any]], tz, fmt: str) -> str:
    if not session or "_start" not in session:
        return ""
    return sanitize(session["_start"].astimezone(tz).strftime(fmt), 10)
