"""Tests for the FiestaBoard F1 plugin."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from src.plugins.base import PluginResult
from src.plugins.testing import PluginTestCase, create_mock_response

from .conftest import load_plugin_module

f1 = load_plugin_module()
F1Plugin = f1.F1Plugin

PLUGIN_DIR = Path(__file__).resolve().parent.parent
NOW = datetime.now(timezone.utc)

BOARD_WIDTHS = {"note": 15, "flagship": 22}
BOARD_ROWS = {"note": 3, "flagship": 6}


def tile_count(row: str) -> int:
    """Board tiles a preview row occupies.

    A numeric colour code like ``{66}`` renders as one coloured tile; a named
    tag like ``{green}`` / ``{/green}`` wraps text and occupies none.
    """
    row = re.sub(r"\{/?[a-z_]+\}", "", row)
    chips = len(re.findall(r"\{\d+\}", row))
    return len(re.sub(r"\{\d+\}", "", row)) + chips


def iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def build_sessions(race_offset_minutes: int):
    """Two sessions: a Race positioned relative to now, and a later Race."""
    race_start = NOW + timedelta(minutes=race_offset_minutes)
    return [
        {
            "session_key": 9001,
            "session_type": "Race",
            "session_name": "Race",
            "date_start": iso(race_start),
            "date_end": iso(race_start + timedelta(hours=2)),
            "meeting_key": 1292,
            "circuit_short_name": "Zandvoort",
            "country_code": "NED",
            "country_name": "Netherlands",
            "location": "Zandvoort",
            "year": NOW.year,
            "is_cancelled": False,
        },
        {
            "session_key": 9002,
            "session_type": "Practice",
            "session_name": "Practice 1",
            "date_start": iso(race_start + timedelta(days=14)),
            "date_end": iso(race_start + timedelta(days=14, hours=1)),
            "meeting_key": 1293,
            "circuit_short_name": "Monza",
            "country_code": "ITA",
            "country_name": "Italy",
            "year": NOW.year,
            "is_cancelled": False,
        },
        {
            "session_key": 9003,
            "session_type": "Race",
            "session_name": "Race",
            "date_start": iso(race_start + timedelta(days=99)),
            "date_end": iso(race_start + timedelta(days=99, hours=2)),
            "meeting_key": 1294,
            "circuit_short_name": "Cancelled Park",
            "country_code": "XXX",
            "year": NOW.year,
            "is_cancelled": True,
        },
    ]


DRIVERS = [
    {"driver_number": 12, "name_acronym": "ANT", "last_name": "Antonelli", "full_name": "Andrea Kimi ANTONELLI", "team_name": "Mercedes"},
    {"driver_number": 44, "name_acronym": "HAM", "last_name": "Hamilton", "full_name": "Lewis HAMILTON", "team_name": "Ferrari"},
    {"driver_number": 1, "name_acronym": "NOR", "last_name": "Norris", "full_name": "Lando NORRIS", "team_name": "McLaren"},
]

POSITIONS = [
    {"driver_number": 12, "position": 1, "date": iso(NOW - timedelta(seconds=30))},
    {"driver_number": 44, "position": 3, "date": iso(NOW - timedelta(seconds=90))},
    {"driver_number": 44, "position": 2, "date": iso(NOW - timedelta(seconds=25))},
    {"driver_number": 1, "position": 3, "date": iso(NOW - timedelta(seconds=20))},
    {"driver_number": None, "position": 9, "date": iso(NOW)},
]

INTERVALS = [
    {"driver_number": 12, "gap_to_leader": 0, "interval": 0, "date": iso(NOW - timedelta(seconds=30))},
    {"driver_number": 44, "gap_to_leader": 1.234, "interval": 1.234, "date": iso(NOW - timedelta(seconds=28))},
    {"driver_number": 1, "gap_to_leader": 95.0, "interval": 4.8, "date": iso(NOW - timedelta(seconds=26))},
]

STINTS = [
    {"driver_number": 12, "stint_number": 1, "compound": "MEDIUM", "tyre_age_at_start": 0},
    {"driver_number": 12, "stint_number": 2, "compound": "HARD", "tyre_age_at_start": 2},
    {"driver_number": 44, "stint_number": 1, "compound": "SOFT", "tyre_age_at_start": 0},
]

LAPS = [{"driver_number": 12, "lap_number": n} for n in range(1, 35)]

RACE_CONTROL_TRACK = [
    {"flag": "GREEN", "scope": "Track", "date": iso(NOW - timedelta(minutes=40)), "message": "GREEN LIGHT"},
    {"flag": "YELLOW", "scope": "Track", "date": iso(NOW - timedelta(minutes=2)), "message": "YELLOW"},
]

SESSION_RESULT = [
    {"position": 1, "driver_number": 12, "gap_to_leader": 0, "dnf": False, "dns": False, "dsq": False},
    {"position": 2, "driver_number": 44, "gap_to_leader": 15.08, "dnf": False, "dns": False, "dsq": False},
    {"position": 3, "driver_number": 1, "gap_to_leader": None, "dnf": True, "dns": False, "dsq": False},
]

DRIVER_STANDINGS = {
    "MRData": {
        "StandingsTable": {
            "season": "2026",
            "StandingsLists": [
                {
                    "season": "2026",
                    "round": "11",
                    "DriverStandings": [
                        {
                            "position": "1",
                            "points": "219",
                            "wins": "6",
                            "Driver": {"code": "ANT", "familyName": "Antonelli", "givenName": "Andrea Kimi"},
                            "Constructors": [{"constructorId": "mercedes", "name": "Mercedes"}],
                        },
                        {
                            "position": "2",
                            "points": "169",
                            "wins": "1",
                            "Driver": {"code": "HAM", "familyName": "Hamilton", "givenName": "Lewis"},
                            "Constructors": [{"constructorId": "ferrari", "name": "Ferrari"}],
                        },
                        {
                            "position": "3",
                            "points": "160.5",
                            "wins": "2",
                            "Driver": {"code": "RUS", "familyName": "Russell", "givenName": "George"},
                            "Constructors": [],
                        },
                    ],
                }
            ],
        }
    }
}

CONSTRUCTOR_STANDINGS = {
    "MRData": {
        "StandingsTable": {
            "season": "2026",
            "StandingsLists": [
                {
                    "season": "2026",
                    "round": "11",
                    "ConstructorStandings": [
                        {"position": "1", "points": "379", "wins": "8", "Constructor": {"constructorId": "mercedes", "name": "Mercedes"}},
                        {"position": "2", "points": "307", "wins": "3", "Constructor": {"constructorId": "ferrari", "name": "Ferrari"}},
                        {"position": "3", "points": "252", "wins": "0", "Constructor": {"constructorId": "red_bull", "name": "Red Bull"}},
                    ],
                }
            ],
        }
    }
}

EMPTY_STANDINGS = {"MRData": {"StandingsTable": {"StandingsLists": []}}}


def make_router(race_offset_minutes=-60, *, empty_positions=False, standings=True, fail=None):
    """Return a requests.get replacement that serves canned API payloads."""
    sessions = build_sessions(race_offset_minutes)

    def router(url, params=None, timeout=None):
        params = params or {}
        if fail and fail in url:
            raise requests.RequestException("boom")

        # Standings are matched first: "/driverstandings" contains "/drivers".
        if "driverstandings" in url:
            return create_mock_response(DRIVER_STANDINGS if standings else EMPTY_STANDINGS)
        if "constructorstandings" in url:
            return create_mock_response(CONSTRUCTOR_STANDINGS if standings else EMPTY_STANDINGS)
        if "/sessions" in url:
            return create_mock_response(sessions)
        if "/drivers" in url:
            return create_mock_response(DRIVERS)
        if "/position" in url:
            return create_mock_response([] if empty_positions else POSITIONS)
        if "/session_result" in url:
            return create_mock_response(SESSION_RESULT)
        if "/intervals" in url:
            return create_mock_response(INTERVALS)
        if "/stints" in url:
            return create_mock_response(STINTS)
        if "/laps" in url:
            return create_mock_response(LAPS)
        if "/race_control" in url:
            if params.get("category") == "SafetyCar":
                return create_mock_response([])
            return create_mock_response(RACE_CONTROL_TRACK)
        return create_mock_response([])

    return router


def make_plugin(config=None):
    with open(PLUGIN_DIR / "manifest.json") as handle:
        manifest = json.load(handle)
    plugin = F1Plugin(manifest)
    base = {
        "board": "note",
        "display_mode": "auto",
        "fallback_mode": "countdown",
        "timezone": "Australia/Sydney",
    }
    base.update(config or {})
    plugin.config = base
    return plugin


# ----------------------------------------------------------------------
# Core contract
# ----------------------------------------------------------------------


class TestF1PluginCore(PluginTestCase):
    def test_plugin_id_matches_manifest(self):
        with open(PLUGIN_DIR / "manifest.json") as handle:
            manifest = json.load(handle)
        assert make_plugin().plugin_id == "f1" == manifest["id"]

    def test_manifest_is_well_formed(self):
        with open(PLUGIN_DIR / "manifest.json") as handle:
            manifest = json.load(handle)
        assert manifest["version"].count(".") == 2
        assert manifest["category"] in {"art", "data", "transit", "weather", "entertainment", "utility", "home"}
        assert isinstance(manifest["variables"]["simple"], dict)
        assert isinstance(manifest["variables"]["groups"], dict)
        assert "arrays" in manifest["variables"]

    def test_live_session_returns_timing(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            result = plugin.fetch_data()

        assert isinstance(result, PluginResult)
        assert result.available is True
        assert result.error is None
        data = result.data
        assert data["status"] == "LIVE"
        assert data["mode"] == "LIVE"
        assert data["leader"] == "ANT"
        assert (data["p1"], data["p2"], data["p3"]) == ("ANT", "HAM", "NOR")
        assert data["circuit"] == "ZANDVOORT"
        assert data["lap"] == "L34/72"
        assert data["flag"] == "YELLOW"

    def test_live_entries_are_ordered_and_deduplicated(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            data = plugin.fetch_data().data

        positions = [e["position"] for e in data["live"]]
        assert positions == ["1", "2", "3"]
        assert data["live"][0]["gap"] == "LEADER"
        assert data["live"][1]["gap"] == "+1.2"
        assert data["live"][1]["tyre"] == "S"
        # Gaps of a minute or more collapse to minutes so they fit the board.
        assert data["live"][2]["gap"] == "+2M"
        # Latest stint wins.
        assert data["live"][0]["tyre"] == "H"

    def test_countdown_when_no_session_live(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            data = plugin.fetch_data().data

        assert data["status"] == "OFF"
        assert data["mode"] == "COUNTDOWN"
        assert data["next_circuit"] == "ZANDVOORT"
        assert data["next_session"] == "RACE"
        assert data["countdown_days"] == "1"
        assert data["countdown"].endswith("H")

    def test_standings_are_parsed(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            data = plugin.fetch_data().data

        assert data["wdc_leader"] == "ANT"
        assert data["wdc_points"] == "219"
        assert data["wdc_gap"] == "50"
        assert data["wcc_leader"] == "MERCEDES"
        assert data["wcc_points"] == "379"
        assert data["drivers"][2]["points"] == "160.5"
        assert data["constructors"][2]["short"] == "RED BULL"

    def test_finished_session_falls_back_to_results(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60, empty_positions=True)):
            data = plugin.fetch_data().data

        assert data["status"] == "SOON"
        assert data["live"][0]["code"] == "ANT"
        assert data["live"][2]["gap"] == "DNF"

    def test_preseason_falls_back_to_previous_year(self):
        plugin = make_plugin()
        calls = {"n": 0}

        def router(url, params=None, timeout=None):
            if "standings" in url:
                calls["n"] += 1
                # First two calls (current year) are empty, next two succeed.
                if calls["n"] <= 2:
                    return create_mock_response(EMPTY_STANDINGS)
                if "driverstandings" in url:
                    return create_mock_response(DRIVER_STANDINGS)
                return create_mock_response(CONSTRUCTOR_STANDINGS)
            return make_router(60 * 30)(url, params, timeout)

        with patch.object(f1.requests, "get", side_effect=router):
            data = plugin.fetch_data().data

        assert data["wdc_leader"] == "ANT"
        assert data["season"] == str(NOW.year - 1)


# ----------------------------------------------------------------------
# Board formatting
# ----------------------------------------------------------------------


class TestBoardFormatting:
    def test_note_lines_fit_fifteen_columns(self):
        plugin = make_plugin({"board": "note"})
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            result = plugin.fetch_data()

        assert len(result.formatted_lines) == 3
        for line in result.formatted_lines:
            assert len(line) <= 15, line
        assert result.formatted_lines[0].startswith("NED RACE")
        assert "1ANT" in result.formatted_lines[1]

    def test_flagship_lines_fit_twentytwo_columns(self):
        plugin = make_plugin({"board": "flagship"})
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            result = plugin.fetch_data()

        assert len(result.formatted_lines) == 6
        for line in result.formatted_lines:
            assert len(line) <= 22, line

    @pytest.mark.parametrize("mode", ["live", "countdown", "drivers", "constructors"])
    @pytest.mark.parametrize("board,width,rows", [("note", 15, 3), ("flagship", 22, 6)])
    def test_every_mode_fits_every_board(self, mode, board, width, rows):
        plugin = make_plugin({"board": board, "display_mode": mode})
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            result = plugin.fetch_data()

        assert result.available is True
        assert len(result.formatted_lines) == rows
        for line in result.formatted_lines:
            assert len(line) <= width, (mode, board, line)

    def test_all_output_uses_board_safe_characters(self):
        plugin = make_plugin({"board": "flagship", "display_mode": "drivers"})
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            data = plugin.fetch_data().data

        def check(value):
            for char in value:
                assert char in f1.ALLOWED_CHARS, f"unsupported character {char!r} in {value!r}"

        for key, value in data.items():
            if isinstance(value, str):
                check(value)
            elif isinstance(value, list):
                for item in value:
                    for inner in item.values():
                        if isinstance(inner, str):
                            check(inner)

    def test_declared_simple_variables_are_all_present(self):
        with open(PLUGIN_DIR / "manifest.json") as handle:
            manifest = json.load(handle)
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            data = plugin.fetch_data().data

        for name in manifest["variables"]["simple"]:
            assert name in data, f"variable '{name}' declared in manifest but missing from data"
        for name in manifest["variables"]["arrays"]:
            assert name in data, f"array '{name}' declared in manifest but missing from data"

        declared = set(manifest["variables"]["simple"]) | set(manifest["variables"]["arrays"])
        extra = set(data) - declared
        assert not extra, f"data returns undeclared keys: {sorted(extra)}"

    def test_array_items_expose_declared_fields(self):
        with open(PLUGIN_DIR / "manifest.json") as handle:
            manifest = json.load(handle)
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            data = plugin.fetch_data().data

        for array_name, spec in manifest["variables"]["arrays"].items():
            for item in data[array_name]:
                for field in spec["item_fields"]:
                    assert field in item, f"{array_name}.{field} missing"

    def test_simple_variables_respect_declared_max_length(self):
        with open(PLUGIN_DIR / "manifest.json") as handle:
            manifest = json.load(handle)
        plugin = make_plugin({"board": "flagship"})
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            data = plugin.fetch_data().data

        for name, spec in manifest["variables"]["simple"].items():
            limit = spec["max_length"]
            value = data.get(name)
            if isinstance(value, str):
                assert len(value) <= limit, f"{name} = {value!r} exceeds {limit}"

    def test_array_items_respect_max_lengths(self):
        with open(PLUGIN_DIR / "manifest.json") as handle:
            manifest = json.load(handle)
        plugin = make_plugin({"board": "flagship"})
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            data = plugin.fetch_data().data

        for name, limit in manifest["max_lengths"].items():
            array, _, field = name.split(".")
            for item in data.get(array, []):
                value = item.get(field, "")
                assert len(value) <= limit, f"{name} = {value!r} exceeds {limit}"

    def test_every_simple_variable_is_fully_described(self):
        with open(PLUGIN_DIR / "manifest.json") as handle:
            manifest = json.load(handle)
        groups = set(manifest["variables"]["groups"])

        for name, spec in manifest["variables"]["simple"].items():
            for key in ("description", "type", "max_length", "group", "example"):
                assert key in spec, f"{name} is missing '{key}'"
            assert spec["group"] in groups, f"{name} references unknown group {spec['group']}"
            assert spec["type"] in {"string", "number", "boolean"}, name
            assert len(spec["example"]) <= spec["max_length"], f"{name} example exceeds its own max_length"


# ----------------------------------------------------------------------
# Plugin directory presentation (previews, teaser, demo, screenshots)
# ----------------------------------------------------------------------


class TestPresentation:
    @staticmethod
    def _manifest():
        with open(PLUGIN_DIR / "manifest.json") as handle:
            return json.load(handle)

    def test_previews_fit_their_board(self):
        for preview in self._manifest()["previews"]:
            device = preview["device_type"]
            width, rows = BOARD_WIDTHS[device], BOARD_ROWS[device]
            assert len(preview["rows"]) == rows, f"{device} preview needs exactly {rows} rows"
            for row in preview["rows"]:
                assert tile_count(row) <= width, f"{device} preview row {row!r} is {tile_count(row)} tiles (max {width})"

    def test_previews_cover_both_boards(self):
        devices = {p["device_type"] for p in self._manifest()["previews"]}
        assert devices == {"note", "flagship"}

    def test_teaser_fits_a_note(self):
        teaser = self._manifest()["teaser"]
        assert tile_count(teaser) <= BOARD_WIDTHS["note"]

    def test_demo_templates_reference_real_variables(self):
        manifest = self._manifest()
        known = set(manifest["variables"]["simple"]) | set(manifest["variables"]["arrays"])

        for device, demo in manifest["demo"].items():
            assert len(demo["template"]) == BOARD_ROWS[device], f"{device} demo needs {BOARD_ROWS[device]} rows"
            assert len(demo["line_metadata"]) == len(demo["template"])
            for reference in re.findall(r"\{\{f1\.([a-z0-9_.]+)\}\}", " ".join(demo["template"])):
                root = reference.split(".")[0]
                assert root in known, f"{device} demo references unknown variable f1.{reference}"

    def test_screenshot_files_exist(self):
        for shot in self._manifest().get("screenshots", []):
            assert (PLUGIN_DIR / shot["src"]).exists(), f"missing screenshot {shot['src']}"
            assert shot.get("alt"), "screenshots need alt text"

    def test_preview_characters_are_board_safe(self):
        for preview in self._manifest()["previews"]:
            for row in preview["rows"]:
                stripped = re.sub(r"\{/?[a-z_0-9]+\}", "", row)
                for char in stripped:
                    assert char in f1.ALLOWED_CHARS, f"unsupported character {char!r} in preview {row!r}"


# ----------------------------------------------------------------------
# Configuration and failure handling
# ----------------------------------------------------------------------


class TestConfiguration:
    def test_valid_config_passes(self):
        errors = make_plugin().validate_config(
            {
                "board": "note",
                "display_mode": "auto",
                "fallback_mode": "drivers",
                "timezone": "Europe/London",
                "live_refresh_seconds": 30,
            }
        )
        assert errors == []

    def test_invalid_values_are_caught(self):
        errors = make_plugin().validate_config(
            {
                "board": "billboard",
                "display_mode": "telemetry",
                "fallback_mode": "nope",
                "timezone": "Mars/Olympus_Mons",
                "live_refresh_seconds": 2,
                "refresh_seconds": "soon",
            }
        )
        joined = " ".join(errors).lower()
        assert len(errors) == 6
        assert "board" in joined and "timezone" in joined

    def test_empty_config_is_valid(self):
        assert make_plugin().validate_config({}) == []

    def test_config_change_clears_cache(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            plugin.fetch_data()
        assert plugin._cache
        plugin.on_config_change({}, {"board": "flagship"})
        assert plugin._cache == {}

    def test_cleanup_clears_cache(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            plugin.fetch_data()
        plugin.cleanup()
        assert plugin._cache == {}

    def test_practice_sessions_can_be_excluded(self):
        plugin = make_plugin({"include_practice": False, "display_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            data = plugin.fetch_data().data
        # Practice 1 at Monza is skipped, so the next session is still the race.
        assert data["next_session"] == "RACE"

    def test_unknown_timezone_falls_back_to_utc(self):
        plugin = make_plugin({"timezone": "Nowhere/Nothing"})
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            result = plugin.fetch_data()
        assert result.available is True

    def test_env_var_timezone_is_used(self, monkeypatch):
        plugin = make_plugin()
        plugin.config = {"board": "note"}
        monkeypatch.setenv("F1_TIMEZONE", "Europe/Rome")
        assert plugin._tz() is not None


class TestFailureHandling:
    def test_calendar_failure_reports_unavailable(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=requests.RequestException("down")):
            result = plugin.fetch_data()
        assert result.available is False
        assert "unavailable" in result.error

    def test_standings_failure_still_returns_live_data(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60, fail="standings")):
            result = plugin.fetch_data()
        assert result.available is True
        assert result.data["leader"] == "ANT"
        assert result.data["wdc_leader"] == ""

    def test_live_failure_still_returns_standings(self):
        plugin = make_plugin({"display_mode": "drivers"})
        with patch.object(f1.requests, "get", side_effect=make_router(-60, fail="/intervals")):
            result = plugin.fetch_data()
        assert result.available is True
        assert result.data["wdc_leader"] == "ANT"

    def test_race_control_failure_is_swallowed(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60, fail="/race_control")):
            result = plugin.fetch_data()
        assert result.available is True
        assert result.data["flag"] == ""

    def test_unexpected_error_is_captured(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=ValueError("kaboom")):
            result = plugin.fetch_data()
        assert result.available is False
        assert "kaboom" in result.error

    def test_empty_api_responses_do_not_crash(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=lambda *a, **k: create_mock_response([])):
            result = plugin.fetch_data()
        assert result.available is True
        assert result.data["status"] == "OFF"
        assert result.data["live"] == []

    def test_get_formatted_display_returns_lines(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            lines = plugin.get_formatted_display()
        assert isinstance(lines, list) and len(lines) == 3


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class TestHelpers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Montréal", "MONTREAL"), ("São Paulo", "SAO PAULO"),
            ("Spa-Francorchamps", "SPA-FRANCORCHAMPS"), (None, ""),
            ("  double   spaces ", "DOUBLE SPACES"), ("emoji ✅ here", "EMOJI HERE"),
        ],
    )
    def test_sanitize(self, raw, expected):
        assert f1.sanitize(raw) == expected

    def test_sanitize_truncates(self):
        assert f1.sanitize("ZANDVOORT", 4) == "ZAND"

    def test_pad_row_right_justifies(self):
        assert f1.pad_row("1 ANT", "219", 15) == "1 ANT       219"
        assert len(f1.pad_row("A VERY LONG LEFT SIDE", "999", 15)) == 15

    @pytest.mark.parametrize(
        "value,leader,expected",
        [(None, True, "LEADER"), (None, False, ""), (0, True, "LEADER"),
         (1.234, False, "+1.2"), (120.0, False, "+2M"), ("DNF", False, "DNF")],
    )
    def test_format_gap(self, value, leader, expected):
        assert f1._format_gap(value, leader=leader) == expected

    def test_format_points(self):
        assert f1._format_points("219") == "219"
        assert f1._format_points("160.5") == "160.5"
        assert f1._format_points("bad") == "0"

    def test_parse_iso(self):
        assert f1._parse_iso("2026-08-23T13:00:00Z").hour == 13
        assert f1._parse_iso("not a date") is None
        assert f1._parse_iso(None) is None

    @pytest.mark.parametrize(
        "delta,expected",
        [
            (timedelta(days=2, hours=3), "2D 3H"),
            (timedelta(hours=5, minutes=20), "5H 20M"),
            (timedelta(minutes=42), "42M"),
            (timedelta(seconds=-5), ""),
            (None, ""),
        ],
    )
    def test_countdown(self, delta, expected):
        assert f1._format_countdown(*f1._split_delta(delta)) == expected
