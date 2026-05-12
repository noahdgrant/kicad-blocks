"""Domain-core operations on a hierarchical-sheet block.

This slice only implements the read side: identifying which footprints in a
PCB belong to a given hierarchical sheet. ``apply`` / track + via / zone /
silkscreen handling all arrive in later slices.
"""

from __future__ import annotations

from pathlib import Path

from kicad_blocks.kicad_io import Footprint, Pcb


def footprints_in_sheet(pcb: Pcb, sheet: str | Path) -> list[Footprint]:
    """Return the footprints in ``pcb`` whose ``Sheetfile`` matches ``sheet``.

    Comparison uses POSIX-normalized paths so a config authored with backslashes
    matches a PCB that stores forward slashes (KiCAD always writes
    forward-slash paths, but configs are user-edited).

    Args:
        pcb: The loaded PCB.
        sheet: The hierarchical sheet file path, relative to the project root,
            as it appears in the footprint's ``Sheetfile`` property.

    Returns:
        Footprints that belong to ``sheet``, in source order.
    """
    needle = _normalize(str(sheet))
    return [fp for fp in pcb.footprints if fp.sheet_file and _normalize(fp.sheet_file) == needle]


def _normalize(path: str) -> str:
    """Lower-friction path comparison: forward slashes, no leading ``./``."""
    return path.replace("\\", "/").removeprefix("./")
