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


def test_plan_apply_fails_fast_on_net_mismatch(tmp_path: Path) -> None:
    """An in-block source net not present in the target aborts before any write."""
    # Build a target PCB missing the SIG net.
    target_text = TARGET_PCB.read_text()
    bad_target_text = target_text.replace('(net 3 "SIG")\n', "").replace(
        '(net 3 "SIG")', '(net 99 "OTHER")'
    )
    bad_target = tmp_path / "target.kicad_pcb"
    bad_target.write_text(bad_target_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(bad_target)

    with pytest.raises(ApplyError, match="net"):
        plan_apply(
            source_pcb=source,
            target_pcb=target,
            sheet="sheets/mcu.kicad_sch",
            anchor_ref="ANCHOR1",
        )


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
