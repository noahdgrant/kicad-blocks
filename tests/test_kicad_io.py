"""Tests for the ``kicad_io`` module."""

from pathlib import Path

import pytest

from kicad_blocks.kicad_io import KicadIoError, load_pcb

FIXTURE = Path(__file__).parent / "fixtures" / "minimal" / "minimal.kicad_pcb"


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
