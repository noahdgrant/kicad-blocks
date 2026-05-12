"""Structured diff between a source block and a target PCB's block region.

The sync workflow's read side. Given a source PCB, a target PCB, and the same
``sheet``/``anchor_ref`` pair the user would pass to ``reuse``, computes what
would change if the apply were re-run today:

- footprints that have moved (matched by symbol UUID; target's current position
  differs from where the source dictates they belong),
- footprints the source has dropped (still present on the sheet in the target),
- footprints the source still has but with no target counterpart,
- in-block tracks the source produces that aren't in the target yet,
- in-block tracks in the target that the source no longer produces,
- nets that map source → target through a different name (auto or via override).

The diff is intentionally permissive about layer stackup differences — it passes
``allow_layer_mismatch=True`` so the user sees the diff even when ``reuse``
would refuse. The CLI surfaces both pieces of information separately.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from kicad_blocks.block import footprints_in_sheet, plan_apply
from kicad_blocks.kicad_io import Footprint, Pcb, Track

_COORD_TOLERANCE_MM = 1e-3
_PAD_TOLERANCE_MM = 1e-3


@dataclass(frozen=True)
class MovedFootprint:
    """A footprint matched by symbol UUID whose current target position differs."""

    symbol_uuid: str
    target_reference: str
    from_position: tuple[float, float]
    to_position: tuple[float, float]
    from_rotation: float
    to_rotation: float


@dataclass(frozen=True)
class AddedFootprint:
    """A source-block footprint with no symbol counterpart in the target."""

    symbol_uuid: str
    source_reference: str


@dataclass(frozen=True)
class RemovedFootprint:
    """A target footprint on the block's sheet with no source counterpart."""

    symbol_uuid: str
    target_reference: str


@dataclass(frozen=True)
class TrackChange:
    """A track segment added or removed by the diff."""

    start: tuple[float, float]
    end: tuple[float, float]
    width: float
    layer: str
    net_name: str


@dataclass(frozen=True)
class RenamedNet:
    """A source-block net that resolves to a differently-named target net."""

    source_net: str
    target_net: str


@dataclass(frozen=True)
class BlockDiff:
    """The full structured diff of a source block against a target PCB region."""

    moved_footprints: tuple[MovedFootprint, ...] = ()
    added_footprints: tuple[AddedFootprint, ...] = ()
    removed_footprints: tuple[RemovedFootprint, ...] = ()
    added_tracks: tuple[TrackChange, ...] = ()
    removed_tracks: tuple[TrackChange, ...] = ()
    renamed_nets: tuple[RenamedNet, ...] = ()

    @property
    def is_empty(self) -> bool:
        """Return ``True`` if the diff has no changes in any category."""
        return not (
            self.moved_footprints
            or self.added_footprints
            or self.removed_footprints
            or self.added_tracks
            or self.removed_tracks
            or self.renamed_nets
        )


def compute_diff(
    *,
    source_pcb: Pcb,
    target_pcb: Pcb,
    sheet: str | Path,
    anchor_ref: str,
    net_overrides: Mapping[str, str] | None = None,
) -> BlockDiff:
    """Compare what ``plan_apply`` would do against the target's current state.

    Args:
        source_pcb: The source PCB whose layout is canonical for the block.
        target_pcb: The target PCB.
        sheet: The shared sheet path used to scope the block.
        anchor_ref: The anchor footprint's refdes in the target PCB.
        net_overrides: Optional source-name → target-name overrides from the
            config's ``[blocks.<name>.net_map]`` table.

    Returns:
        A structured :class:`BlockDiff`.
    """
    plan = plan_apply(
        source_pcb=source_pcb,
        target_pcb=target_pcb,
        sheet=sheet,
        anchor_ref=anchor_ref,
        net_overrides=net_overrides,
        allow_layer_mismatch=True,
    )

    target_by_symbol: dict[str, Footprint] = {
        fp.symbol_uuid: fp for fp in target_pcb.footprints if fp.symbol_uuid is not None
    }

    moved: list[MovedFootprint] = []
    for placement in plan.placements:
        target_fp = target_by_symbol.get(placement.symbol_uuid)
        if target_fp is None:
            continue
        if _positions_differ(target_fp.position, placement.target_position) or not math.isclose(
            target_fp.rotation, placement.target_rotation, abs_tol=_COORD_TOLERANCE_MM
        ):
            moved.append(
                MovedFootprint(
                    symbol_uuid=placement.symbol_uuid,
                    target_reference=placement.target_reference,
                    from_position=target_fp.position,
                    to_position=placement.target_position,
                    from_rotation=target_fp.rotation,
                    to_rotation=placement.target_rotation,
                )
            )

    source_block = footprints_in_sheet(source_pcb, sheet)
    source_symbol_uuids = {fp.symbol_uuid for fp in source_block if fp.symbol_uuid is not None}

    added: list[AddedFootprint] = []
    for fp in source_block:
        if fp.symbol_uuid is None:
            continue
        if fp.symbol_uuid not in target_by_symbol:
            added.append(AddedFootprint(symbol_uuid=fp.symbol_uuid, source_reference=fp.reference))

    target_block = footprints_in_sheet(target_pcb, sheet)
    removed: list[RemovedFootprint] = []
    for fp in target_block:
        if fp.symbol_uuid is None or fp.symbol_uuid in source_symbol_uuids:
            continue
        removed.append(RemovedFootprint(symbol_uuid=fp.symbol_uuid, target_reference=fp.reference))

    planned_tracks = tuple(
        TrackChange(
            start=t.start,
            end=t.end,
            width=t.width,
            layer=t.layer,
            net_name=t.net_name,
        )
        for t in plan.tracks
    )
    target_in_block_tracks = _target_in_block_tracks(target_pcb, target_block)
    target_track_changes = tuple(
        TrackChange(
            start=t.start,
            end=t.end,
            width=t.width,
            layer=t.layer,
            net_name=t.net,
        )
        for t in target_in_block_tracks
    )

    added_tracks = tuple(t for t in planned_tracks if not _track_in(t, target_track_changes))
    removed_tracks = tuple(t for t in target_track_changes if not _track_in(t, planned_tracks))

    renamed = tuple(
        RenamedNet(source_net=src, target_net=dst)
        for src, dst in sorted(plan.net_map.mapping.items())
        if src != dst
    )

    return BlockDiff(
        moved_footprints=tuple(moved),
        added_footprints=tuple(added),
        removed_footprints=tuple(removed),
        added_tracks=added_tracks,
        removed_tracks=removed_tracks,
        renamed_nets=renamed,
    )


def _positions_differ(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Return ``True`` when the two ``(x, y)`` points differ beyond floating-point noise."""
    return not (
        math.isclose(a[0], b[0], abs_tol=_COORD_TOLERANCE_MM)
        and math.isclose(a[1], b[1], abs_tol=_COORD_TOLERANCE_MM)
    )


def _track_in(track: TrackChange, others: tuple[TrackChange, ...]) -> bool:
    """Return ``True`` when ``others`` contains a track matching ``track`` within tolerance."""
    for other in others:
        if (
            track.layer == other.layer
            and track.net_name == other.net_name
            and math.isclose(track.width, other.width, abs_tol=_COORD_TOLERANCE_MM)
            and not _positions_differ(track.start, other.start)
            and not _positions_differ(track.end, other.end)
        ):
            return True
    return False


def _target_in_block_tracks(target_pcb: Pcb, target_block: list[Footprint]) -> list[Track]:
    """Return tracks whose both endpoints land on a pad of an in-block target footprint."""
    pad_positions = _absolute_pad_positions(target_block)
    in_block: list[Track] = []
    for track in target_pcb.tracks:
        if _is_near_any(track.start, pad_positions) and _is_near_any(track.end, pad_positions):
            in_block.append(track)
    return in_block


def _absolute_pad_positions(footprints: list[Footprint]) -> list[tuple[float, float]]:
    """Return every pad's absolute world position across ``footprints``."""
    positions: list[tuple[float, float]] = []
    for fp in footprints:
        cos_r, sin_r = _cos_sin(fp.rotation)
        fx, fy = fp.position
        for pad in fp.pads:
            px, py = pad.position
            positions.append((fx + cos_r * px - sin_r * py, fy + sin_r * px + cos_r * py))
    return positions


def _cos_sin(angle_deg: float) -> tuple[float, float]:
    """Return ``(cos, sin)`` for ``angle_deg`` with axis-aligned angles exact."""
    a = angle_deg % 360.0
    if a == 0.0:
        return (1.0, 0.0)
    if a == 90.0:
        return (0.0, 1.0)
    if a == 180.0:
        return (-1.0, 0.0)
    if a == 270.0:
        return (0.0, -1.0)
    theta = math.radians(a)
    return (math.cos(theta), math.sin(theta))


def _is_near_any(point: tuple[float, float], candidates: list[tuple[float, float]]) -> bool:
    """Return ``True`` if ``point`` is within :data:`_PAD_TOLERANCE_MM` of any candidate."""
    px, py = point
    for cx, cy in candidates:
        if abs(px - cx) <= _PAD_TOLERANCE_MM and abs(py - cy) <= _PAD_TOLERANCE_MM:
            return True
    return False
