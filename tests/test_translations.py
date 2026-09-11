"""Translation tests: every language file mirrors the English key tree.

Spec 0011: runtime translations live only in `translations/<BCP47>.json`
(no `strings.json`, no `[%key:...]` placeholders); each language file has a
key tree identical to `en.json`, non-empty string leaves, and select state
cards covering exactly the raw option strings of `select.py`.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from custom_components.danalock_ble.select import SETTING_OPTIONS

TRANSLATIONS_DIR = (
    Path(__file__).resolve().parent.parent
    / "custom_components"
    / "danalock_ble"
    / "translations"
)
ENGLISH_PATH = TRANSLATIONS_DIR / "en.json"
RUSSIAN_PATH = TRANSLATIONS_DIR / "ru.json"


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name} must contain a JSON object"
    return data


def _iter_leaves(
    node: Any, prefix: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Yield (path, value) for every non-object leaf."""
    if isinstance(node, dict):
        for key, value in node.items():
            assert isinstance(key, str), f"{prefix}: keys must be strings"
            yield from _iter_leaves(value, prefix + (key,))
    else:
        yield prefix, node


def _leaf_paths(data: dict[str, Any]) -> set[tuple[str, ...]]:
    return {path for path, _ in _iter_leaves(data)}


def _translation_files() -> list[Path]:
    return sorted(TRANSLATIONS_DIR.glob("*.json"))


def test_english_translation_is_valid() -> None:
    """en.json exists, parses, and is a non-empty object."""
    assert ENGLISH_PATH.is_file()
    assert _load(ENGLISH_PATH)


def test_russian_translation_exists() -> None:
    """The Russian runtime translation is shipped (spec 0011 R3)."""
    assert RUSSIAN_PATH.is_file()


@pytest.mark.parametrize("path", _translation_files(), ids=lambda path: path.name)
def test_key_tree_matches_english(path: Path) -> None:
    """Every language file has the exact English key tree (spec 0011 R3/R6)."""
    assert _leaf_paths(_load(path)) == _leaf_paths(_load(ENGLISH_PATH))


@pytest.mark.parametrize("path", _translation_files(), ids=lambda path: path.name)
def test_leaf_values_are_non_empty_strings(path: Path) -> None:
    """Every leaf is a non-empty string (spec 0011 R6)."""
    for leaf, value in _iter_leaves(_load(path)):
        assert isinstance(value, str), f"{path.name}: {leaf} is not a string"
        assert value.strip(), f"{path.name}: {leaf} is empty"


def test_key_placeholders_are_absent() -> None:
    """No core-only `[%key:...]` placeholder in any runtime translation."""
    for path in _translation_files():
        assert "[%key:" not in path.read_text(encoding="utf-8")


def test_strings_json_is_absent() -> None:
    """`strings.json` is a core build-time file: forbidden in a component."""
    assert not (TRANSLATIONS_DIR.parent / "strings.json").exists()


def test_russian_translation_differs_from_english() -> None:
    """The translation is real, not a byte copy of en.json (spec 0011 R6)."""
    assert RUSSIAN_PATH.read_bytes() != ENGLISH_PATH.read_bytes()


def test_select_state_cards_cover_raw_options() -> None:
    """Every language file maps each select raw option of select.py."""
    for path in _translation_files():
        tree = _load(path)
        for suffix, options in SETTING_OPTIONS.items():
            raw_options = {label for _, label in options}
            state = tree["entity"]["select"][suffix]["state"]
            assert set(state) == raw_options, (
                f"{path.name}: entity.select.{suffix}.state does not match "
                "the raw options of select.py"
            )
