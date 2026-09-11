"""Manifest/schema requirements for the HACS default store (spec 0020)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

import custom_components.danalock_ble as integration


def _manifest() -> dict[str, Any]:
    path = Path(integration.__file__).parent / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_manifest_has_no_bluetooth_discovery_matcher() -> None:
    """An unused matcher spawns failing UnknownStep discovery flows
    (spec 0020 R1)."""
    assert "bluetooth" not in _manifest()


def test_manifest_orders_bluetooth_setup_without_a_hard_dependency() -> None:
    """Bluetooth is set up first when configured, but a host without a
    Bluetooth adapter can still load the entry (spec 0020 R2)."""
    manifest = _manifest()
    assert "bluetooth" in manifest["after_dependencies"]
    assert "bluetooth" not in manifest.get("dependencies", [])


def test_config_schema_is_config_entry_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An integration defining async_setup declares a schema that rejects
    YAML setup (spec 0020 R3)."""
    schema = integration.CONFIG_SCHEMA
    assert callable(schema)
    assert schema({}) == {}
    with caplog.at_level(logging.ERROR):
        assert schema({integration.DOMAIN: {}}) == {integration.DOMAIN: {}}
    assert "does not support YAML setup" in caplog.text
