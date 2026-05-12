"""Tests for the ``block`` module's footprint-membership half."""

from pathlib import Path

from kicad_blocks.block import footprints_in_sheet
from kicad_blocks.kicad_io import load_pcb

FIXTURE = Path(__file__).parent / "fixtures" / "minimal" / "minimal.kicad_pcb"


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
