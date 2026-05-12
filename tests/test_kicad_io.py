"""Tests for the ``kicad_io`` module."""

import os
import shutil
from pathlib import Path

import pytest

from kicad_blocks.kicad_io import (
    FootprintPlacement,
    KicadIoError,
    apply_placements,
    load_pcb,
)

FIXTURE = Path(__file__).parent / "fixtures" / "minimal" / "minimal.kicad_pcb"
REUSE_TARGET = Path(__file__).parent / "fixtures" / "reuse" / "target" / "target.kicad_pcb"


def test_load_pcb_returns_typed_footprints() -> None:
    """``load_pcb`` should return a Pcb with typed footprints."""
    pcb = load_pcb(FIXTURE)

    assert pcb.path == FIXTURE
    refs = {fp.reference for fp in pcb.footprints}
    assert refs == {"R1", "R2", "C1"}


def test_footprint_carries_sheet_file_and_uuid() -> None:
    """Each footprint exposes its Sheetfile, symbol UUID, position, and layer."""
    pcb = load_pcb(FIXTURE)
    by_ref = {fp.reference: fp for fp in pcb.footprints}

    r1 = by_ref["R1"]
    assert r1.sheet_file == "sheets/mcu.kicad_sch"
    assert r1.symbol_uuid == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert r1.layer == "F.Cu"
    assert r1.position == (100.0, 50.0)
    assert r1.rotation == 0.0

    r2 = by_ref["R2"]
    assert r2.rotation == 90.0


def test_load_pcb_missing_file_raises() -> None:
    """A missing file path raises ``KicadIoError`` with the path in the message."""
    missing = Path("/nonexistent/does_not_exist.kicad_pcb")
    with pytest.raises(KicadIoError) as excinfo:
        load_pcb(missing)
    assert str(missing) in str(excinfo.value)


def test_load_pcb_unparseable_raises(tmp_path: Path) -> None:
    """A garbage file raises ``KicadIoError``."""
    bad = tmp_path / "bad.kicad_pcb"
    bad.write_text("this is not s-expressions")
    with pytest.raises(KicadIoError):
        load_pcb(bad)


def test_pcb_exposes_board_nets() -> None:
    """``Pcb.nets`` lists the named nets on the board (the ``""`` net is filtered)."""
    pcb = load_pcb(REUSE_TARGET)
    assert set(pcb.nets) == {"+3V3", "GND", "SIG"}


def test_footprint_carries_pad_nets() -> None:
    """Each footprint's pads contribute their net names to ``pad_nets``."""
    pcb = load_pcb(REUSE_TARGET)
    by_ref = {fp.reference: fp for fp in pcb.footprints}
    assert set(by_ref["R10"].pad_nets) == {"+3V3", "SIG"}
    assert set(by_ref["ANCHOR1"].pad_nets) == {"+3V3", "GND", "SIG"}


def test_apply_placements_updates_position_atomically(tmp_path: Path) -> None:
    """``apply_placements`` rewrites positions and is atomic (no temp leftovers)."""
    src = REUSE_TARGET
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(src, dst)

    apply_placements(
        dst,
        [
            FootprintPlacement(
                symbol_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                position=(150.0, 75.0),
                rotation=45.0,
                layer="F.Cu",
            )
        ],
    )

    after = load_pcb(dst)
    by_ref = {fp.reference: fp for fp in after.footprints}
    assert by_ref["R10"].position == (150.0, 75.0)
    assert by_ref["R10"].rotation == 45.0

    # No temp scraps left behind in the destination directory.
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "target.kicad_pcb"]
    assert leftovers == []


def test_apply_placements_rolls_back_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the temp file write blows up, the original target is byte-identical."""
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)
    original = dst.read_bytes()

    # Make ``os.replace`` blow up after the temp file is written but before
    # the atomic rename completes — the realistic failure mode for "the disk
    # ran out of space" or "permission denied on the final swap".
    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(KicadIoError, match="Failed to write"):
        apply_placements(
            dst,
            [
                FootprintPlacement(
                    symbol_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    position=(1.0, 1.0),
                    rotation=0.0,
                    layer="F.Cu",
                )
            ],
        )

    assert dst.read_bytes() == original
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "target.kicad_pcb"]
    assert leftovers == []


def test_apply_placements_rejects_unknown_symbol(tmp_path: Path) -> None:
    """If any placement's symbol UUID is absent from the target, the file is untouched."""
    dst = tmp_path / "target.kicad_pcb"
    shutil.copy(REUSE_TARGET, dst)
    original = dst.read_bytes()

    with pytest.raises(KicadIoError, match="no target footprint matched"):
        apply_placements(
            dst,
            [
                FootprintPlacement(
                    symbol_uuid="ffffffff-ffff-ffff-ffff-ffffffffffff",
                    position=(1.0, 1.0),
                    rotation=0.0,
                    layer="F.Cu",
                )
            ],
        )

    assert dst.read_bytes() == original
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "target.kicad_pcb"]
    assert leftovers == []
