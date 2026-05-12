"""Thin typed layer over kiutils for reading KiCAD board files.

This module isolates the kiutils dependency behind a small typed surface so the
rest of the codebase never imports kiutils directly. The PRD calls out a single
boundary that absorbs file-format churn — this is it.
"""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kiutils.board import Board


class KicadIoError(Exception):
    """Raised when a KiCAD file cannot be opened or parsed."""


@dataclass(frozen=True)
class Footprint:
    """A footprint placement read from a ``.kicad_pcb`` file.

    Attributes:
        reference: The visible refdes (e.g. ``R1``).
        uuid: The footprint's own tstamp UUID (unique within this PCB).
        symbol_uuid: The shared-schematic symbol's UUID — derived from the
            footprint's ``path`` field. Stable across projects that share the
            same hierarchical sheet, and therefore the right key for
            cross-project footprint matching.
        sheet_file: Value of the footprint's ``Sheetfile`` property — the
            hierarchical sheet file that owns this footprint, relative to the
            project root. ``None`` if the footprint has no such property.
        layer: Canonical layer name (e.g. ``F.Cu``).
        position: ``(x, y)`` in mm, in the PCB's coordinate frame.
        rotation: Rotation in degrees.
    """

    reference: str
    uuid: str
    symbol_uuid: str | None
    sheet_file: str | None
    layer: str
    position: tuple[float, float]
    rotation: float


@dataclass(frozen=True)
class Pcb:
    """A loaded ``.kicad_pcb`` file."""

    path: Path
    footprints: tuple[Footprint, ...]


def load_pcb(path: Path) -> Pcb:
    """Load a ``.kicad_pcb`` file into the typed ``Pcb`` model.

    Args:
        path: Filesystem path to the board file.

    Returns:
        A ``Pcb`` populated with the file's footprints.

    Raises:
        KicadIoError: If the file does not exist or cannot be parsed.
    """
    if not path.exists():
        msg = f"PCB file not found: {path}"
        raise KicadIoError(msg)
    try:
        board = Board.from_file(str(path))
    except Exception as exc:
        msg = f"Failed to parse PCB {path}: {exc}"
        raise KicadIoError(msg) from exc

    footprints = tuple(_convert_footprint(fp) for fp in board.footprints)
    return Pcb(path=path, footprints=footprints)


def _convert_footprint(fp: object) -> Footprint:
    """Convert a kiutils ``Footprint`` to our typed model."""
    properties: dict[str, str] = dict(getattr(fp, "properties", {}) or {})
    reference = properties.get("Reference", "")
    sheet_file = properties.get("Sheetfile") or properties.get("Sheet file")

    position = getattr(fp, "position", None)
    x = float(getattr(position, "X", 0.0) or 0.0)
    y = float(getattr(position, "Y", 0.0) or 0.0)
    rotation = float(getattr(position, "angle", 0.0) or 0.0)

    return Footprint(
        reference=reference,
        uuid=str(getattr(fp, "tstamp", "") or ""),
        symbol_uuid=_extract_symbol_uuid(getattr(fp, "path", None)),
        sheet_file=sheet_file,
        layer=str(getattr(fp, "layer", "") or ""),
        position=(x, y),
        rotation=rotation,
    )


def _extract_symbol_uuid(path: str | None) -> str | None:
    """Pull the trailing UUID out of a hierarchical ``path`` value.

    KiCAD records each footprint's symbol location as ``/<sheet-uuid>/.../<symbol-uuid>``.
    For root-level sheets the path is a single ``/<symbol-uuid>``. We return the
    last segment, which is the symbol UUID we match across projects.
    """
    if not path:
        return None
    segments = [seg for seg in path.split("/") if seg]
    return segments[-1] if segments else None
