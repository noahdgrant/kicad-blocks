"""Thin typed layer over kiutils for reading + writing KiCAD board files.

This module isolates the kiutils dependency behind a small typed surface so the
rest of the codebase never imports kiutils directly. The PRD calls out a single
boundary that absorbs file-format churn — this is it.
"""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kiutils.board import Board


class KicadIoError(Exception):
    """Raised when a KiCAD file cannot be opened, parsed, or written."""


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
        pad_nets: Net names referenced by this footprint's pads. ``""`` (the
            unconnected net) is filtered out so callers can do a clean
            "what nets does this footprint touch?" comparison.
    """

    reference: str
    uuid: str
    symbol_uuid: str | None
    sheet_file: str | None
    layer: str
    position: tuple[float, float]
    rotation: float
    pad_nets: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pcb:
    """A loaded ``.kicad_pcb`` file.

    Attributes:
        path: The on-disk path the PCB was loaded from.
        footprints: All footprints in the file, in source order.
        nets: All net names declared at the board level (excluding the
            unconnected ``""`` net), in source order.
    """

    path: Path
    footprints: tuple[Footprint, ...]
    nets: tuple[str, ...] = ()


@dataclass(frozen=True)
class FootprintPlacement:
    """A single planned mutation to a target footprint.

    Identifies the footprint by ``symbol_uuid`` (the last segment of the
    footprint's hierarchical ``path``), which is stable across projects that
    share the same schematic sheet. Refdes is not used — see the PRD's
    "Footprint identity across projects" note.
    """

    symbol_uuid: str
    position: tuple[float, float]
    rotation: float
    layer: str


def load_pcb(path: Path) -> Pcb:
    """Load a ``.kicad_pcb`` file into the typed ``Pcb`` model.

    Args:
        path: Filesystem path to the board file.

    Returns:
        A ``Pcb`` populated with the file's footprints and net names.

    Raises:
        KicadIoError: If the file does not exist or cannot be parsed.
    """
    board = _load_board(path)
    footprints = tuple(_convert_footprint(fp) for fp in board.footprints)
    nets = tuple(net.name for net in board.nets if net.name)
    return Pcb(path=path, footprints=footprints, nets=nets)


def apply_placements(path: Path, placements: Sequence[FootprintPlacement]) -> None:
    """Apply ``placements`` to the PCB at ``path``, atomically.

    Loads the PCB, mutates each matching footprint's position/rotation/layer,
    writes the result to a temp file in the same directory, then ``os.replace``s
    it onto the original. On any failure the original is left untouched (the
    temp file is removed).

    Matching is by ``symbol_uuid`` — the last segment of the footprint's
    hierarchical ``path``. Placements that don't match any footprint raise
    :class:`KicadIoError` with the list of misses, before any write happens.

    Args:
        path: Path to the target board file.
        placements: Planned mutations.

    Raises:
        KicadIoError: If the file cannot be read, written, or if any placement
            does not match a footprint in the target.
    """
    board = _load_board(path)

    by_symbol_uuid: dict[str, list[object]] = {}
    for fp in board.footprints:
        sym = _extract_symbol_uuid(getattr(fp, "path", None))
        if sym is not None:
            by_symbol_uuid.setdefault(sym, []).append(fp)

    misses: list[str] = [p.symbol_uuid for p in placements if p.symbol_uuid not in by_symbol_uuid]
    if misses:
        msg = f"no target footprint matched symbol UUID(s): {', '.join(misses)}"
        raise KicadIoError(msg)

    for placement in placements:
        for fp in by_symbol_uuid[placement.symbol_uuid]:
            _mutate_footprint(fp, placement)

    _write_board_atomic(board, path)


def _load_board(path: Path) -> Board:
    """Load and return the raw kiutils Board, normalizing errors to KicadIoError."""
    if not path.exists():
        msg = f"PCB file not found: {path}"
        raise KicadIoError(msg)
    try:
        return Board.from_file(str(path))
    except Exception as exc:
        msg = f"Failed to parse PCB {path}: {exc}"
        raise KicadIoError(msg) from exc


def _write_board_atomic(board: Board, path: Path) -> None:
    """Write ``board`` to ``path`` via a same-directory temp file + ``os.replace``.

    ``os.replace`` is atomic on POSIX and Windows when source and destination
    live on the same filesystem; using the target's parent guarantees that.
    """
    parent = path.parent
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(board.to_sexpr())
        os.replace(tmp_path, path)  # noqa: PTH105 — kept on `os` so tests can monkeypatch the atomic-rename point
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        msg = f"Failed to write PCB {path}: {exc}"
        raise KicadIoError(msg) from exc


def _mutate_footprint(fp: object, placement: FootprintPlacement) -> None:
    """Update a kiutils Footprint's position, rotation, and layer in place."""
    position = getattr(fp, "position", None)
    if position is None:
        msg = f"footprint missing position attribute (symbol={placement.symbol_uuid})"
        raise KicadIoError(msg)
    position.X = placement.position[0]
    position.Y = placement.position[1]
    position.angle = placement.rotation
    setattr(fp, "layer", placement.layer)  # noqa: B010 — kiutils Footprint is opaque to pyright here


def _convert_footprint(fp: object) -> Footprint:
    """Convert a kiutils ``Footprint`` to our typed model."""
    properties: dict[str, str] = dict(getattr(fp, "properties", {}) or {})
    reference = properties.get("Reference", "")
    sheet_file = properties.get("Sheetfile") or properties.get("Sheet file")

    position = getattr(fp, "position", None)
    x = float(getattr(position, "X", 0.0) or 0.0)
    y = float(getattr(position, "Y", 0.0) or 0.0)
    rotation = float(getattr(position, "angle", 0.0) or 0.0)

    pad_nets: list[str] = []
    seen: set[str] = set()
    for pad in getattr(fp, "pads", []) or []:
        net = getattr(pad, "net", None)
        name = str(getattr(net, "name", "") or "")
        if name and name not in seen:
            seen.add(name)
            pad_nets.append(name)

    return Footprint(
        reference=reference,
        uuid=str(getattr(fp, "tstamp", "") or ""),
        symbol_uuid=_extract_symbol_uuid(getattr(fp, "path", None)),
        sheet_file=sheet_file,
        layer=str(getattr(fp, "layer", "") or ""),
        position=(x, y),
        rotation=rotation,
        pad_nets=tuple(pad_nets),
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
