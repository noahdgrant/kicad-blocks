"""Tests for the ``block`` module: footprint membership and the apply planner."""

import math
from pathlib import Path

import pytest

from kicad_blocks.block import ApplyError, footprints_in_sheet, plan_apply
from kicad_blocks.kicad_io import load_pcb

FIXTURE = Path(__file__).parent / "fixtures" / "minimal" / "minimal.kicad_pcb"
SOURCE_PCB = Path(__file__).parent / "fixtures" / "reuse" / "source" / "source.kicad_pcb"
TARGET_PCB = Path(__file__).parent / "fixtures" / "reuse" / "target" / "target.kicad_pcb"


def test_extract_returns_only_footprints_on_sheet() -> None:
    """Sheet membership is determined by the Sheetfile property."""
    pcb = load_pcb(FIXTURE)
    mcu = footprints_in_sheet(pcb, "sheets/mcu.kicad_sch")
    refs = sorted(fp.reference for fp in mcu)
    assert refs == ["R1", "R2"]


def test_extract_handles_path_argument() -> None:
    """A ``Path`` is accepted as well as a string."""
    pcb = load_pcb(FIXTURE)
    mcu = footprints_in_sheet(pcb, Path("sheets/mcu.kicad_sch"))
    assert {fp.reference for fp in mcu} == {"R1", "R2"}


def test_extract_returns_empty_for_unknown_sheet() -> None:
    """No matching footprints yields an empty list, not an error."""
    pcb = load_pcb(FIXTURE)
    assert footprints_in_sheet(pcb, "sheets/nope.kicad_sch") == []


def test_extract_normalizes_path_separators() -> None:
    """Backslashes in the query are normalized so Windows authors of the config
    can collaborate with POSIX authors of the source PCB without surprise."""
    pcb = load_pcb(FIXTURE)
    mcu = footprints_in_sheet(pcb, "sheets\\mcu.kicad_sch")
    assert {fp.reference for fp in mcu} == {"R1", "R2"}


def test_plan_apply_anchors_target_on_anchor_refdes() -> None:
    """The target anchor refdes pins the target frame; the source anchor is
    found by symbol UUID, not by refdes (target has ANCHOR1, source has U1)."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    assert plan.source_anchor_ref == "U1"
    assert plan.target_anchor_ref == "ANCHOR1"
    assert plan.transform_angle_deg == 90.0
    # The anchor itself isn't in the placements list — it's already in place.
    placed_refs = {p.target_reference for p in plan.placements}
    assert placed_refs == {"R10", "R20"}


def test_plan_apply_translates_when_anchor_rotation_matches() -> None:
    """With matching anchor rotations the transform is pure translation."""
    source = load_pcb(SOURCE_PCB)

    # Synthesize a target whose anchor sits at the same rotation as the source.
    # We do this by re-using the source as the target; the anchor in both is U1
    # at (105, 60, 0), so the transform should be the identity.
    plan = plan_apply(
        source_pcb=source,
        target_pcb=source,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="U1",
    )

    by_ref = {p.target_reference: p for p in plan.placements}
    # Source R1 sits at (100, 50, 0). With identity transform the target is unchanged.
    r1 = by_ref["R1"]
    assert r1.target_position == (100.0, 50.0)
    assert r1.target_rotation == 0.0


def test_plan_apply_rotates_around_anchor_correctly() -> None:
    """A 90° anchor rotation rotates other footprints around the source anchor."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    # Source anchor U1 is at (105, 60, 0); target anchor ANCHOR1 is at (200, 200, 90).
    # Source R1 (100, 50, 0) is at offset (-5, -10) from the source anchor.
    # Rotated by +90°: offset becomes (10, -5); plus target anchor (200, 200) → (210, 195).
    by_ref = {p.target_reference: p for p in plan.placements}
    r10 = by_ref["R10"]
    assert math.isclose(r10.target_position[0], 210.0, abs_tol=1e-9)
    assert math.isclose(r10.target_position[1], 195.0, abs_tol=1e-9)
    # Source R1 rotation 0° + 90° transform = 90° in target.
    assert r10.target_rotation == 90.0


def test_plan_apply_uses_symbol_uuid_not_refdes() -> None:
    """The plan resolves source→target by symbol UUID; refdes mismatches don't matter."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    by_target_ref = {p.target_reference: p for p in plan.placements}
    # Source R1 (sym aaaa) maps to target R10 (same sym, different refdes).
    assert by_target_ref["R10"].symbol_uuid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert by_target_ref["R20"].symbol_uuid == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def test_plan_apply_fails_fast_on_missing_target_anchor() -> None:
    """A missing anchor refdes in the target is a clear, actionable error."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    with pytest.raises(ApplyError, match="anchor"):
        plan_apply(
            source_pcb=source,
            target_pcb=target,
            sheet="sheets/mcu.kicad_sch",
            anchor_ref="NONESUCH",
        )


def test_plan_apply_reports_unresolved_nets_instead_of_raising(tmp_path: Path) -> None:
    """A net in the source block but missing from the target surfaces in the plan.

    Slice 3 raised; Slice 4 lets the planner produce a plan with the unresolved
    list so ``reuse --dry-run`` can show the diff before the user fixes it. The
    CLI aborts the actual apply when ``unresolved_nets`` is non-empty.
    """
    # Build a target PCB missing the SIG net.
    target_text = TARGET_PCB.read_text()
    bad_target_text = target_text.replace('(net 3 "SIG")\n', "")
    bad_target = tmp_path / "target.kicad_pcb"
    bad_target.write_text(bad_target_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(bad_target)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    assert "SIG" in plan.unresolved_nets


def test_plan_apply_resolves_renamed_nets_via_overrides(tmp_path: Path) -> None:
    """A target with renamed nets resolves cleanly when overrides cover the diff."""
    # Rename the target's SIG net to SIG_T; without an override this would be unresolved.
    target_text = TARGET_PCB.read_text().replace('"SIG"', '"SIG_T"')
    renamed_target = tmp_path / "target.kicad_pcb"
    renamed_target.write_text(target_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(renamed_target)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
        net_overrides={"SIG": "SIG_T"},
    )

    assert plan.unresolved_nets == ()
    assert plan.net_map.lookup("SIG") == "SIG_T"
    assert plan.net_map.lookup("GND") == "GND"


def test_plan_apply_extracts_in_block_tracks() -> None:
    """Tracks whose endpoints land on in-block footprint pads are planned at target coords."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    # Two of the three source segments are fully in-block (SIG: R1↔U1, +3V3: R1↔U1);
    # the third (C1↔R1) crosses the boundary and must be excluded.
    assert len(plan.tracks) == 2
    sig_track = next(t for t in plan.tracks if t.net_name == "SIG")
    # Source SIG segment runs (100.75, 50) → (105, 60.95). With the 90° rotation
    # around U1 at source (105, 60) → target (200, 200, 90°): (sx, sy) maps to
    # (260 - sy, 95 + sx).
    assert math.isclose(sig_track.start[0], 210.0, abs_tol=1e-6)
    assert math.isclose(sig_track.start[1], 195.75, abs_tol=1e-6)
    assert math.isclose(sig_track.end[0], 199.05, abs_tol=1e-6)
    assert math.isclose(sig_track.end[1], 200.0, abs_tol=1e-6)
    assert sig_track.layer == "F.Cu"


def test_plan_apply_excludes_boundary_crossing_tracks() -> None:
    """A track with one endpoint outside the block is reported, not silently dropped."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    # The C1↔R1 segment (one endpoint outside the block) must be on the
    # excluded list so dry-run can surface it. Identify by start position.
    excluded_endpoints = {(t.start, t.end) for t in plan.excluded_tracks}
    assert ((49.25, 50.0), (99.25, 50.0)) in excluded_endpoints


def test_plan_apply_extracts_in_block_vias_with_layer_span() -> None:
    """A via on an in-block pad keeps its layer span and gets its position rotated."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    assert len(plan.vias) == 1
    via = plan.vias[0]
    assert via.layers == ("F.Cu", "B.Cu")
    assert via.net_name == "SIG"
    assert math.isclose(via.position[0], 210.0, abs_tol=1e-6)
    assert math.isclose(via.position[1], 195.75, abs_tol=1e-6)


def test_plan_apply_excludes_out_of_block_vias() -> None:
    """A via that lands on an out-of-block pad is excluded and surfaced for dry-run."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    excluded_positions = {v.position for v in plan.excluded_vias}
    assert (49.25, 50.0) in excluded_positions


def test_plan_apply_rewrites_track_nets_via_overrides(tmp_path: Path) -> None:
    """A renamed in-block net flows through to the planned track's ``net_name``."""
    target_text = TARGET_PCB.read_text().replace('"SIG"', '"SIG_T"')
    renamed_target = tmp_path / "target.kicad_pcb"
    renamed_target.write_text(target_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(renamed_target)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
        net_overrides={"SIG": "SIG_T"},
    )

    sig_tracks = [t for t in plan.tracks if t.net_name == "SIG_T"]
    assert sig_tracks
    sig_vias = [v for v in plan.vias if v.net_name == "SIG_T"]
    assert sig_vias


def test_plan_apply_reports_unmatched_source_footprints(tmp_path: Path) -> None:
    """Source footprints with no symbol-UUID match in the target are reported."""
    # Build a target that drops the R20 footprint (so source R2's symbol has no
    # target counterpart).
    target_text = TARGET_PCB.read_text()
    # Drop the R20 footprint section by slicing it out.
    marker = '(tstamp "bbbb2222'
    tstamp_pos = target_text.index(marker)
    start = target_text.rindex("(footprint", 0, tstamp_pos)
    end = target_text.index("  )\n", tstamp_pos) + len("  )\n")
    bad_target_text = target_text[:start] + target_text[end:]
    bad_target = tmp_path / "target.kicad_pcb"
    bad_target.write_text(bad_target_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(bad_target)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )
    assert "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb" in plan.unmatched_source


def test_plan_apply_extracts_in_block_zone_and_preserves_net_and_layer() -> None:
    """A zone whose outline lies inside the in-block footprint hull is carried over."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    assert len(plan.zones) == 1
    zone = plan.zones[0]
    assert zone.net_name == "GND"
    assert zone.layers == ("F.Cu",)


def test_plan_apply_excludes_out_of_block_zone() -> None:
    """A zone whose outline lies outside the hull is reported under ``excluded_zones``."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    assert len(plan.excluded_zones) == 1
    # Empty-net zone outside the hull.
    assert plan.excluded_zones[0].net_name == ""


def test_plan_apply_carries_in_block_graphic() -> None:
    """Board-level graphics inside the hull travel with the block."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    layers = {g.layer for g in plan.graphics}
    assert "F.SilkS" in layers
    # Two in-block graphics: the gr_text "MCU" and the gr_line.
    assert len(plan.graphics) == 2


def test_plan_apply_excludes_out_of_hull_graphic() -> None:
    """A board-level graphic outside the hull is reported under ``excluded_graphics``."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    # The "OUT" gr_text at (50, 50) is the only excluded graphic.
    assert len(plan.excluded_graphics) == 1
    assert (50.0, 50.0) in plan.excluded_graphics[0].points


def test_plan_apply_refuses_layer_stackup_mismatch(tmp_path: Path) -> None:
    """A source/target stackup diff raises ``ApplyError`` with a clear diff."""
    target_text = TARGET_PCB.read_text()
    # Strip the last user layer (Eco2.User) to force a stackup difference.
    bad_text = target_text.replace('    (43 "Eco2.User" user)\n', "")
    bad_target = tmp_path / "target.kicad_pcb"
    bad_target.write_text(bad_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(bad_target)

    with pytest.raises(ApplyError, match="layer stackup"):
        plan_apply(
            source_pcb=source,
            target_pcb=target,
            sheet="sheets/mcu.kicad_sch",
            anchor_ref="ANCHOR1",
        )


def test_plan_apply_layer_mismatch_override_proceeds(tmp_path: Path) -> None:
    """``allow_layer_mismatch=True`` lets the plan proceed despite a stackup diff."""
    target_text = TARGET_PCB.read_text()
    bad_text = target_text.replace('    (43 "Eco2.User" user)\n', "")
    bad_target = tmp_path / "target.kicad_pcb"
    bad_target.write_text(bad_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(bad_target)

    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
        allow_layer_mismatch=True,
    )

    # Plan still gets built; the layer_mismatch field flags the override.
    assert plan.layer_mismatch
    assert any("Eco2.User" in line for line in plan.layer_mismatch)
