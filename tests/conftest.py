"""Test fixtures for the F1 plugin.

When these tests run inside the FiestaBoard repo, ``src.plugins.base`` is
imported for real. When the plugin repository is checked out standalone that
module does not exist, so a minimal stand-in is registered first. The stub
mirrors the documented PluginBase contract exactly.
"""

import importlib.util
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _install_stub_base() -> None:
    if importlib.util.find_spec("src.plugins.base") is not None:
        return

    src = types.ModuleType("src")
    plugins = types.ModuleType("src.plugins")
    base = types.ModuleType("src.plugins.base")
    testing = types.ModuleType("src.plugins.testing")

    @dataclass
    class PluginResult:
        available: bool
        data: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        formatted_lines: Optional[List[str]] = None

    class PluginBase:
        def __init__(self, manifest: Dict[str, Any]):
            self._manifest = manifest or {}
            self._config: Dict[str, Any] = {}
            self._enabled = False
            self._data_cache = None
            self._data_cache_time = None

        @property
        def manifest(self) -> Dict[str, Any]:
            return self._manifest

        @property
        def config(self) -> Dict[str, Any]:
            return self._config

        @config.setter
        def config(self, value: Dict[str, Any]) -> None:
            self._config = value or {}

        @property
        def enabled(self) -> bool:
            return self._enabled

        @property
        def refresh_seconds(self):
            return self._config.get("refresh_seconds")

        def validate_config(self, config):
            return []

        def cleanup(self):
            return None

        def on_config_change(self, old_config, new_config):
            return None

        def get_formatted_display(self):
            return None

        def clear_cache(self):
            self._data_cache = None
            self._data_cache_time = None

        def get_data(self):
            ttl = self.refresh_seconds
            now = datetime.now()
            if self._data_cache is not None and ttl and self._data_cache_time:
                if (now - self._data_cache_time).total_seconds() < ttl:
                    return self._data_cache
            result = self.fetch_data()
            self._data_cache = result
            self._data_cache_time = now
            return result

        def get_variables_schema(self):
            return self._manifest.get("variables", {})

        def get_max_lengths(self):
            return self._manifest.get("max_lengths", {})

        def get_settings_schema(self):
            return self._manifest.get("settings_schema", {})

        def get_env_vars(self):
            return self._manifest.get("env_vars", [])

    class _MockResponse:
        def __init__(self, data=None, status_code=200):
            self._data = data if data is not None else {}
            self.status_code = status_code

        def json(self):
            return self._data

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"HTTP {self.status_code}")

    def create_mock_response(data=None, status_code=200):
        return _MockResponse(data=data, status_code=status_code)

    class PluginTestCase:
        """Marker base class mirroring the FiestaBoard test helper."""

    base.PluginBase = PluginBase
    base.PluginResult = PluginResult
    testing.create_mock_response = create_mock_response
    testing.PluginTestCase = PluginTestCase
    plugins.base = base
    plugins.testing = testing
    src.plugins = plugins

    sys.modules.setdefault("src", src)
    sys.modules.setdefault("src.plugins", plugins)
    sys.modules.setdefault("src.plugins.base", base)
    sys.modules.setdefault("src.plugins.testing", testing)


_install_stub_base()


def load_plugin_module():
    """Import the plugin package by path so tests work in either layout."""
    if "f1_plugin_under_test" in sys.modules:
        return sys.modules["f1_plugin_under_test"]
    spec = importlib.util.spec_from_file_location(
        "f1_plugin_under_test", PLUGIN_DIR / "__init__.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["f1_plugin_under_test"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def reset_plugin_singletons():
    yield


@pytest.fixture
def plugin_module():
    return load_plugin_module()


@pytest.fixture
def manifest():
    import json

    with open(PLUGIN_DIR / "manifest.json") as handle:
        return json.load(handle)


@pytest.fixture
def sample_config():
    return {
        "board": "note",
        "display_mode": "auto",
        "fallback_mode": "countdown",
        "timezone": "Australia/Sydney",
        "live_window_minutes": 15,
        "live_refresh_seconds": 20,
        "standings_refresh_seconds": 1800,
        "refresh_seconds": 30,
    }
