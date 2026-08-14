"""Formula 1 plugin for FiestaBoard.

Data sources (both free, no API key required):

* OpenF1 (https://openf1.org)      - session calendar, live timing, tyres, flags
* Jolpica-F1 (https://api.jolpi.ca) - driver and constructor championship standings
"""

from __future__ import annotations

import logging
import os
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

# Vestaboard's printable character set (uppercase only, plus these symbols).
ALLOWED_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 !@#$()-+&=;:'\"%,./?°"
)

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
    "rb": "RACING B",
    "racing_bulls": "RACING B",
    "sauber": "SAUBER",
    "audi": "AUDI",
    "haas": "HAAS",
    "cadillac": "CADILLAC",
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
    "Racing Bulls": "RACING B",
    "RB": "RACING B",
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


def sanitize(text: Any, limit: Optional[int] = None, collapse: bool = True) -> str:
    """Fold text down to characters a split-flap board can actually show.

    ``collapse`` squeezes runs of whitespace, which is right for raw API
    values but wrong for already-laid-out rows where the padding *is* the
    column alignment.
    """
    if text is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(text))
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    upper = stripped.upper()
    cleaned = "".join(c if c in ALLOWED_CHARS else " " for c in upper)
    if collapse:
        cleaned = " ".join(cleaned.split())
    else:
        cleaned = cleaned.rstrip()
    if limit is not None:
        cleaned = cleaned[:limit]
    return cleaned


def fit(text: str, width: int) -> str:
    """Truncate a laid-out row to width, preserving internal padding."""
    return sanitize(text, collapse=False)[:width]


def pad_row(left: str, right: str, width: int) -> str:
    """Left/right justify two fragments inside a row of the given width."""
    left = sanitize(left)
    right = sanitize(right)
    if len(left) + len(right) + 1 > width:
        left = left[: max(0, width - len(right) - 1)]
    gap = width - len(left) - len(right)
    return (left + " " * max(1, gap) + right)[:width]


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
        return value

    @staticmethod
    def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        response = requests.get(url, params=params or {}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        return response.json()

    def _openf1(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        data = self._get_json(f"{OPENF1_BASE}/{endpoint}", params)
        return data if isinstance(data, list) else []

    def _jolpica(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        data = self._get_json(f"{JOLPICA_BASE}/{path}", params)
        return data.get("MRData", {}) if isinstance(data, dict) else {}

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
            lambda: {d["driver_number"]: d for d in self._openf1("drivers", {"session_key": key})},
        )

        since = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%S")
        positions = self._openf1("position", {"session_key": key, "date>": since})
        latest_pos: Dict[int, Dict[str, Any]] = {}
        for row in positions:
            number = row.get("driver_number")
            if number is None:
                continue
            current = latest_pos.get(number)
            if current is None or str(row.get("date")) >= str(current.get("date")):
                latest_pos[number] = row

        finished = False
        if not latest_pos:
            # Session over (or not started): fall back to the classification.
            for row in self._openf1("session_result", {"session_key": key}):
                number = row.get("driver_number")
                if number is not None and row.get("position") is not None:
                    latest_pos[number] = row
                    finished = True

        intervals = self._openf1(
            "intervals",
            {"session_key": key, "date>": (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%S")},
        )
        latest_int: Dict[int, Dict[str, Any]] = {}
        for row in intervals:
            number = row.get("driver_number")
            if number is None:
                continue
            current = latest_int.get(number)
            if current is None or str(row.get("date")) >= str(current.get("date")):
                latest_int[number] = row

        stints = self._openf1("stints", {"session_key": key})
        latest_stint: Dict[int, Dict[str, Any]] = {}
        for row in stints:
            number = row.get("driver_number")
            if number is None:
                continue
            current = latest_stint.get(number)
            if current is None or (row.get("stint_number") or 0) >= (current.get("stint_number") or 0):
                latest_stint[number] = row

        entries: List[Dict[str, Any]] = []
        for number, pos_row in latest_pos.items():
            position = pos_row.get("position")
            if position is None:
                continue
            driver = roster.get(number, {})
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

            compound = (stint.get("compound") or "").upper()
            tyre = TYRE_CHARS.get(compound, "")
            tyre_age = stint.get("tyre_age_at_start")

            code = sanitize(driver.get("name_acronym") or f"#{number}", 3)
            entry = {
                "position": str(int(position)),
                "code": code,
                "number": str(number),
                "name": sanitize(driver.get("last_name") or driver.get("full_name") or code, 12),
                "team": sanitize(TEAM_SHORT_LIVE.get(driver.get("team_name", ""), driver.get("team_name", "")), 9),
                "gap": _format_gap(gap_value, leader=int(position) == 1),
                "interval": _format_gap(interval_value, leader=int(position) == 1),
                "tyre": tyre,
                "tyre_age": "" if tyre_age is None else str(tyre_age),
                "_position": int(position),
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
            laps = RACE_LAPS.get(session.get("circuit_short_name", ""))
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
            numbers = [r.get("lap_number") for r in rows if r.get("lap_number") is not None]
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

        lists = mrdata.get("StandingsTable", {}).get("StandingsLists", [])
        if not lists:
            return []
        block = lists[0]
        rows: List[Dict[str, Any]] = []

        if resource == "driverstandings":
            leader_points = None
            for row in block.get("DriverStandings", []):
                driver = row.get("Driver", {})
                teams = row.get("Constructors", [])
                points = _to_number(row.get("points"))
                if leader_points is None:
                    leader_points = points
                team_id = teams[0].get("constructorId") if teams else ""
                team_name = teams[0].get("name", "") if teams else ""
                entry = {
                    "position": sanitize(row.get("position"), 2),
                    "code": sanitize(driver.get("code") or driver.get("familyName"), 3),
                    "name": sanitize(driver.get("familyName"), 12),
                    "team": sanitize(TEAM_SHORT.get(team_id, team_name), 9),
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
            for row in block.get("ConstructorStandings", []):
                team = row.get("Constructor", {})
                points = _to_number(row.get("points"))
                if leader_points is None:
                    leader_points = points
                entry = {
                    "position": sanitize(row.get("position"), 2),
                    "name": sanitize(team.get("name"), 14),
                    "short": sanitize(TEAM_SHORT.get(team.get("constructorId", ""), team.get("name", "")), 9),
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
            "session_short": SESSION_SHORT.get(session_name, sanitize(session_name, 6)),
            "session_type": sanitize(live_session.get("session_type", "") if live_session else "", 10),
            "circuit": sanitize(circuit, 18),
            "country": sanitize(country, 3),
            "gp_name": sanitize(f"{country} GP" if country else "", 22),
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
                f"{countdown_target.get('country_code', '')} GP" if countdown_target else "", 22
            ),
            "next_circuit": sanitize(
                countdown_target.get("circuit_short_name", "") if countdown_target else "", 18
            ),
            "next_country": sanitize(
                countdown_target.get("country_code", "") if countdown_target else "", 3
            ),
            "next_session": SESSION_SHORT.get(
                countdown_target.get("session_name", "") if countdown_target else "",
                sanitize(countdown_target.get("session_name", "") if countdown_target else "", 6),
            ),
            "next_local_date": _local(countdown_target, tz, "%a %d %b") if countdown_target else "",
            "next_local_time": _local(countdown_target, tz, "%H%M") if countdown_target else "",
            "countdown": _format_countdown(days, hours, minutes),
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
            "updated": datetime.now(tz).strftime("%H%M"),
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
            lines = self._table_lines(data.get("constructors", []), "WCC", "short", width, rows)
        else:
            lines = self._table_lines(data.get("drivers", []), "WDC", "code", width, rows)

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
        when = " ".join(x for x in (data.get("next_local_date", ""), data.get("next_local_time", "")) if x)

        if width <= 15:
            lines = [
                fit(f"NEXT {circuit}", width),
                fit(f"{session} IN {countdown}".strip(), width),
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
        self, rows_data: List[Dict[str, Any]], title: str, label_key: str, width: int, rows: int
    ) -> List[str]:
        lines: List[str] = []
        if width > 15:
            lines.append(pad_row(f"{title} STANDINGS", rows_data[0].get("round", "") if rows_data else "", width))
        available = rows - len(lines)
        for entry in rows_data[:available]:
            label = entry.get(label_key, "")
            if width <= 15:
                lines.append(pad_row(f"{entry.get('position', '')} {label}", entry.get("points", ""), width))
            else:
                lines.append(
                    pad_row(
                        f"{entry.get('position', '')} {label} {entry.get('team', '')}".strip(),
                        entry.get("points", ""),
                        width,
                    )
                )
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


def _to_number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


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
    except (TypeError, ValueError):
        return sanitize(value, 7)
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


def _format_countdown(days: str, hours: str, minutes: str) -> str:
    if not any((days, hours, minutes)):
        return ""
    if days and days != "0":
        return f"{days}D {hours}H"
    if hours and hours != "0":
        return f"{hours}H {minutes}M"
    return f"{minutes}M"


def _local(session: Optional[Dict[str, Any]], tz, fmt: str) -> str:
    if not session or "_start" not in session:
        return ""
    return sanitize(session["_start"].astimezone(tz).strftime(fmt), 10)
