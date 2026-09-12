"""Module-level parallel update limits (spec 0024)."""

from __future__ import annotations

import importlib
from types import ModuleType

import pytest

from custom_components.danalock_ble import entity

# Expected module-level ``PARALLEL_UPDATES`` per platform module (spec 0024 R1).
PLATFORM_MODULES: list[tuple[str, int]] = [
    ("custom_components.danalock_ble.binary_sensor", 0),
    ("custom_components.danalock_ble.sensor", 0),
    ("custom_components.danalock_ble.lock", 1),
    ("custom_components.danalock_ble.select", 1),
    ("custom_components.danalock_ble.button", 1),
    ("custom_components.danalock_ble.update", 1),
]


def _platform_module(name: str) -> ModuleType:
    return importlib.import_module(name)


@pytest.mark.parametrize(("module_name", "expected"), PLATFORM_MODULES)
def test_platform_module_sets_parallel_updates(module_name: str, expected: int) -> None:
    """Each platform module defines PARALLEL_UPDATES with the spec value."""
    module = _platform_module(module_name)
    assert "PARALLEL_UPDATES" in vars(module)
    assert module.PARALLEL_UPDATES == expected


def test_parallel_updates_is_defined_per_module_not_inherited() -> None:
    """HA reads the constant from the platform module, not a base class."""
    assert "PARALLEL_UPDATES" not in vars(entity)
    for module_name, _ in PLATFORM_MODULES:
        assert "PARALLEL_UPDATES" in vars(_platform_module(module_name))


@pytest.mark.parametrize(("module_name", "expected"), PLATFORM_MODULES)
def test_parallel_updates_values_are_structural(module_name: str, expected: int) -> None:
    """The constant is structural (module dict), not HA platform internals."""
    module = _platform_module(module_name)
    assert vars(module)["PARALLEL_UPDATES"] == expected
