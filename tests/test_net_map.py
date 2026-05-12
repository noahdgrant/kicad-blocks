"""Tests for the ``net_map`` module.

The module is pure: given source/target net lists plus user overrides, it
returns a :class:`NetMap` and a list of unresolved source net names. These
tests cover the cases called out in Slice 4: identical-name auto-match, case
sensitivity, hierarchical-path normalization, override precedence, and the
unresolved-net error path.
"""

from __future__ import annotations

from kicad_blocks.net_map import NetMap, build


def test_build_auto_matches_identical_names() -> None:
    """A source net with the same name as a target net auto-maps to itself."""
    net_map, unresolved = build(
        source_nets=["+3V3", "GND", "SIG"],
        target_nets=["+3V3", "GND", "SIG", "EXTRA"],
    )

    assert unresolved == []
    assert net_map.lookup("+3V3") == "+3V3"
    assert net_map.lookup("GND") == "GND"
    assert net_map.lookup("SIG") == "SIG"


def test_build_is_case_sensitive() -> None:
    """``GND`` and ``gnd`` are distinct net names; auto-match does not lowercase."""
    net_map, unresolved = build(
        source_nets=["GND"],
        target_nets=["gnd"],
    )

    assert unresolved == ["GND"]
    # No mapping was made — lookup returns the input unchanged.
    assert net_map.lookup("GND") == "GND"


def test_build_normalizes_leading_slashes() -> None:
    """A hierarchical source net with a leading slash matches an unprefixed target.

    KiCAD records nets that originate inside a sub-sheet with a leading slash
    (``/PWR/+3V3``) and root-level nets without (``+3V3``). The PRD calls this
    out as a normalization concern; the same physical net should match across
    both forms.
    """
    net_map, unresolved = build(
        source_nets=["/PWR/+3V3"],
        target_nets=["PWR/+3V3"],
    )

    assert unresolved == []
    # The mapping preserves the target's original spelling.
    assert net_map.lookup("/PWR/+3V3") == "PWR/+3V3"


def test_build_normalizes_target_leading_slash() -> None:
    """The slash normalization is symmetric: target has the slash, source does not."""
    net_map, unresolved = build(
        source_nets=["+3V3"],
        target_nets=["/+3V3"],
    )

    assert unresolved == []
    assert net_map.lookup("+3V3") == "/+3V3"


def test_build_override_takes_precedence_over_auto_match() -> None:
    """When the user declares an override, the explicit target is used."""
    # Source has +3V3 which would auto-match; override sends it to VCC instead.
    net_map, unresolved = build(
        source_nets=["+3V3"],
        target_nets=["+3V3", "VCC"],
        overrides={"+3V3": "VCC"},
    )

    assert unresolved == []
    assert net_map.lookup("+3V3") == "VCC"


def test_build_override_resolves_mismatched_name() -> None:
    """The canonical override case: source name differs from target name."""
    net_map, unresolved = build(
        source_nets=["+3v3_source"],
        target_nets=["+3V3"],
        overrides={"+3v3_source": "+3V3"},
    )

    assert unresolved == []
    assert net_map.lookup("+3v3_source") == "+3V3"


def test_build_override_to_missing_target_is_unresolved() -> None:
    """An override pointing at a target net that doesn't exist still fails."""
    net_map, unresolved = build(
        source_nets=["A"],
        target_nets=["B"],
        overrides={"A": "C"},
    )

    assert unresolved == ["A"]
    # No mapping was recorded for A.
    assert "A" not in net_map.mapping


def test_build_unresolved_lists_all_missing_nets_sorted() -> None:
    """All unresolved source nets are surfaced, deterministically ordered for diffing."""
    net_map, unresolved = build(
        source_nets=["ZED", "ALPHA", "BETA"],
        target_nets=["GND"],
    )

    assert unresolved == ["ALPHA", "BETA", "ZED"]
    assert net_map.mapping == {}


def test_build_dedupes_repeated_source_nets() -> None:
    """A source net referenced by multiple footprints is only mapped/reported once."""
    net_map, unresolved = build(
        source_nets=["GND", "GND", "GND"],
        target_nets=["GND"],
    )

    assert unresolved == []
    assert net_map.mapping == {"GND": "GND"}


def test_build_dedupes_unresolved() -> None:
    """A repeated unresolved source net appears once in the unresolved list."""
    _, unresolved = build(
        source_nets=["MISSING", "MISSING"],
        target_nets=[],
    )

    assert unresolved == ["MISSING"]


def test_net_map_lookup_returns_input_for_unmapped() -> None:
    """``lookup`` is a no-op for nets the build step never saw — callers ask only
    about in-block nets, which the unresolved check has already validated."""
    net_map = NetMap(mapping={"A": "B"})
    assert net_map.lookup("A") == "B"
    assert net_map.lookup("UNKNOWN") == "UNKNOWN"


def test_build_override_with_slash_in_key() -> None:
    """Override keys are also normalized: ``/X`` works as a key when source has ``X``."""
    net_map, unresolved = build(
        source_nets=["X"],
        target_nets=["TARGET"],
        overrides={"/X": "TARGET"},
    )

    assert unresolved == []
    assert net_map.lookup("X") == "TARGET"
