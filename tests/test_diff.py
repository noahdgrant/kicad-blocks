"""Tests for the block-vs-target-PCB-region diff."""

from __future__ import annotations

from pathlib import Path

from kicad_blocks.block import plan_apply
from kicad_blocks.diff import compute_diff
from kicad_blocks.kicad_io import apply_placements, load_pcb

SOURCE_PCB = Path(__file__).parent / "fixtures" / "reuse" / "source" / "source.kicad_pcb"
TARGET_PCB = Path(__file__).parent / "fixtures" / "reuse" / "target" / "target.kicad_pcb"


def _stage_target(tmp_path: Path) -> Path:
    """Copy the target fixture into a writable scratch path."""
    scratch = tmp_path / "target.kicad_pcb"
    scratch.write_bytes(TARGET_PCB.read_bytes())
    return scratch


def _apply_source_to(target_path: Path) -> None:
    """Run the full plan_apply + apply_placements pipeline source → target."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(target_path)
    plan = plan_apply(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )
    apply_placements(
        target_path,
        [p.placement for p in plan.placements],
        tracks=plan.tracks,
        vias=plan.vias,
        zones=plan.zones,
        graphics=plan.graphics,
    )


def test_diff_after_apply_is_empty(tmp_path: Path) -> None:
    """After a fresh apply, the diff between source and target is empty."""
    target_path = _stage_target(tmp_path)
    _apply_source_to(target_path)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(target_path)
    diff = compute_diff(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    assert diff.is_empty, diff


def test_diff_detects_moved_footprints_before_apply() -> None:
    """A fresh target (footprints at origin) shows the in-block footprints as moved."""
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(TARGET_PCB)

    diff = compute_diff(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    moved_refs = {m.target_reference for m in diff.moved_footprints}
    assert moved_refs == {"R10", "R20"}
    r10 = next(m for m in diff.moved_footprints if m.target_reference == "R10")
    # Target currently has R10 at (0, 0, 0); source places it at (210, 195, 90).
    assert r10.from_position == (0.0, 0.0)
    assert r10.to_position == (210.0, 195.0)
    assert r10.to_rotation == 90.0


def test_diff_detects_removed_footprint_when_source_drops_one(tmp_path: Path) -> None:
    """A footprint on the sheet in target but no longer in source surfaces as removed."""
    # Drop R1 from source so its symbol UUID is gone from the source block.
    source_text = SOURCE_PCB.read_text()
    marker = '(tstamp "11111111-1111-1111-1111-111111111111")'
    start = source_text.rindex("(footprint", 0, source_text.index(marker))
    end = source_text.index("  )\n", source_text.index(marker)) + len("  )\n")
    trimmed_source = tmp_path / "source.kicad_pcb"
    trimmed_source.write_text(source_text[:start] + source_text[end:])

    source = load_pcb(trimmed_source)
    target = load_pcb(TARGET_PCB)

    diff = compute_diff(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    removed_refs = {r.target_reference for r in diff.removed_footprints}
    assert "R10" in removed_refs


def test_diff_reports_track_added_by_source(tmp_path: Path) -> None:
    """A target without the source's in-block tracks shows them as added."""
    target_path = _stage_target(tmp_path)
    source = load_pcb(SOURCE_PCB)
    target = load_pcb(target_path)

    diff = compute_diff(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
    )

    # Source has two in-block segments (SIG, +3V3); target starts with none.
    assert len(diff.added_tracks) == 2
    nets = {t.net_name for t in diff.added_tracks}
    assert nets == {"SIG", "+3V3"}


def test_diff_reports_net_rename_via_overrides(tmp_path: Path) -> None:
    """A target with a renamed SIG net surfaces the rename when overrides resolve it."""
    target_text = TARGET_PCB.read_text().replace('"SIG"', '"SIG_T"')
    renamed_target = tmp_path / "target.kicad_pcb"
    renamed_target.write_text(target_text)

    source = load_pcb(SOURCE_PCB)
    target = load_pcb(renamed_target)

    diff = compute_diff(
        source_pcb=source,
        target_pcb=target,
        sheet="sheets/mcu.kicad_sch",
        anchor_ref="ANCHOR1",
        net_overrides={"SIG": "SIG_T"},
    )

    renames = {(r.source_net, r.target_net) for r in diff.renamed_nets}
    assert ("SIG", "SIG_T") in renames
