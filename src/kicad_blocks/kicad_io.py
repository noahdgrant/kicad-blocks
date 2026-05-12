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
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kiutils.board import Board
from kiutils.items.brditems import Segment, Via
from kiutils.items.common import Position


class KicadIoError(Exception):
    """Raised when a KiCAD file cannot be opened, parsed, or written."""


@dataclass(frozen=True)
class Pad:
    """A footprint pad's relative position and net.

    The position is the pad's ``(x, y)`` in the footprint's local frame —
    callers can rotate by the footprint's rotation and add its position to get
    the absolute board-frame coordinate, which is what the in-block boundary
    check needs.
    """

    position: tuple[float, float]
    net: str


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
        pads: All pads (with relative position and net), in source order.
            Includes the unconnected ``""`` net entries; ``pad_nets`` is the
            filtered name-only summary.
    """

    reference: str
    uuid: str
    symbol_uuid: str | None
    sheet_file: str | None
    layer: str
    position: tuple[float, float]
    rotation: float
    pad_nets: tuple[str, ...] = ()
    pads: tuple[Pad, ...] = ()


@dataclass(frozen=True)
class Track:
    """A track segment read from a ``.kicad_pcb`` file.

    Net names are surfaced as strings; the file format stores integer net
    numbers, but those are board-local. Strings travel cleanly across boards.

    Attributes:
        start: ``(x, y)`` of the segment's start, in mm.
        end: ``(x, y)`` of the segment's end, in mm.
        width: Track width in mm.
        layer: Canonical layer name (e.g. ``F.Cu``).
        net: The net's name as it appears in the board's net table.
    """

    start: tuple[float, float]
    end: tuple[float, float]
    width: float
    layer: str
    net: str


@dataclass(frozen=True)
class ViaItem:
    """A via read from a ``.kicad_pcb`` file.

    Attributes:
        position: ``(x, y)`` of the via, in mm.
        size: Outer diameter in mm.
        drill: Drill diameter in mm.
        layers: Layer span (start … end) as canonical layer names.
        net: The net's name as it appears in the board's net table.
    """

    position: tuple[float, float]
    size: float
    drill: float
    layers: tuple[str, ...]
    net: str


@dataclass(frozen=True)
class Pcb:
    """A loaded ``.kicad_pcb`` file.

    Attributes:
        path: The on-disk path the PCB was loaded from.
        footprints: All footprints in the file, in source order.
        nets: All net names declared at the board level (excluding the
            unconnected ``""`` net), in source order.
        tracks: All track segments in the file, in source order.
        vias: All vias in the file, in source order.
    """

    path: Path
    footprints: tuple[Footprint, ...]
    nets: tuple[str, ...] = ()
    tracks: tuple[Track, ...] = ()
    vias: tuple[ViaItem, ...] = ()


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


@dataclass(frozen=True)
class TrackPlacement:
    """A track segment to append to a target PCB.

    Coordinates are already in the target's frame. ``net_name`` must exist in
    the target's net table; :func:`apply_placements` resolves it to the
    board-local net number on write.
    """

    start: tuple[float, float]
    end: tuple[float, float]
    width: float
    layer: str
    net_name: str


@dataclass(frozen=True)
class ViaPlacement:
    """A via to append to a target PCB.

    Coordinates are already in the target's frame. ``net_name`` is resolved to
    the target's net number on write; see :class:`TrackPlacement`.
    """

    position: tuple[float, float]
    size: float
    drill: float
    layers: tuple[str, ...]
    net_name: str


def load_pcb(path: Path) -> Pcb:
    """Load a ``.kicad_pcb`` file into the typed ``Pcb`` model.

    Args:
        path: Filesystem path to the board file.

    Returns:
        A ``Pcb`` populated with the file's footprints, net names, tracks, and
        vias.

    Raises:
        KicadIoError: If the file does not exist or cannot be parsed.
    """
    board = _load_board(path)
    footprints = tuple(_convert_footprint(fp) for fp in board.footprints)
    nets = tuple(net.name for net in board.nets if net.name)
    nets_by_number: dict[int, str] = {int(n.number): str(n.name or "") for n in board.nets}
    tracks: list[Track] = []
    vias: list[ViaItem] = []
    for item in board.traceItems:
        if isinstance(item, Segment):
            tracks.append(_convert_segment(item, nets_by_number))
        elif isinstance(item, Via):
            vias.append(_convert_via(item, nets_by_number))
    return Pcb(
        path=path,
        footprints=footprints,
        nets=nets,
        tracks=tuple(tracks),
        vias=tuple(vias),
    )


def apply_placements(
    path: Path,
    placements: Sequence[FootprintPlacement],
    *,
    tracks: Sequence[TrackPlacement] = (),
    vias: Sequence[ViaPlacement] = (),
) -> None:
    """Apply mutations to the PCB at ``path``, atomically.

    Footprints in ``placements`` are *moved* (located by symbol UUID and
    overwritten in place). Tracks and vias are *appended* to the board's
    routing — Slice 5 does not remove or deduplicate existing routing; that
    arrives with the sync apply (Slice 8).

    The write is atomic: the result is composed in a temp file in the same
    directory and ``os.replace``d onto the original. On any failure the
    original is left untouched.

    Args:
        path: Path to the target board file.
        placements: Footprint placements to apply.
        tracks: Track segments to append, with net names already resolved to
            target-side spelling.
        vias: Vias to append, same convention as ``tracks``.

    Raises:
        KicadIoError: If the file cannot be read or written, if any footprint
            placement does not match the target, or if any track/via references
            a net name absent from the target's net table.
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

    nets_by_name: dict[str, int] = {str(n.name or ""): int(n.number) for n in board.nets}
    missing_nets: list[str] = []
    for placement in tracks:
        if placement.net_name not in nets_by_name:
            missing_nets.append(placement.net_name)
    for via_placement in vias:
        if via_placement.net_name not in nets_by_name:
            missing_nets.append(via_placement.net_name)
    if missing_nets:
        msg = f"target PCB is missing net(s): {', '.join(sorted(set(missing_nets)))}"
        raise KicadIoError(msg)

    for placement in placements:
        for fp in by_symbol_uuid[placement.symbol_uuid]:
            _mutate_footprint(fp, placement)

    for track_placement in tracks:
        board.traceItems.append(_build_segment(track_placement, nets_by_name))
    for via_placement in vias:
        board.traceItems.append(_build_via(via_placement, nets_by_name))

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
    pads: list[Pad] = []
    for pad in getattr(fp, "pads", []) or []:
        net = getattr(pad, "net", None)
        name = str(getattr(net, "name", "") or "")
        pad_pos = getattr(pad, "position", None)
        px = float(getattr(pad_pos, "X", 0.0) or 0.0)
        py = float(getattr(pad_pos, "Y", 0.0) or 0.0)
        pads.append(Pad(position=(px, py), net=name))
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
        pads=tuple(pads),
    )


def _convert_segment(item: Segment, nets_by_number: dict[int, str]) -> Track:
    """Convert a kiutils ``Segment`` to our typed :class:`Track` model."""
    start_x = float(getattr(item.start, "X", 0.0) or 0.0)
    start_y = float(getattr(item.start, "Y", 0.0) or 0.0)
    end_x = float(getattr(item.end, "X", 0.0) or 0.0)
    end_y = float(getattr(item.end, "Y", 0.0) or 0.0)
    return Track(
        start=(start_x, start_y),
        end=(end_x, end_y),
        width=float(item.width or 0.0),
        layer=str(item.layer or ""),
        net=nets_by_number.get(int(item.net), ""),
    )


def _convert_via(item: Via, nets_by_number: dict[int, str]) -> ViaItem:
    """Convert a kiutils ``Via`` to our typed :class:`ViaItem` model."""
    x = float(getattr(item.position, "X", 0.0) or 0.0)
    y = float(getattr(item.position, "Y", 0.0) or 0.0)
    layers: tuple[str, ...] = tuple(str(layer) for layer in (item.layers or []))
    return ViaItem(
        position=(x, y),
        size=float(item.size or 0.0),
        drill=float(item.drill or 0.0),
        layers=layers,
        net=nets_by_number.get(int(item.net), ""),
    )


def _build_segment(placement: TrackPlacement, nets_by_name: dict[str, int]) -> Segment:
    """Build a kiutils ``Segment`` from a :class:`TrackPlacement`.

    A fresh UUID is minted for the segment's ``tstamp`` so the written file is
    parseable by kiutils (an empty ``tstamp`` round-trips as ``(tstamp )``,
    which fails reparse) and so re-applies don't collide with existing items.
    """
    return Segment(
        start=Position(X=placement.start[0], Y=placement.start[1]),
        end=Position(X=placement.end[0], Y=placement.end[1]),
        width=placement.width,
        layer=placement.layer,
        net=nets_by_name[placement.net_name],
        tstamp=str(uuid.uuid4()),
    )


def _build_via(placement: ViaPlacement, nets_by_name: dict[str, int]) -> Via:
    """Build a kiutils ``Via`` from a :class:`ViaPlacement`.

    See :func:`_build_segment` for the tstamp rationale.
    """
    return Via(
        position=Position(X=placement.position[0], Y=placement.position[1]),
        size=placement.size,
        drill=placement.drill,
        layers=list(placement.layers),
        net=nets_by_name[placement.net_name],
        tstamp=str(uuid.uuid4()),
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
