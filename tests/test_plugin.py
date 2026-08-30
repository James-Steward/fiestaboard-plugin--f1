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

    def router(url, params=None, timeout=None, **kwargs):
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

        def router(url, params=None, timeout=None, **kwargs):
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
            assert tile_count(line) <= width, (mode, board, line, tile_count(line))

    def test_all_output_uses_board_safe_characters(self):
        plugin = make_plugin({"board": "flagship", "display_mode": "drivers"})
        with patch.object(f1.requests, "get", side_effect=make_router(-60)):
            data = plugin.fetch_data().data

        def check(value):
            # Colour codes are template markup, not board characters.
            for char in f1.COLOR_TOKEN.sub("", value):
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
# Hostile / malformed upstream data
#
# Both APIs are unauthenticated and community-run, so every response is
# untrusted input. None of it should be able to crash the plugin, blank the
# board, inject board control codes, or exhaust memory.
# ----------------------------------------------------------------------


class TestUntrustedUpstream:
    @pytest.mark.parametrize(
        "hostile",
        ["{66}{66}{66}", "NORRIS{63}", "{{f1.line1}}", "{/green}", "\x1b[31mRED", "{71}" * 50],
    )
    def test_upstream_cannot_inject_colour_tiles(self, hostile):
        """Braces are not board characters, so sanitize() must strip them."""
        cleaned = f1.sanitize(hostile, 12)
        assert "{" not in cleaned and "}" not in cleaned, cleaned

        row = f1.pad_row(cleaned, "999", 15)
        assert not [t for t in f1.tiles(row) if f1.COLOR_TOKEN.fullmatch(t)]

    def test_driver_named_with_a_colour_code_renders_as_text(self):
        drivers = [{"driver_number": 12, "name_acronym": "{66}", "last_name": "{63}Evil", "team_name": "Mercedes"}]

        def router(url, params=None, timeout=None, **kwargs):
            if "/drivers" in url:
                return create_mock_response(drivers)
            return make_router(-60)(url, params, timeout)

        plugin = make_plugin({"board": "note", "display_mode": "live"})
        with patch.object(f1.requests, "get", side_effect=router):
            data = plugin.fetch_data().data

        for line in (data["line1"], data["line2"], data["line3"]):
            assert "{" not in line, line

    @pytest.mark.parametrize(
        "position", ["P1", None, {"a": 1}, [], float("inf"), float("nan"), "1e400", 10**9, -5]
    )
    def test_unusable_position_drops_one_row_not_the_board(self, position):
        positions = [
            {"driver_number": 12, "position": 1, "date": iso(NOW)},
            {"driver_number": 44, "position": position, "date": iso(NOW)},
        ]

        def router(url, params=None, timeout=None, **kwargs):
            if "/position" in url:
                return create_mock_response(positions)
            return make_router(-60)(url, params, timeout)

        plugin = make_plugin({"board": "note", "display_mode": "live"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.available is True, result.error
        assert any(e["code"] == "ANT" for e in result.data["live"])

    @pytest.mark.parametrize(
        "payload",
        [
            {"MRData": "not a dict"},
            {"MRData": {"StandingsTable": []}},
            {"MRData": {"StandingsTable": {"StandingsLists": "nope"}}},
            {"MRData": {"StandingsTable": {"StandingsLists": [[]]}}},
            {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": "nope"}]}}},
            {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": ["a string"]}]}}},
            {"MRData": {"StandingsTable": {"StandingsLists": [{"DriverStandings": [{"Driver": "x", "Constructors": "y"}]}]}}},
            [],
            None,
        ],
    )
    def test_malformed_standings_never_crash(self, payload):
        def router(url, params=None, timeout=None, **kwargs):
            if "standings" in url:
                return create_mock_response(payload)
            return make_router(60 * 30)(url, params, timeout)

        plugin = make_plugin({"display_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.available is True, result.error
        assert isinstance(result.data["drivers"], list)

    @pytest.mark.parametrize("garbage", ["not json", "", "[1,2,3]", '{"MRData": 1}'])
    def test_non_json_and_scalar_bodies_are_survivable(self, garbage):
        class Body:
            headers = {}
            status_code = 200

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                yield garbage.encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        plugin = make_plugin()
        with patch.object(f1.requests, "get", return_value=Body()):
            result = plugin.fetch_data()
        assert result.available in (True, False)  # never raises

    def test_oversized_response_is_refused(self):
        """A hostile upstream must not be able to buffer unbounded bytes."""
        chunk = b"x" * (1024 * 1024)

        class Huge:
            headers = {}
            status_code = 200
            served = 0

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                while True:
                    Huge.served += len(chunk)
                    yield chunk

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(f1.requests, "get", return_value=Huge()):
            with pytest.raises(ValueError):
                f1.F1Plugin._get_json("https://example.test/x")

        assert Huge.served <= f1.MAX_RESPONSE_BYTES + len(chunk)

    def test_declared_oversize_is_refused_before_reading(self):
        class Declared:
            headers = {"Content-Length": str(f1.MAX_RESPONSE_BYTES + 1)}
            status_code = 200
            read = False

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                Declared.read = True
                yield b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch.object(f1.requests, "get", return_value=Declared()):
            with pytest.raises(ValueError):
                f1.F1Plugin._get_json("https://example.test/x")
        assert Declared.read is False

    def test_row_count_is_capped(self):
        flood = [{"driver_number": n, "position": 1, "date": iso(NOW)} for n in range(f1.MAX_ROWS * 2)]

        def router(url, params=None, timeout=None, **kwargs):
            if "/position" in url:
                return create_mock_response(flood)
            return make_router(-60)(url, params, timeout)

        plugin = make_plugin({"display_mode": "live"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()
        assert result.available is True

    def test_cache_stays_bounded(self):
        """Cache keys include the session id, so a season must not accumulate."""
        plugin = make_plugin()
        for index in range(f1.MAX_CACHE_ENTRIES * 3):
            plugin._cached(f"session:{index}", 3600, lambda: index)
        assert len(plugin._cache) <= f1.MAX_CACHE_ENTRIES

    @pytest.mark.parametrize("tz", ["../../../../etc/passwd", "/etc/passwd", "Nowhere/Nothing", ""])
    def test_timezone_config_cannot_escape(self, tz):
        plugin = make_plugin({"timezone": tz})
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            result = plugin.fetch_data()
        assert result.available is True

    @pytest.mark.parametrize("container", [{"evil": 1}, ["NED"], {"a": {"b": 1}}])
    def test_container_valued_fields_never_reach_the_board(self, container):
        """A dict where a name was expected must render as nothing, not its repr.

        These are also unhashable, and several upstream fields are used as
        dict lookup keys - which raised TypeError and blanked the board.
        """
        sessions = build_sessions(60 * 30)
        for row in sessions:
            row["session_name"] = container
            row["circuit_short_name"] = container
            row["country_code"] = container

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(sessions)
            return create_mock_response([])

        plugin = make_plugin({"display_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.available is True, result.error
        for key, value in result.data.items():
            if isinstance(value, str):
                # A leaked repr shows up as quotes; times legitimately have colons.
                assert "'" not in value and '"' not in value, (key, value)
                assert "EVIL" not in value.upper(), (key, value)

    def test_sanitize_refuses_containers(self):
        for container in ({"evil": 1}, ["x"], ("y",), set()):
            assert f1.sanitize(container) == ""
        assert f1.sanitize(42) == "42"
        assert f1.sanitize("ok") == "OK"

    def test_unhashable_team_identifiers_do_not_crash(self):
        standings = {
            "MRData": {"StandingsTable": {"StandingsLists": [{
                "round": {"x": 1},
                "ConstructorStandings": [
                    {"position": "1", "points": "10",
                     "Constructor": {"constructorId": {"a": 1}, "name": ["L"]}}
                ],
            }]}}
        }

        def router(url, params=None, timeout=None, **kwargs):
            if "standings" in url:
                return create_mock_response(standings)
            return make_router(60 * 30)(url, params, timeout)

        plugin = make_plugin({"display_mode": "constructors"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()
        assert result.available is True, result.error

    def test_output_stays_board_safe_under_hostile_input(self):
        drivers = [
            {"driver_number": 12, "name_acronym": "Ünïcødé", "last_name": "A" * 400, "team_name": "{66}" * 20}
        ]

        def router(url, params=None, timeout=None, **kwargs):
            if "/drivers" in url:
                return create_mock_response(drivers)
            return make_router(-60)(url, params, timeout)

        plugin = make_plugin({"board": "flagship", "display_mode": "live"})
        with patch.object(f1.requests, "get", side_effect=router):
            data = plugin.fetch_data().data

        for entry in data["live"]:
            for key, value in entry.items():
                if isinstance(value, str):
                    assert all(c in f1.ALLOWED_CHARS for c in f1.COLOR_TOKEN.sub("", value)), (key, value)
        for index in range(1, 7):
            assert tile_count(data[f"line{index}"]) <= 22


# ----------------------------------------------------------------------
# Graceful degradation
#
# Regression cover for the race-weekend outage: the board showed "???" on
# every row and froze, because a non-network error during live timing
# escaped and made the whole plugin unavailable — wiping even the countdown,
# which needs no live data at all.
# ----------------------------------------------------------------------


def broken_body(payload):
    """A response that returns HTTP 200 but a body json can't parse."""

    class Body:
        headers = {}
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield payload

        def json(self):
            return json.loads(payload)

        def close(self):
            pass

    return Body()


class TestGracefulDegradation:
    @pytest.mark.parametrize(
        "payload",
        [
            b"<html><body>502 Bad Gateway</body></html>",  # CDN error page with a 200
            b'[{"driver_number": 12, "positi',                # truncated mid-object
            b"",                                              # empty body
            b"\x00\x01\x02",                                  # binary garbage
        ],
    )
    def test_malformed_live_response_falls_back_instead_of_blanking(self, payload):
        """This is the exact failure: it must degrade, never go unavailable."""

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(build_sessions(-60))
            if "driverstandings" in url:
                return create_mock_response(DRIVER_STANDINGS)
            if "constructorstandings" in url:
                return create_mock_response(CONSTRUCTOR_STANDINGS)
            if any(part in url for part in ("/position", "/intervals", "/drivers", "/stints", "/laps")):
                return broken_body(payload)
            return create_mock_response([])

        plugin = make_plugin({"board": "note", "display_mode": "auto"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.available is True, f"went unavailable on {payload[:20]!r}"
        assert result.formatted_lines, "no lines rendered"
        # The countdown needs no live data, so it must still be there.
        assert any(line.strip() for line in result.formatted_lines)
        assert result.data["wdc_leader"] == "ANT", "standings should survive too"

    def test_oversized_live_response_falls_back(self):
        chunk = b"x" * (1024 * 1024)

        class Huge:
            headers = {}
            status_code = 200

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                while True:
                    yield chunk

            def close(self):
                pass

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(build_sessions(-60))
            if "standings" in url:
                return create_mock_response(DRIVER_STANDINGS)
            if "/position" in url:
                return Huge()
            return create_mock_response([])

        plugin = make_plugin({"display_mode": "auto"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()
        assert result.available is True

    def test_standings_survive_when_calendar_dies(self):
        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return broken_body(b"not json")
            if "driverstandings" in url:
                return create_mock_response(DRIVER_STANDINGS)
            if "constructorstandings" in url:
                return create_mock_response(CONSTRUCTOR_STANDINGS)
            return create_mock_response([])

        plugin = make_plugin({"display_mode": "drivers"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.available is True, result.error
        assert result.data["wdc_leader"] == "ANT"
        assert any("ANT" in line for line in result.formatted_lines)

    def test_unavailable_only_when_everything_is_gone(self):
        def router(url, params=None, timeout=None, **kwargs):
            return broken_body(b"not json")

        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.available is False
        assert "unavailable" in result.error

    def test_mid_session_failure_shows_standings_not_a_distant_countdown(self):
        """Counting down to a session a fortnight away mid-race is misleading."""

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(build_sessions(-60))
            if "driverstandings" in url:
                return create_mock_response(DRIVER_STANDINGS)
            if "constructorstandings" in url:
                return create_mock_response(CONSTRUCTOR_STANDINGS)
            if "/position" in url:
                return broken_body(b"nope")
            return create_mock_response([])

        plugin = make_plugin({"display_mode": "auto", "fallback_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.data["mode"] == "DRIVERS", result.data["mode"]
        assert any("ANT" in line for line in result.formatted_lines)

    def test_between_sessions_still_uses_the_configured_fallback(self):
        """The override applies only while a session is actually running."""
        plugin = make_plugin({"display_mode": "auto", "fallback_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            result = plugin.fetch_data()
        assert result.data["mode"] == "COUNTDOWN"

    def test_stale_position_rows_still_produce_live_timing(self):
        """/position is a change log, not a feed.

        Real races leave 15-20 minute gaps with no rows at all, and a driver
        who took the lead on the grid may have no row for an hour. A rolling
        window dropped the board out of live mode mid-race, and sometimes
        lost the leader entirely.
        """
        old = iso(NOW - timedelta(minutes=55))
        older = iso(NOW - timedelta(minutes=90))
        positions = [
            {"driver_number": 12, "position": 1, "date": older},   # led from the grid
            {"driver_number": 44, "position": 2, "date": old},
            {"driver_number": 1, "position": 3, "date": old},
        ]

        def router(url, params=None, timeout=None, **kwargs):
            if "/position" in url:
                # No date filter may be sent, or the fixture is pointless.
                assert not any(k.startswith("date") for k in (params or {})), \
                    "position must not be windowed"
                return create_mock_response(positions)
            return make_router(-60)(url, params, timeout)

        plugin = make_plugin({"board": "note", "display_mode": "auto"})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.data["mode"] == "LIVE", result.data["mode"]
        assert result.data["leader"] == "ANT", "leader lost to the window"
        assert [e["code"] for e in result.data["live"]] == ["ANT", "HAM", "NOR"]

    def test_finished_session_prefers_the_classification(self):
        """After the flag, session_result carries final gaps and DNFs."""
        sessions = build_sessions(-180)  # started 3h ago, ended 1h ago

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(sessions)
            return make_router(-180)(url, params, timeout)

        plugin = make_plugin({"display_mode": "live", "live_window_minutes": 120})
        with patch.object(f1.requests, "get", side_effect=router):
            result = plugin.fetch_data()

        assert result.available is True
        assert any(e["gap"] == "DNF" for e in result.data["live"]), result.data["live"]

    def test_live_failure_does_not_poison_the_cache(self):
        """A failed live fetch must not be cached as a good empty result."""
        calls = {"n": 0}

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(build_sessions(-60))
            if "standings" in url:
                return create_mock_response(DRIVER_STANDINGS)
            if "/position" in url:
                calls["n"] += 1
                return broken_body(b"nope")
            return create_mock_response([])

        plugin = make_plugin({"display_mode": "auto"})
        with patch.object(f1.requests, "get", side_effect=router):
            plugin.fetch_data()
            plugin.fetch_data()

        assert calls["n"] >= 2, "a failed fetch was cached and never retried"


# ----------------------------------------------------------------------
# Session time formatting
# ----------------------------------------------------------------------


class TestTimeFormatting:
    def test_times_carry_a_colon(self):
        """Without one, "0030" reads as a year rather than half past midnight."""
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            data = plugin.fetch_data().data

        assert re.fullmatch(r"\d{2}:\d{2}", data["next_local_time"]), data["next_local_time"]
        assert re.fullmatch(r"\d{2}:\d{2}", data["updated"]), data["updated"]

    def test_date_is_weekday_and_numeric(self):
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            data = plugin.fetch_data().data

        # A numeric month keeps the weekday and still fits 15 tiles with the time.
        assert re.fullmatch(r"[A-Z]{3} \d{2}/\d{2}", data["next_local_date"]), data["next_local_date"]
        assert len(f"{data['next_local_date']} {data['next_local_time']}") == 15

    def test_one_date_format_for_both_boards(self):
        """The Note no longer needs its own compact variant."""
        rendered = {}
        for board in ("note", "flagship"):
            plugin = make_plugin({"board": board, "display_mode": "countdown"})
            with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
                result = plugin.fetch_data()
            rendered[board] = result.data["next_local_date"]
        assert rendered["note"] == rendered["flagship"]

    @pytest.mark.parametrize("offset_minutes", [30, 90, 60 * 5, 60 * 13, 60 * 30, 60 * 24 * 6, 60 * 24 * 40])
    def test_countdown_row_fits_a_note_at_any_hour(self, offset_minutes):
        """Every clock time must fit; 23:00 is a tile wider than 0:00 would be."""
        plugin = make_plugin({"board": "note", "display_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=make_router(offset_minutes)):
            lines = plugin.fetch_data().formatted_lines

        for line in lines:
            assert tile_count(line) <= 15, f"{line!r} is {tile_count(line)} tiles"
        assert ":" in lines[2], lines[2]

    @pytest.mark.parametrize("session_name", list(f1.SESSION_SHORT))
    @pytest.mark.parametrize("offset_minutes", [12, 45, 90, 60 * 5, 60 * 23, 60 * 26, 60 * 24 * 13])
    def test_every_session_name_fits_beside_every_countdown(self, session_name, offset_minutes):
        """SQUALI is two characters longer than RACE, which is what broke this."""
        sessions = build_sessions(offset_minutes)
        for row in sessions:
            row["session_name"] = session_name

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(sessions)
            return create_mock_response([])

        plugin = make_plugin({"board": "note", "display_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=router):
            lines = plugin.fetch_data().formatted_lines

        for line in lines:
            assert tile_count(line) <= 15, f"{session_name} @ {offset_minutes}m: {line!r}"
        # the countdown must survive whole, never sliced mid-unit
        assert lines[1].rstrip().endswith(("D", "H", "M", "SOON")), lines[1]

    @pytest.mark.parametrize("days", [1, 9, 12, 99, 120])
    def test_multi_digit_day_counts_fit(self, days):
        """The winter break can be 100+ days; 'SQUALI 100D 23H' is exactly 15."""
        plugin = make_plugin({"board": "note", "display_mode": "countdown"})
        sessions = build_sessions(60 * 24 * days + 60 * 23)
        for row in sessions:
            row["session_name"] = "Sprint Qualifying"

        def router(url, params=None, timeout=None, **kwargs):
            if "/sessions" in url:
                return create_mock_response(sessions)
            return create_mock_response([])

        with patch.object(f1.requests, "get", side_effect=router):
            lines = plugin.fetch_data().formatted_lines

        assert tile_count(lines[1]) <= 15, lines[1]
        assert f"{days}D" in lines[1], lines[1]

    @pytest.mark.parametrize(
        "delta,expected",
        [
            (timedelta(days=11, hours=18), "11D 18H"),
            (timedelta(days=2, hours=3), "2D 3H"),
            (timedelta(hours=23, minutes=59), "23H 45M"),
            (timedelta(hours=14, minutes=30), "14H 30M"),
            (timedelta(hours=13, minutes=9), "13H 00M"),
            (timedelta(hours=2), "2H 00M"),
            (timedelta(minutes=59), "45M"),
            (timedelta(minutes=45), "45M"),
            (timedelta(minutes=44), "30M"),
            (timedelta(minutes=15), "15M"),
            (timedelta(minutes=14), "SOON"),
            (timedelta(minutes=1), "SOON"),
        ],
    )
    def test_countdown_granularity(self, delta, expected):
        """Minutes are bucketed so the board isn't flapping every minute."""
        assert f1._format_countdown(*f1._split_delta(delta)) == expected

    def test_countdown_step_is_configurable(self):
        assert f1._format_countdown("", "", "38", step=1) == "38M"
        assert f1._format_countdown("", "", "38", step=5) == "35M"
        assert f1._format_countdown("", "", "38", step=30) == "30M"
        assert f1._format_countdown("", "", "38", step=60) == "SOON"

    def test_board_changes_four_times_an_hour_inside_a_day(self):
        """The reported problem: 13H 09M ticking every minute for hours."""
        seen = []
        for minutes_left in range(24 * 60 - 1, 0, -1):
            value = f1._format_countdown("0", str(minutes_left // 60), str(minutes_left % 60), step=15)
            if not seen or seen[-1] != value:
                seen.append(value)

        # 23 hours x 4 buckets, then the final hour's 45/30/15/SOON
        assert len(seen) == 24 * 4, len(seen)
        assert seen[0] == "23H 45M"
        assert seen[-4:] == ["45M", "30M", "15M", "SOON"]
        assert all(re.fullmatch(r"(\d+H \d{2}M|\d+M|SOON)", v) for v in seen)

    @pytest.mark.parametrize("hour", range(1, 24))
    def test_only_the_minutes_move_within_an_hour(self, hour):
        """13H 45M -> 13H 30M flips two tiles, not the whole row."""
        rendered = [f1._format_countdown("0", str(hour), str(m), step=15) for m in (0, 15, 30, 45, 59)]

        assert len({len(v) for v in rendered}) == 1, rendered
        prefixes = {v.split()[0] for v in rendered}
        assert prefixes == {f"{hour}H"}, prefixes
        assert [v.split()[1] for v in rendered] == ["00M", "15M", "30M", "45M", "45M"]

    def test_board_changes_at_most_four_times_in_the_final_hour(self):
        """The whole point: a split-flap board is noisy, so count the changes."""
        seen = []
        # 60 minutes out is "1H"; the minute branch only sees 59 and below.
        for minutes_left in range(59, 0, -1):
            value = f1._format_countdown("", "", str(minutes_left), step=15)
            if not seen or seen[-1] != value:
                seen.append(value)
        assert seen == ["45M", "30M", "15M", "SOON"], seen

    def test_component_variables_stay_exact(self):
        """countdown is rounded for the board; the components are not."""
        plugin = make_plugin({"countdown_step_minutes": 15})
        with patch.object(f1.requests, "get", side_effect=make_router(38)):
            data = plugin.fetch_data().data

        assert data["countdown"] == "30M"
        assert data["countdown_minutes"] in {"37", "38"}

    def test_flagship_keeps_the_roomier_date(self):
        plugin = make_plugin({"board": "flagship", "display_mode": "countdown"})
        with patch.object(f1.requests, "get", side_effect=make_router(60 * 30)):
            lines = plugin.fetch_data().formatted_lines

        assert re.search(r"[A-Z]{3} \d{2}/\d{2} \d{2}:\d{2}", lines[3]), lines[3]
        for line in lines:
            assert tile_count(line) <= 22


# ----------------------------------------------------------------------
# Team colour blocks on the standings pages
# ----------------------------------------------------------------------

GRID = [
    ("mercedes", "Mercedes", "MER", "MERCEDES"),
    ("ferrari", "Ferrari", "FER", "FERRARI"),
    ("mclaren", "McLaren", "MCL", "MCLAREN"),
    ("red_bull", "Red Bull", "RBR", "RED BULL"),
    ("aston_martin", "Aston Martin", "AST", "ASTON"),
    ("alpine", "Alpine", "ALP", "ALPINE"),
    ("williams", "Williams", "WIL", "WILLIAMS"),
    ("racing_bulls", "Racing Bulls", "RBT", "VCARB"),
    ("audi", "Audi", "AUD", "AUDI"),
    ("haas", "Haas", "HAA", "HAAS"),
    ("cadillac", "Cadillac", "CAD", "CADILLAC"),
]
DRIVER_CODES = ["ANT", "HAM", "NOR", "VER", "ALO", "GAS", "SAI", "LAW", "HUL", "OCO", "BOR"]


def full_grid_router(points="300"):
    """Standings covering all eleven teams, so every colour cycle is exercised."""
    drivers = {"MRData": {"StandingsTable": {"StandingsLists": [{
        "season": "2026", "round": "11", "DriverStandings": [
            {"position": str(i + 1), "points": points if i == 0 else str(300 - i * 20), "wins": "1",
             "Driver": {"code": DRIVER_CODES[i], "familyName": DRIVER_CODES[i].title()},
             "Constructors": [{"constructorId": t[0], "name": t[1]}]}
            for i, t in enumerate(GRID)]}]}}}
    constructors = {"MRData": {"StandingsTable": {"StandingsLists": [{
        "season": "2026", "round": "11", "ConstructorStandings": [
            {"position": str(i + 1), "points": str(400 - i * 25), "wins": "2",
             "Constructor": {"constructorId": t[0], "name": t[1]}}
            for i, t in enumerate(GRID)]}]}}}

    def router(url, params=None, timeout=None, **kwargs):
        if "driverstandings" in url:
            return create_mock_response(drivers)
        if "constructorstandings" in url:
            return create_mock_response(constructors)
        if "/sessions" in url:
            return create_mock_response(build_sessions(60 * 24))
        return create_mock_response([])

    return router


def colour_tiles(line):
    return [t for t in f1.tiles(line) if f1.COLOR_TOKEN.fullmatch(t)]


class TestColourBlocks:
    def test_three_letter_codes(self):
        plugin = make_plugin({"display_mode": "constructors"})
        with patch.object(f1.requests, "get", side_effect=full_grid_router()):
            data = plugin.fetch_data().data

        actual = {c["short"]: c["code"] for c in data["constructors"]}
        for _, _, code, short in GRID:
            assert actual[short] == code, f"{short} should be {code}"

    def test_every_team_has_colours(self):
        for _, _, _, short in GRID:
            assert f1.swatch(short, 5), f"{short} has no colour block"
            assert len(f1.tiles(f1.swatch(short, 5))) == 5

    @pytest.mark.parametrize("mode", ["drivers", "constructors"])
    def test_note_rows_fill_exactly_fifteen_tiles(self, mode):
        plugin = make_plugin({"board": "note", "display_mode": mode})
        with patch.object(f1.requests, "get", side_effect=full_grid_router()):
            lines = plugin.fetch_data().formatted_lines

        for line in lines:
            assert tile_count(line) == 15, f"{line!r} is {tile_count(line)} tiles"
            assert len(colour_tiles(line)) == 5, f"{line!r} should have a 5-tile block"
            # points sit in the final tiles, with a blank tile either side of the block
            grid = f1.tiles(line)
            indexes = [i for i, t in enumerate(grid) if f1.COLOR_TOKEN.fullmatch(t)]
            assert grid[indexes[0] - 1] == " ", f"no gap before block in {line!r}"
            assert grid[indexes[-1] + 1] == " ", f"no gap after block in {line!r}"
            assert grid[-1] != " ", f"points not flush right in {line!r}"

    @pytest.mark.parametrize("mode", ["drivers", "constructors"])
    def test_flagship_rows_stay_uniform(self, mode):
        plugin = make_plugin({"board": "flagship", "display_mode": mode})
        with patch.object(f1.requests, "get", side_effect=full_grid_router()):
            lines = plugin.fetch_data().formatted_lines

        for line in lines[1:]:
            assert tile_count(line) <= 22, f"{line!r} is {tile_count(line)} tiles"
            assert len(colour_tiles(line)) <= f1.MAX_SWATCH_TILES

    def test_half_points_shrink_the_block(self):
        """A 5-character total steals tiles from the block rather than overflowing."""
        plugin = make_plugin({"board": "note", "display_mode": "drivers"})
        with patch.object(f1.requests, "get", side_effect=full_grid_router(points="300.5")):
            lines = plugin.fetch_data().formatted_lines

        assert tile_count(lines[0]) == 15
        assert len(colour_tiles(lines[0])) == 3
        assert lines[0].endswith("300.5")

    def test_unknown_team_falls_back_to_plain_text(self):
        assert f1.swatch("SOME NEW TEAM", 5) == ""
        assert f1.swatch("MERCEDES", 0) == ""
        assert f1.swatch(None, 5) == ""

    def test_live_and_countdown_stay_colour_free(self):
        for mode in ("live", "countdown"):
            plugin = make_plugin({"board": "note", "display_mode": mode})
            with patch.object(f1.requests, "get", side_effect=make_router(-60)):
                lines = plugin.fetch_data().formatted_lines
            for line in lines:
                assert "{" not in line, f"{mode} should not emit colour codes: {line!r}"


class TestTileHelpers:
    def test_tiles_splits_colour_codes(self):
        assert f1.tiles("AB{66}C") == ["A", "B", "{66}", "C"]
        assert len(f1.tiles("1 ANT {66}{69}{66}{69}{66} 219")) == 15

    def test_fit_truncates_by_tile_never_splitting_a_code(self):
        assert f1.fit("{66}{69}{63}{64}", 2) == "{66}{69}"
        assert f1.fit("ABCDEF", 3) == "ABC"

    def test_fit_preserves_colours_and_alignment(self):
        line = "1 ANT {66}{69}{66}{69}{66} 219"
        assert f1.fit(line, 15) == line

    def test_pad_row_measures_tiles_not_characters(self):
        row = f1.pad_row("1 MER {66}{69}{66}{69}{66}", "379", 15)
        assert len(f1.tiles(row)) == 15
        assert row.endswith("379")


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
        """Non-network errors are contained, and reported without a traceback."""
        plugin = make_plugin()
        with patch.object(f1.requests, "get", side_effect=ValueError("kaboom")):
            result = plugin.fetch_data()
        assert result.available is False
        assert "unavailable" in result.error
        # the raw exception text belongs in the log, not on someone's board
        assert "kaboom" not in result.error

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
            (timedelta(hours=5, minutes=20), "5H 15M"),
            (timedelta(minutes=42), "30M"),
            (timedelta(seconds=-5), ""),
            (None, ""),
        ],
    )
    def test_countdown(self, delta, expected):
        assert f1._format_countdown(*f1._split_delta(delta)) == expected
