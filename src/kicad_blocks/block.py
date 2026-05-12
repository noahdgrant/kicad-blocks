"""Domain-core operations on a hierarchical-sheet block.

Two halves live here:

- The read side (``footprints_in_sheet``) — given a parsed PCB and a sheet
  path, return the footprints whose ``Sheetfile`` matches.
- The write-path planner (``plan_apply``) — given a source PCB, a target PCB,
  a sheet, and the target's anchor refdes, return a structured
  :class:`ApplyPlan` that says exactly which target footprints get moved where
  and which in-block tracks/vias get appended to the target.

Slice 3 was footprint-only with a stub net check; Slice 4 added the real
``net_map`` module. Slice 5 carries tracks and vias through the same pipeline:
items with both endpoints landing on in-block footprint pads are transformed
into the target frame and surfaced on the plan, items that cross the boundary
are reported under ``excluded_*`` so dry-run can show them. Zones and
silkscreen still arrive in later slices.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from kicad_blocks.kicad_io import (
    Footprint,
    FootprintPlacement,
    Pcb,
    Track,
    TrackPlacement,
    ViaItem,
    ViaPlacement,
)
from kicad_blocks.net_map import NetMap
from kicad_blocks.net_map import build as build_net_map
from kicad_blocks.transform import Transform


class ApplyError(Exception):
    """Raised when an apply cannot be planned (missing anchor, net mismatch, …)."""


@dataclass(frozen=True)
class PlannedPlacement:
    """A single planned mutation in an :class:`ApplyPlan`.

    Carries both source and target coordinates so dry-run output can show what
    moves to where; the ``placement`` field is what :func:`kicad_io.apply_placements`
    actually consumes.
    """

    symbol_uuid: str
    source_reference: str
    target_reference: str
    source_position: tuple[float, float]
    source_rotation: float
    target_position: tuple[float, float]
    target_rotation: float
    layer: str

    @property
    def placement(self) -> FootprintPlacement:
        """Return the kicad_io-level mutation this plan entry produces."""
        return FootprintPlacement(
            symbol_uuid=self.symbol_uuid,
            position=self.target_position,
            rotation=self.target_rotation,
            layer=self.layer,
        )


def _empty_net_map() -> NetMap:
    return NetMap(mapping={})


@dataclass(frozen=True)
class ApplyPlan:
    """The full plan produced by :func:`plan_apply`.

    Attributes:
        sheet: The sheet path used to scope the block.
        source_anchor_ref: Refdes of the source-side anchor (looked up by
            matching the target anchor's symbol UUID — may differ from
            ``target_anchor_ref``).
        target_anchor_ref: Refdes of the target-side anchor.
        transform_angle_deg: The net rotation the transform applies.
        placements: Footprint placements to write to the target, in source
            order. The anchor itself is excluded — it's already in place.
        unmatched_source: Symbol UUIDs of source-block footprints that had no
            counterpart in the target. Surfaced so dry-run can report them;
            the apply itself does not write anything for these.
        net_map: Resolved source-net → target-net mapping for this block.
        unresolved_nets: Source net names with no auto-match and no override
            entry pointing at an existing target net. Non-empty means the
            actual apply must be refused; ``--dry-run`` still prints the plan
            so the user can see what to fix.
        tracks: Track segments to append to the target, with endpoints already
            transformed into the target frame and net names rewritten through
            ``net_map``. Empty when no in-block tracks were found.
        vias: Vias to append, same convention as ``tracks``.
        excluded_tracks: Source-frame tracks whose endpoints don't both land
            on in-block footprint pads. Surfaced for ``--dry-run`` so the user
            knows what was left behind.
        excluded_vias: Source-frame vias whose position doesn't land on an
            in-block footprint pad.
    """

    sheet: str
    source_anchor_ref: str
    target_anchor_ref: str
    transform_angle_deg: float
    placements: tuple[PlannedPlacement, ...]
    unmatched_source: tuple[str, ...]
    net_map: NetMap = field(default_factory=_empty_net_map)
    unresolved_nets: tuple[str, ...] = ()
    tracks: tuple[TrackPlacement, ...] = ()
    vias: tuple[ViaPlacement, ...] = ()
    excluded_tracks: tuple[Track, ...] = ()
    excluded_vias: tuple[ViaItem, ...] = ()


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


def plan_apply(
    *,
    source_pcb: Pcb,
    target_pcb: Pcb,
    sheet: str | Path,
    anchor_ref: str,
    net_overrides: Mapping[str, str] | None = None,
) -> ApplyPlan:
    """Plan the footprint half of ``reuse`` for a single block.

    Steps:

    1. Find the target anchor footprint by refdes.
    2. Use its symbol UUID to find the source-side anchor.
    3. Build the affine transform that carries the source frame to the target
       anchor's frame.
    4. For each in-block source footprint (excluding the anchor), find the
       target footprint by symbol UUID and compute its new position/rotation.
    5. Resolve every net referenced by an in-block source footprint against
       the target's net list, applying ``net_overrides`` for divergent names.

    Net resolution is permissive: unresolved names are returned on the plan
    (``unresolved_nets``) rather than raised, so the dry-run path can show the
    full diff. The CLI refuses the actual apply when ``unresolved_nets`` is
    non-empty.

    Args:
        source_pcb: The source PCB whose layout is canonical for the block.
        target_pcb: The target PCB; ``anchor_ref`` is its anchor refdes.
        sheet: The shared sheet path used to scope the block.
        anchor_ref: The anchor footprint's refdes in the target PCB.
        net_overrides: Optional source-name → target-name overrides from the
            config's ``[blocks.<name>.net_map]`` table.

    Returns:
        A structured :class:`ApplyPlan`.

    Raises:
        ApplyError: If the anchor can't be found or the source has no matching
            anchor symbol. Net mismatches do *not* raise — they populate
            ``unresolved_nets`` on the returned plan.
    """
    target_anchor = _find_by_reference(target_pcb, anchor_ref)
    if target_anchor is None:
        msg = f"anchor footprint '{anchor_ref}' not found in target PCB {target_pcb.path}"
        raise ApplyError(msg)
    if target_anchor.symbol_uuid is None:
        msg = (
            f"anchor footprint '{anchor_ref}' in target PCB {target_pcb.path} "
            f"has no symbol UUID — cannot cross-match with the source"
        )
        raise ApplyError(msg)

    source_block = footprints_in_sheet(source_pcb, sheet)
    if not source_block:
        msg = (
            f"no footprints found on sheet '{sheet}' in source PCB {source_pcb.path}; "
            f"check the sheet path matches the source's Sheetfile property"
        )
        raise ApplyError(msg)

    source_anchor = next(
        (fp for fp in source_block if fp.symbol_uuid == target_anchor.symbol_uuid),
        None,
    )
    if source_anchor is None:
        msg = (
            f"target anchor '{anchor_ref}' (symbol {target_anchor.symbol_uuid}) "
            f"has no counterpart on sheet '{sheet}' in source PCB {source_pcb.path}"
        )
        raise ApplyError(msg)

    block_nets = _collect_block_nets(source_block)
    net_map, unresolved_nets = build_net_map(
        source_nets=block_nets,
        target_nets=target_pcb.nets,
        overrides=net_overrides,
    )

    transform = Transform.from_anchors(
        source=source_anchor.position,
        source_angle=source_anchor.rotation,
        target=target_anchor.position,
        target_angle=target_anchor.rotation,
    )

    target_by_symbol: dict[str, Footprint] = {
        fp.symbol_uuid: fp for fp in target_pcb.footprints if fp.symbol_uuid is not None
    }

    placements: list[PlannedPlacement] = []
    unmatched: list[str] = []
    for fp in source_block:
        if fp.symbol_uuid is None or fp.symbol_uuid == source_anchor.symbol_uuid:
            continue
        target_fp = target_by_symbol.get(fp.symbol_uuid)
        if target_fp is None:
            unmatched.append(fp.symbol_uuid)
            continue
        placements.append(
            PlannedPlacement(
                symbol_uuid=fp.symbol_uuid,
                source_reference=fp.reference,
                target_reference=target_fp.reference,
                source_position=fp.position,
                source_rotation=fp.rotation,
                target_position=transform.apply(fp.position),
                target_rotation=transform.apply_angle(fp.rotation),
                layer=fp.layer,
            )
        )

    tracks, vias, excluded_tracks, excluded_vias = _plan_routing(
        source_pcb=source_pcb,
        in_block_footprints=source_block,
        transform=transform,
        net_map=net_map,
    )

    return ApplyPlan(
        sheet=str(sheet),
        source_anchor_ref=source_anchor.reference,
        target_anchor_ref=target_anchor.reference,
        transform_angle_deg=transform.angle_deg,
        placements=tuple(placements),
        unmatched_source=tuple(unmatched),
        net_map=net_map,
        unresolved_nets=tuple(unresolved_nets),
        tracks=tuple(tracks),
        vias=tuple(vias),
        excluded_tracks=tuple(excluded_tracks),
        excluded_vias=tuple(excluded_vias),
    )


_PAD_TOLERANCE_MM = 1e-3  # 1 micrometre — well below pad-pitch noise; KiCAD writes nm-precise.


def _plan_routing(
    *,
    source_pcb: Pcb,
    in_block_footprints: list[Footprint],
    transform: Transform,
    net_map: NetMap,
) -> tuple[list[TrackPlacement], list[ViaPlacement], list[Track], list[ViaItem]]:
    """Split source routing into kept-and-transformed vs. excluded-for-the-report lists.

    The strict-boundary rule: a track is kept iff *both* endpoints land on an
    in-block footprint pad; a via is kept iff its position lands on an in-block
    pad. Items where at least one endpoint matches an out-of-block pad are
    reported as ``excluded_*``. Items unrelated to the block (neither endpoint
    near any pad we care about) are ignored.
    """
    in_block_pad_positions = _absolute_pad_positions(in_block_footprints)
    out_of_block_footprints = [fp for fp in source_pcb.footprints if fp not in in_block_footprints]
    out_of_block_pad_positions = _absolute_pad_positions(out_of_block_footprints)

    tracks: list[TrackPlacement] = []
    excluded_tracks: list[Track] = []
    for track in source_pcb.tracks:
        start_in = _is_near_any(track.start, in_block_pad_positions)
        end_in = _is_near_any(track.end, in_block_pad_positions)
        start_out = _is_near_any(track.start, out_of_block_pad_positions)
        end_out = _is_near_any(track.end, out_of_block_pad_positions)
        if start_in and end_in:
            tracks.append(
                TrackPlacement(
                    start=transform.apply(track.start),
                    end=transform.apply(track.end),
                    width=track.width,
                    layer=track.layer,
                    net_name=net_map.lookup(track.net),
                )
            )
        elif (start_in or end_in) and (start_out or end_out):
            # Straddles the boundary — surface so dry-run shows what's not coming with.
            excluded_tracks.append(track)

    vias: list[ViaPlacement] = []
    excluded_vias: list[ViaItem] = []
    for via in source_pcb.vias:
        if _is_near_any(via.position, in_block_pad_positions):
            vias.append(
                ViaPlacement(
                    position=transform.apply(via.position),
                    size=via.size,
                    drill=via.drill,
                    layers=via.layers,
                    net_name=net_map.lookup(via.net),
                )
            )
        elif _is_near_any(via.position, out_of_block_pad_positions):
            excluded_vias.append(via)

    return tracks, vias, excluded_tracks, excluded_vias


def _absolute_pad_positions(footprints: list[Footprint]) -> list[tuple[float, float]]:
    """Return every pad's absolute world position across ``footprints``.

    Pads in the file are stored relative to the footprint origin; we rotate
    them by the footprint's rotation and add the footprint's position.
    """
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


def _collect_block_nets(source_block: list[Footprint]) -> list[str]:
    """Return the deduped list of net names referenced by ``source_block``'s pads."""
    seen: set[str] = set()
    nets: list[str] = []
    for fp in source_block:
        for net in fp.pad_nets:
            if net in seen:
                continue
            seen.add(net)
            nets.append(net)
    return nets


def _find_by_reference(pcb: Pcb, reference: str) -> Footprint | None:
    """Return the first footprint with ``reference``, or ``None``."""
    return next((fp for fp in pcb.footprints if fp.reference == reference), None)


def _normalize(path: str) -> str:
    """Lower-friction path comparison: forward slashes, no leading ``./``."""
    return path.replace("\\", "/").removeprefix("./")
